# SPEC-WEEKLY 周度复盘事实包口径

- 日期：2026-10-31
- 归属：AI 周报 R11（QUOT-149）
- 实现：`src/ladderstock/weekly/week.py`（周标签）、`weekly/facts.py`（事实包）、
  `weekly/checkpoint.py`（五段落盘布局）、`weekly/pipeline.py`（编排 + 产物）、
  `src/ladderstock/cli/weekly.py`（`ldst weekly`）、
  `src/ladderstock/web/server.py::weekly`（`GET /api/weekly`）、
  `frontend/src/screens/WeeklyScreen.tsx`（`/weekly`）
- 提示词：仓库根 `prompts/weekly.md`（与每日复盘的 `prompts/<section>.md` 同一目录、同一读法）
- 夹具：`tests/fixtures/weekly_cache.py`（`2026-W44` 三天有数、两天空）+
  `tests/unit/weekly/`、`tests/unit/web/test_weekly_endpoint.py`、
  `frontend/tests/weekly-screen.test.tsx`
- 相关：DEC-006（不采集、AI 只写叙述、数据只落本机）、D-07（指标口径不改）
- 口径不变项：本文**不修改**任何单日指标算式。周度数字全部由**聚合**得到，
  单日指标一律复用每日复盘那同一批函数（见 §2）。

## 0. 一句话

周报 = **一周的每日复盘产物**确定性聚合出的事实包，加上**五段 AI 叙述**。
数字全部由程序算且可逐字节复算；AI 只写字、不写数；模型失败时事实包与模板照写，
命令不整体失败。

## 1. 周标签契约

| 项 | 口径 |
|---|---|
| 形态 | ISO-8601 周历 `YYYY-Www`（`W01`…`W53`），一周固定周一到周日 |
| 归一 | `normalize_week()` 接受 `2026-W44` / `2026-w44` / `2026-10-28`（日期归一到所在周） |
| 非法 | 抛 `ValueError`：格式不对、周序越界（`W00` / `W54`）、该年没有这一周（`2025-W53`）；CLI → 退出码 **2**，Web → **400** |
| 本周 | `current_week(today=None)`：`facts.py` **不调 `now()`**，「今天是哪天」由调用方注入，跨零点跑不会自己换周 |
| 交易日窗口 | `weekday_days()` 只取周一…周五；周末没有行情，不进缺数据清单 |
| 路径安全 | 周标签直接拼进文件名（`weekly/<week>.json`），所以**先过白名单再拼路径**；`../evil`、`2026-W44/../../etc/passwd` 一律非法（同 `research/checkpoint.normalize_code()` 那道防线） |

示例：`2026-W44` = 2026-10-26（周一）～ 2026-11-01（周日）。

## 2. 输入（全部只读本机，零外呼）

| 输入 | 位置 | 取用 | 复用而非重算 |
|---|---|---|---|
| 每日复盘产物 | `state/review/<date>.json` | 五段 `metrics` 快照 | `review.pipeline.load_report()`；产物在 → 数字与当天发布的复盘**逐字一致**，来源标 `artifact` |
| 单日指标补算 | 本机缓存 | 产物缺某段时补那一段 | `review.pipeline.collect_metrics()` → `core/sentiment` / `core/ladder` / `core/pool`，来源标 `recomputed` |
| 每日 AI checkpoint | `state/ai_checkpoint/<date>.json` | **只读段落状态**（`pending/ok/degraded/failed`）算本周 AI 完成度 | `ai/checkpoint` 的路径函数；**不读叙述正文** |
| 涨停池 / 概念板块 / 个股日 K | `data/archive.py` 回放 | `limit_pool`、`concept_board`、`daily_k` | `core/ladder.dataset_snapshot()`、`core/pool.theme_board_index()` |
| 待验证项 | `state/validation/<date>.json` | `conditions` + `reconcile.results` | `core/validation.load_validation()` |

三条硬约束：

1. **不复制公式**（验收项「口径与每日复盘一致」）：本模块只做聚合（求和、均值、序列、
   连续天数、分箱计数），不重新定义任何单日指标；`temperature` / `promotion_rate` /
   `seal_rate` / 连板高度等一律来自上述函数，事实包把公式原文抄进 `caliber` 供读者核对。
2. **不新增采集入口**：不发起任何网络请求；缓存没有就是没有 → 该分项 `available=false`
   + `reason`，绝不猜值、不补 0。
3. **不碰隐私库**：不 import `journal` / `data.sqlite_store`，周度数字经 AI 上下文、
   页面、API 三条出口都不可能带出持仓与交易日志。

## 3. 聚合算式

事实包共 21 个顶层键；算得出就给数，算不出给 `null` + `reason`。

| 分项 | 键 | 算式 |
|---|---|---|
| 情绪温度序列 | `sentiment.series` / `sentiment.temperature` | 逐日一行（温度、晋级率、封板率、涨停数、炸板数、最高板、断层）；`temperature/promotion_rate/seal_rate` 走 `_trend()` → `first` / `last` / `change` / `avg` / `samples`，**只统计可用读数**（`null` 剔除，不补 0） |
| 涨跌分布 | `breadth` | 本周逐日 `daily_k` 的 `change_pct` 按 `BREADTH_BANDS` 分箱计数（9 箱：跌≥9% … 涨≥9%，`平盘` 是 `[0,0]` 单点），区间 `[low, high)` 按顺序命中一次；同日同代码只计首条（天梯 D-04 去重口径），键名走 `core/pool.CHANGE_PCT_KEYS` 那张容忍表 |
| 连板高度变化 | `ladder` | 逐日 `height`（当日最高板）→ `_trend()`；`peak_height` = 本周最大值；`leaders` 合并逐日 `leader_candidates`，同一代码只留连板数**严格更大**的那一天（并列保留更早一天），按 `-boards, code` 排序截前 `LEADER_TOP_N=5` |
| 主线题材持续天数 | `themes` | 逐日 `concept_board` 涨幅前 `THEME_WINDOW=10` 名记为一个窗口（同涨幅按名称升序，不依赖插入顺序）；`days_on_top` = 入窗交易日数，`streak_days` = 本周内最长连续；输出按 `streak_days → days_on_top → avg_change_pct → name` 排序截前 `THEME_TOP_N=10` |
| 晋级率趋势 | `sentiment.promotion_rate` | 与温度同一条 `_trend()`，`first → last` 即周内的方向 |
| 待验证项结算 | `validation` | 本周 5 个工作日生成的条件 + **上周五的条件在本周结算**（`CARRY_LOOKBACK_DAYS=5` 个日历日内回看）；`settled` 只认 `reconcile.results` 里有结论的那批，未对账进 `pending`，判定不出进 `unknown`；周外的日子只带「对账落点 `next_session` 落在本周内」的条目，不算本周的账 |
| AI 完成度 | `ai_coverage` | 逐日 checkpoint 的段落状态计数（`done` = `ok`+`degraded`） |

`missing_days` 逐日给日期、原因、当天复盘产物在不在；`missing_inputs` 给整周级别的缺失大类
（`state/review` / `state/ai_checkpoint` / `state/validation`）。`source_detail` 只含**后端名与条数**，
不含时间戳——事实包必须可复算。

## 4. 确定性（验收：两次运行 diff 为空）

- `facts.py` 与 `week.py` 里没有 `now()`、没有随机数；
- 所有排序都有确定次键（`-streak, -days_on_top, -avg, name` / `generated_date, id, metric`）；
- `generated_at` 只装在**信封层**（`pipeline.build_report()`），不进 `facts` 子树；
- 同一份本机缓存连跑两次 `build_facts()`，`json.dumps(sort_keys=False)` 结果逐字节相同
  （回归测试：`tests/unit/weekly/test_weekly_pipeline.py`）。

## 5. 边界：事实包不含模型输出（DEC-006）

- `facts` 子树里没有 `ai` / `narrative` 键，也不从每日 checkpoint 读叙述正文，只读段落状态；
- 五段叙述经 `core/boundary.py:merge_narrative` 闸门合并，AI 可写集合仍是
  `AI_WRITABLE = {narrative, commentary, highlights, risks, evidence, degraded, model}`；
- 每段的 `metrics` 是程序快照的**原样拷贝**（`section_snapshots()` 只搬运、不再算）；
  测试逐段断言 `"narrative" not in metrics`；
- 喂给模型的 user 消息里明确「不得输出任何指标数字键、不得新算指标、快照里为 `null`
  的字段就直说没有读数」（`pipeline.build_messages()`）。

## 6. 五段编排与 checkpoint

段落顺序、状态语义、`--force` / 断点续跑全部复用每日复盘的 `Orchestrator` 与
`ai/checkpoint.select_targets()`，`WeeklyOrchestrator` 只改两处：落盘布局与提示词。

**为什么单独一个目录**：周度 checkpoint 落 `state/weekly_checkpoint/<week>.json`，
不与每日的 `state/ai_checkpoint/` 混。`ldst ai status` 取「最近一份」是按文件名倒序扫目录，
而 `2026-W44` 字典序大于 `2026-10-30`，混进去会把每日复盘的状态视图永久顶掉。

## 7. 降级（要求 4：不得整体失败）

| 情况 | 行为 |
|---|---|
| 某段模型失败 | 只补那一段：程序模板叙述 + `mode="template"`、`degraded=true`、`notes[0]=TEMPLATE_NOTE`，其余段落保留 AI 成果 |
| 全部段落失败 | 五段全部模板，`source_status="partial"`，两份产物照写 |
| 整周没有可用单日指标 | **一次模型调用都不发**（对着空数字写字只会写出幻觉，也白烧额度）：五段仍按模板补齐，事实包与两份产物照写 |
| 模型未配置凭据 | 同上「全部段落失败」路径，`run.configured=false`，产物里如实标 `degraded` |

模板段落只**复述**事实包里已有的值（`_line()` 遇 `None` 的分项直接不写），不新增计算。
落盘走 `atomic_write()`，任一文件写失败不影响另一件，绝不留半截产物。

## 8. 退出码

| 形态 | 退出码 |
|---|---|
| `ldst weekly`（缺省本周、有数、AI 正常） | 0 |
| `ldst weekly --week 2026-w00` 等非法周标签 | 2（参数错误） |
| 整周没有可用单日指标（事实包 `available=false`） | 6 |
| 事实包有数但 AI 段落降级 / 失败 | 9（`EXIT_AI_DEGRADED`，与 `ldst review` 同口径） |
| 缺省本周且 AI 未配置 | 9（产物仍完整） |

**「6 优先于 9」**：整周没数据时 AI 降级只是附带现象，脚本该看的是前者。
两种情况产物都已落盘；`--force` 不改变退出码语义。

> 注：走「整周无数据」这条路径时**根本不构造编排器**，所以载荷里的 `configured` 是
> `RunResult` 的默认值 `true`，**读作「未校验」**而不是「凭据已配置」；真凭据缺失时看
> 有数据的那条路径（`configured=false` + 退出码 9）。

## 9. 产物 / API / 页面契约

- 产物：`<data_dir>/weekly/<week>.json`（页面与 API 消费）+ `<week>.md`（人读，
  版式与每日复盘同一条：抬头 → 事实包表格 → 五段叙述 → 口径 → 来源 → 页脚）。
- JSON 文档 15 个顶层键：`title` / `week` / `range` / `generated_at` / `freshness` /
  `source_status` / `available` / `reason` / `headline` / `facts` / `ai` / `sections` /
  `ai_failures` / `checkpoint` / `footer`。`sections` **恒定五段、顺序固定**。
- `GET /api/weekly?week=YYYY-Www`：只读产物，不重算任何东西；
  `domain="review"`（`LadderEnvelope` 的域名枚举里没有 weekly，不为此新增枚举值）、
  `topic="weekly_report"`、`payload={week, weekly}`。
  非法 `week` → **400**；没有产物 → **200** + `data_source_status="missing"` + `weekly=null` +
  `data_as_of=null`（与 `/api/review` 同一条空态口径，不 404、不伪造数字）；
  产物 `available=false` → 同样 `missing`，但五段模板仍在；文件不可读 / 顶层不是对象 → **500**。
  响应过 `strip_private()`。
- 页面 `/weekly`：复用报告式版式与每日复盘同一批组件；`?week=` 缺省为本周（非法值回落本周）；
  上一周 / 下一周按钮改 `week` 重新请求；离线 / 错误 / 空态三条路径与每日复盘页行为一致；
  `mode="template"` 的段落显示降级标识并可跳 `/settings?tab=ai`；**页面不新算任何数字**，
  没有读数的地方是 `MISSING` 而不是 0。

## 10. 验收对照

| 验收项 | 落点 |
|---|---|
| 给定一周夹具事实包稳定可复算（两次运行 diff 为空） | §4 + `tests/unit/weekly/test_weekly_pipeline.py`（同一 `pack` 两次 `build_facts()` 字节相同） |
| 假 ChatClient 失败时命令退出码语义正确且产物存在 | §7 / §8 + `tests/unit/weekly/test_cli_weekly.py`（6 优先于 9、离线 9、空周 6） |
| 口径与每日复盘一致（复用同一批计算函数，不复制公式） | §2 硬约束 1 + `day_snapshots()` 走 `review.pipeline.load_report/collect_metrics` |
| 事实包不得含模型输出 | §5 + 逐段 `"narrative" not in facts_json` |
| 降级不整体失败 | §7 + 全失败用例断言两份产物都存在 |
