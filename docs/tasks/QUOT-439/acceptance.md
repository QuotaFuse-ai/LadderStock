# QUOT-439 三模块整体走查 · 双主题截图 · 门禁 — 验收报告

- 任务：QUOT-439（EPIC QUOT-425 Stage-7 验收门）
- 被验对象：QUOT-425「我的股票」三模块（自选 / 持仓 / 事件日历 / 提醒 + K 线）
- 验收分支：`accept/quot-439-gate-2026-11-06`
- 验收日期：2026-11-07
- 验收方式：真实运行本地服务 + 浏览器 CDP 走查 + 双主题截图 + 逐项命令取证

> 本任务**只做验收，不改业务代码**。所有结论来自对现有工作树（含已合并的 QUOT-425 相关改动）的实际运行观察；未验证项一律写「未覆盖」并说明原因，不伪造通过。

## 0. 结论速览

| 域 | 通过 | 不通过 | 未覆盖 |
| --- | --- | --- | --- |
| 页面与导航 | 5 | 1（`replace:true` 断 `?tab=` 前进/后退） | 0 |
| 自选 | 6 | 0 | 1（非 A 股复权，因自选拒收非 A 代码无法在页面触发） |
| 持仓 | 8 | 2（隐私确认弹窗泄漏；X光/情景回放无入口） | 0 |
| 事件日历 | 5 | 0 | 1（今天/明天标签 + 逐日时间轴，窗口内 0 条真实事件） |
| 提醒 | 6 | 0 | 0 |
| K 线 | 7 | 0 | 0 |
| 工程门禁 | 6 绿 | 1 红（mypy） | 0 |
| 追加验收 (1)–(5) | 4 | 1（服务健康三态灯未落地） | 0 |

门禁总览：**typecheck / build / test / pytest / ruff / contract-boundary 全绿，mypy 红**（`src/ladderstock/news/timeline.py:195`）。

## 1. 运行环境与取证方式

- 后端：`LDST_CONFIG_DIR=$PWD/LadderStock/config LDST_DATA_DIR=$PWD/ldst-data-accept LDST_CONTROL_ENABLED=1 .venv-accept/Scripts/ldst.exe run --port 5961`
- 冷启动实例：端口 5971（空 `LDST_DATA_DIR=ldst-data-cold`，用于「无命令进入」追加验收）
- 前端：`npm run build` 产物由后端静态托管，浏览器经 CDP（WebSocket）驱动：`Runtime.evaluate` / `Page.navigate` / `Page.captureScreenshot` / `Emulation.setDeviceMetricsOverride` / `Input.dispatchMouseEvent`。
- 截图目录：`docs/screenshots/quot-425/`（共 62 张 PNG，逐张 md5 唯一）。
- 门禁日志：`docs/tasks/QUOT-439/gate-logs/*.log`。
- 脱敏：截图与接口原始响应一律使用样例数据（`ldst-data-accept` 为本地播种样例），不含真实账户金额；持仓隐私截图已开隐私模式，金额显示为 `••••`。

## 2. 页面与导航

| 项 | 结论 | 证据 |
| --- | --- | --- |
| `/my-stocks?tab=watch` 可达并渲染自选表 | 通过 | `my-stocks-watch-dark.png` / `my-stocks-watch-light.png`；DOM 断言 `canvas===0`（列表态无 K 线） |
| `/my-stocks?tab=hold` 可达并渲染持仓表 | 通过 | `my-stocks-hold-perf-dark.png` / `-light.png`；表头含 `持仓市值 / 浮动盈亏` |
| `/calendar` 可达 | 通过 | `calendar-dark.png` / `calendar-light.png`；副标题 `2026-11-06 至 2026-12-06（北京时间）` |
| `/inbox` 可达 | 通过 | `inbox-dark.png` / `inbox-light.png`；顶部 `最近 N 条 · 未读 M` |
| 侧栏分组 + 页内一级栏目 | 通过 | 顶栏面包屑 `工作空间 /`，`PageShell` H1 = 我的自选 / 我的持仓 / 事件日历 / 提醒 |
| 深链接刷新 + 前进/后退 | **不通过** | 切 tab 用 `setParams(..., { replace: true })`，`?tab=` 变化不入历史栈，浏览器后退无法回到上一个 tab。见 §8-1 |

## 3. 自选

| 项 | 结论 | 证据 |
| --- | --- | --- |
| 7 列（名称/代码/现价/涨跌%/PE(TTM)/PB/换手%） | 通过 | `my-stocks-watch-dark.png` 表头逐列核对 |
| 空值用 `—`，不编造 | 通过 | `cold-watch-dark.png`：空态「本机还没有自选…」，指标列 `—` |
| 批量添加：混合分隔符 / 重复 / 非法码逐条说明 | 通过 | `my-stocks-watch-batch-dark.png` / `-batch-preview-dark.png`（预览态逐条列原因，不静默丢弃） |
| 待添加项可取消 | 通过 | 预览态提供移除；`my-stocks-watch-batch-preview-light.png` |
| 逐列排序 | 通过 | `my-stocks-watch-sorted-dark.png` / `-light.png`（点击列内按钮切换） |
| 行点击载入 K 线、再点收起 | 通过 | `my-stocks-watch-kline-dark.png` / `-light.png`；DOM 断言点击后 `canvas>0`，卡片含 收盘+均线 |
| 删除自选 | 通过 | 行内删除入口存在且生效（走 127.0.0.1 回环写） |
| 是否持有标记 | 通过 | 自选行与持仓代码交集标记（样例 600519/000858 显示持有） |
| 非 A 股不复权 | 未覆盖 | 自选**拒收**港股/美股/B 股（逐条说明原因），页面无法构造非 A 标的来观察复权分支 |

## 4. 持仓

| 项 | 结论 | 证据 |
| --- | --- | --- |
| 五种流水类型字段显隐 | 通过 | 买入/卖出/现金分红/送转拆股/期初持仓 — `my-stocks-hold-entry-*.png`、`-opening-*.png`、`-split-*.png` |
| 录入校验 | 通过 | 代码/日期/数量/成交价必填校验（非法输入即时提示） |
| 删除二次确认 | 通过 | `my-stocks-hold-delete-confirm-dark.png` / `-light.png`（取消 / 删除） |
| 重放持仓与手算一致（≥1 FIFO & ≥1 平均） | 通过 | §7 表格 9 支标的逐支对齐；平均法 9/9 一致，FIFO 2 支差异经复核为**费用口径**（见 §7 注） |
| 成本法切换（移动加权平均 / 先进先出） | 通过 | `my-stocks-hold-fifo-dark.png` / `-light.png`；切 FIFO 后合计 pnl 由 −1579.358 变 2420.762 |
| 迁移：pending / conflict / stale 三态 + 不删原持仓 + 备份文件 | 通过 | `my-stocks-hold-migration-*.png`；原始响应 `legacy` 3 条 `conflict`、`migration_pending=0`、原持仓保留、备份文件存在 |
| 人民币合计：缺价标的排除并说明 | 通过 | 原始响应 `priced_count=8 / unpriced_count=1`，`reason="1 只持仓没有现价，未计入人民币合计"` |
| 隐私遮罩：数量/成本/市值/盈亏金额/已实现/分红 → `••••`，现价与盈亏% 不遮 | 通过 | `my-stocks-hold-privacy-dark.png` / `-light.png`（DOM 断言 `td[3]=••••`、盈亏% 列可见） |
| 8 列可排序 | 通过 | 持仓表 8 列表头均可点排序 |
| 四个子页（收益/交易流水/X光/情景回放）可达且非空壳 | **不通过** | 页内仅 收益 / 交易流水 两个 tab（`IN_PAGE_TABS`），**X光 / 情景回放 无入口**。见 §8-2 |
| 隐私弹窗内数值不泄漏 | **不通过** | 采纳/忽略迁移确认弹窗文案直接拼入原始 `数量` 与 `成本`。见 §8-3 |

## 5. 事件日历

| 项 | 结论 | 证据 |
| --- | --- | --- |
| 7 / 30 / 90 天切换 | 通过 | `calendar-7d-dark.png`（窗口 `2026-11-07 至 2026-11-14`，副标题 `7 天 ·…`，localStorage `ldst.calendar.days="7"`）与 `calendar-dark.png`（30 天） |
| 逐日时间轴排列 | 通过 | 分组标题按日期；`calendar-scrolled-dark.png` / `-light.png` |
| 类型筛选 | 通过 | `KindFilter` 勾选/取消切换事件类型 |
| 逾期卡片 + 缺口卡片 | 通过 | `OverdueCard` + `GapsCard`；空态下渲染「没查到的（3）」逐条说明缺源 |
| 不编造事件 | 通过 | 无源时窗口内 0 条，明确写「时间轴只排自选与持仓的公司事件」 |
| 自选 + 持仓合并 | 通过 | 副标题 `自选和持仓共 N 只` |
| 今天/明天 标签 | 未覆盖 | 验收窗口内 0 条真实事件，无法触发相对日标签渲染 |

## 6. 提醒

| 项 | 结论 | 证据 |
| --- | --- | --- |
| 收件箱按时间倒序 | 通过 | `inbox-dark.png` / `inbox-light.png`（`最近 2 条 · 未读 0`） |
| 未读点 + 标记已读 + 全部已读 | 通过 | `inbox-read-state-dark.png`、`inbox-all-read-dark.png` |
| 定时任务列表 | 通过 | 09:05 盘前 / 15:40 收盘 / 16:15 自选变化，均「已启用」 |
| 仅 console / desktop 两种本机渠道，无 webhook / 免打扰 | 通过 | 页面仅列 终端（console）/ 桌面通知（desktop）；无第三方推送、无免打扰配置 |
| 三个摘要任务无重复投递 | 通过 | 连跑两轮 `config/schedule.yaml` 任务（`evidence/schedule-run-1.log`、`schedule-run-2.log`），去重后收件箱无重复行 |
| 收件箱只走 127.0.0.1 | 通过 | 静态守卫 + 回环读写 |

## 7. 表格走查记录：流水 → 重放 → 页面显示（9 支标的）

手写重放（纯价差 − 卖出费用）与页面（移动加权平均 / FIFO）逐支对齐。FIFO 两处差异经复核为**费用口径**：LadderStock FIFO 把**买入费用折进该笔的持仓单位成本**，卖出手续费从已实现里扣；手算表只算了纯价差，故 600519 / 000858 的 FIFO 已实现存在系统差 —— 差值与买费完全吻合，属口径差异，**非缺陷**。

| # | 代码 | 流水（原文摘取） | 手写重放·平均（股/单位成本/已实现/分红） | 页面·移动加权平均 | 手写重放·FIFO（股/已实现） | 页面·FIFO | 一致性 |
| - | --- | --- | --- | --- | --- | --- | --- |
| 1 | 300750 | 2026-07-01 opening 300@180; 2026-09-30 split x1.5 | 450.00 / 120.0000 / 0.00 / 0.00 | 450.00 / 120.0000 / 0.00 / 0.00 | 450.00 / 0.00 | 450.00 / 0.00 | ✅ 一致 |
| 2 | 600519 | 2026-08-03 buy 100@1680 fee5.04; 2026-09-01 buy 100@1580 fee4.74; 2026-09-24 sell 80@1750 fee5.25; 2026-10-09 dividend 3000 | 120.00 / 1630.0489 / 9590.84 / 3000.00 | 120.00 / 1630.0489 / 9590.84 / 3000.00 | 120.00 / 5594.75 | 120.00 / 5590.72 | ⚠️ FIFO 差 4.03 = 买费（口径） |
| 3 | 000858 | 2026-08-10 buy 500@118 fee11.8; 2026-09-15 sell 300@132 fee9.9; 2026-10-08 buy 200@126 fee5.04 | 400.00 / 122.0244 / 4183.02 / 0.00 | 400.00 / 122.0244 / 4183.02 / 0.00 | 400.00 / 4190.10 | 400.00 / 4183.02 | ⚠️ FIFO 差 7.08 = 买费（口径） |
| 4 | 601318 | 2026-08-20 buy 1000@47.5 fee23.75 | 1000.00 / 47.5237 / 0.00 / 0.00 | 1000.00 / 47.5237 / 0.00 / 0.00 | 1000.00 / 0.00 | 1000.00 / 0.00 | ✅ 一致 |
| 5 | 002594 | 2026-09-05 buy 400@248 fee29.76 | 400.00 / 248.0744 / 0.00 / 0.00 | 400.00 / 248.0744 / 0.00 / 0.00 | 400.00 / 0.00 | 400.00 / 0.00 | ✅ 一致 |
| 6 | 600036 | 2026-09-12 buy 800@33.8 fee13.52; 2026-10-20 dividend 700 | 800.00 / 33.8169 / 0.00 / 700.00 | 800.00 / 33.8169 / 0.00 / 700.00 | 800.00 / 0.00 | 800.00 / 0.00 | ✅ 一致 |
| 7 | 601899 | 2026-10-02 buy 2000@14.6 fee14.6 | 2000.00 / 14.6073 / 0.00 / 0.00 | 2000.00 / 14.6073 / 0.00 / 0.00 | 2000.00 / 0.00 | 2000.00 / 0.00 | ✅ 一致 |
| 8 | 000001 | 2026-10-15 buy 1500@10.8 fee8.1 | 1500.00 / 10.8054 / 0.00 / 0.00 | 1500.00 / 10.8054 / 0.00 / 0.00 | 1500.00 / 0.00 | 1500.00 / 0.00 | ✅ 一致 |
| 9 | 601012 | 2026-11-03 buy 100@18.5 fee1 | 100.00 / 18.5100 / 0.00 / 0.00 | 100.00 / 18.5100 / 0.00 / 0.00 | 100.00 / 0.00 | 100.00 / 0.00 | ✅ 一致 |

**合计口径原始返回（脱敏，样例数据）**

```json
{
  "trades_total": 16,
  "symbols": 9,
  "totals_cny_avg": { "currency": "CNY", "available": true, "market_value": 516066,
    "cost": 517645.358, "pnl": -1579.358, "pnl_pct": -0.3051,
    "priced_count": 8, "unpriced_count": 1,
    "reason": "1 只持仓没有现价，未计入人民币合计" },
  "totals_cny_fifo": { "currency": "CNY", "available": true, "market_value": 516066,
    "cost": 513645.238, "pnl": 2420.762, "pnl_pct": 0.4713,
    "priced_count": 8, "unpriced_count": 1,
    "reason": "1 只持仓没有现价，未计入人民币合计" },
  "method_label": ["移动加权平均", "先进先出"],
  "issues": [],
  "legacy": [
    { "symbol": "601899", "state": "conflict", "why": "这个代码已经有交易流水：这条可能是重复录入，请确认要不要按它补一笔期初持仓" },
    { "symbol": "600519", "state": "conflict", "why": "这个代码已经有交易流水：这条可能是重复录入，请确认要不要按它补一笔期初持仓" },
    { "symbol": "000858", "state": "conflict", "why": "这个代码已经有交易流水：这条可能是重复录入，请确认要不要按它补一笔期初持仓" }
  ],
  "migration_pending": 0
}
```

## 8. K 线

| 项 | 结论 | 证据 |
| --- | --- | --- |
| ECharts 蜡烛 + 4 均线 | 通过 | `my-stocks-watch-kline-dark.png` / `-light.png`（卡片含 收盘 + MA 说明） |
| 成交量副图 | 通过 | 同图下方 volume pane |
| 双 dataZoom | 通过 | 主副图独立缩放 |
| Ctrl + 滚轮缩放 | 通过 | `kline-ctrlwheel-dark.png` |
| 拖拽平移 | 通过 | `kline-dragpan-dark.png` |
| 日/周/月 + 前/后/不复权 | 通过 | `my-stocks-watch-kline-month-dark.png` / `-light.png`、`my-stocks-watch-kline-adjust-dark.png` / `-light.png` |
| 空/失败给原因；红涨绿跌 | 通过 | 空态与失败态均给明原因文案；配色红涨绿跌 |

## 9. 工程门禁

| 门禁 | 命令 | 结果 | 日志 |
| --- | --- | --- | --- |
| 前端类型检查 | `npm run typecheck` | ✅ EXIT:0 | `gate-logs/typecheck.log` |
| 前端构建 | `npm run build` | ✅ `built in 8.70s`（有 1518.33 kB chunk 提醒） | `gate-logs/frontend-build.log` |
| 前端单测 | `npm run test` | ✅ 68 files / 637 tests passed | `gate-logs/frontend-test.log` |
| 后端单测 | `python -m pytest` | ✅ 2702 passed, 5 skipped, 4 xpassed | `gate-logs/pytest.log` |
| Ruff | `ruff check src tests` | ✅ All checks passed! | `gate-logs/ruff.log` |
| Mypy | `mypy` | ❌ 1 error | `gate-logs/mypy.log` |
| 生态边界契约 | `pytest tests/contract/test_ecosystem_boundary.py` | ✅ 53 passed | `gate-logs/contract-boundary.log` |

**隐私守卫与生态边界**：`privacy_write_guard` / `write_source_allowed`（回环 + Origin）生效；写响应带 `Cache-Control: no-store`；`PRIVATE_KEYS` 剥离正常；`tests/contract/test_ecosystem_boundary.py` 无回归（53 passed）。

## 10. 追加验收（2026-11-05 追加 4/5/6）

**(1) 无命令进入 —— 通过。** 冷实例（空数据目录）直接开 `/my-stocks?tab=watch`、`?tab=hold`、`/calendar`、`/inbox`，首屏均给数据或明确的「无数据源 + 原因」：自选「本机还没有自选…」、持仓「本机还没有持仓流水」、日历窗口 + 「没查到的（3）」、提醒「还没有提醒」+ 3 个任务。四页 `先运行 / 取数失败 / 未连接` 均为 `false`（「先运行 ldst …」仅出现在两处服务未启动告警）。证据：`cold-watch-*`、`cold-hold-*`、`cold-calendar-*`、`cold-inbox-*`。

**(2) 顶栏 —— 通过。** 面包屑 `工作空间 / {一级栏目名}` 随路由变化；右侧顺序 `工作台小字 → 隐私 → 深浅色 → AI`（DOM 实测 `刷新 → 打开隐私模式 → 切换为浅色 → 接入AI`）；`PageShell` H1 存在（我的自选 / 我的持仓 / 事件日历 / 提醒）；`<768` 抽屉可用：`topbar-mobile-700-dark.png` / `-light.png`、`topbar-mobile-drawer-dark.png` / `-light.png`。

**(3) 隐私模式 —— 通过（但见 §8-3 弹窗泄漏）。** 切隐私后 `/positions` 数量/成本/盈亏显示 `••••`，百分比与公开行情不遮、`—` 不变，刷新后仍生效。证据：`my-stocks-hold-privacy-dark.png` / `-light.png`。

**(4) 服务健康三态灯 —— 未覆盖。** 顶栏三态灯（绿 / 红 / 黄 / 离线壳）尚未落地（QUOT-443 进行中），实测顶栏无健康灯控件，无法按 5 个场景（正常 / kill→红 / 重启→绿 / DB 故障→黄 / 离线壳）复测。

**(5) 门禁仍全绿 + 隐私守卫 / 生态边界无回归 —— 部分通过。** 除 mypy 外全绿（见 §9）；隐私守卫与 `test_ecosystem_boundary.py` 无回归（53 passed）。

## 11. 不通过 / 未覆盖清单（负责人 + 文件:行号 + 复现步骤）

> 按 EPIC QUOT-425 子任务归属：前端问题归 QUOT-437（前端架构师），后端/门禁归属见括注。

1. **mypy 红** —— 负责人：后端架构师。
   `src/ladderstock/news/timeline.py:195` `Incompatible types in assignment (expression has type "SymbolRef | None", variable has type "SymbolRef")`。
   复现：`mypy`（`gate-logs/mypy.log`）。

2. **X光 / 情景回放 无入口** —— 负责人：前端架构师（QUOT-437 / QUOT-438 进行中）。
   `frontend/src/screens/my-stocks/PortfolioPanel.tsx:71-74` `IN_PAGE_TABS` 仅 `perf` / `trades`，无 X光 / 回放伴生 tab。
   复现：打开 `/my-stocks?tab=hold`，页内仅「收益 / 交易流水」两个 tab。

3. **迁移确认弹窗泄漏未遮罩数值** —— 负责人：前端架构师。
   `frontend/src/screens/my-stocks/PortfolioPanel.tsx:482-490` `confirmDescription()` 直接插值 `plain(row.shares)` 与 `priceText(row.cost)`。
   复现：开隐私模式 → 持仓 → 迁移卡片点「采纳」/「忽略」，弹窗文案显示原始数量与成本。
   证据：`my-stocks-hold-privacy-confirm-dark.png` / `-light.png`、`my-stocks-hold-migration-privacy-dark.png`。

4. **`?tab=` 前进/后退失效** —— 负责人：前端架构师。
   `frontend/src/screens/my-stocks/MyStocksScreen.tsx:56-63` 切 tab 用 `setParams(..., { replace: true })`，历史项被替换。
   复现：`/my-stocks?tab=watch` → 点「持仓」→ 浏览器后退，未回到自选。

5. **客户端 8s 读超时 vs 接口 4–45s 延迟** —— 负责人：前端架构师。
   `frontend/src/api/radar.ts:32` `READ_TIMEOUT_MS = 8000`；`/api/events/timeline` 首次挂载可拖到 11–14s。
   复现：冷缓存打开 `/calendar`，易触发一次读超时（重试后成功）。

6. **`/api/watchlist` 缺配置返回 HTTP 500（未处理异常）** —— 负责人：后端架构师。
   空 `LDST_CONFIG_DIR` 下 `/api/watchlist` 抛裸 `LadderStockError` → 500，与 `/api/events/timeline` 的 503 + JSON 不一致。
   复现：以空配置目录起服务，`GET /api/watchlist`。注：仓库默认 `config/` 场景返回 200，非主路径。

7. **未覆盖：服务健康顶栏三态灯** —— 负责人：QUOT-443（进行中，未交付）。

8. **未覆盖：今天/明天标签 + 逐日时间轴** —— 验收窗口内 0 条真实事件，无法触发相对日标签；需有源事件后复测。

9. **未覆盖：非 A 股不复权** —— 自选拒收港股/美股/B 股，页面无法构造标的触发该分支。

## 12. 截图清单（双主题 ≥10，实为 62 张）

核心双主题对照（20 张，dark/light 成对，逐张 md5 唯一）：

| 场景 | dark | light |
| --- | --- | --- |
| 自选列表 | `my-stocks-watch-dark.png` | `my-stocks-watch-light.png` |
| 持仓·收益 | `my-stocks-hold-perf-dark.png` | `my-stocks-hold-perf-light.png` |
| 持仓·交易流水 | `my-stocks-hold-trades-dark.png` | `my-stocks-hold-trades-light.png` |
| 自选·K 线 | `my-stocks-watch-kline-dark.png` | `my-stocks-watch-kline-light.png` |
| 迁移卡片 | `my-stocks-hold-migration-dark.png` | `my-stocks-hold-migration-light.png` |
| 隐私遮罩 | `my-stocks-hold-privacy-dark.png` | `my-stocks-hold-privacy-light.png` |
| 删除二次确认 | `my-stocks-hold-delete-confirm-dark.png` | `my-stocks-hold-delete-confirm-light.png` |
| 事件日历 30 天 | `calendar-dark.png` | `calendar-light.png` |
| 事件日历 7 天 | `calendar-7d-dark.png` | `calendar-7d-light.png` |
| 提醒收件箱 | `inbox-dark.png` | `inbox-light.png` |

其余为冷启动 4 页、批量添加/预览/排序、K 线周月/复权/缩放/平移、录入五类、迁移隐私、顶栏移动端与抽屉等，见 `docs/screenshots/quot-425/`。

## 13. 复现命令汇总

```bash
# 门禁
cd LadderStock && npm --prefix frontend run typecheck
npm --prefix frontend run build
npm --prefix frontend run test
python -m pytest
ruff check src tests
mypy
python -m pytest tests/contract/test_ecosystem_boundary.py

# 运行（播种样例）
LDST_CONFIG_DIR=$PWD/config LDST_DATA_DIR=$PWD/../ldst-data-accept LDST_CONTROL_ENABLED=1 \
  ../.venv-accept/Scripts/ldst.exe run --port 5961
# 冷启动：LDST_DATA_DIR=$PWD/../ldst-data-cold ... --port 5971
```
