# MCP 工具清单与隐私边界

- 日期：2026-10-28
- 归属：个人端 / 本地工作台
- 相关：`src/ladderstock/mcp/tools.py`、`docs/MCP_TOOLS.md` 对应实现

本文登记 LadderStock 对外暴露的 MCP 工具（`TOOL_SPECS`），并明确一条硬约束：
**本机隐私库 `data/journal.sqlite` 不进入任何 MCP 工具结果、`LadderEnvelope` 或 AI prompt。**

## 1. 工具清单

| 工具 | 只读 | 域 | 入参 | 说明 |
|---|---|---|---|---|
| `ladder_get_watch` | 是 | `watch` | `date` | 盘中盯盘信号与阈值状态 |
| `ladder_get_sentiment_series` | 是 | `sentiment` | `date` | 情绪温度与晋级率时序 |
| `ladder_get_limit_ladder` | 是 | `sentiment` | `date` | 连板天梯与梯队断层 |
| `ladder_get_theme_rotation` | 是 | `sentiment` | `date` | 题材轮动与强度 |
| `ladder_get_dragon_tiger` | 是 | `sentiment` | `date` | 龙虎榜与席位结构 |
| `ladder_get_leader` | 是 | `sentiment` | `date` | 龙头股识别与地位变化 |
| `ladder_get_sources_health` | 是 | `meta` | — | 数据源主备与降级状态 |
| `ladder_start_research` | **否** | `review` | `date`、`sections?` | 发起一次复盘研究任务（异步，返回任务 ID） |
| `ladder_get_review` | 是 | `review` | `date` | 某日复盘结构化结果 |
| `ladder_get_validation` | 是 | `validation` | `date` | 明日验证条件与对账结果 |
| `ladder_get_sentiment` | 是 | `sentiment` | `date` | 情绪温度、晋级率、梯队断层 |
| `research_status` | 是 | `research` | `code`、`date?` | 个股研究六段进度（R1-1） |
| `candidate_pool` | 是 | `sentiment` | `date` | 当日候选池评分：强度 / 换手 / 地位 / 资金 / 题材分项与总分（口径见 `docs/specs/SPEC-CANDIDATE-POOL.md`） |
| `industry_snapshot` | 是 | `sentiment` | `industry` | 申万一级行业五维画像：景气 / 资金 / 估值 / 动量 / 拥挤度，缺数显式 `null` 并给原因（口径见 `docs/specs/SPEC-INDUSTRY-METRICS.md`，R5） |

除 `ladder_start_research` 外全部只读；所有日期入参统一为 `YYYY-MM-DD`。共 **14 个**工具（11 个 P0 + R1-1 增量 `research_status` 1 个 + R7 增量 `candidate_pool` 1 个 + R5 增量 `industry_snapshot` 1 个）。

> **命名偏差（已声明-接受）**：`candidate_pool` 没有沿用 `ladder_get_*` 前缀，
> 验收口径直接点名「MCP 只读工具 `candidate_pool`」。
> 现有测试只锁「清单与文档一致 + 全部只读 + 无隐私关键字」，不锁前缀；
> 若后续要统一成 `ladder_get_candidate_pool`，必须同时改 CLI 契约文档、`DOCUMENTED_TOOLS`
> 两处（`tests/contract/test_mcp_tools.py`、`tests/contract/test_mcp_stdio.py`）与本表，属新的改名决策。

> **`research_status` 的两处有意偏差**（2026-10-30，R1-1）：
> ① 工具名不带 `ladder_` 前缀——issue 里定死的名字，与 CLI 命令 `ldst research` 同名，便于跨面引用；
> ② 入参以 `code` 定位、`date` 可选（缺省取该代码最近一次 checkpoint），因为「读当前进度」
> 本来就没有日期。契约测试用 `CODE_SCOPED_TOOLS` 显式登记这个例外，见
> `tests/contract/test_mcp_tools.py`。信封域仍是 `review`（`LadderDomain` 取值集合不变）。

> **`industry_snapshot` 的两处有意偏差**（2026-10-31，R5）：
> ① 名字不带 `ladder_` 前缀——issue 验收文字直接点名「MCP 只读工具 `industry_snapshot`」，
> 与 CLI 命令 `ldst industry` 同源，便于跨面引用（同 `candidate_pool` 的先例）；
> ② **入参没有 `date`**：按行业名定位，口径是「本机缓存里的最新一批」，各维度自己给出
> `trade_date` / `as_of`（SPEC §1.2）。契约测试用 `INDUSTRY_SCOPED_TOOLS` 登记这个例外，
> 并反向断言 schema 里没有 `date`——要加日期就得先改口径文件。信封域是 `sentiment`（取值集合不变）。

### 1.1 `ladder_get_watch` 的数据来源

`ladder_get_watch` 不取数、不计算信号，只读 `ldst watch start` 落盘的 `<data_dir>/watch/status.json`
（判型实现只有一份：`ladderstock.watch.status`）。四种状态：

| `watch_state` | `data_source_status` | 含义 |
|---|---|---|
| `ok` | `ok` | 透出 `running` / `mode` / `detail` / `event_count` / `recent_events`（最多 50 条，与 CLI 同口径） |
| `missing` | `missing` | 尚未执行 `ldst watch start`，`reason` 明说且带路径 |
| `corrupt` | `missing` | 文件读不出来或不是合法 JSON，`reason` 带异常类名 |
| `unsupported` | `missing` | schema 不认识，`reason` 逐字段说明哪个字段不对 |

后三种一律 `available=false`、`payload.watch=null`：**没有状态就说没有，不伪造任何信号**。

### 1.2 `candidate_pool` 的数据来源与载荷

`candidate_pool` 不取数、不算第二套公式，只把 `core/pool.compute_pool(day, root=…, limit=30)`
的结果包成信封——与 `ldst pool` **同一条装配路径**（MCP 侧条数固定 `POOL_MCP_LIMIT=30`，`--limit` 是 CLI 侧的参数）。

| 位置 | 载荷 |
|---|---|
| MCP | `payload.pool` = 打分结果对象，`payload.summary` = 一行人类可读结论（程序生成，AI 不得改写） |
| `ldst pool` | `payload` 直接平铺打分结果对象（与 `ldst sentiment show` 一致） |

四个输入全部来自本机缓存回放（`limit_pool` / `concept_board` / `fund_flow` + 复用 `compute_ladder` 的当日最高板），
**不发起网络请求**。缓存缺失 → `available=false` + `missing` 点名字段 + `reason`，信封 `source_status=partial`；
分项取不到数给 `null` 并把它的权重从分母剔除（重归一），不补 0。

「地位」这个分项对外叫 **`standing`**：`position` 是本文件的禁止字段名（§2 补充约定 1），
不能出现在工具结果里，中文标签「地位」只用于人类可读输出（`SUB_SCORE_LABELS`）。

### 1.3 `industry_snapshot` 的数据来源与载荷

`industry_snapshot` 不取数、不算第二套公式，只把 `industry/snapshot.industry_snapshot(name, root=…)`
的快照包成信封——与 `ldst industry show` **同一条计算路径**（同一份 `META_PRIMARY_DATASET`
常量决定信封 `data_as_of`，两个出口不会各说各话）。

| 位置 | 载荷 |
|---|---|
| MCP | `payload.industry_metrics` = 五维快照（`industry` / `membership` / `dimensions` / `windows` / `formulas` / `datasets` / `provenance` / `missing` / `metrics_present`） |
| `ldst industry show` | `payload` 直接平铺同一份快照对象（与 `ldst pool` 一致） |
| `ldst industry list` | 平铺清单视图（配置视图，**不构造信封**，恒退出 0） |

六个输入数据集全部来自本机缓存回放（`sw_industry_map` / `finance_indicators` /
`valuation_history` / `fund_flow` / `industry_fund_flow` / `daily_k`），**不发起网络请求**，
也不 import 其他子模块的内部代码。空缓存 → `available=false`、`data_source_status=missing`、
五维字段**一个不少地铺成 `null`**，每个 `null` 都带 `reason`；
`provenance.values` 里无数的路径是 `sources: []` + `unverified: true`，
不存在「有值但无出处」，也不存在「有出处但没读到这批数据」。
行业北向净流入与申万行业指数涨跌幅是**结构性缺口**（上游无对应工具，SPEC §9），
永远显式 `null`——不降级成成分股聚合值，因为那是另一个字段。

## 2. 隐私边界（硬约束）

本机隐私库 `data/journal.sqlite`（表 `trades` / `positions` / `watchlist`；`positions` 自 schema v2 起有
`code / cost / qty / entry_date / industry / note / updated_at`，见 `docs/specs/SPEC-MY-POSITION.md` §1）是**只进不出本机**的数据：

| 出口 | 是否允许 | 依据 / 实现 |
|---|---|---|
| `ldst journal add/list/stats` | 允许（读写本机） | `journal/log.py`、`journal/stats.py`，只依赖本地 SQLite |
| `ldst position add/list/update/remove` | 允许（读写本机，**只在终端打印**） | `journal/position.py`（只 import `data/sqlite_store` + `errors`）、`cli/position.py`；输出不构造信封，口径见 `docs/specs/SPEC-MY-POSITION.md` |
| 本机只读 API（`127.0.0.1:5950`） | **不允许** | `web/server.py` 的 `_strip_private()` 递归剥掉 `journal` / `trades` / `positions` / `watchlist` |
| `LadderEnvelope`（`extra="forbid"`） | **不允许** | 信封无隐私字段，journal 也不构造信封 |
| MCP 工具结果 | **不允许** | `mcp/tools.py` 的 `TOOL_SPECS` 不含隐私域；`FORBIDDEN_KEYWORDS` 做静态护栏 |
| AI prompt / 叙述上下文 | **不允许** | `core/boundary.py:AI_WRITABLE` 只放叙述字段 |
| `ldst export` 导出文件 | 默认**不含**；`--with-journal` 显式才附加纪律统计摘要 | `cli/export.py`：默认走 `core/privacy.strip_private()` **递归**剥离（任意层级，含中文键写法，与 web / MCP 共用同一份实现），剥掉的字段名写 **stderr** 明示；`--with-journal` 才附加纪律统计摘要（不含明细/备注），并在 **stderr** 提示「隐私数据，请自行保管，勿上传」 |
| `ldst position list --export <path>` | 默认**不落任何文件**；只有显式给路径才写出，内容与 stdout 同一份，stderr 同样提示隐私 | `cli/position.py::_write_export()`；目标不可写 → 退出码 3 且 stdout 为空 |
| `ldst pool` / MCP `candidate_pool` | 允许，但**读不到隐私库** | `core/pool.py`、`cli/pool.py` 不 import `journal` / `sqlite_store` / `sqlite3`（AST 级断言），持仓不参与打分 |
| `ldst industry` / MCP `industry_snapshot` | 允许，但**读不到隐私库** | `industry/` 只 import `data/archive.py` 的缓存回放与 `core/ladder.py` 的取值工具；快照里的 `industry` 指**申万行业名**（配置数据），与 `positions.industry`（持仓标注）没有任何关系，两者不同源 |

补充约定：

1. **禁止字段名**：`journal`、`trades`、`positions`、`watchlist`、`position`、`holding`、`自选` 不得出现在任何 MCP 工具名、描述或 `input_schema` 中。候选池因此把「地位」分项对外命名为 `standing`（中文标签「地位」只用于人类可读输出，不参与键名）。
2. **禁止旁路**：MCP 不提供任何「查交易日志 / 持仓 / 自选」的工具；模型若需要纪律统计，只能由人类在 CLI 侧自行查看。
3. **只读边界**：`/api/*` 全部为 `GET`，无写接口；写路径只有 CLI（`journal add`）。
4. **本机回环**：web 只监听 `127.0.0.1:5950`（MCP `5951`）；端口被占用时 `ldst run` 直接以退出码 3 失败并提示 `--port`，绝不静默换端口。
5. **非回环必须鉴权**（R-1）：两条监听面共用 `core/listener.py` 的同一判据——回环放行；非回环必须 `<前缀>_ALLOW_REMOTE=1` + `<前缀>_TOKEN`（≥16 字符，web `LDST_WEB_*`、MCP `LDST_MCP_*`），否则拒绝启动（退出码 3）。开启后 `/api/*` 与 `/mcp` 均要求 `Authorization: Bearer <token>`，`hmac.compare_digest` 比较，失败 401。

## 3. 自动化护栏

`tests/integration/test_journal_privacy.py` 覆盖以下断言（每次 `pytest` 都会跑）：

- `tool_listing()` 的工具名 / 描述 / schema 中不出现 `FORBIDDEN_KEYWORDS`；
- 写入哨兵数据后，`/api/health`、`/api/state/config`、`/api/review`、`/api/sentiment`、`/api/ladder` 的响应正文不含哨兵内容与隐私字段；
- `LadderEnvelope.model_dump_json()` 不含隐私字段；
- `ldst export` 默认不含 journal 摘要，`--with-journal` 才写入并在 stderr 提示；
- 隐私库 schema 迁移幂等、WAL 生效、更高版本库被拒绝（退出码 3）。

`tests/integration/test_position_privacy.py`（R7 增量验收项）补四件事：

- MCP 清单里没有 position 相关工具，且 `mcp/` `ai/` `web/` 三个对外面的 import 里不出现 `ladderstock.journal`
  （`web/server.py` 的 `/api/journal/stats` 是唯一白名单，闸门由回环 + Host 校验负责）；
- 13 个只读工具逐个 `dispatch`，信封里不含持仓哨兵、不含 `entry_date` 等只在持仓里出现的字段名
  （`industry_snapshot` 缺 `industry` 入参走报错分支，同样断言报错文本里没有哨兵）；
- `strip_private()` → `build_messages()` 的 AI 上下文序列化结果不含持仓字段名（并反向断言哨兵在**未剥离**的载荷里确实存在，防止测试写成空断言）；
- `ldst position` 不加 `--export` 时数据目录里只有 `journal.sqlite*`，加了才落文件；
  目标不可写退出码 3 且 stdout 为空；
- 候选池与持仓互不引用：`core/pool.py` / `cli/pool.py` 不 import 隐私库（AST），
  `journal/position.py` 不 import 行情 / AI / MCP / 候选池。

## 4. 变更纪律

- 新增 MCP 工具或字段前，先确认它不属于隐私域；若确需纪律统计或持仓查询，走 CLI + 人类显式导出，**不新增工具**。
- 任何触达 `data/journal.sqlite` 的新代码都必须仅依赖 `data/sqlite_store.py`，不得在 `adapters/`、`core/`、`ai/`、`mcp/` 中出现对该库的引用。
- 隐私域的新分项命名要绕开 `FORBIDDEN_KEYWORDS`：候选池的「地位」对外叫 `standing`（§2）。
- 改 `positions` 表结构要 `SCHEMA_VERSION +1`、在 `_SCHEMA_V*` 追加一段 DDL，
  并保证「旧库升级不丢行、改结构前先备份、更高版本库被拒绝」三条仍有测试覆盖
  （`tests/unit/journal/test_position.py` §1）。
