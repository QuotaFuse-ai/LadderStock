# DEC-079：端口诊断归属口径——origin 决定可重拉性，认领态失败关闭（QUOT-547）

日期：2026-11-11
关联：QUOT-547（端口诊断 + 安全自愈）、DEC-078（adopted peer 停止边界）、
DEC-014（绝不终止非本控制面进程 / 不自动换端口 DEC-004）、QUOT-545（adopt-only 红线）
状态：已定稿

## 背景

QUOT-547 给控制面新增「端口占用四分类 + 安全自愈」：诊断 API 逐端口判
`own_child / adopted_peer / foreign / free`，自愈只允许重拉 `own_child`，
`adopted_peer` 只认领 + 健康复检，`foreign` 只报告。分类必须与 supervisor 账本
统一口径，否则外部启动、被 supervisor 认领的 peer（如 TickHub-CN 的 8000）会被
误判成「端口被其他进程占用」。

现网只读核验（2026-11-11，部署机）发现一个 DEC-078 未覆盖的真实账本态：

- 8000 的持有者 PID 27572 就是 TickHub-CN peer 本身
  （`th-cn.exe start-all --host 127.0.0.1 --port 8000 --crawler on`）；
- 生产账本 `D:\QuotaFuseModules\LadderStock\data\supervisor\ledger.json` 里
  `tickhub` 记录为 `origin=adopted, adopted_peer=False, state=running`——
  由**旧代码**写成（外部 peer 被认领但漏打 `adopted_peer` 标记）；
- `PageBoot._record_peer()` 与 `engine.adopt_module()` 对「已存活且 cmdline 指纹
  相符」的记录都会**提前返回、不回填标记**，所以即便升级到 QUOT-545 的新代码，
  这条滞后标记也不会被自动纠正。

DEC-078 §旧账本兼容 明确「不做启发式回填」，并假设「生产账本没有常托管的外部
peer」。实测推翻了这个假设：tickhub 正是常托管的外部 peer，且标记滞后为 False。
若诊断分类只信 `adopted_peer` 标记，这条记录会被判成 `own_child`，新增的自愈
就会**自动重拉 8000 的 peer**——直接踩 QUOT-545/547 红线（绝不终止 / 重启 peer）。

## 决策

1. **可重拉性由 `origin` 决定，不只由 `adopted_peer` 标记决定**
   （`web/port_diagnostics.py::classify_port_owner`，纯函数）：
   - PID 在本控制面状态账（`mine_pids`）→ `own_child`（本控制面亲手启动的最强证据）；
   - 账本认得（pid 相符 + 正在服务）且实时命令行与指纹相符：
     - `adopted_peer=True` → `adopted_peer`；
     - `origin="spawned"` → `own_child`（本 supervisor 会话亲手拉起，可停可重拉）；
     - `origin="adopted"`（即便 `adopted_peer=False`）→ `adopted_peer`（认领态，只观测）；
   - 实时命令行与指纹不符 → `foreign`（PID 复用，不终止）；
   - 拿不到 PID / 账本不认 → `foreign`。
2. **失败关闭**：自愈是自动化路径（无人工确认），证明不了「本会话亲手 spawn」
   就一律不自动重拉。滞后标记不可信，`origin=adopted` 一律按外部 peer 只观测。
   这与 DEC-014 / DEC-078「无法可靠区分时不猜测归属、不做破坏性动作」同一精神，
   只是方向落在新增的自愈面：宁可少修（只观测），不可误杀（自动重拉 peer）。
3. **不改引擎停止语义**：`classify_port_owner` 只服务控制面诊断 / 自愈。
   `engine._stop_module()` 仍直接读 `adopted_peer` 标记（DEC-078 闸门原样保留）：
   用户显式 `stop_all` 对前任遗留自有进程（`adopted` 无标记）仍可停，崩溃恢复
   语义不变。本决策不给 `stop_all` 加新限制，也不回填账本标记。
4. **健康探针修复**（同单）：MCP peer（注册表带 `mcp_name`）的 MCP 端口往往只
   暴露 `/mcp`、没有 `/healthz`。HTTP 健康探针全失败时再用 MCP 协议判活
   （`probe_mcp_alive`：GET 无会话回 400 + `mcp-session-id` 头 / JSON-RPC 体 = 存活），
   绝不把 404 / 无会话当成健康检查失败。现网 8000/mcp 实测 `alive=True`。

## 考虑过的替代方案

- **只信 `adopted_peer` 标记（标记 False 即 own_child）**：被否——现网账本标记
  滞后为 False，会把外部 peer 判成可重拉，自愈自动重拉 8000，踩红线。
- **升级时启发式回填标记（按模块名 / cmdline 推断外部 peer 并置 True）**：被否——
  DEC-078 已否过启发式回填（单入口自有模式下 supervisor 会亲自 spawn peer 模块，
  按模块名回填会破坏崩溃恢复）；且回填要写生产账本，风险大于收益。分类面失败
  关闭即可达成安全目标，无需动账本。
- **自愈对 `origin=adopted` 也允许重拉（与 stop_all 对齐）**：被否——stop_all 是
  用户显式动作（有人工兜底），自愈是自动动作；自动化路径必须更保守。前任遗留
  自有进程的重拉仍可走用户显式 `restart_module`（引擎入口，不经诊断分类）。

## 验收

- `tests/unit/web/test_port_diagnostics.py`（35 项，fake host + 真账本）：四分类 /
  PID 复用 / MCP 判活 / 统一口径 / 健康修复 / 自愈只动 own_child / 反向验证；
  含 `test_classify_stale_adopted_flag_is_observe_only` 与
  `test_heal_stale_adopted_flag_never_restarts` 钉住现网滞后标记态。
- `tests/integration/test_stop_all_ownership.py`（4 项，真实临时进程）：QUOT-545
  两条停止语义 + QUOT-547 自愈两条（adopted peer  heal 只认领、stop_all 跳过、
  PID 不变；own_child heal 重拉换 PID）。
- 现网只读核验：8000（PID 27572）用真实生产账本分类得 `adopted_peer`（非 foreign），
  `probe_mcp_alive(8000/mcp)=True`；全程只读，未终止 / 重启 / 写任何生产进程或账本。
- `ruff check .` / `mypy`（191 文件）全绿；不改端口白名单、不改 DEC-078 停止闸门。