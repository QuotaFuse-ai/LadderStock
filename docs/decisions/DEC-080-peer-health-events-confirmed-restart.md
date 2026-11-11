# DEC-080：peer 健康事件通道 + 确认后一键重启（QUOT-556）

日期：2026-11-11
关联：QUOT-556（拆自 QUOT-519）、DEC-078（adopted peer 停止边界）、
DEC-079（端口诊断归属口径，失败关闭）、DEC-004（不自动换端口）、
QUOT-526（自愈冷启动 / 附加模式）、QUOT-545 / 546（adopt-only 红线）
状态：已定稿

## 背景

前端 S5 弹窗需要订阅 peer 级健康事件（crash / offline / port_conflict /
needs_human / recovered），并在用户确认后一键重启。此前 supervisor 只有
`/api/supervisor/state` 的瞬时快照：前端看不到「什么时候坏过、坏的原因」，
也没有任何事件驱动恢复通知；`/restart` 对认领态 peer 没有确认闸门，
`reset` 又只清窗口不拉起——「一键重启」语义缺失。

红线（派单正文）：adopt-only 安全不破——不杀外来 / 非控制面进程，
8000/9100/9000 既有 peer 绝不终止；不自动换端口（DEC-004）；端口冲突处置归 S4。

## 决策

1. **事件通道用轮询，不用 SSE**：`GET /api/supervisor/events?since=<seq>`
   （回环 + Host 只读闸门，与 /state 同一份 `privacy_write_guard`）。
   `HealthEventBus` = 线程安全环形缓冲（容量 500）+ 单调 seq；客户端落后被挤掉
   时 seq 出现空洞，提示重新全量对齐。`/state` 顶层新增 `events_latest`，
   前端沿用既有 /state 轮询即可发现「有没有新事件」，再按需拉 /events。
   理由：看板前端已有 /state 轮询骨架，轮询零新增连接管理 / 心跳 / 断线重连
   成本；事件量低（每模块只发跃迁），SSE 的实时性收益不抵复杂度。
2. **只发跃迁，不刷屏**（`HealthEventMonitor`）：每轮巡检把账本翻译成「条件」
   （ok / crash / offline / port_conflict / needs_human / stopped / restarting /
   unknown），条件变化才发事件；`recovered` 只在「先坏过」之后发一次。
   - 主动停（`stopping_by_user`）与从未拉起（无 pid / 无 started_at）全程静默；
   - restart 的 stop→start 间隙靠 `stop_reason=stopped_by_request` 静默
     （与 state=starting 同笔落账），不误报 crash；
   - **health=restarting 不掩盖 crash**：正在自愈的崩溃也是崩溃，弹窗要弹；
     只有 state=starting（引擎拉起中）是静默过渡态。
3. **观测不动作**：monitor 只读账本 + 注入的存活 / 端口探针，绝不终止、绝不
   拉起、绝不改账本。端口归属分类复用 QUOT-547 的 `classify_port_owner`
   （DEC-079 失败关闭口径），装配侧一次连接表扫描摊薄成本，按 5s 节流复用；
   观测失败按「本轮无端口信息」降级，只发存活类事件，不误报端口类。
   处置仍走引擎既有闸门（DEC-078 / DEC-004 原样保留）。
4. **确认后一键重启 = 只授权拉起替代，绝不授权终止**：
   - HTTP 闸门：`POST /restart` 的模块只要 `adopted_peer=True` **或**
     `origin="adopted"`（含旧账本滞后标记，DEC-079 口径）就必须
     `confirm: true`（布尔，字符串不算），否则 422，引擎一次都不被调；
   - 引擎闸门（`restart_module`）：外部 peer 进程活着但端口未监听（卡死）→
     失败关闭报错，不终止、不造双实例；活着且在听 → 幂等续 running；
     已死才允许 spawn 替代实例（归属回 spawned、清 adopted_peer 标记）；
   - 确认重启成功后清自愈失败窗口（`reset_heal`），让 QUOT-526 自愈重新有
     完整预算看护新进程；`reset` 端点保持辅助清窗口语义不变。
5. **同单修复 `BaseAdapter.start()` 换绑错 pid**（集成测试暴露的既有缺陷）：
   原实现返回 `self._roots[0]`（适配器历史上第一个根），而 `_roots` 只在
   stop()/kill() 清空。自有进程被外部 kill 后自愈重拉不走 stop()，账本被换绑到
   已死的旧 pid：恢复判定永远失败，下一轮还把真正的新进程误认领成「外部 peer」。
   改为返回**本次拉起的主面 pid**。其余三个适配器（financecoffer / proxyfleet /
   proxypool）本就返回新 pid，不受影响。

## 考虑过的替代方案

- **SSE 长连接推事件**：被否——前端已有 /state 轮询，事件量低（跃迁式），
  SSE 要新增连接管理 / 心跳 / 代理穿透处理，收益不抵复杂度。轮询 + seq 对齐
  已满足弹窗时效（巡检周期 1s）。
- **每轮巡检都发事件（不做跃迁去重）**：被否——crash 持续期间每秒一条会刷爆
  弹窗与环形缓冲；前端要的是「状态变化」，不是心跳。
- **confirm 同时授权「先杀后拉」**：被否——直接踩 QUOT-545/546 红线。卡死的
  外部 peer 只能人工处置（任务管理器 / 运维），控制面失败关闭并白话说明。
- **monitor 放进 web 层**：被否——supervisor 层不应反向依赖 web；端口观测器
  以 `PortInspector` 回调注入（装配侧 `build_port_inspector` 提供 psutil 实现），
  supervisor 层只消费 `own_child / adopted_peer / foreign / free` 字符串。

## 验收

- `tests/unit/supervisor/test_health_events.py`（17 项）：总线 seq / 过滤 /
  环形挤旧；crash 一次、recovered 一次、主动停 / 从未拉起 / restart 间隙静默；
  offline / port_conflict（死 / 活两态）；节流与观测失败降级；自愈联动
  （crash→recovered、attach 模式 needs_human→确认重启→recovered、快照契约）。
- `tests/unit/supervisor/test_restart_adopted_peer.py`（7 项）：死 peer 重启
  换归属、活 peer 幂等、卡死失败关闭不杀、外来占口 DEC-004 不杀、自有模块
  回归、旧账本无标记引擎不设闸、跨会话重启不牵连其它模块。
- `tests/unit/web/test_supervisor_events_endpoint.py`（10 项）：/events 契约
  （since/module/limit、未接线降级、回环只读 / 远程 403）；/restart confirm
  闸门（布尔严格、混合模块只闸认领态、失败不清窗口、无账本假件不加闸）；
  /state events_latest + adopted_peer。
- `tests/integration/test_peer_health_events.py`（2 项，真实临时进程 + 生产
  事件装配）：adopted peer kill→crash→确认重启→recovered，同场无关 peer 全程
  PID 不变；owned peer kill→crash→526 自愈重拉→recovered。复用 QUOT-545 World
  （临时端口 + 生产端口红线守卫 + 兜底清理，conftest 共享 fixture）。
- 回归：QUOT-545/546/547 全部既有测试原样通过；`ruff check .` / `mypy` 全绿。
- 后端全量 `python -m pytest -o addopts=""`（本机，24m56s）：4069 passed /
  14 skipped / 4 xpassed / 11 failed。11 项失败全部环境性，与本单改动无关，
  已在干净 HEAD worktree 反向验证同果：
  - `tests/ops` 10 项：需要兄弟仓部署布局（th-cn.exe / th-us.exe / qfw.exe /
    ssenate.exe 与本仓同目录）且靶口冷启——本任务 workdir 只有单仓检出；
    `port_conflict[8100]` 3 项另受现网 WSL financecoffer 常驻 8100 影响
    （该口冷时基线同样通过，setup 前置断言如实拒绝占热口）；
  - `tests/test_senate.py` 1 项：本机 editable `stock_senate` 指向另一任务
    检出（其 `_DEFAULT_BASE_URLS` 已含 ollama），契约层如实照抄真实包所致；
    CI 干净环境走内联镜像表（7 provider），不受影响。
  supervisor / web 全部既有与本单新增测试在全量中通过。