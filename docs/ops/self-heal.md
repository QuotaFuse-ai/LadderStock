# 自愈引擎（崩溃重拉 + needs_human）——QUOT-491 / 482-C

> 决策：DEC-069（自愈执行口径）。前置：QUOT-489（482-A，未落地）、QUOT-490（482-B，未落地）。

## 1. 范围与边界

| 归属 | 内容 | 状态 |
|---|---|---|
| 482-A | 账本结构、顺序启停引擎、归属判定（origin / cmdline_hash） | **未落地**，本模块以最小账本承载（见 §6） |
| 482-B | 模块启动命令（adapters） | **未落地**，重拉走命令表（见 §4） |
| 482-C（本文） | 崩溃识别、退避重拉、needs_human、通知、`/api/supervisor/state` | 本仓 |
| 482-D | 验收脚本 | 另单 |
| QUOT-484 | 前端面板（frontend/） | 另单，本仓只给数据 |

## 2. 崩溃识别：只有「非主动退出」才是崩溃（D4）

账本条目带 `stopping_by_user` 标记：`SupervisorEngine.stop_module / stop_all`
在停下前写入（`Ledger.mark_stop_intent` 的等价字段），显式 `start` 清除。

- 主动停（面板「停止」/ 关机钩子）先落标记 → 巡检看到标记，**绝不重拉**，健康置 `stopped`；
- 没有标记的进程消失 = 崩溃 → 进入退避重拉。

判定顺序（`SelfHealEngine._inspect`）：活着 → 恢复确认；主动停 → 静息；`needs_human` → 等人工；窗口超限 → 放弃；否则重拉。

## 3. 退避与上限（D5）

- 等待序列 **5s → 15s → 45s**（`BACKOFF_SECONDS`）；
- **5 分钟滑动窗口内最多 3 次**重拉（`WINDOW_SECONDS=300`，`restart_window`）；
- 超限：停止重拉，健康置 `needs_human`，通知栏弹出「需人工处理」，面板标红；
- 人工解除：`POST /api/supervisor/state/{module}/reset`（清窗口、健康回 ok）；
- 窗口随时间自然滑出（例：3 次失败后等 5 分钟，退避重新从 5s 起步）。

「发起 ≠ 恢复」：重拉成功只置 `restarting`，下一轮巡检确认新 pid 存活才回 `ok`；等待期间进程自己恢复了（如外部拉起）则不重复拉起，防双实例。

## 4. 重拉通道：bundle 命令表（QUOT-526）

生产实现是 `AdapterRestarter`（`restart.py`）：所有动作都交给 `SupervisorEngine`，
用 482-B 适配器（`build_default_adapters`，路径全部相对安装根）作为**唯一**命令表。

- **先认领、后拉起**：端口上已有命中识别规则的实例 → `adopt_module()` 记账
  （`origin=adopted`，pid 不变），不 spawn；否则按适配器启动面拉起；
- **失败关闭**：端口被非本模块进程占着 / 读不到识别证据 → 不启动、不终止，
  失败结论照常走退避与上限；
- **冷启动**：`SelfHealEngine(modules=..., cold_start=True)` 时托管这份模块表，
  账本里没有 pid 的模块立刻拉一次（0s），失败后按 5s→15s→45s 重试；
- **模式闸门**：`LDST_SUPERVISOR_OWN_PROCESSES=1`（单入口）才允许 spawn；
  默认附加模式只认领、绝不自动拉起（peer adopt-only，DEC-071）；
- **旧通道**：`restart-commands.json` / `CommandTableRestarter` 仅作历史过渡保留，
  生产装配不再读取。

决策全文见 [DEC-075](../decisions/DEC-075-self-heal-cold-start-bundle-command-table.md)。

## 5. 通知（D5：失败必告警，通知失败必被吞）

通道（`supervisor/notify.py`）：

| 通道 | 行为 |
|---|---|
| `EventLogSink` | 本地滚动日志 `<data_root>/supervisor/notifications.log`，保留最近 200 行，永不抛 |
| `ToastSink` | Windows Toast（PowerShell），**只在失败 / 放弃时弹**，恢复不弹（不制造噪音）；进程内 try/except 全吞，超时 10s |

文案固定携带：模块名、端口、第几次（`2/3`）、原因摘要；放弃时标「需人工处理」。

**边界（D6）**：通知不引入托盘驻留、不引入任何常驻 UI——Toast 是一次性的系统通知，弹出即结束。

**通知失败被吞**：任一 sink 抛错（PowerShell 不存在 / 超时 / 非零码）只记一行日志，自愈主流程照常走（`MultiNotifier` 逐 sink 隔离）。

## 6. 最小账本承载（482-A 未落地的过渡决策）

`supervisor/ledger.py` 只存自愈必需字段：`pid` / `stopping_by_user` / `restart_count` / `restart_window` / `health` / `last_exit_reason`；`origin` / `cmdline_hash` 只透传不判定（归属是 482-A 的职责）。落盘 `data/supervisor/ledger.json`（原子写），进程重启后自愈状态可恢复。

482-A 落地后由其账本平替：字段超集，接口（`ensure` / `update` / `record_restart` / `prune_window` / `reset_heal`）保持兼容。

## 7. 日志滚动（D5：不许写无上限日志）

每模块一份 `<data_root>/supervisor/logs/<module>.log`：追加前裁剪，**只保留最近 200 行**（`DEFAULT_LOG_MAX_LINES`）。写 500 行后文件里只有最后 200 行（验收 ⑤）。日志写入永不抛（目录占位等 OSError 吞掉）。

## 8. 对外接口

`GET /api/supervisor/state`（**仅回环**，复用雷达面的隐私读闸门；`Cache-Control: no-store`）：

```json
{
  "ok": true,
  "engine": {"running": true, "window_seconds": 300.0, "max_restarts": 3, "backoff": [5.0, 15.0, 45.0]},
  "modules": [{
    "module": "senate", "ports": [8765], "pid": 202,
    "health": "needs_human",           // 对外三值：ok | restarting | needs_human（stopped 折叠为 ok）
    "state": "needs_human",            // 原始生命周期（含 stopped）
    "restarts_5min": 3, "restart_count": 3,
    "stopping_by_user": false, "origin": null, "cmdline_hash": null,
    "last_exit_reason": null,
    "self_heal": {"attempts": 3, "max": 3, "status": "gave_up"}   // QUOT-484 前端契约
  }]
}
```

`POST /api/supervisor/state/{module}/reset`：写面来源证据（`X-LDST-Client: 1` 或同源 Origin），未知模块 404。

## 9. 引擎挂载

`create_app` 时随 FastAPI 启停（`app.router.on_startup/on_shutdown`，starlette 1.10 已移除 `add_event_handler`）；轮询间隔 1s，`stop(timeout)` 会打断等待中的退避。测试可注入 `supervisor_engine=` 假引擎。

## 10. 验收对照（QUOT-491 ①–⑥）

| # | 场景 | 证据 |
|---|---|---|
| ① | kill → 5s 重拉成功 | `tests/unit/supervisor/test_selfheal.py::test_crash_restarts_after_five_seconds_and_health_returns_ok` |
| ② | 连续 4 次 → 第 3 次后停、needs_human | `...::test_four_crashes_in_a_row_stop_at_three_and_mark_needs_human` |
| ③ | 主动 stop → 不自愈 | `...::test_intentional_stop_never_self_heals` |
| ④ | 失败弹通知、通知模块挂掉不影响主流程 | `tests/unit/supervisor/test_notify.py` |
| ⑤ | 写 500 行日志只留上限 | `...::test_log_roll_keeps_only_the_cap` |
| ⑥ | pytest 全绿 | 本轮全量运行 |
