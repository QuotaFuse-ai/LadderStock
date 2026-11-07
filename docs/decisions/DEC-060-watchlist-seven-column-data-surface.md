# DEC-060 自选七列数据面：字段来源、容差键与「缺数据不估算」

- 日期：2026-11-06
- 提出：产品经理（QUOT-440，父任务「按 vibe-astock 整体复刻我的股票三模块」）
- 状态：已采纳（对应实现 QUOT-427，后端已合入 `origin/main`；页面随 QUOT-431 落地）
- 编号：`DEC-060`（任务书预定号，`docs/decisions/` 无同名文件；
  `core/watch_prices.py` 注释已引用本号）
- 关联：DEC-052（`GET /api/watchlist` 只读面）、DEC-055（自选写面）、
  DEC-028（不拿成本价顶替现价）、DEC-059（自选页路由）、QUOT-427、QUOT-431
- 生效版本：`docs/UI-SPEC.md` v1.19、`docs/specs/SPEC-RESEARCH-PAGES.md` v0.5

## 1. 背景

自选页要一眼回答「这几只票现在怎么样」。参考产品给的是七列：
名称 / 代码 / 现价 / 涨跌% / PE(TTM) / PB / 换手%，并且**七列都能排序**。
本仓的 `GET /api/watchlist` 最初只有现价与涨跌，PE / PB / 换手没有出口；
上游 `tencent_quote_batch` 的字段命名并不统一，缺字段时如果补 0 或拿别的数顶替，
排序和筛选会直接给出错误结论。

## 2. 决定

### 2.1 七列与来源

| 列 | 响应字段 | 来源 / 口径 | 排序 |
|---|---|---|---|
| 名称 | `name` | 自选表 / 报价附带名称；为空显示 `—`，`title`「名称未取到」 | 是（`localeCompare`） |
| 代码 | `code` | 本机自选表，裸 6 位 | 是（`localeCompare`） |
| 现价 | `latest_price` | `spot_quote` → `tencent_quote_batch`；2 位小数 | 是 |
| 涨跌% | `change_pct` | 同一报价；带方向色与箭头 | 是 |
| PE(TTM) | `pe_ttm` | 上游报价投影；2 位小数 | 是 |
| PB | `pb` | 上游报价投影；2 位小数 | 是 |
| 换手% | `turnover_pct` | 上游报价投影；2 位小数 | 是 |

另有「操作」列（移除），不属于数据七列。

### 2.2 容差键：上游命名不统一时按优先级取第一个有限值

- PE：`pe_ttm` → `pe`
- PB：`pb` → `pb_mrq`
- 换手：`turnover_rate` → `turnover` → `换手率` → `换手`

规则：

- **第一个有限数值生效**；`NaN` / `Infinity` / 空串视为缺失；
- **`0` 是有效读数**（亏损股 PE 可为负，停牌换手可为 0），不当缺失；
- 前端只认投影后的 `pe_ttm` / `pb` / `turnover_pct` / `metrics_reason`，
  不在页面做第二次别名回退——容差只在一个地方实现。

### 2.3 缺失口径：`null` + 原因，禁止估算

- 三项里缺哪项，该列为 `null`，页面显示 `—`，单元格 `title` 写原因；
- 三项都缺时 `metrics_reason = "上游 tencent_quote_batch 不返回 PE(TTM) / PB / 换手%"`；
- **不补 0、不估算、不拿成本价 / 昨收 / 其它字段顶替**（DEC-028）；
- 缺失不影响其它列，也不影响同一行其它票的读数；
- 数字一律 `.ldst-num`，缺失值统一引用 `MISSING = "—"`。

### 2.4 页面行为（随 QUOT-431 落地）

- 表头可排序，循环为升序 → 降序 → 不排序，带 `aria-sort`；
- 批量添加：一个 textarea 混写代码；先「预览」列出「待加入 / 已在自选 / 输入重复 /
  不能加入」及原因，**确认后才写库**；取消文案「不加了」；
- 点表格任意一行，在表格下方渲染该票的 K 线卡；再点一次收起；
- 移除按钮 `aria-label="移除 <code>"`，走确认框；
- 空态：「还没有自选」+「去批量添加」；
- 数据时点：页头写 `数据时点 <price_as_of>`，附注「本页是快照，不是逐笔」；
- **不实现自动轮询**：只在挂载、手动刷新、增删后取数（与参考产品的 3 秒自动刷新不同，
  按本仓代码如实登记）。

### 2.5 接口形状

- `GET /api/watchlist` 保持 DEC-052 的平面 JSON：顶层 `items` + `watch_count`，
  **不进 `LadderEnvelope`**，`note` 不出；
- 单条新增字段：`pe_ttm / pb / turnover_pct / metrics_reason`；
- 写面按 DEC-055：`POST /api/watchlist`、`POST /api/watchlist/{code}/delete`，
  不提供 `PUT` / `PATCH`；
- 全部回环 + `Host` 白名单，写面另要同源证据，响应 `no-store`。

## 3. 理由

- **七列是用户核对一只票的最小集合**：现价与涨跌回答「现在」，PE / PB / 换手回答「贵不贵、
  活不活跃」；少一列就要跳转，多一列会挤压表格宽度；
- **容差键放在后端**：上游字段名随数据源变化，前端做回退会把「缺失」与「另一个指标」
  混在一起；后端投影一次，页面只消费稳定契约；
- **`0` ≠ 缺失**：把停牌换手 0 当缺失会让排序把真实读数排到最后，是数据错误；
- **不估算**：这是本产品反复确认的底线（DEC-028），估算值无法被用户复核。

## 4. 代价与风险

- **上游缺 PE / PB / 换手时，三列会整列 `—`**：这是如实呈现，但页面看起来「信息很少」；
  已在单元格 `title` 与页脚说明原因，不通过补数美化。
- **容差键有先后顺序**：同一批数据里若 `pe_ttm` 与 `pe` 同时存在且不同，按优先级取前者；
  这是约定，不是自动择优。
- **无自动刷新**：用户需要手动点刷新；对盘中盯价是弱点，但避免高频请求与页面闪烁。
  若后续要做，应复用 `/api/watchlist` 的静默重取模式单独立项。
- **批量添加是「先预览后确认」而不是搜索确认**：没有搜索框；用户输入的名称 / 拼音
  不能解析，只能按代码加入。参考产品的搜索确认不在本版。

## 5. 影响文件

- `src/ladderstock/core/watch_prices.py`（容差键、投影、原因）
- `src/ladderstock/web/radar.py`（`watchlist_view`、写接口）
- `src/ladderstock/journal/watchlist.py`（本机读写）
- `frontend/src/screens/my-stocks/WatchlistPanel.tsx`（七列、排序、批量、行点击）
- `frontend/src/api/radar.ts`（`WatchlistItem` 解析）
- `frontend/src/components/KlineCard.tsx`（行点击后的 K 线卡）
- `frontend/tests/my-stocks-screen.test.tsx`、`frontend/tests/nav-routes.test.tsx`
- `docs/UI-SPEC.md` §3.11 / §7.1 / §10 / §11.11、
  `docs/specs/SPEC-RESEARCH-PAGES.md` §8

## 6. 生效版本与验收

- 生效：QUOT-427 后端合入起；页面随 QUOT-431，文档随 `UI-SPEC` v1.19 登记。
- 验收：
  - 七列齐备且都可排序，循环为升序 → 降序 → 不排序；
  - PE / PB / 换手按容差键取第一个有限值，`0` 保留为有效读数；
  - 缺失列显示 `—` 且 `title` 有原因，全仓无补 0 / 估算；
  - 批量添加必须经过预览与确认，取消不写库；
  - 点行出现 K 线卡，再点收起；移除按钮 `aria-label` 含代码。
