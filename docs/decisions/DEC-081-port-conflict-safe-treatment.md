# DEC-081：端口冲突安全处置——仅自有进程安全自愈，外来 / 认领只上报弹窗（QUOT-557）

日期：2026-11-12
关联：QUOT-557（拆自 QUOT-519）、DEC-004（不自动换端口）、
DEC-014（绝不终止非本控制面进程）、DEC-078（adopted peer 停止边界）、
DEC-079（端口诊断归属口径，失败关闭）、DEC-080（peer 健康事件通道 + 确认重启）、
QUOT-547（端口四分类 + 控制面安全自愈）、QUOT-545 / 546（adopt-only 红线）
状态：已定稿

## 背景

QUOT-547 已落地端口四分类（`classify_port_owner`）与控制面 `heal_ports`，但那是
**用户显式触发**的处置。QUOT-556（DEC-080）把健康事件接进 S3 通道，`port_conflict`
事件却只有一句通用原因，前端 S5 弹窗无法区分「自有进程占口（可安全重启）」与
「外来 / 认领进程占口（绝不能动）」；自愈巡检也没有任何端口冲突的自动处置。

本单（S4）要把「安全处置」接进自愈巡检：**只有可证明自有的占用者**才自动安全重启
解冲突；外来 / 认领占用者一律只发分类事件供弹窗引导用户确认。

红线（派单正文，逐条保留）：不杀外来 / adopted / 非控制面进程，8000/9100/9000
绝不终止；不自动换端口（DEC-004）；不改 frontend/src；不删断言；不 sleep 掩盖偶发；
禁止整文件 skip；密钥不进 yaml / 日志 / 提交。

## 决策

1. **冲突事件带占用者分类（观测侧，只读）**：`HealthEvent.conflicts` 新增字段
   （`{port, owner, pid}` 列表，owner ∈ own_child / adopted_peer / foreign），
   `port_conflict` 事件的 `reason` 改为按占用者分类的白话原因
   （`port_conflict_reason`，纯函数）。混合占用取**最保守**一类
   （foreign > adopted_peer > own_child）：只要有一个不能自动处置的占用者，
   就按「需人工确认」引导，绝不暗示可以自动终止外部进程。其余事件该字段为空。
2. **占用者 pid 进观测，精确区分本尊与残留**：`PortObservation.pid` 新增字段。
   一个在听端口算冲突，当且仅当占用者**不是本模块的本尊**：
   - foreign 在听 → 一律冲突（只报告）；
   - own_child / adopted_peer 在听：pid 与账本相符 → 本尊（活着即健康、死了即崩溃，
     交给存活判定），非冲突；pid 与账本不符（残留 / 他人占口）→ 仅当占用者**此刻仍
     存活**（注入的 `is_pid_alive` 探针）才算冲突。**未给 pid 时沿用 QUOT-556 口径**
     （own / adopted 在听即健康本尊），保证 S3 既有测试零回归。
   - **判活探针挡节流误报**：端口观测按 5s 节流复用，旧观测可能记着已死的本尊 pid；
     若只看「pid 不符」就把已死本尊误报成冲突，会把 crash / recovered 误判成
     port_conflict（S3 集成回归）。故残留冲突必须经 `is_pid_alive` 复核占用者此刻仍
     活着；未注入探针时按 QUOT-556 口径不报残留冲突（宁可少报，不可误判崩溃）。
3. **可证明自有 = 三重证据，失败关闭**：引擎新增 `_spawned_pids`（本任亲手 spawn
   的 pid→命令行指纹）。`build_port_inspector(spawned_pids=…)` 只把**此刻仍存活且
   指纹相符**的 pid 作为 `mine_pids`——残留自有进程占口也能认成 own_child；死 pid /
   指纹不符（PID 复用）绝不进 mine_pids。处置前引擎 `restart_own_occupant` 再验
   `_is_provably_own_spawn`（本任 spawn + 存活 + 指纹相符），任何一条不满足一律抛错
   不动（宁可少修，不可误杀）。
4. **处置是独立的有界步骤，观测仍只读**：monitor 保持 observe-only（DEC-080 不变）。
   自愈 `tick` 在 `observe` 之后新增 `_treat_port_conflicts`：仅当冲突占用者是
   own_child 才调处置通道（`_own_conflict_restarter` → `engine.restart_own_occupant`
   重绑账本 pid 到可证明自有的占用者，再走 `restart_module` 既有闸门停 + 重拉）；
   foreign / adopted 冲突一律不动——它们的事件已在观测侧发出供弹窗。复用自愈失败
   窗口限次（`max_restarts`），超限不再自动重试（交 needs_human / 弹窗）。
5. **绝不改端口配置（DEC-004）**：处置 = 安全重启自有模块，不是换端口。外来占口
   时引擎既有 `start_module` 闸门（端口被非本模块进程占着拒绝启动）原样保留。

## 考虑过的替代方案

- **monitor 直接处置冲突**：被否——破坏 DEC-080「观测不动作」分层，且会让只读
  事件通道带上终止副作用。处置拆成 selfheal 的独立有界步骤，monitor 仍只读。
- **own_child 冲突也走通用 crash 重拉**：被否——通用 crash 路径会先 `adopt_module`，
  把残留自有进程误认领成 adopted_peer（账本 pid 已漂移时），反而失去可重拉性。
  专用 `restart_own_occupant` 先重绑到可证明自有的占用者再重启，停止路径对准它，
  绝不指向外来进程。
- **信 adopted_peer 标记判可重拉**：被否——沿用 DEC-079 失败关闭：标记可能滞后，
  只有本任亲手 spawn（`_spawned_pids` + 实时指纹）才允许自动处置。
- **冲突即自动换端口**：被否——直接踩 DEC-004。外来占口只能引导用户确认。

## 验收

- `tests/unit/supervisor/test_health_events.py`（26 项，+10）：分类原因纯函数
  （最保守优先）；foreign / own_child 残留 / adopted 残留冲突事件带 owner+pid；
  健康本尊（pid 相符）非冲突；pid=None 兼容 QUOT-556；节流旧观测里已死本尊经
  `is_pid_alive` 判死过滤、不误报冲突（S3 crash/recovered 语义守卫）；`port_conflicts`
  访问器与事件 detail 同口径；处置联动 own_child→调通道、foreign / adopted→绝不调通道。
- `tests/unit/supervisor/test_engine.py`（27 项，+5）：`_spawned_pids` 只记自有
  spawn；残留自有占口重绑 + 安全重启（PID 换新、端口重听、其它模块不牵连）；
  外来 / 已死 / PID 复用三类占用者一律失败关闭、绝不终止（红线回归）。
- `tests/integration/test_port_conflict_safe_heal.py`（2 项，真实临时进程 + 生产
  装配）：外来进程占口→port_conflict(foreign) 事件 + 外来进程全程存活 + 处置通道
  零调用 + 账本 pid 不换绑；自有残留占口→port_conflict(own_child)→安全重启 PID
  换新 / 端口重听 / 旧进程退出→recovered。复用 QUOT-545 World（临时端口 + 生产
  端口红线守卫 + 兜底清理）。
- 回归：QUOT-547 / 556 全部既有测试原样通过（含 S3 健康事件 16 项、端口诊断 35 项、
  events 端点契约——`conflicts` 字段按 S3 adopted_peer 先例**扩展**契约键集，不删断言）；
  `ruff check` / `mypy` 全绿。