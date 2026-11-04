# DEC-052（已批准）自选读面：`GET /api/watchlist` 照 DEC-033 口径开，只读、回环独占、平面 JSON

- 日期：2026-11-03（批准：2026-11-03）
- 提出：后端架构师（QUOT-379 S1，承接 QUOT-377 首页改版 A 案）
- **状态：已批准（项目领导人 2026-11-03 拍板，落点 QUOT-380）**
- 编号：`DEC-052`。按 DEC-045 §2 取值：
  `git ls-tree -r --name-only origin/main docs/decisions/` → **已扫到的最大号 = 051**，本单取 052。
  ⚠️ **本单原草稿编号为 `DEC-049`**（草稿里记录的快照「已扫到的最大号 = 048」是 2026-11-03
  QUOT-379 开工当时在 `agent/agent/bb41d7700c64` 分支上扫到的值）。落地时发现
  `DEC-049` 在 main 上**已被两份实体占用**（`DEC-049-review-data-vs-model-screen-split.md`
  复盘拆屏 / `DEC-049-theme-membership-and-board-name-join.md` 题材归属，且已被
  `docs/UI-SPEC.md`、`docs/specs/SPEC-CANDIDATE-POOL.md` 大量引用）。按 DEC-045 §2
  「取值以 origin/main 扫描为准」与 §3「零反向引用的一方承担让号成本」，
  **让号的是本单**——本单从未合并、无反向引用，故改为 `DEC-052`，
  引用方（QUOT-380 任务书 / `HOME-V2-DATA-SURFACE.md` / 代码注释）同步改到 052。
  批准动作针对的是本单内容，与编号变更无关。
- 批注（项目领导人 2026-11-03 拍板，QUOT-380 任务书口径）：
  1. 批准「自选代码清单经回环 HTTP 出到本机页面」，只读、回环独占、平面 JSON、只 GET 不开写、空态 200；
  2. `note` 这一版**不出**（§2.2 的建议被采纳，自由文本不进这个出口）；
  3. 指数条按「8 格改 4 格、只留 A 股」确认（§6 问题 3）；
  4. §2.3 那条前置（`ldst watch add|rm|list` CLI 写路径）纳入 QUOT-380 范围。
  正文其余部分不改写（DEC-045 / 本仓「决策只增不改」纪律）。
- 关联：DEC-033（持仓读写面，本单照抄其约束）/ DEC-015 Tier 1（隐私库不进信封与 MCP）/
  DEC-004（默认只绑回环 5950）/ DEC-006（数据只落本机）/ DEC-044（自选无已交付页面）/
  DEC-045（编号规则，本单让号依据）/
  `docs/UI-SPEC.md` §0「不覆盖」条 / `docs/SOURCES.md` §2.3 /
  `docs/specs/SPEC-RADAR-PAGES.md` §6.1 / `docs/specs/HOME-V2-DATA-SURFACE.md`（本单的事实来源）/
  `scripts/check_privacy.py` 第 3 组

## 1. 背景与为什么要放开

首页改版 A 案（QUOT-377）第二块是「我的自选（带现价/涨跌）」。数据只在本机：

- 自选存在隐私库 `data/journal.sqlite` 的 `watchlist` 表
  （`data/sqlite_store.py:38` 把它列进 `TABLES`，建表在 `:221`），字段只有
  `code` / `note` / `created_at`；
- **今天没有任何 HTTP 读路径**。全仓唯一读它的函数是 `cli/sync.py:182 watchlist_codes()`，
  只 `SELECT code FROM watchlist ORDER BY created_at`，用途是同步代码池
  （`docs/SOURCES.md` §2.3 第三来源），日志与对外输出里「只出现数量，不出现自选内容」；
- 前端也没有：`frontend/src/api/` 下无自选客户端，`components/Layout.tsx:132` 明写
  「自选、提醒没有已交付页面，登记为范围外，不建占位入口」（DEC-044）。

`docs/UI-SPEC.md` §0 把「交易日志 / 持仓 / 自选的网络读写」登记为**不覆盖**，
`web/server.py:11-12` 是同一句话。**DEC-033 已经为持仓开过一次完全相同形状的口子**
（`GET /api/positions` 读面 + 两条 POST 写面，回环独占 + `Host` 白名单 + 写方法同源校验）。
本单是**同口径的第二次决定**，不是第一次破例。

需要老板拍板的只有一句话：**允许「自选代码清单」这一项隐私数据经回环 HTTP 出到本机页面**。
它的敏感度低于持仓（清单不含成本、数量、金额），但性质同类——「这台机器在看什么」本身就是信号，
所以不能默认放开，要显式记一次。

## 2. 决定（批准后生效）

### 2.1 只开一条读路由

`GET /api/watchlist` —— 只读，返回本机自选清单 + 每条的现价与涨跌。
**照抄 DEC-033 §2.5 的三条闸门，一条不改**：

1. **回环独占**：`client_host(request.client)` 必须在 `is_loopback_host()` 白名单内，
   否则 `403`（同 `web/radar.py:849 _positions_guard`）；
2. **`Host` 白名单**：只接受 `127.0.0.1` / `localhost` / `::1`，`host_header_allowed()`
   不过就 `403`，`detail` 里带上收到的 Host；
3. **平面 JSON + `Cache-Control: no-store`**：**不进 `LadderEnvelope`**
   （`payload` / `domain` / `fetched_at` 这些键一个都不出现）。
   这是隐私数据进不了 export / MCP / AI 三条出口的**结构性**原因，不是装饰
   （`web/server.py:12-16` 那段注释是同一逻辑）。

额外两条沿用 DEC-015 `/api/journal/stats` 的硬约束（`web/server.py:1381`）：
**只 GET**、**空态 200**（本机没自选就回 `available=false` + 空清单，不是 404、不是伪造）。

### 2.2 字段与投影

| 字段 | 来源 | 说明 |
| --- | --- | --- |
| `code` | `journal.sqlite` `watchlist.code` | 唯一真相，不缓存副本 |
| `note` | `watchlist.note` | **默认不返回**（见下） |
| `latest_price` / `change_pct` / `prev_close` | `spot_quote` → `tencent_quote_batch` | 已登记数据集（`core/boundary.py:326`），`core/intraday.py:466` 已在用 |
| `price_as_of` / `price_status` | 同上 | 取不到就是 `null` + 原因，**不补 0、不拿成本价顶替**（DEC-028） |

- **`note` 是自由文本，建议这一版就不出**。DEC-015 那条纪律里 `/api/journal/stats`
  明确「不出自由文本」；`note` 是用户自己写的观察理由。持仓那边 `items[].note` 已经出了
  （DEC-033 的既定事实，纯文本渲染），但自选没有页面包袱，不必继承这个口子。
  要出请在批注里明写，否则默认关闭。
- **响应字段名不得叫 `watchlist` / `自选`**：这两个词都在
  `core/privacy.py:23-31` 的 `PRIVATE_KEYS` 里，走任何 envelope 出口会被 `strip_private()`
  **静默删字段**（`web/server.py:358-360` 记过一次这个坑）。建议顶层用 `items` + `watch_count`。
- **不落第二份存储**：不新增缓存表、不写 IndexedDB、不进 `data/` 下的任何文件。
  前端拿到就只存在 React 内存里（沿用 `SPEC-RADAR-PAGES.md` §6.3）。

### 2.3 明确不放开什么

- **不加写路由**。自选的增 / 删 / 改**不经 HTTP**。
  照 DEC-033 的做法，写面若要开是**再一次独立决定**，不与本单捆绑。
  首页只做只读展示 + 空态文案指向本机命令。
  ⚠️ **这条有一个必须先解决的前置**：`src/` 里**没有任何写 `watchlist` 的代码路径**
  （`grep INSERT INTO watchlist` 只在 `tests/integration/test_sync_code_pool.py:86` 命中），
  也没有 `ldst watchlist add` 这类 CLI 子命令——`ldst watch` 是盯盘信号，不是自选
  （DEC-037 的 `watch_settings` 分片是阈值持久化，同样不是自选清单）。
  也就是说：**这张表目前只有 schema，没有任何生产者，实际数据恒为空**。
  本单只批读面，但 S2 开工前要先补一条 CLI 写路径（建议 `ldst watch add/rm/list`），
  否则上线的是一个永远显示空态的卡片。该 CLI 由后端开发实现，不需要新决策——
  它写的是本机 SQLite，正是本单「写面不经 HTTP」这句话要求走的路。
- **不放开 `journal` / `trades`**：交易日志仍一条 HTTP 路由都不开
  （`/api/journal/stats` 只出聚合数字这条边界不动）。
- **不做跨站 / 远程读**：不回环之外绑定，不加远程 Token 复用
  （DEC-015：Token 管「谁连得进来」，不管「隐私数据该不该出去」——见 `web/radar.py:853` 注释）。
- **`LDST_CONTROL_ENABLED=0` 不连带关这条路由**，与 DEC-033 §2.5 对持仓写面的处理一致：
  关的是控制面，不是本机数据面。

## 3. 与 `journal*` 不经 HTTP 那条既有纪律的关系

**不冲突，且不需要修改那条纪律的措辞。** 理由要说清楚，因为这句话很容易被理解成「又开口子了」：

1. `journal*` 不经 HTTP 约束的是**三条出口**：`LadderEnvelope` 分发、MCP 工具结果、
   AI 上下文注入。`core/privacy.py:strip_private()` 是这三条出口的公共兜底。
2. 本单开的是一条**回环独占的平面 JSON 直连路由**，它**结构上进不了那三条出口**——
   不进信封就不会被 envelope 携带，MCP 侧 `mcp/tools.py` 的 `FORBIDDEN_KEYWORDS` 与
   `mcp/server.py:136 strip_private()` 两道都不放行。
   DEC-033 的 `GET /api/positions` 走的就是同一形状，落地后 `check_privacy.py` 全绿。
3. 所以 UI-SPEC §0 那条的准确边界（DEC-033 已经这么收窄过一次）是：
   **「隐私数据不经任何非回环出口」**，而不是「一个字节都不许离开 SQLite」。
   本单沿用这个已收窄的表述，**不改写 UI-SPEC §0 的原文**，只在 §0 的
   「不覆盖」清单里把「自选」一项标注为「已按 DEC-052 开只读回环口」。

## 4. 落地必须同时做的事（不做就是红灯）

1. **`scripts/check_privacy.py` 第 3 组的白名单要加第三条授权例外**。
   现在 `JOURNAL_IMPORT_ALLOWLIST`（`165-181` 行）只有两条：
   `web/server.py → journal.stats`、`web/radar.py → journal.position`，
   **其它任何对 `ladderstock.journal` 的引用一律判 FAIL**（`_journal_import_problems()`，`195` 行起）。
   新路由若取自选必然命中这条规则。加例外时**四项判据一个都不能少**
   （照 `journal.position` 那条：`def _watchlist_guard(` + `is_loopback_host(` +
   `host_header_allowed(` + `"Cache-Control": "no-store"`），
   并在 `_journal_rule_selftest()`（`219-283` 行）补一条反例：
   「自选模块出现在 `mcp/server.py` 里 → 必须判 FAIL」。**这条规则要求有牙——白名单不能变成后门**
   （`check_privacy.py:220` 原话）。
2. 新增单测 `tests/unit/web/test_watchlist_read_endpoint.py`：闸门（非回环 `403`、
   Host 不在白名单 `403`）、空态 200、`no-store`、取不到价时 `latest_price=null` 不补 0、
   响应不含 `payload` / `domain` / `fetched_at` 三个信封键。
3. 隐私回归：`scripts/check_privacy.py` 与 `tests/integration/test_position_privacy.py`
   同族断言要覆盖自选（`for keyword in ("position","holding","journal","watchlist")` 那段）。
4. `docs/UI-SPEC.md` §0 与 `docs/specs/SPEC-RADAR-PAGES.md` 补引用本单编号；
   `config/state.yaml` / `frontend/stores/manifest.json` **不动**（自选不进前端持久化分片）。

## 5. 影响与工作量

- 后端：新路由 + 投影 + 白名单例外 + 单测 ≈ **4h**（`HOME-V2-DATA-SURFACE.md` A-2）。
- 前置：`ldst watch add/rm/list` CLI（§2.3 ⚠️ 那条）≈ **3h**，不在本决策范围内但必须先做。
- 新增暴露面：**0 条非回环路径**。
- 风险：`note` 若放开，自由文本第一次进自选出口（建议本版不出，§2.2）。

## 6. 待老板回答的三个问题

1. 批 / 不批「自选代码清单经回环 HTTP 出到本机页面」？
2. `note` 这一版出不出？（默认建议：不出）
3. 美股两格与港股两格一起确认：按 `HOME-V2-DATA-SURFACE.md` §3-C 的建议
   **指数条从 8 格改 4 格（只留 A 股）**，可以吗？
   （恒指 / 恒生科技实测无源；道指 / 纳指有点位但本仓没有 `tickhub_us` 取数适配器，
   写适配器不属于首页改版的量级。）
