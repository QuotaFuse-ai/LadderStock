# DEC-036 跨进程写 DuckDB：sync 入口进程级锁 + connect 有界退避重试

- 日期：2026-11-01
- 提出：后端架构师（QUOT-234，来源 QUOT-164 复核）
- 状态：生效（随 QUOT-234 实现落地）
- 关联：DEC-007 §2.3（DuckDB 幂等落库与事务边界）、DEC-006（生态边界，写侧仍只落本机）、
  `docs/ARCHITECTURE.md` §2「跨进程写同一个 `market.duckdb`」、`docs/CLI.md` §B/§F

## 1. 背景

QUOT-164 复核实测：3 个进程同时对同一个 `data/market.duckdb` 发起同步，
2 个抛未捕获的 `_duckdb.IOException`（文件被占用）、exit 1、没有重试也没有
`LadderStockError` 包装。脚本侧看到的现象是「同步随机挂两个」，退出码 1 不携带任何语义。

事实前提（本机复现，duckdb 2.0 / Windows + Linux 文案不同但同源）：

- DuckDB 的库文件锁是**独占**的，且贯穿整个连接生命周期——不是「写的一瞬间」冲突，
  是「别人连着，我就连不上」；`read_only=True` 也一样被挡；
- 一次 `ldst sync` 的连接窗口是分钟级（逐只代码取数），任何重试预算都不该指望
  「等它写完」；
- 同一个库文件只服务一个 `data_dir`，所以「同库串行」的锁粒度就是数据目录，不用更细。

## 2. 决定

### 2.1 两层，各管一类调用方

| 层 | 落点 | 覆盖谁 |
|---|---|---|
| 进程级写锁 | `src/ladderstock/data/db_lock.py:writer_lock()`，在 `ldst sync` 入口取 | 所有 sync 之间串行，包括 `--dataset`、`--full`、autopilot 起的第二次 sync |
| 有界退避重试 | `src/ladderstock/data/duckdb_store.py:connect()` | 不走 sync 入口的读写：`ldst review`、`archive.snapshot` 回放、`validate reconcile`、MCP/web 只读 |

理由：锁只能加在我们控制的入口上，而 `connect()` 是全仓库唯一的开库口子
（`archive.py`、`watermark` 读侧都经它）。只加锁不覆盖非 sync 调用方，只重试又扛不住
分钟级的 sync——两层叠起来才把「谁在写这个库」这件事收敛成一句话。

### 2.2 锁的实现：advisory lock，不是哨兵文件

- Windows `msvcrt.locking` / POSIX `fcntl.flock`，锁文件 `data/market.duckdb.lock`；
- **不用** `O_CREAT|O_EXCL` 哨兵文件：进程崩溃后哨兵文件就是死锁，还得自己判陈旧、
  自己清；advisory lock 由内核在 fd 关闭 / 进程退出时释放，崩溃不留锁；
- 锁只占文件开头 1 个字节，持有者说明（命令名 + pid）写在偏移 64 之后的定宽字段里。
  Windows 的字节范围锁会连读取一起挡，写在被锁范围内等于报错时读不到「谁占着」；
- 同进程重入不再向内核要第二次锁（引用计数）：`schedule run` 里再跑一轮 sync、
  测试里嵌套调用，第二次上锁会被**自己**挡住，那是自锁死不是等别人；
- 等待上限 `LDST_DB_LOCK_TIMEOUT`（秒，默认 30）。环境变量而不是新命令行开关：
  这是运维旋钮，不是每次调用要表达的意图，加 `--lock-timeout` 会改命令契约。

### 2.3 重试的判据：只有「被占用」才重试

`connect()` 按异常文案区分两类，四类真实文案（中英 Windows、POSIX）钉在单测里：

- 命中「already open / could not set a lock / being used by another process / 另一个程序正在使用」
  → 指数退避重试，最多 6 次、总等待约 1.25s（`CONNECT_BACKOFF_BASE` 0.05s、上限 0.4s），
  耗尽后 `LadderStockError(EXIT_DB_BUSY)`，报错里写清是哪个库、重试了几次、下一步做什么；
- 没命中（`Permission denied`、库损坏）→ **不重试**，立刻 `LadderStockError(EXIT_CONFIG)`
  并带上原始文案。数据目录配错不该被重试掩盖成「等一下就好」。

两条路径都保证 `connect()` 不往外抛裸 `_duckdb` 异常——这是 QUOT-164 的直接病灶。

### 2.4 退出码 10，不复用 4/8

`EXIT_DB_BUSY = 10`，登记在 `errors.py` 与 `docs/CLI.md` §F，明确标注**不属于对外契约的 6 个码**。

不复用的原因：4（数据源不可用）与 8（部分数据源失败）的主语是生态 peer。把库锁争用报成 4，
脚本会去看 `sources health`、换备源、重跑取数——全是错方向。10 让「本机串行问题」可单独分流。

顺带修的一处根因：`cli/main.py:main()` 原来在 `except LadderStockError` 里
`raise typer.Exit(exc.code)`。`app()` 已经退出它自己的上下文，这个 `Exit` 没人接，
最终是 traceback + 退出码 1——**所有**靠 `main()` 兜底的业务异常都在丢码。
改成 `sys.exit(exc.code)`；命令体内自行 `raise typer.Exit` 的路径不受影响。

## 3. 权衡与不做的

- **不做跨进程写队列 / IPC**：本机只有一个用户、同步由 autopilot 或人手动触发，
  排队语义用一把锁就够了；引入消息队列是过度设计（DEC-007 简单优先）。
- **不改成 WAL / 多写引擎**：DuckDB 是列存分析库，换引擎等于推翻 DEC-007 §2.3 的
  幂等落库与 Parquet 分区口径。并发压力真正的来源是「同一个 data_dir 起了多个 sync」，
  这是用法问题，用锁纠正比换存储便宜。
- **重试预算故意做小**（约 1.25s）：非 sync 调用方大多是读或小额写，等久了就是把
  `ldst review` 卡成十几秒。真要串行长任务，走 sync 那把锁。
- **不追求读写的细粒度并发**（例如多个只读进程共享一个实例）：DuckDB 同进程内本来
  就共享实例，跨进程只读并发在这套单机用量下不是瓶颈。

## 4. 验收

`tests/unit/data/test_duckdb_concurrency.py`：

- 3 进程并发写同一个库 → 退出码只允许 {0, 10}，stderr 无 `Traceback`；判定不读子进程
  stdout（那是管道时序），改按 code 前缀清点落库行数：成功的进程 60 行全在，
  被判占用的进程一行都没有（一个事务的原子性正好被并发验到）；
- 一个进程按住库不放 → 另一个进程拿到 `LadderStockError(10)`，不是裸 `IOException`；
- 退避序列单调、总等待有界；非锁冲突不重试且报 3；只读连接同样被包装；
- 锁：跨进程互斥且报错点名持有者 pid；释放后立即可拿；等待期内前一轮跑完则正常拿到；
  同进程重入不自锁死；`ldst sync` 入口确实在锁内（锁被占时 sync 子进程 exit 10 且一次取数不发）。

回归：既有单线程 / 多线程用例不改动，全量 1984 passed，ruff + mypy 全绿（Linux 侧的
`fcntl` 分支由 CI 覆盖，本地 Windows 走 `msvcrt` 分支）。
