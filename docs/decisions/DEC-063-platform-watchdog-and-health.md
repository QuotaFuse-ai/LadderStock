# DEC-063 平台服务状态灯、独立看门狗（5952）与全局重启

- 日期：2026-11-06
- 提出：项目领导人（QUOT-443，承接用户「服务挂了没人知道、只能自己去命令行重启」的反馈）
- 状态：待复核（GM）
- 编号：立单指定 `DEC-063`。仓内 `DEC-063` 已被 `regression-a-fund-flow-batch-caliber` 与
  `single-topbar` 各用一次（历史重号，未回改），本文件按单面指定路径落盘，不改旧文件；
  下一个可用号为 `DEC-065`。
- 关联：QUOT-457 / DEC-062（顶栏工具区）/ DEC-063-single-topbar（单顶栏）/
  DEC-004（端口占用即失败，不漂移）/ `docs/ARCHITECTURE.md` §1 / `docs/CLI.md` §E /
  `docs/UI-SPEC.md` §2

## 1. 问题与候选

看板跑在 `ldst run` 里。进程一旦退出（崩溃、被误杀、机器重启后没起来），
页面只剩一片请求失败，用户既**看不出是服务死了还是网络慢**，也**没有任何页内手段把它拉起来**。
两个候选：

- **A. 主服务自重启**：`/api/health` 返回 `db_ok=false` 时由主进程自己 `os.execv` 或重新初始化。
  否决：进程已经死了就没人执行重启；把「进程存活」和「业务可用」混在一个生命周期里，
  库损坏 / 端口占用 / 依赖缺失这类故障重启也修不好，只会变成重启循环。
- **B. 独立看门狗进程**：第二个常驻进程盯住主服务的健康，页面状态灯读它，
  重启按钮调它。**采用 B。**

## 2. 决定

### 2.1 两进程、两端口

| 角色 | 进程 | 端口 | 职责 |
|---|---|---|---|
| 主服务 | `ldst run` | `5950` 固定 | 看板 + 数据面 API（`/api/*`），`GET /api/health` |
| 看门狗 | `ldst watchdog` | `5952` 固定 | 探活、离线壳页、`GET /platform/health`、`POST /platform/restart` |

- 看门狗端口 **`5952`**：`5951` 已是 MCP http（`cli/main.py:53 DEFAULT_MCP_PORT`、
  `docs/ARCHITECTURE.md` §1、`docs/CLI.md` §F），`5950` 是看板。三者互不替代。
- 看门狗与主服务**同机、独立进程**：主服务死掉时看门狗仍活着，才可能给出状态并拉起它。
- 看门狗只监听回环；非回环地址启动直接拒绝（退出码 3），不提供远程管理面。
- 端口被占用时与 `run` 同口径：**直接失败（退出码 3）并提示 `--port`，不静默换端口**
  （复用 `run.py` 的 `port_free` / `is_addr_in_use`，DEC-004）。
- **前端 origin 不变**：看板仍 `http://127.0.0.1:5950`，`localStorage['ldst.theme']`、
  `ldst.nav.expanded`、分片书签一个都不迁。看门狗不托管 SPA，只托管一张离线壳页。

### 2.2 健康口径：只加字段，不删字段

`GET /api/health`（主服务）在原有 `ok` / `name` / `version` / `time` / `data_dir` /
`read_only` **之外追加**：

```json
{ "status": "ok|degraded", "uptime_sec": 0, "db_ok": true }
```

- `status` 由 `db_ok` 派生：`db_ok=false` → `degraded`，其余 `ok`；
- `uptime_sec` 取进程单调时钟（`time.monotonic()`），不受系统时间调整影响；
- `db_ok`：对 `data/journal.sqlite` 做 `SELECT 1` + `PRAGMA user_version`，只读 URI、
  `timeout=2.0`；**任何异常一律回 `false`，绝不 500**（包括文件不存在以外的打不开、
  路径是目录、SQLite 损坏）。库文件不存在视为「尚未建库」→ `true`。
- 老消费方只认 `ok`，新字段全为增量，不构成破坏性变更。

`GET /platform/health`（看门狗）汇总一次探活：

```json
{
  "status": "ok|degraded|offline",
  "uptime_sec": 0,
  "version": "0.1.0",
  "db_ok": true,
  "checked_at": "2026-11-06T09:30:00+08:00",
  "api": { "...": "主服务 /api/health 原样透传" },
  "watchdog": { "uptime_sec": 0, "version": "0.1.0" }
}
```

- `api` 探不到时为 `null`；`status` 判据：探不到 → `offline`；
  `db_ok=false` → `degraded`；否则 `ok`（对老主服务缺 `status` 时按 `ok` 字段回退）。
- 顶层 `uptime_sec` / `version` / `db_ok` **转发主服务**的对应字段（离线时 `null`），
  `watchdog` 块才是看门狗自己的运行时长与版本——两段 uptime 不能混用。
- 探活超时 2s，响应 `Cache-Control: no-store`。

### 2.3 重启：只杀「它自己记录的 PID」，不按映像名批量杀

`POST /platform/restart` 的执行顺序：

1. **既不配计划任务也不配脚本 → `409`**，明确告诉用户缺哪一项配置；
2. 记录当前占用 `5950` 的 PID（`netstat -ano` / `lsof` 反查），**再**杀这一个 PID；
3. 端口释放后按 `--restart-task`（`schtasks /run /tn <name>`）→
   `--restart-script`（绝对路径 `.ps1`）顺序尝试；两级都失败 → `503` 并附最后几行输出；
4. 30 秒内重复请求 → **`429`**（进程内锁 + 时间戳，不落盘）；
5. `GET` 无副作用：只有 `POST` 触发重启；预检 `OPTIONS` 只回 CORS 头。

硬约束（实现见 `cli/watchdog.py:kill_guard` / `restart_service`）：

- **绝不按 `ldst` / `python` 映像名批量 kill**——会误杀用户在终端里跑的其他 CLI；
- 杀之前二次确认目标 PID 仍是当前 `5950` 的监听者，且**映像名不是 `multica*`**
  （工作区守护进程，误杀会打断整个 workspace）；
- 目标等于看门狗自身 PID 时拒绝；
- 命令一律固定参数、`shell=False`：**参数、路径、任务名都来自启动参数，不从 HTTP 输入拼接**；
  请求体不参与任何命令构造；
- 看门狗只绑回环，且写方法校验 `Origin` ∈ 白名单
  （`http://127.0.0.1:5950` / `localhost:5950` / `127.0.0.1:5952` / `localhost:5952` /
  `127.0.0.1:5173` / `localhost:5173`）。**缺 `Origin` → 403**，不回 `*`。
  - 加入 `5952` 两项，是因为离线壳页本身就挂在 5952 上，它点「重启平台」必须能过闸；
    这是白名单内的显式放行，不是放开任意来源。

### 2.4 前端：两拍才红，自愈优先、按钮兜底

`frontend/src/lib/usePlatformHealth.ts`：

- 每 **2s** 拉一次 `/platform/health`；生产（页面在 5950）直连
  `http://127.0.0.1:5952`，开发走 Vite 代理 `/platform`（`vite.config.ts`）。
  节奏从最初的 5s 收紧：看门狗每次请求都实时探活主服务（连接挂起时最多等 2s），
  实测 5s 间隔 + 2s 探测超时最坏 **7.1s** 才转红，违反本单「杀进程后 ≤5s 转红」；
  2s 间隔把最坏情况压到 ~4s，页面可见性暂停逻辑不变。
- **连续两次失败才转红**：单次抖动只记录失败数，颜色保持上一态；
  但看门狗明确回 `status=offline` 时立即转红（这是有依据的结论，不是抖动）。
- `db_ok=false` / `degraded` → 黄；`ok` → 绿；未知 → 灰。
- `document.hidden` 时暂停轮询，`visibilitychange` 回到前台立即补一拍。
- 状态灯在顶栏工具区**最左**（DEC-062 §6 / DEC-063-single-topbar §2.5 的预留位），
  悬浮显示状态 / 已运行 / 版本 / 数据库；**重启按钮只在红灯渲染**，绿 / 黄不占位：
  - 自愈优先：看门狗在后台按 `--restart-task` 的计划任务口径重启，正常几秒内恢复；
  - 按钮兜底：红灯且未恢复时，用户「确认重启」→ `POST /platform/restart` →
    「重启中」禁用 → 轮询至多 **90s** → 恢复后 `location.reload()`；
  - 超时给出真实原因（看门狗日志 / 手动 `ldst run`），不假装成功。

### 2.5 自愈：两个 S4U 计划任务

`scripts/install-service.ps1`（**需管理员运行一次**）注册：

| 任务名 | 动作 | 触发 |
|---|---|---|
| `LadderStock API` | `python -m ladderstock.cli.main run` | 开机 + 失败重启 |
| `LadderStock Watchdog` | `python -m ladderstock.cli.main watchdog --restart-task "LadderStock API"` | 开机 |

- 主体 S4U、`Highest`、`PT0S` 时限、`Hidden`、`IgnoreNew`、`RestartCount 999`；
- **`RestartInterval` 用 `PT1M`**：Windows 任务计划程序的失败重启间隔**最小 1 分钟**，
  立单文字里的 `PT30S` 会被注册接口拒绝；本机参考任务 `\Vibe AStock API` 实测也是 `PT1M`。
- 同时生成 `%LOCALAPPDATA%\LadderStock\restart-api.ps1`，作为计划任务不可用时的兜底：
  固定路径、固定参数，只停「当前 5950 监听者且不是 `multica*`」的那一个 PID，
  再以隐藏窗口拉起 `ldst run`。
- 安装脚本不写 PATH、不改系统代理、不杀无关进程；非管理员运行时打印**可直接复制的提权命令**
  并以退出码 2 结束，不静默降级。

### 2.6 `ldst watchdog` 契约

```
ldst watchdog [--host 127.0.0.1] [--port 5952]
              [--api http://127.0.0.1:5950]
              [--restart-task "LadderStock API"]
              [--restart-script <绝对 .ps1 路径>]
              [--probe-interval 2] [--output table|json]
```

- 长驻进程，与 `run` 同口径**不进** `tests/fixtures/command_matrix.py` 的 argv 矩阵，
  但叶子路径 `("watchdog",)` 必须登记（单一事实源）。
- 启动即写 `state/watchdog.json`（`pid` / `host` / `port` / `version` / `started_at`，原子写），
  `restart-api.ps1` 与排障都读它。
- 后台线程按 `--probe-interval` 探活，成功时把当前 API PID 记进 `state/watchdog.json`，
  供「只杀记录的 PID」这条规则落地。

## 3. 为什么不让主服务自重启

1. **死亡进程无法自救**：崩溃 / 被系统杀掉后没有执行者；
2. **健康 ≠ 存活**：库损坏、端口被占、依赖缺失属于「进程活着但不可用」，重启只会让故障循环；
3. **权限与失控**：自重启需要 `execv` / 重新拉起自身，参数与环境容易漂移，
   出错时用户连日志都拿不到；
4. **可观测性**：第二进程天然是「外部观察者」，能区分「主服务死了」和「主服务在但库坏了」，
   这正是状态灯要表达的三态。

## 4. 代价与已知边界

- 多一个常驻进程与 5952 端口；未安装计划任务时看门狗只能看、不能自愈
  （按钮仍可用，但需要 `--restart-script` 或 `--restart-task`）。
- `db_ok` 只在探活时查一次 `SELECT 1`，不校验业务表结构与数据完整性；
  更深的体检仍归 `ldst doctor`。
- 状态灯读的是 5952（看门狗）而不是 5950（主服务）：**看门狗自己也挂了时页面会转红**，
  这是有意的——两个进程都不在时更需要告诉用户「平台服务没起来」。
- 重启只面向本机单用户场景：不做多实例、不做远程运维、不保存重启历史（只回本次 `attempts`）。
- 离线壳页不托管业务数据、不读隐私库、不引用任何 CDN；恢复后只做一次 `location.replace` 回 5950。

## 5. 证据（本单）

- 后端：`tests/unit/cli/test_watchdog.py`（三态判定 / Origin 白名单与 403 / 30s 429 /
  任务→脚本回落 / 只杀当前端口占用者 / `multica` 拒杀 / 壳页无外链）、
  `tests/unit/web/test_health_endpoint.py`（旧字段保留、`db_ok` 真实库、
  `journal.sqlite` 是目录时 200 + `degraded`、uptime 单调）。
- 前端：`frontend/tests/quot-443-platform-health.test.tsx`（三态、两拍才红、
  单次抖动保持绿、默认轮询间隔 2s、可见性暂停 / 恢复、绿黄不渲染重启、红灯两步确认、
  提示浮层字段、超时文案）。
- 安装：`scripts/install-service.ps1`（本机管理员实测注册两任务 + 生成重启脚本）。
- 回归：后端 `pytest` / `ruff` / `mypy`、前端 `typecheck` / `test` / `build` 全绿；
  `scripts/check_commands.py` 与 `LEAF_PATHS` 同步（新增 `watchdog` 叶子）。
