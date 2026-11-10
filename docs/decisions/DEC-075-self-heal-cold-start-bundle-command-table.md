# DEC-075：自愈冷启动改用 bundle 命令表（QUOT-526）

日期：2026-11-11
关联：QUOT-519（伞单第 5 根柱子）、QUOT-482（supervisor 内核）、QUOT-491（482-C 自愈）、
QUOT-523（单入口）、DEC-069（崩溃自愈口径）、DEC-071（页面自启 / peer adopt-only）
状态：已定稿

## 背景

`SelfHealEngine` 只遍历账本里**已有条目**，且生产重拉通道读的是空的
`data/supervisor/restart-commands.json`：没记过账的模块永远不会被拉起，记过账的
模块崩了也只能报「无可用重拉通道」。QUOT-482-B 的适配器（`build_default_adapters`）
已经是 bundle 自带、路径相对安装根的命令表，继续维护一份外部 JSON 属于重复来源。

伞单同时要求「peer 边界保留：tickhub / mem0 / financecoffer adopt-only，不自动重启」，
所以不能把「看到缺模块就一律拉起」做成默认行为。

## 决策

1. **生产重拉通道 = `AdapterRestarter`**（`supervisor/restart.py`），内部只调
   `SupervisorEngine`：先认领、再按适配器启动面拉起。生产装配不再读
   `restart-commands.json`；`CommandTableRestarter` 仅作为历史过渡实现保留，
   不再接线。
2. **冷启动是显式能力，不是默认行为**。`SelfHealEngine` 新增 `modules=` /
   `cold_start=`：给了模块表才托管这份表；账本无 pid 的模块**立刻**拉一次
   （delay 0），失败后按 5s→15s→45s 退避、5 分钟 3 次上限、超限 `needs_human`。
   没给模块表时保持旧口径（只看账本已有条目）。
3. **先认领、后拉起**。新增 `SupervisorEngine.adopt_module()`：端口监听者命中
   适配器 `identify()` 的识别规则才记账（`origin=adopted`，pid 不变），并回填
   `cmdline_hash`；读不到证据 / 持有者不匹配一律返回 `None`（失败关闭），由调用方
   决定是否启动。认领**不 spawn、不 kill**，是 peer adopt-only 的同一口径。
4. **认领不算一次崩溃恢复**。`RestartOutcome.adopted=True` 时不累计
   `restart_count` / `restart_window`，但必须把账本 pid 换绑到认领到的实例，
   否则下一轮会把它当「从没拉起」再冷启动一次（双实例风险）。
5. **模式闸门**：`LDST_SUPERVISOR_OWN_PROCESSES=1`（单入口模式）才启用
   `modules=MODULE_ORDER` + `cold_start=True` + restarter 允许 spawn；默认附加模式
   只认领、绝不自动拉起（`AdapterRestarter(spawn=False)`）。这样默认看板不会把
   peer 拉起来，符合 DEC-071 与伞单第 6 根柱子。
6. **主动停必须被自愈尊重**。`SupervisorEngine._stop_module` 落
   `stopping_by_user=True` / `health=stopped`；显式 `start` 清掉该意图。否则接上
   真重拉通道后，面板「停止」会在 5 秒后被自愈原样拉起，单入口停止语义失效。
7. **自愈 / 启停 / 页面自启共用同一份 `Ledger` 实例**。`create_app` 把
   `engines.ledger` 传给 `build_page_boot()`，避免两份内存视图各自 `save()` 覆盖。

## 后果

- 「全停」（崩溃 / 断电 / 进程被杀）后，下一次 `heal.tick()` 能把账本里的模块
  全部拉回；账本为空（新 data_root / 丢账本）时按 bundle 模块表冷启动全表。
- 主动 `stop_all` / 面板停止不会被自愈撤销；要恢复需显式 `start`。
- 默认附加模式下 peer 依旧 adopt-only：已在跑的认领记账，没跑的不拉起、不终止。
- 端口被非本模块进程占用时启动失败关闭（DEC-004 / DEC-014），绝不换端口、绝不
  杀外部进程；同一模块连续失败 3 次进 `needs_human`，等人工 reset。
- 冷启动的首轮等待是 0s；若首轮失败，第二次等待按「第 2 次尝试」走 15s，
  这是退避梯子按尝试次数计数的直接结果，不是漏了 5s。

## 验收

| 场景 | 用例 |
|---|---|
| 空账本 + 模块表 → 立刻拉起 | `tests/unit/supervisor/test_cold_start.py::test_cold_start_launches_unledgered_modules_immediately` |
| 未开冷启动 / 无模块表 → 不动 | `...::test_cold_start_disabled_leaves_unledgered_modules_alone` / `test_without_module_table_ledger_only_covers_existing_entries` |
| 主动停不被冷启动覆盖 | `...::test_cold_start_respects_intentional_stop` |
| 认领不计重拉次数、pid 记账 | `...::test_adoption_does_not_count_as_a_restart` |
| 冷启动失败走退避 | `...::test_cold_start_failure_walks_the_backoff_ladder` |
| 认领只认命中证据、不 spawn / 不杀 | `tests/unit/supervisor/test_adopt_and_restarter.py::test_adopt_module_*` |
| 附加模式 restarter 不 spawn | `...::test_restarter_in_attach_mode_never_spawns` |
| 装配模式闸门 | `tests/unit/web/test_supervisor_engines_wiring.py` |
| 真实宿主：全停 → 自愈全拉起、adopted 不变 | `tests/ops/test_lifecycle_selfheal_cold_start.py`（`LDST_SELFHEAL_COLD_START=1` 显式开启） |
