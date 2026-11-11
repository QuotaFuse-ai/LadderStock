# DEC-078：adopted peer 停止边界——账本标记区分外部认领与前任遗留（QUOT-545）

日期：2026-11-11
关联：QUOT-519（伞单第 6 根柱子 peer 边界）、QUOT-523（单入口）、QUOT-527（托盘退出只停自有子进程）、
QUOT-528（peer adopt-only 四路径验收，缺陷发现方）、DEC-014（绝不终止非本控制面进程）、
DEC-069（崩溃自愈）、DEC-075（自愈冷启动 bundle 命令表）
状态：已定稿

## 背景

QUOT-528 验收实测：`SupervisorEngine.stop_all()` 会把 `adopt_module()` 认领的外部
peer 一起终止。根因是账本里两种来源不同的 `origin=adopted` 无法区分：

1. **前任 supervisor 遗留的自有进程**——上一任自己 spawn、崩溃后对账
   （`Ledger.reconcile`）确认仍存活而续托管。按崩溃恢复语义，下一次 `stop_all`
   **应当能停**（它本来就是本控制面的进程）。
2. **外部 peer 认领**——`adopt_module()` / `PageBoot._record_peer()` 按识别规则
   认领的既有实例，账本证明不了是本控制面拉的。按 QUOT-519/523/527 红线，
   **只认领，不终止、不重启，退出后原样保留**。

`_stop_module()` 只看 `state` 不看来源，`BaseAdapter._stop_targets()` 又把
「端口上命中识别关键字的现行持有者」纳入终止目标（该机制本是为场景 1 的多面
模块找回另一面的树），于是场景 2 被误杀。

## 决策

1. **账本新增持久化标记 `ModuleRecord.adopted_peer: bool`（默认 False）**，
   随 `ledger.json` 原子落盘、跨会话保留：
   - `adopt_module()` 的**证据认领路径**置 `True`（账本已证明存活的续健康分支
     不打标记——那是场景 1）；
   - `PageBoot._record_peer()` 直接记账同样置 `True`（与引擎同一口径）；
   - `_start_module()` 真正 spawn 成功置 `False`（归属回到本控制面）；
   - `Ledger.record_restart(ok=True)` 换绑新 spawn 进程时置 `False`；
   - `reconcile()` 原样保留标记：外部 peer 跨会话仍是外部 peer。
2. **停止边界在引擎层统一执行**：`_stop_module()` 入口见 `adopted_peer=True`
   直接返回——不写 `stopping_by_user`、不改状态、不调适配器 `stop()`、不强杀。
   `stop_all` / `stop_module` / `restart_module` / CLI / HTTP / 看板退出钩子全部
   经过这一个闸门。`stop_all` 汇总对被跳过的模块记 `{"ok": true, "skipped":
   "adopted_peer"}`；HTTP `_stop_result` 把它们如实放进 `skipped`（没停，不是
   失败），`ok` 不受影响。
3. **适配器不感知账本**：`BaseAdapter._stop_targets()` 的「端口持有者找回」
   机制原样保留（场景 1 的多面模块仍需要它）。归属判定是账本/引擎的职责，
   适配器只是执行机构——引擎不对被标记的记录调 `stop()`，即满足派单
   「适配器 stop() 对前者一律跳过」的要求，不引入反向依赖。
4. **`origin` 取值不变**（`spawned` / `adopted`）：QUOT-528 验收钉住
   `origin == "adopted"`，新增 origin 值会破坏验收契约；标记是 origin 内部的
   证据链细分，不是第三种 origin。

## 考虑过的替代方案

- **新增 origin 值（如 `adopted_peer`）**：被否——验收用例断言
  `origin == "adopted"`，且 origin 语义（谁拉起的 vs 认领的）本身没错，错在
  认领的两种证据链没有区分。
- **按模块名判定（`ADOPT_ONLY_MODULES` 一律跳过 adopted）**：被否——
  单入口自有进程模式（`LDST_SUPERVISOR_OWN_PROCESSES=1`）下 supervisor 会
  亲自 spawn peer 模块；这些进程崩溃恢复后同样是 `adopted`，按模块名跳过会
  破坏「前任自有进程仍可停」的崩溃恢复语义。区分必须落在**记录**上，不是模块上。
- **按 cmdline 与适配器启动面 argv 比对推断归属**：被否——外部 peer 常常就是
  用与适配器相同的命令拉起的（计划任务/手工），指纹相同无法区分；且多面模块
  argv 随环境漂移，误判两个方向都会发生。

## 旧账本兼容

无 `adopted_peer` 字段的旧记录读入默认 `False`：前任自有进程的崩溃恢复语义
不变。旧代码认领的外部 peer（无标记）在升级后仍可能被停——单入口切换尚未
批准（single-entry.md §6），生产账本没有常托管的外部 peer；升级后所有新认领
（自愈冷启 / 页面自启 / 手工 adopt）都会带上标记。不做启发式回填：无法可靠
区分时宁可保持旧语义，也不猜测归属（DEC-014 失败关闭的同一精神）。

## 与 QUOT-527 的关系

QUOT-527（PR #207）新增 `stop_owned()`：托盘/服务退出只停**本任会话 spawn**
的模块（`origin != "spawned"` 全跳过），比本单边界更严。两者叠加后语义分层：

| 路径 | 外部 peer 认领 | 前任遗留自有进程 | 本任 spawn |
|---|---|---|---|
| `stop_all`（用户显式全停 / 单入口退出钩子） | 跳过（本单） | 停 | 停 |
| `stop_owned`（QUOT-527 托盘退出） | 跳过 | 跳过 | 停 |

看板 shutdown 钩子在 main 上仍接 `stop_all`，本单使其对 peer 安全；#207 合并后
改接 `stop_owned`，两个闸门互不冲突。

## 验收

- `tests/integration/test_peer_adopt_only.py` 六用例全绿（exit 用例 xfail 已删）。
- `tests/unit/supervisor/test_adopted_peer_stop.py`（11 项，fake 世界）：
  跳过 / 单模块停 / 重启不换 pid / 混跑选择性 / 前任进程仍可停 / 标记跨会话 /
  显式 spawn 清标记 / 旧账本默认 / 落盘往返 / record_restart 清标记。
- `tests/integration/test_stop_all_ownership.py`（2 项，真实临时进程）：
  前任 supervisor spawn 的进程被下一次 stop_all 停止；同一次 stop_all
  停自有进程、外部 peer PID 不变。
- 不改端口白名单、不改 `stopping_by_user` 自愈语义；生产 5950/8000/9100/9000/8100
  不触碰（真实宿主 ops 验收不在本机执行，沿用 QUOT-526 先例）。