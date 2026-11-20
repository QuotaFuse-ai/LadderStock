# DEC-086 FinanceCoffer 增强包（子模块安装 + 自动配置）

状态：**已决策，待实现**（六条口径已由产品负责人拍板，方案锁定）
日期：2026-11-16
关联：DEC-014（控制面白名单）、DEC-047（端口口径）、DEC-078（外部托管归属）、QUOT-618（端口平移）

## 一、结论

便携包 = **主包 + FinanceCoffer 增强包**。增强包只做**完整形态**：

```
postgres(pgvector) / redis / minio / migrate / api / worker   +   可选：embedding
```

三条硬规矩：

1. **体积不压**：按完整形态打，安装向导必须先告诉用户「多大、下载解压多久、多了什么、少了什么」，
   由用户点确认才继续（不静默扩容）。
2. **数据不落 C 盘**：全部数据目录跟随安装目录，落在 `<LadderStock>/file/modules/financecoffer/` 内；
   安装目录在 C 盘时向导明确提示换盘。
3. **卸载不留数据**：进程停干净后整目录删除，包括 pgdata / miniodata / redisdata。

不做「轻量模式」（只 PG + api + migrate 那种）：ETL 不跑就没有新数据进入，
留着只会让用户看到一个永远不更新的空库，比没有更糟。

## 二、六条已定决策

| # | 决策 | 落地做法 | 直接影响 |
| --- | --- | --- | --- |
| 1 | embedding **由用户勾选**，不是打包固定 | 安装向导第二屏一个勾选项，旁边写清两条路线的优劣（见 §三） | 体积 330MB ↔ 2.7GB；不勾则 FC 长期 `degraded`、向量检索不可用，结构化查询照常 |
| 2 | Redis 用**社区移植版**，但必须**三端适配** | 统一 `redis.conf` 模板 + 平台分支的二进制与启停封装（见 §四） | Linux / macOS 用官方原生，Windows 用固定 tag 的移植版，同一套控制面代码 |
| 3 | 体积**加上去**，且**必须让用户确认** | 安装向导展示「预计体积 / 解压后体积 / 各组件明细」+ 确认按钮 | 不允许静默安装、不允许默认勾选 embedding |
| 4 | 数据目录**安装目录内**，不放 C 盘 | 数据根固定为 `<安装目录>/file/modules/financecoffer/`，无 `%LOCALAPPDATA%` 回退 | 整个文件夹拷走即迁移；C 盘安装时向导提示换盘 |
| 5 | 卸载**不保留数据** | 停进程 → 删整个 `file/modules/financecoffer/`（含 pgdata / miniodata / redisdata / logs / run）→ 校验端口释放 | 重装必须重新初始化，没有「续用旧库」路径 |
| 6 | **不要轻量模式** | 删除该形态；状态机里只有「完整包」一条路径 | 不存在 `partial` 安装态，避免半套服务 |

## 三、包内容、体积与告知话术

| 组件 | 形态 | 解压后 | 说明 |
| --- | --- | --- | --- |
| postgres + pgvector | Windows 官方 zip（PG 17 系）+ `vector.dll` | ≈ 130 MB | 与 FC 的 `FC_DB_URL` 强绑定，`postgresql://` 是唯一 DSN 前缀 |
| redis | 平台分支（见 §四） | ≈ 12 MB | ETL 的 stream / DLQ / 控制键 |
| minio | `minio.exe`（Windows）/ 对应平台二进制 | ≈ 110 MB | 原始件对象存储、归档 |
| financecoffer | 源码或 wheel + extras 依赖 | ≈ 80 MB | `fcoffer` 入口，`requires-python>=3.11` |
| **小计（不含 embedding）** | | **≈ 330 MB** | 下载约 330MB，解压后约 500MB |
| embedding（可选） | `scripts/embedding_server.py` + torch + transformers + `BAAI/bge-m3` | ≈ 2.3 GB | CPU 推理，1024 维，CLS pooling，L2 归一 |
| **含 embedding 合计** | | **≈ 2.7 GB** | 解压后约 3.1GB |

安装向导必须原样展示这两行，并附一句话对照：

> 勾选 embedding：公告/研报可以做**语义检索**（找得到「说人话」的相关段落），代价是体积 +2.3GB、
> 首次加载模型约 30–60 秒、常驻内存 +1.5GB 左右。
> 不勾选：结构化查询（宽表筛选、财报字段、公告元数据）**完全可用**，向量检索不可用，
> FC 健康检查会一直显示 `embedding: unreachable`，页面会如实标注，不会假装检索成功。

## 四、Redis 三端适配

官方 Redis 没有 Windows 原生版，三端不能只用一套二进制，但**控制面必须是同一套代码**。

```
<安装目录>/file/modules/financecoffer/
├── runtime/
│   └── redis/
│       ├── win-x64/     redis-server.exe + 依赖 dll（社区移植版，固定 tag）
│       ├── linux-x64/   redis-server（官方源码编译产物）
│       ├── linux-arm64/ redis-server（同上，交叉编译）
│       └── macos-arm64/ redis-server（官方源码编译 / 预编译）
├── conf/redis.conf      # 三端共用同一份模板
└── scripts/redis_service.py
```

约束：

1. **同一份 `redis.conf`**：`bind 127.0.0.1`、`port` 由 `packaging/port_probe.py` 取空闲值注入、
   `appendonly yes`、`dir` 指向 `redisdata/`、`daemonize no`（由控制面托管进程）。
2. **`redis_service.py` 是唯一入口**，按 `sys.platform` 选二进制目录，对外只暴露
   `start() / stop() / status() / conf_values()`；控制面的状态机、健康检查、卸载都只调它。
3. **Windows 移植版风险要用工程手段兜**：固定版本 tag 并锁校验和、随包分发（不联网下载）、
   只监听回环、日志按天切分、崩溃时由控制面按退避重拉（最多 3 次，失败则整单回滚）。
   移植版只承担 ETL 的 stream / DLQ，不承载任何持久化真相（真相在 PG）。
4. **三端矩阵必须逐个实测**：Windows / Linux x64 / macOS arm64 各跑一遍
   「安装 → 起 5 进程 → /health 200 → 卸载零残留」，结果写进增强包说明页。

## 五、目录布局（全在安装目录内）

```
<LadderStock>/file/modules/financecoffer/
├── runtime/          # 解压出来的二进制（只读，卸载可整块删）
├── pgdata/           # initdb 初始化的数据目录（卸载全删）
├── miniodata/
├── redisdata/
├── conf/             # 生成的 .env / redis.conf / pg 配置（0600）
├── logs/             # 各进程日志，按天切
└── run/              # pid / 状态文件 / 上次 Job 结果
```

- 数据目录 = 安装目录，**没有** `%LOCALAPPDATA%` 回退路径；
- 安装目录在 `C:\` 时，向导显示：「增强包会写入 3GB 左右数据，建议装到非系统盘」，
  用户换盘后继续；不换也能装，但提示不可跳过（不阻断安装，避免无盘可装的机器卡死）；
- 升级：`runtime/` 整体替换，`pgdata/` 保留并由 `fcoffer db migrate` 前滚。

## 六、安装流程（控制面承担，用户点一次）

```
queued
  → 预检：磁盘空间（按是否勾选 embedding 分别算）、安装目录可写、4 个端口可用、包完整性校验
  → 解压 runtime（PG / pgvector / Redis / MinIO / FC）
  → initdb（pg，只监听 127.0.0.1）+ 写 pg 配置
  → 起 pg → CREATE EXTENSION vector → fcoffer db migrate
  → 起 redis → 起 minio（按平台选二进制）
  → 生成 .env（端口全部来自 port_probe，不让用户填）
  → 起 api（fcoffer serve api --port <p>）→ 起 worker（fcoffer etl consume）
  → 若勾选 embedding：起 embedding server（首次加载模型较慢，健康检查给 90s 窗口）
  → 健康检查 /health：未通过则整单回滚
```

失败回滚：停已起进程 → 删本次生成的 `runtime/ pgdata/ miniodata/ redisdata/ conf/ logs/ run/`
（安装前已存在的同名目录按「用户已有」跳过，不删）→ 卡片回 `未接入`，最后 20 行日志挂 `last_job`。

## 七、卸载

1. 依次停 embedding（若在跑）→ worker → api → minio → redis → pg（先停从，后停主）；
2. 校验端口全部释放、无同名进程残留；
3. **整目录 `file/modules/financecoffer/` 删除**（含 pgdata / miniodata / redisdata / logs / run / conf）；
4. 校验删除后目录不存在；在卸载结果里写明「数据已一并删除，不可恢复」。

## 八、验收标准

1. 干净 Windows（无 WSL、无 Docker、无 Python）双击安装：卡片 `未接入` → `安装中` → `运行中`，`/health` 200；
2. **不勾选 embedding**：`/health` 为 `degraded` 且 `checks.embedding=unreachable`，结构化查询可用，
   页面明确标注「向量检索未启用」，不做假检索；
3. **勾选 embedding**：`/health` 200、向量检索可用、模型只在本地加载（`HF_HUB_OFFLINE=1`）；
4. 安装向导显示的体积与实际解压后体积误差 ≤ 10%；
5. 卸载后：目录不存在、端口全释放、无孤儿进程、`data/` 下没有任何 FC 残留；
6. 三端矩阵（Windows / Linux x64 / macOS arm64）各自跑通第 1–5 条；
7. 关闭 LadderStock 后 5–6 个进程全部退出，不留孤儿占端口。

## 九、实施顺序

1. **打包骨架**：PG + pgvector + FC + migrate + api，先把「装得上、起得来、卸得干净」跑通；
2. **补齐完整形态**：Redis（先 Windows 移植版）→ MinIO → worker；
3. **embedding 可选分支**：勾选与不勾选两条路径都要有健康检查与页面文案；
4. **三端适配**：Linux / macOS 的 Redis 与 MinIO 二进制、端口探测、进程封装逐个实测；
5. **告知与体积**：向导的体积表、优劣说明、确认按钮（这一步不后置，和 1 一起做）。
