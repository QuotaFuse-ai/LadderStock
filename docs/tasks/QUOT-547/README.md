# QUOT-547：子模块端口诊断 + 安全自愈 API（统一认领口径 + 修 health 探针 + 四类占用判定）

关联：QUOT-545 / DEC-078（adopted peer 停止边界，本单复用其红线）、
DEC-079（本单决策：origin 决定可重拉性，认领态失败关闭）、
DEC-014（绝不终止非本控制面进程）、DEC-004（不自动换端口）、
QUOT-519 / 523 / 527（单入口与退出只停自有子进程）
状态：已交付，待验收

## 1. 目标

设置页把**外部启动、已被 supervisor 认领的 peer**（如 TickHub-CN 的 8000）误判成
「端口被其他进程占用」，又因健康探针打错端点把活着的 peer 标成「异常」。本单给
控制面新增「端口占用四分类 + 安全自愈」，并修健康探针：

1. **四分类**：逐模块端口判 `own_child / adopted_peer / foreign / free`，回 PID +
   cmdline（打码）+ health + 人类可读理由；
2. **统一口径**：`adopted_peer` 不再算 foreign——`_port_conflicts` / `_decide_status`
   咨询 supervisor 账本认领态（`origin=adopted` / `adopted_peer`），与 page_boot 一致；
3. **修健康探针**：MCP peer 的 MCP 端口往往只有 `/mcp`、没有 `/healthz`，HTTP 探针
   全失败时改用 MCP 协议判活，**绝不把 404 / 无会话当成健康检查失败**；
4. **安全自愈**：只停 + 重拉 `own_child`；`adopted_peer` 只认领 + 健康复检，
   **绝不终止 / 重启**；`foreign` 只报告 + 引导，**绝不自动杀**。复用 QUOT-545 adopt-only 边界。

验收：fake-host 单元覆盖四分类 + 自愈只动 own_child；真实临时进程集成验证 adopted
peer 在 stop_all / 自愈下原样保留；反向验证（还原修复即复现「adopted peer 误判 foreign」）；
现网只读核验 8000 分类得 `adopted_peer`（非 foreign）、health 与 peer 真实状态一致；
CI 双绿（后端 pytest/ruff/mypy + 前端 tsc/vitest/build）。

## 2. 实现

核心：把归属判定抽成**纯函数** `classify_port_owner`（不碰宿主），诊断 / 自愈 / 冲突
三处共用同一口径；可重拉性由 `origin` 决定（DEC-079），不只信滞后的 `adopted_peer` 标记。

| 改动 | 文件 | 说明 |
|---|---|---|
| 四分类纯函数 | `web/port_diagnostics.py`（新增） | `classify_port_owner`：本控制面 PID→own_child；账本认得 + 指纹相符按 origin 分（spawned→own_child，adopted→adopted_peer，失败关闭）；指纹不符→foreign（PID 复用）；拿不到 PID / 账本不认→foreign。`mcp_path_for` / `probe_mcp_alive` / `_mcp_verdict`：MCP 协议判活（无会话 400 + `mcp-session-id` 头 / JSON-RPC 体 = 存活） |
| 诊断 / 自愈 API | `web/control.py` | `_diagnose_peer` 逐端口四分类 + 实时 cmdline（过 `mask_secrets`）+ health；`diagnose_ports`（`GET /ports`）、`heal_ports`（`POST /ports/{peer}/heal`）按分类处置：adopted_peer→`adopt_module`、own_child→`restart_module`、foreign→report、free→none |
| 统一冲突口径 | `web/control.py` | `_port_conflicts` 用 `classify_port_owner`：own_child 不算冲突；adopted_peer 记 `foreign=False, severity=info`；只有 foreign 是真冲突。`_decide_status` 的 `foreign_ports` 只收 `foreign=True`，adopted peer 不再被标 failed |
| 健康探针修复 | `web/control.py` | `_health_snapshot` HTTP 探针全失败 + MCP 端口在听 + 注册表带 `mcp_name` → 用 `probe_mcp_alive` 判活，detail 记 `mcp-protocol` |
| 账本只读入口 | `supervisor/engine.py` | 新增 `ledger` 只读 property（`record` / `records`），诊断面据此统一口径；写仍走引擎方法，诊断绝不改账本 |
| 接线 | `web/server.py` | `wire_supervisor` 标志：生产合并视图必接 `plane.supervisor`（诊断读得到认领态）；heal-only 注入保持 flat-view 契约（`configured=False`），不破坏既有端点测试 |

诊断面只读账本、自愈只经引擎入口（`adopt_module` / `restart_module`），引擎对
`adopted_peer` 的 DEC-078 停止闸门原样保留——自愈重拉 own_child 时引擎仍会跳过 peer。

## 3. 边界（红线，逐条保留）

- **绝不终止 / 重启 peer**：8000(tickhub) / 9100(mem0) / 9000(mem0-dev) / 8100(financecoffer)，adopt-only；
- **绝不自动杀 foreign 占用者**；DEC-004 不自动换端口；
- 生命周期测试只停自己拉起的进程；teardown 后 adopted 进程原样保留；
- 不整文件 skip / continue-on-error / 删用例换绿；`xfail(strict=True)` 仅限已知 bug 钉桩；
- 密钥 / 连接串不进 yaml / 日志 / 提交（cmdline 过 `mask_secrets` 才回传）；
- 后端不写页面（前端面板另开子单指派前端架构师）；
- 不改端口白名单、不改 DEC-078 停止闸门、不改 `stopping_by_user` 自愈语义。

## 4. 验收证据

| 检查 | 结果 |
|---|---|
| `tests/unit/web/test_port_diagnostics.py`（35 项，fake host + 真账本） | 全过 |
| `tests/integration/test_stop_all_ownership.py`（4 项，真实临时进程） | 全过（Windows） |
| `tests/unit/supervisor/`（164 项，回归） | 全过 |
| control / supervisor 端点 / 接线回归（168 项 + 1 既有 skip） | 全过 |
| AI 聊天 / 研究假件控制面回归（176 项，CI 首轮捕获后补跑） | 全过 |
| `ruff check .` / `mypy`（191 文件） | 干净 |
| 现网只读核验（部署机 8000，PID 27572） | 分类 `adopted_peer`（非 foreign）；`probe_mcp_alive(8000/mcp)=True`；全程只读 |
| PR #210 CI（backend pytest/ruff/mypy + frontend tsc/vitest/build） | 双绿（backend 9m29s / frontend 1m43s） |

四分类与自愈安全断言（`test_port_diagnostics.py`）：

- 四分类各一例 + PID 复用（实时 cmdline 与指纹不符→foreign）；
- MCP 判活：2xx / `mcp-session-id` 头 / 400+JSON-RPC 体→alive；404 / 无会话头→按 HTTP 失败处理但不误判服务死；
- 统一口径：adopted peer 在 `_port_conflicts` 记 `foreign=False`，`_decide_status` 不标 failed；
- 自愈只动 own_child：adopted_peer→`adopt_calls` 有、`restart_calls` 空；own_child→`restart_calls` 有；foreign→两者皆空（只报告）；
- **滞后标记钉桩**：`test_classify_stale_adopted_flag_is_observe_only` /
  `test_heal_stale_adopted_flag_never_restarts`——`origin=adopted, adopted_peer=False`
  仍判 adopted_peer、自愈只认领不重拉（现网真实账本态）；
- 反向验证：还原 origin 口径即复现「adopted peer 误判 foreign / 被重拉」。

真实进程集成（`test_stop_all_ownership.py`，QUOT-545 两条 + QUOT-547 两条）：

- `test_heal_spares_adopted_peer`：认领外部 peer→诊断 adopted_peer→heal `action=adopt`→
  peer 存活→`stop_all` 记 `skipped=adopted_peer`→peer PID 不变；
- `test_heal_restarts_own_child`：spawn wire→诊断 own_child→heal `action=restart`→
  新 PID≠旧 PID→健康探针 True→旧 PID 退出（`await_pid_gone`，只看 PID 不看端口）。

## 5. 已知取舍

- **可重拉性由 origin 决定，不只信 adopted_peer 标记**（DEC-079）：现网只读核验发现
  生产账本 tickhub 为 `origin=adopted, adopted_peer=False`（旧代码写成，标记滞后），
  且 `page_boot._record_peer` / `engine.adopt_module` 对「已存活且指纹相符」的记录提前
  返回、不回填标记。若只信标记，自愈会自动重拉 8000 的 peer——直接踩红线。故分类面
  失败关闭：`origin=adopted` 一律只观测，只有 `origin=spawned` / 本控制面 PID 才可重拉。
  这与 DEC-078「不启发式回填」同一精神，方向落在新增自愈面（宁可少修，不可误杀）。
- **不改引擎 stop_all 语义**：`classify_port_owner` 只服务诊断 / 自愈；`engine._stop_module`
  仍直接读 `adopted_peer` 标记（DEC-078 闸门原样），用户显式 stop_all 对前任遗留自有
  进程（adopted 无标记）仍可停，崩溃恢复语义不变。前任遗留进程的重拉走用户显式
  `restart_module`（引擎入口，不经诊断分类）。
- **MCP 判活而非 HTTP 200**：MCP streamable-HTTP 端口无 `/healthz`，无会话 GET 回 400 +
  `mcp-session-id` 头即证明服务活着；绝不把 404 / 无会话当健康失败。
- **真实宿主生命周期（tests/ops）本机不跑**：会起停生产端口红线，沿用 QUOT-526 / 545
  先例，由运维在批准窗口显式开启；本单在 fake-host 单元 + 受控真实临时进程集成层证明
  四分类、统一口径、健康修复与自愈安全边界。
- **接线只接真控制面（CI 首轮捕获）**：`create_app` 的 `wire_supervisor` 接线起初直接读
  `plane.supervisor`，但 AI 聊天 / 研究等测试假件（`_Plane` / `_NullControlPlane`）不声明
  该属性 → CI 后端 162 项 `AttributeError`。补 `hasattr(plane, "supervisor")` 守卫：只接
  `__init__` 必置 `self.supervisor` 的真控制面，假件保持原样，生产接线语义不变。本地补跑
  全部受影响文件（176 项）+ 接线契约（56 项）全过，CI 复跑双绿。