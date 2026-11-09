# supervisor 账本与生命周期设计（QUOT-482-A）

> 范围：supervisor 内核——「我们启动过谁」的账本、顺序启停引擎、归属判定。
> 不含：各模块具体启动命令（QUOT-482-B 的 adapters 接线）、自愈/退避/通知（QUOT-482-C）。

## 1. 目标与铁律

supervisor 回答一个问题：**这 6 个模块的进程，哪些是我们拉起的，哪些是别人占的？**

归属判定的铁律：**只认账本 + cmdline 匹配，不扫端口猜归属。**
端口在听只能证明「有进程绑了它」，证明不了「是谁拉的」——只能作为
「账本证明不了」时的外部证据，绝不能反过来当 managed 依据。

红线（继承 DEC-014）：

- 只终止**账本可以证明**属于我们的进程树；对账判死 / PID 复用 / 外部占用的记录一律不碰。
- 不拼 shell 命令；Windows 用 `taskkill /PID <pid> /T /F` 参数列表直传。
- 主程序只绑 127.0.0.1；`/api/supervisor/*` 复用控制面同一把回环 `_guard`。

## 2. 模块与启动序

六个托管模块（`supervisor/adapters.py`）：

| 序 | 模块 | 端口 |
|---|------|------|
| 1 | tickhub | 8000, 8787 |
| 2 | tickhub_us | 8002 |
| 3 | wire | 8790, 8791 |
| 4 | senate | 8765, 8786 |
| 5 | financecoffer | 8100 |
| 6 | proxyfleet | 8788, 8789 |

- **启动**严格按 `MODULE_ORDER` 顺序；**停止**严格反向。依赖在前、被依赖在后。
- 单模块失败**不挡后续**：`start_all` / `stop_all` 返回逐模块汇总 report，由调用方决定怎么呈现。
- 端口白名单写在 `MODULE_PORTS`：模块名与端口必须一一登记，未登记的模块名在 registry 构造时直接报错。

## 3. 进程账本（`supervisor/ledger.py`）

### 3.1 记录结构

账本每模块一条 `ModuleRecord`：

| 字段 | 说明 |
|------|------|
| `module` | 模块名（`MODULE_ORDER` 之一） |
| `ports` | 该模块的端口清单（拷贝自 `MODULE_PORTS`） |
| `pid` / `parent_pid` | 入口进程 PID / supervisor 自身 PID（用于核对父子链） |
| `started_at` / `stopped_at` | ISO-8601 UTC 时间戳 |
| `origin` | `spawned` = 本任 supervisor 生命周期内自己拉起；`adopted` = 崩溃重启后对账确认仍存活、继续托管的前任进程 |
| `cmdline_hash` | 拉起时命令行的 sha256（lowercase 归一后 JSON 序列化再哈希），对账时与实时 cmdline 比对，**堵 PID 复用** |
| `state` | `starting / running / stopping / stopped / failed`（状态机见 §4） |
| `forced_kill` | 优雅停超时被强杀过——升级强杀必须记账 |
| `stop_reason` | `stopped_by_request` / `process_dead_on_reconcile` / `suspected_pid_reuse_on_reconcile` / 启停失败原因（自由文本，只给人看不做分支） |

唯一真源落盘 `data/supervisor/ledger.json`。

### 3.2 原子落盘

- 写入：先写同目录 `ledger.json.tmp`，再 `os.replace` 原子换名（Windows 同卷可用）。
  主程序崩在写一半也不会留下半个 JSON。
- 读取：文件缺失 → 空账本起；JSON 损坏 / 顶层非 dict → 坏文件改名
  `ledger.json.corrupt` 留证，按空账本起。坏字段（未知 state/origin、非整数 pid）
  回退到安全默认值，绝不抛异常中断启动。

### 3.3 崩溃恢复对账（`reconcile`）

崩溃恢复的铁律：**只信活进程，不信账本上的旧状态。**
每次 `load()` 后、以及 `state()` 查询前，对 `starting/running` 记录逐条探测：

| probe 结论 | 判定 | 动作 |
|-----------|------|------|
| `alive`（PID 存活 + cmdline_hash 匹配） | 还是我们拉的 | 继续托管；`origin`：本任 spawn 过的保持 `spawned`，否则标 `adopted` |
| `reuse`（PID 存活但 cmdline 对不上） | 疑似 PID 复用 | 标 `stopped` + `stop_reason=suspected_pid_reuse_on_reconcile`；**失败关闭，不认人** |
| `dead`（PID 不存在） | 进程已死 | 标 `stopped` + `stop_reason=process_dead_on_reconcile`，**绝不误判 running** |

`stopping / stopped / failed` 是终态，对账一律不动（`kept:<state>`）。

`origin` 语义由引擎维护：引擎内存里持有 `_spawned_here` 集合（本任 spawn 成功即加入），
对账时传给 `reconcile(spawned_this_session=...)`——这区分了「我拉起的」和
「上一任留下的」，两者都是 managed，但证据链不同。

### 3.4 归属判定（`engine._ownership`）

`GET /api/supervisor/state` 对每模块给出 `ownership` 与 `evidence`，翻译规则：

| 账本事实 | ownership | evidence |
|----------|-----------|----------|
| PID 活 + cmdline 匹配 | `managed` | `ledger` |
| 记录 stopped + 疑似 PID 复用（端口多半还在听） | `external` | `cmdline_mismatch` |
| 账本进程死了且端口在听（端口是别人占的） | `external` | `port_taken_by_unknown` |
| 账本进程死了且端口没听（早就退了） | `managed` | `not_running` |
| 无账本记录但端口在听 | `external` | `port_taken_by_unknown` |
| 无账本记录且端口没听（静止态） | `managed` | `not_running` |

判定依据只来自账本与 cmdline 匹配，**不扫端口猜归属**；`listening` 只作为
附加事实单独返回，供页面提示「端口上可能有野进程」。

## 4. 生命周期状态机

```
                    start() 成功
   ┌──────────┐  spawn  ┌──────────┐ probe ✓ ┌─────────┐
   │ stopped  │ ──────▶ │ starting │ ──────▶ │ running │
   └──────────┘         └────┬─────┘         └────┬────┘
        ▲                    │ probe 超时          │ stop()
        │                    ▼                    ▼
        │              ┌──────────┐         ┌──────────┐
        │  kill ✓      │  failed  │ ◀────── │ stopping │
        │  ┌───────────│(记原因)   │  stop ✗ └────┬─────┘
        │  ▼           └──────────┘              │ 优雅停超时
        ├────────────────────────────────────────┘ → 升级强杀 → stopped
        ▼
   ┌──────────┐
   │ stopped  │（stop_reason 记录原因）
   └──────────┘
```

- `starting`：spawn 已发起，正在等端口起来（`_await_probe` 轮询）。
- `stopping`：优雅停已发起，正在等端口消失（`_await_quiet`）。
- `failed`：**不自动流转**——自愈是 482-C 的事，内核只如实记账。
- 每步失败都写 `stop_reason` / `forced_kill`，事后可完整回放。

## 5. 停止策略：优雅停 → 超时 → 整树强杀

每条等待路径都带超时（`stop_grace_sec` / `kill_grace_sec`，可注入时钟驱动）：

1. **优雅停**：调 adapter `stop()`（482-B 里通常是 compose stop / 信号），
   轮询等端口消失。
2. **超时升级**：grace 内没停干净 → `adapter.kill()` 整树强杀，**必须连子孙
   进程一起停**（cmd → python → worker 链）。Windows 用 `taskkill /PID X /T /F`；
   POSIX 用 PPID 递归遍历。孤儿进程的 PPID 仍指向原父 PID，全表扫描可把树找齐。
3. **记账**：升级强杀记 `forced_kill=True`，`stop_reason` 带 `+forced_kill` 后缀
   （如 `graceful_stop_timeout+forced_kill`）。
4. **停完回读端口确认**：kill 后再 probe 一次，端口还被占 → 标 `failed`
   （进程可能逃出了树，482-C 的自愈范畴）。

启动侧对称：spawn 后 `start_grace_sec` 内端口没起来 → 整树强杀 +
`failed`（`start_timeout+forced_kill`），不留半启动的链。

启动前先验端口：`MODULE_PORTS` 里任一端口已被占 → 拒绝启动该模块并如实报错
（DEC-004：不自动换端口），**不碰**占用者。

## 6. 并发互斥

引擎全部启停操作（`start_all` / `stop_all` / `restart_module`）持一把
`threading.RLock` 互斥：

- 同一时刻只允许一条启停路径在跑；并发请求在锁上排队（测试用 gate 验证）。
- `state()` 读也要拿同一把锁（对账是写操作，账本可能落盘），保证读到的
  归属视图与账本一致。
- `restart_module(name)` 的「其它模块 pid 前后不变」验收点因此天然成立：
  重启期间没有别的路径能碰其它模块。

## 7. HTTP 面

- `GET /api/supervisor/state`（`web/control.py::build_supervisor_router`）：
  每模块 `ports / ownership / evidence / listening` + 账本记录视图，只读。
  启停动作**不进 HTTP**（DEC-014：控制面不提供远程杀进程入口）；
  `restart_module` 暴露在引擎 API 层，供 CLI / 482-B 后续接线。
- 引擎未注入（482-B 的 adapters 未接线前）→ `configured=False`，
  页面据此隐藏启停面板，不报错。
- 路由挂同一把 `_guard`：非回环来源 403，Host 白名单同控制面。

## 8. 测试策略

全部走 fake（`tests/unit/supervisor/fakes.py`），不起真进程、不占真端口：

- `FakeProcs`：进程表（pid → cmdline / ppid / 端口），`tree_pids` 按
  ParentProcessId 递归语义实现，`kill_tree` 整树删除；
- `FakeAdapter`：start 造进程链，probe 看链上端口，可注入
  留孤儿 / stop 抛异常 / 端口被外人占用等故障；
- `FakeClock`：推进引擎轮询等待，超时路径毫秒级跑完。

判过证据对应的用例：

| 验收证据 | 用例 |
|---------|------|
| ① 冷启动进程树证明（pid+PPID 链） | `test_started_process_tree_is_recorded_with_parentage` |
| ② 全停后 10 端口消失 + 无孤儿 | `test_stop_all_runs_in_reverse_and_clears_every_port` / `test_stop_all_takes_down_whole_descendant_tree` |
| ③ 只重启 wire，其它模块 pid 不变 | `test_restart_module_leaves_other_pids_alone` |
| ④ 杀 supervisor 再起，已死进程标 stopped | `test_crash_recovery_marks_dead_processes_stopped` / `test_stop_after_crash_recovery_kills_nothing` |
| ⑤ pytest 全绿 + ruff/mypy 干净 | CI 门禁 |

## 9. 边界与移交

- **482-B**：实现真实 `ModuleAdapter`（compose / 命令行），在 server 组装处
  构造引擎注入 `ControlPlane(supervisor=...)`；本内核只依赖接口签名
  `start() → SpawnedProcess(pid, parent_pid, cmdline) / stop() / kill() / probe()`。
- **482-C**：消费 `failed` 状态与 `stop_reason`，实现退避重启与通知；
  内核不自动重试。
- 引擎不感知 482-B 之后的健康检查、日志聚合等扩展——adapters 自己长。

## 10. 与 QUOT-482-C 自愈的账本平替（合并记录）

QUOT-491（482-C 自愈引擎）先期合并时自带了一份最小账本
`supervisor/ledger.py`，其文档字符串明言「482-A 落地后由其账本平替」。
本仓库落地时执行了该平替：**本设计文档描述的账本是 482-C 账本 API 的严格超集**，
`SelfHealEngine` 零逻辑改动继续工作。具体口径：

- **`LedgerEntry = ModuleRecord` 别名**：482-C 代码全部沿用旧名；
- **字段并集**：482-A 的 11 字段 + 482-C 的
  `stopping_by_user / restart_count / restart_window / health /
  last_exit_reason / updated_at`，旧字段语义不变；
- **`update()` 联合签名**：`update(record)` 整条替换（482-A 引擎用法）
  与 `update(module, **fields)` 补丁式（482-C 用法）双形态兼容；
- **API 别名与扩展**：`get = record`、`entries()`、`ensure(module, ports=())`、
  `mark_stop_intent / record_restart / prune_window / reset_heal` 全部落在本账本；
- **落盘格式**采用 482-C 已定口径
  `{"version":1,"updated_at":...,"modules":[...]}`（list 形态），
  同时兼容读旧的 dict-of-dicts；损坏文件照旧 `.corrupt` 隔离；
- **类型口径**：`ports` 统一为 `list[int]`；482-C 的 `selfheal.py`
  三处把 `entry.ports` 传给期望 tuple 的 notifier / snapshot 签名，
  在调用点做 `tuple(...)` 适配（语义零变化）；
- **端口白名单单源化**：`MODULE_PORTS` 唯一来源是 482-C 的
  `supervisor/modules.py`，本模块 `adapters.py` 改为 import，
  不再各自定义。

`GET /api/supervisor/state` 相应升级为合并视图：以 482-C 的自愈快照
（前端 QUOT-484 消费的字段）为底，叠加本单的
`ownership / evidence / listening / supervisor_pid / order` 键。
两套路由注册顺序上 482-A 的先挂，`GET /state` 由合并版响应；
482-C 的 `POST /state/{module}/reset` 不受注册顺序影响。
