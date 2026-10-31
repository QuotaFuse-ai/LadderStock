# DEC-030 雷达四接口的后端落点：HTTP 层只投影，资讯读账本，持仓回环独占

- 日期：2026-10-31
- 提出：后端架构师（QUOT-279 / R12 收口）
- 状态：生效（随 QUOT-279 实现落地）
- 关联：DEC-006（数据只落本机）、DEC-004（回环 5950，不自动换端口）、DEC-014（控制面 / 数据面分离）、
  DEC-015 Tier 1（隐私库不进信封与 MCP）、DEC-019（资讯来源与日历缺口）、
  DEC-020（隐私库 v2 与持仓边界）、DEC-028（判定在后端、`null` 不当 0）、
  `SPEC-RADAR-PAGES` §3–§6.1（四接口契约）与 §7（缺口清单）、
  `SPEC-NEWS-RADAR`（R6 口径）、`SPEC-CANDIDATE-POOL` / `SPEC-MY-POSITION`（R7 口径）

## 1. 背景

R12 四页（资讯雷达 / 事件日历 / 候选池 / 我的持仓）的前端已经在 `main` 上，字段名写死在
`frontend/src/api/radar.ts` 与 `SPEC-RADAR-PAGES` §3–§6.1，四页因此全在演示态 / 空态。
后端这一轮的活只有「按契约投影」，没有改名空间，也没有重算空间——R6 / R7 已经把数字算完并
落到本机库，HTTP 层再算一遍就是同一份缓存两个答案（违反 D-07「口径只有一份」）。

页面按 8 秒超时读接口（`frontend/src/api/radar.ts:21` `READ_TIMEOUT_MS = 8000`），而本机实测
一次 8 栏目 × 3 天的实时取数要 ~29 秒。这决定了资讯流的形态：**GET 不能替页面去联网取数**。

## 2. 决定

### 2.1 落点：`web/radar.py` 一个模块，只做参数校验 / 载荷组装 / 错误映射

四个接口共用一个 `APIRouter`（前缀 `/api`，`RADAR_PREFIX`），实现在 `web/radar.py`，
业务数字分别来自：

| 接口 | 唯一来源 | 与 CLI 的关系 |
| --- | --- | --- |
| `GET /api/news` | R6 `news.store.load_items()` + `news.events.extract_events()` | 同一个本机库、同一份 `StreamResult` 形状 |
| `GET /api/events` | R6 `news.calendar.build_calendar()` | 与 `ldst news calendar` 同一个函数 |
| `GET /api/pool` | R7 `core.pool.compute_pool()` | 与 `ldst pool` 同一个函数 |
| `GET /api/positions` | R7 `journal.position.compute_metrics()` + `core.quotes.price_map()` | 与 `ldst position list` **同一个函数** |

测试直接对比 HTTP 载荷与这些函数的返回值（`test_positions_are_the_cli_numbers_not_a_reimplementation`），
锁死「接口是 CLI 的第二个消费者，不是第二份实现」。

### 2.2 资讯流读本机库 + 回放取数账本，GET 不取数也不写

- 条目 / 事件：只读 `data/news.sqlite`（`load_items(…, limit=NEWS_READ_MAX)`，
  `NEWS_READ_MAX = 1000` 是**读取上限**，与展示用的 `limit` 不是一回事）。
- 栏目状态与计数：**回放** `news_runs` 账本里窗口被覆盖的最后一轮（`store.covering_run()`
  取轮次，`store.source_counts()` 按栏目数本窗口行数）。挂了就是挂了，不粉饰成 `ok`；
  「这轮没抓到新东西」与「这一栏挂了」在页面上必须是两回事。
- 因此 GET 全程零写入。测试用 `news_store.counts()` 前后对比锁死这条
  （`test_news_get_does_not_write`），空态轮还检查数据目录里一个文件都没被建出来。
- 库里根本没有 `new_items` / `known_items` / `outside_window` 这三个数（只有当轮内存里才有），
  投影给 `null` **不给 0**（DEC-028 §2.1）。
- 没库、窗口空、上游全挂三种情况都是 `200` + 结构完整 + `data_source_status="missing"`
  + `payload.reason` 指路（`ldst news <date> --until …`），不是 404、不是空壳 200、不伪造行。

### 2.3 日历保留有界实时取数（唯一的例外，理由是「本机没有落库路径」)

解禁日历与业绩预告两个有源小节只在 `build_calendar()` 的取数链路里存在，R6 没有给它们落库，
不取数就永远是空。所以 `/api/events` 走实时、但**只碰这两个数据集、有界**，并按 §4 出结果：
来源挂掉 → 该小节 `status="no_source"` + `reason` + `ask`；全挂 → `200` + `missing` + 退出码 4。
三个结构性缺口小节（财报预约披露 / 股东大会 / 政策窗口）永远 `no_source`，`ask` 写的是上游需求，
不拿口径不同的工具顶包（DEC-019）。

`month` 缺失或非法是 `422`，不是「默认本月」——页面深链缺参时静默换月会让人读错日历。

### 2.4 持仓：回环 + `Host` 白名单独占、平面 JSON、`no-store`、只有 GET

- 隐私数据不进信封：信封会流向 MCP / 导出通道，`strip_private` 是兜底不是边界
  （DEC-015 Tier 1 的同一条例外，与 `/api/journal/*` 一致）。载荷是**平面 JSON**，
  前端 `payloadOf()` 读不到 `payload` 时会退回根对象，因此不用改前端。
- 闸门 `_positions_guard` 复用控制面判据（`is_loopback_host` + `host_header_allowed`），
  **不看 `LDST_WEB_ALLOW_REMOTE` / Token**：Token 管「谁连得进来」，不管「数据该不该出去」。
  真实非回环验证（`ldst run --host 0.0.0.0` + `LDST_WEB_ALLOW_REMOTE=1` + Token，
  从本机局域网地址带合法 Token 访问）持仓仍是 `403`，公开面正常 `200`。
- 响应带 `Cache-Control: no-store`：持仓是明文隐私，不能让浏览器 / 中间缓存留副本。
- 只注册 GET：`POST` / `PATCH` / `DELETE` 是 §7 缺口 6，要人先定隐私决策，
  这里不预埋写路径（未注册方法实测 `405` + `Allow: GET`）。
- `?date=` 非法 `422`；现价取不到时 `latest_price` 是 `null`，`price_lookup.reason`
  说清去补哪条 `ldst sync`，不拿成本价冒充现价。

### 2.5 现价口径抽到 `core/quotes.py`：顺手关掉一条旁路

`price_map()` 原先长在 `cli/position.py` 里。接口层要同一个数就得反过来 import CLI，那会把
`typer.Exit` 带进请求线程，也给隐私面留一条「经 CLI 传递引用隐私库」的旁路。现在
`core/quotes.py` 是唯一的现价口径（`PRICE_KEYS` 那张字段容忍表只有一份），日期非法抛
`LadderStockError(EXIT_USAGE)` 而不是 `typer.Exit`——CLI 与 web 各自决定怎么把它变成人话。

### 2.6 隐私白名单：新增一条在册例外，同时封掉载体

`scripts/check_privacy.py` 第 3 组的 `ladderstock.journal` 白名单加
`src/ladderstock/web/radar.py → ladderstock.journal.position`，条件是文件里必须同时出现
四个闸门标记（`def _positions_guard(` / `is_loopback_host(` / `host_header_allowed(` /
`"Cache-Control": "no-store"`）——**在册 ≠ 放开**，标记掉一个就 FAIL。
`tests/integration/test_position_privacy.py` 用 AST 独立查同一件事（防「只改脚本不改检查」）。
同时新增一条规则：`ladderstock.cli.position` 不得被任何对外面 import（§2.5 抽口径之后，
这条旁路本就不该存在）。反例自检（少一个标记 / 走 CLI / 走 web 层新文件）都在这两个检查里。

### 2.7 错误码

| 情况 | 码 | 说明 |
| --- | --- | --- |
| 正常（含空库、空窗口、上游全挂） | 200 | 空态靠 `payload.data_source_status` + `reason` 表达，不用状态码表达「没数据」 |
| 日期 / 月份 / `limit` / 代码 / 来源非法 | 422 | 前端按 422 写死（`radar.ts`），控制面那套 400 在这里不能跟；`detail` 是人话，不截断后继续查 |
| `month` 缺失 | 422 | FastAPI 默认 422，明写 `detail` |
| 非回环来源 / `Host` 不在白名单访问持仓 | 403 | 与合法 Token 同时给也是 403（§2.4） |
| 非回环访问任何 `/api/*` 且没带 Token | 401 | 由 `serve()` 挂的 `TokenGuardMiddleware` 决定，不是本模块的逻辑 |
| 本机库读不动（`OSError` / `sqlite3.Error` / `RuntimeError`） | 500 | `detail` = `本机数据读取失败：<原因>`，不带堆栈、不带绝对路径 |
| 配置类错误（`EXIT_CONFIG`） | 503 | 沿用控制面口径 |
| 未注册的写方法 | 405 | `Allow: GET`（§2.4） |

### 2.8 信封与页面字段

- 外壳沿用 `LadderEnvelope`，不新造：news 是 `domain="news"` + `topic="news_stream"` /
  `event_calendar`，pool 是 `domain="sentiment"` + `topic="candidate_pool"`；
  `data_as_of` 分别是窗口右端 / 月份首日 / 池子数据日，`missing` 时一律 `null`，不拿今天顶替。
- `payload.data_source_status`（`ok / partial / missing`）是**页面读的字段**，由后端注入并
  优先于前端自己推导的值；`payload.data_as_of` 是与信封同值的副本——前端 `unwrap()` 只下钻
  `payload`，信封顶层它读不到（与 `web/industry.py` 同一个处理，实现处有注释）。
- `items[]` 保持 R6 八键契约，`origins` / `industries` 是可选附加键；`industries` 本轮给
  `[]`（§7 缺口 2，R6 还不产出行业标签，页面退化为关键词过滤），键在、值为空数组，
  不省略键——省略在契约里的含义是「后端没提供」。
- `events[]` 带 `item_id`，页面据此在资讯流条目标「已抽取事件」；抽取口径与 `ldst news` 同一个
  `extract_events()`，一份口径。

## 3. 权衡

- **GET 顺带取数**（否决）：一次调用就能「刷新即出数」，但 8 秒超时必然打不完，
  还会让只读页面变成写路径（谁点刷新谁改本机库），并且把上游失败面直接暴露成 500。
  代价是新机器上资讯页要先跑一次 `ldst news`——`reason` 里写清了这条命令。
- **给日历也建本机落库**（否决）：能统一到「只投影不取数」，但那是给 R6 加一张表、
  一套账本、一条写路径，本轮只做收口。日历只有两个数据集、有界，先照 §4 的降级口径走。
- **持仓进信封 + 顶层白名单**（否决）：与 `/api/journal/*` 的既有例外不一致，而且信封
  本身是对外通道的载体。平面 JSON + 回环独占 + `no-store` 才是 §6.1 写的形态。
- **`422` 而不是沿用控制面 `400`**（照契约）：前端 `radar.ts` 按 422 判参数错误，
  后端改成 400 会让页面把「日期写错」显示成「接口未接入」。
- **`empty` 算不算 `partial`**（不自己定）：SPEC §3 line 87 写的是「任一栏 `status != "ok"`
  或 `degraded` → 页面进 `partial`」，所以「窗口内这一栏一条都没有」也算。实现照契约，
  另加一条测试（`test_news_column_without_rows_in_window_is_empty_not_failed`）
  锁住 `empty` 与 `failed` 的区分仍然在 `sources_status` 里可见。
- **进程内缓存**（否决，DEC-006）：接口层再缓存一次会让页面与 CLI 给出两个答案。

## 4. 影响

- 新增：`web/radar.py`（四接口 + 闸门 + 错误映射）、`core/quotes.py`（现价唯一口径）。
- 改动：`news/store.py`（两条只读助手 `covering_run()` / `source_counts()`，写路径不动）、
  `web/server.py`（`create_app()` 注入 `radar_plane`，装配方式与研究面 / 产业面一致）、
  `cli/position.py`（改为 import `core.quotes`，输出不变）、
  `scripts/check_privacy.py` + `tests/integration/test_position_privacy.py`（§2.6）。
- 前端：零改动。四页从演示态 / 空态切真实通道即可（`?demo=1` 仍可用）。
- 本轮不做：`POST/PATCH/DELETE /api/positions`（§7 缺口 6，等人定隐私决策）、
  `items[].industries` 行业标签（§7 缺口 2，要 R6 先产出）。
- 契约测试：`tests/unit/web/test_radar_endpoints.py`（56 条：四接口 × 成功 / 空态 / 非法参数，
  加只读不写、账本回放、闸门、405、500、隐私在册）。
- 本机实测（`ldst run` 起的真实服务、三轮 curl 原始输出）贴在 QUOT-279 的 issue 评论与 PR 描述里；
  原始记录文件按仓库规则（`.gitignore` 的 `docs/tasks/`）只留本机，不入库。内容为夹具数据、
  非实盘行情，一次性 Token 与局域网地址已去敏。
