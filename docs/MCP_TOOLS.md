# MCP 工具清单与隐私边界

- 日期：2026-10-28
- 归属：个人端 / 本地工作台（QUOT-95）
- 相关：`DEC-006`（生态四层边界）、`DEC-004`（回环端口与退出码）、`src/ladderstock/mcp/tools.py`、`docs/MCP_TOOLS.md` 对应实现

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

除 `ladder_start_research` 外全部只读；所有日期入参统一为 `YYYY-MM-DD`。

### 1.1 `ladder_get_watch` 的数据来源

`ladder_get_watch` 不取数、不计算信号，只读 `ldst watch start` 落盘的 `<data_dir>/watch/status.json`
（判型实现只有一份：`ladderstock.watch.status`，见 DEC-011）。四种状态：

| `watch_state` | `data_source_status` | 含义 |
|---|---|---|
| `ok` | `ok` | 透出 `running` / `mode` / `detail` / `event_count` / `recent_events`（最多 50 条，与 CLI 同口径） |
| `missing` | `missing` | 尚未执行 `ldst watch start`，`reason` 明说且带路径 |
| `corrupt` | `missing` | 文件读不出来或不是合法 JSON，`reason` 带异常类名 |
| `unsupported` | `missing` | schema 不认识，`reason` 逐字段说明哪个字段不对 |

后三种一律 `available=false`、`payload.watch=null`：**没有状态就说没有，不伪造任何信号**。

## 2. 隐私边界（硬约束）

本机隐私库 `data/journal.sqlite`（表 `trades` / `positions` / `watchlist`）是**只进不出本机**的数据：

| 出口 | 是否允许 | 依据 / 实现 |
|---|---|---|
| `ldst journal add/list/stats` | 允许（读写本机） | `journal/log.py`、`journal/stats.py`，只依赖本地 SQLite |
| 本机只读 API（`127.0.0.1:5950`） | **不允许** | `web/server.py` 的 `_strip_private()` 递归剥掉 `journal` / `trades` / `positions` / `watchlist` |
| `LadderEnvelope`（`extra="forbid"`） | **不允许** | 信封无隐私字段，journal 也不构造信封 |
| MCP 工具结果 | **不允许** | `mcp/tools.py` 的 `TOOL_SPECS` 不含隐私域；`FORBIDDEN_KEYWORDS` 做静态护栏 |
| AI prompt / 叙述上下文 | **不允许** | `core/boundary.py:AI_WRITABLE` 只放叙述字段 |
| `ldst export` 导出文件 | 默认**不含**；`--with-journal` 显式才附加纪律统计摘要 | `cli/export.py`：默认走 `core/privacy.strip_private()` **递归**剥离（任意层级，含中文键写法，与 web / MCP 共用同一份实现），剥掉的字段名写 **stderr** 明示；`--with-journal` 才附加纪律统计摘要（不含明细/备注），并在 **stderr** 提示「隐私数据，请自行保管，勿上传」 |

补充约定：

1. **禁止字段名**：`journal`、`trades`、`positions`、`watchlist`、`position`、`holding`、`自选` 不得出现在任何 MCP 工具名、描述或 `input_schema` 中。
2. **禁止旁路**：MCP 不提供任何「查交易日志 / 持仓 / 自选」的工具；模型若需要纪律统计，只能由人类在 CLI 侧自行查看。
3. **只读边界**：`/api/*` 全部为 `GET`，无写接口；写路径只有 CLI（`journal add`）。
4. **本机回环**：web 只监听 `127.0.0.1:5950`（MCP `5951`）；端口被占用时 `ldst run` 直接以退出码 3 失败并提示 `--port`，绝不静默换端口（DEC-004）。
5. **非回环必须鉴权**（R-1）：两条监听面共用 `core/listener.py` 的同一判据——回环放行；非回环必须 `<前缀>_ALLOW_REMOTE=1` + `<前缀>_TOKEN`（≥16 字符，web `LDST_WEB_*`、MCP `LDST_MCP_*`），否则拒绝启动（退出码 3）。开启后 `/api/*` 与 `/mcp` 均要求 `Authorization: Bearer <token>`，`hmac.compare_digest` 比较，失败 401。

## 3. 自动化护栏

`tests/integration/test_journal_privacy.py` 覆盖以下断言（每次 `pytest` 都会跑）：

- `tool_listing()` 的工具名 / 描述 / schema 中不出现 `FORBIDDEN_KEYWORDS`；
- 写入哨兵数据后，`/api/health`、`/api/state/config`、`/api/review`、`/api/sentiment`、`/api/ladder` 的响应正文不含哨兵内容与隐私字段；
- `LadderEnvelope.model_dump_json()` 不含隐私字段；
- `ldst export` 默认不含 journal 摘要，`--with-journal` 才写入并在 stderr 提示；
- 隐私库 schema 迁移幂等、WAL 生效、更高版本库被拒绝（退出码 3）。

## 4. 变更纪律

- 新增 MCP 工具或字段前，先确认它不属于隐私域；若确需纪律统计，走 CLI + 人类显式导出，不新增工具。
- 任何触达 `data/journal.sqlite` 的新代码都必须仅依赖 `data/sqlite_store.py`，不得在 `adapters/`、`core/`、`ai/`、`mcp/` 中出现对该库的引用。
