# LadderStock CLI 命令基线（ldst）

> 依据《LadderStock 升级文档 v1.0》第五、七、十节整理。
> **基线状态：P0 已定稿，共 31 条命令**（2026-10-28 定稿）。
> 19 条来自文档明文，12 条为文档已定义能力但未给命令名的缺口，按建议命名补齐并定为 P0。
> 按本表逐条对比，实现方不得改名；确需改名需另开决策记录。
>
> **命令计数口径（四套数字，各有其用，勿混用）**：
> - **31 条命令** —— P0 定稿的**命令**数（定稿口径，逐条对比以此为准）；
> - **35 行** —— 下方 §A–E 表格行号（`sync` 三种形态、`validate` 两级子命令各占一行）；
> - **33 个叶子路径** —— §A–E 表格去重后的命令叶子（`sync` 三种形态合并为一条）；
> - **32 条可执行 argv** —— §A–E 那 33 个叶子对应的矩阵条数（减去长驻服务 `run` / `mcp stdio` / `mcp http`，另加 `sync --dataset` / `sync --full` 两种形态各跑一次）。
>
> **R7 增量（2026-10-30）**：§E″ 另有 **5 条**命令（`pool` + `position add|list|update|remove`），
> 行号续编 37–41（全表 **41 行**），**不计入上面 31 条 P0 基线**；叶子路径因此共 **39** 个，
> `tests/fixtures/command_matrix.py` 的 `LEAF_PATHS` 与之逐一对应（多一条/少一条都有测试点名），
> `scripts/check_commands.py` 实测 argv 相应为 **38 条**（39 个叶子 − 3 个长驻服务 + `sync` 的两种形态各跑一次）。
>
> **R1-1 增量（2026-10-30）**：基线 31 条口径**不变**，在其之上新增 1 条命令
> `ldst research <code>`（个股研究六段管线）。加它之后：叶子 **34** 个、可执行 argv **33** 条。
> 见 §0「研究管线」行与 §D′。
>
> **R5 增量（2026-10-31）**：基线 31 条口径**不变**，在其之上新增 2 条命令
> `ldst industry list` / `ldst industry show <行业>`（申万一级行业五维画像，口径见
> `docs/specs/SPEC-INDUSTRY-METRICS.md`）。行号续编 42–43，见 §D‴。
>
> **R6 增量（2026-10-31）**：在上述增量之上再新增 2 条命令
> `ldst news <date>`（资讯流：公告 / 研报 / 快讯 / 政策 + 规则事件抽取）与
> `ldst events --month YYYY-MM`（事件日历），行号续编 44–45。加它之后：全表 **45 行**、
> 叶子 **43** 个、`scripts/check_commands.py` 实测可执行 argv **42** 条。见 §D″。
>
> **R11 增量（2026-10-31）**：基线 31 条口径**不变**，在其之上新增 1 条命令
> `ldst weekly [--week YYYY-Www]`（周度复盘：事实包确定性聚合 + 五段 AI 叙述，口径见
> `docs/specs/SPEC-WEEKLY.md`）。行号续编 44，全表 **44 行**、叶子 **42** 个、
> `scripts/check_commands.py` 实测可执行 argv **41** 条。见 §A′。
>
> **QUOT-380 增量（2026-11-04）**：基线 31 条口径**不变**，新增 3 条自选清单命令
> `ldst watch add|rm|list`，行号续编 46–48，见 §I。
> ⚠️ 上面的历史计数**彼此不一致**（R6 那条写「全表 45 行 / 叶子 43 / argv 42」，
> R11 那条写「44 行 / 42 叶子 / 41 argv」，两者都自称「行号续编 44」），
> 本单不代为对账；**唯一可复核的计数口径是 `tests/fixtures/command_matrix.py`**
> （QUOT-380 当时 `LEAF_PATHS` 为 45；当前值见下一段）。
>
> **QUOT-443 增量（2026-11-06）**：基线 31 条口径**不变**，新增 1 条长驻命令
> `ldst watchdog`（独立看门狗，端口 `5952`，见 §J 与 `DEC-063-platform-watchdog-and-health`）。
> **QUOT-523 增量（2026-11-10）**：基线 31 条口径**不变**，新增命令组
> `ldst supervisor start|stop|restart|status`（单入口启停，见 §L）。
> **QUOT-522 增量（2026-11-10）**：基线 31 条口径**不变**，`__watch-guard` 是页面自启的内部
> 长驻入口（`ldst run` startup 拉起，不是用户命令；`hidden=True`），只登记叶子契约、不进矩阵。
> **QUOT-524 增量（2026-11-10）**：基线 31 条口径**不变**，新增 `ldst config seed`
> （从随包模板补齐配置，见 §C 的 `+1` 行与 `DEC-071-platformdirs-config-seed`）。
> **以代码为准的当前计数**：`LEAF_PATHS` **58** 个叶子、`COMMAND_MATRIX` **52** 条可执行 argv
> （58 − 7 个不进矩阵的叶子 `run` / `watchdog` / `__watch-guard` / `mcp stdio` / `mcp http` /
> `supervisor start` / `supervisor restart`，+ `sync` 的两种形态各跑一次；
> 复核入口 `tests/unit/test_cli_output_option.py::test_registered_leaves_match_baseline`）。
>
> **前置约束**：所有取数命令只向生态 peer 取数——TickHub-CN（只消费）、QuotaFuse Wire（可选消费）、StockSenate（只读消费）。`sources` / `sync` 里的"源"指 **peer 的工具**，不是行情供应商；主备必须同 peer，peer 整体不可用时回放本地缓存，绝不启用自建采集。

## 0. 总览（31 条）

> 口径说明：本节按 **P0 基线 31 条命令**统计（定稿口径）；下列分组明细展开后为 **35 行**表格、去重 **33 个叶子路径**，对应 `check_commands.py` 矩阵里的 **32 条**可执行 argv（长驻服务 `run` / `mcp stdio` / `mcp http` 不入矩阵）。§D′ 的 `research` 1 条、§E″ 的 R7 增量 5 条、§D‴ 的 R5 增量 2 条、§A′ 的 R11 增量 1 条另计，全表 44 行 / 42 叶子 / 41 argv。四套数字的关系见文首「命令计数口径」。

| 分组 | 命令 |
|---|---|
| 启动与复盘（5） | `run`、`review`、`review show`、`validate`、`validate reconcile` |
| 核心指标（2） | `sentiment show`、`ladder show` |
| 数据同步（4） | `sync`、`sync --dataset`、`sync --full`、`sync status` |
| 数据源（3） | `sources list`、`sources validate`、`sources health` |
| 盘中与提醒（4） | `watch start`、`watch status`、`watch stop`、`notify test` |
| 交易日志（3） | `journal add`、`journal list`、`journal stats` |
| 导出与状态（3） | `export`、`state inspect`、`state reset` |
| AI 编排（3） | `ai status`、`ai retry`、`ai chain test` |
| 研究管线（1，R1-1 增量，不计入 31 条基线） | `research` |
| 资讯雷达（2，R6 增量，不计入 31 条基线） | `news`、`events` |
| MCP（3） | `mcp stdio`、`mcp http`、`mcp tools list` |
| 配置与调度（4） | `config validate`、`config migrate`、`schedule list`、`schedule run` |
| 体检（1） | `doctor` |
| **R7 增量（5，非 P0 基线）** | `pool`、`position add`、`position list`、`position update`、`position remove` |
| **R5 增量（2，非 P0 基线）** | `industry list`、`industry show` |
| **R11 增量（1，非 P0 基线）** | `weekly` |
| **QUOT-443 增量（1，非 P0 基线）** | `watchdog` |
| **QUOT-500 增量（3，非 P0 基线）** | `backtest run`、`backtest list`、`backtest show` |
| **QUOT-523 增量（4，非 P0 基线）** | `supervisor start`、`supervisor stop`、`supervisor restart`、`supervisor status` |

## A. 启动、复盘与核心指标（9）

| # | 命令 | 作用 | 关键参数 | 输出 / 退出码 |
|---|---|---|---|---|
| 1 | `ldst run` | 启动本地工作台（看板 + 本机 API） | `--port 5950`、`--host 127.0.0.1` | 长驻进程 |
| 2 | `ldst review --date YYYY-MM-DD` | 对指定日期重新生成复盘 | `--date`（必填）、`--force` | 结构化结果 stdout；AI 降级完成 9 |
| 3 | `ldst review show --date` | 只读取已生成复盘，不重算 | `--date` | 无结果 6 |
| 4 | `ldst validate --date` | 查看明日验证条件与对账结果 | `--date` | 无结果 6 |
| 5 | `ldst validate reconcile --date` | 触发收盘对账（15:00 后） | `--date` | 对账结果；部分数据缺失 8 |
| 6 | `ldst sentiment show --date` | 情绪温度、晋级率、梯队断层 | `--date` | 程序直出数值，不含 AI 文本；数据不可用 6 |
| 7 | `ldst ladder show --date` | 连板天梯与梯队断层 | `--date`、`--output` | 表格 / JSON；数据不可用 6 |
| 8 | `ldst export --format pdf\|md` | 导出复盘报告 | `--format`、`--date`、`--out` | PDF 失败自动降级 md 并提示 |
| 9 | `ldst state inspect` | 前端持久化状态摘要（调试） | — | 只读 |

## A′. 周度复盘（R11 增量，1）

> **31 条 P0 基线之外的新增命令**，行号从 44 起接排，不动上面的编号。
> 口径来源：`docs/specs/SPEC-WEEKLY.md`（聚合算式、周标签契约、退出码、降级规则）。

| # | 命令 | 作用 | 关键参数 | 输出 / 退出码 |
|---|---|---|---|---|
| 44 | `ldst weekly` | 周度复盘：把一周的 `state/review` + `state/ai_checkpoint` + `state/validation` 与本机缓存**确定性聚合**成周度事实包，再按五段（情绪 / 资金 / 题材 / 龙虎榜 / 龙头）生成 AI 叙述，落 `data/weekly/<week>.json` + `<week>.md` | `--week YYYY-Www`（也接受 `2026-w44` / `2026-10-28`，缺省本周）、`--force`（已完成段落也重跑）、`--output` | `--output json` 载荷 18 个顶层键（`week` / `range` / `ok` / `available` / `reason` / `configured` / `degraded` / `failed` / `models` / `session_days` / `missing_days` / `report_path` / `markdown_path` / `checkpoint` / `sections` / `ai_failures` / `headline` / `exit_code`）；非法周标签 **2**、整周没有可用单日指标 **6**、AI 段落降级或失败 **9**（与 `ldst review` 同口径） |

- **「6 优先于 9」**：整周没数据时 AI 降级只是附带现象，脚本该看的是前者；
  两种情况**两份产物都已落盘**（降级不整体失败）。
- **口径与每日复盘同源**：单日指标一律走 `review.pipeline.load_report` /
  `collect_metrics`（产物缺某段就用同一批函数补算那一段，来源标 `artifact` / `recomputed`），
  题材走 `core/pool.theme_board_index`，本命令**不复制任何公式**（D-07）。
- **事实包不含模型输出**（DEC-006）：`facts` 子树里没有 `ai` / `narrative`，每日 checkpoint
  只读**段落状态**用来算本周 AI 完成度；叙述经 `core/boundary.py:merge_narrative` 合并，指标原样保留。
- **可复算**：聚合层没有 `now()`、没有随机数、排序全有确定次键；同一份缓存连跑两次逐字节相同。
  周标签先过白名单再拼文件名（防目录穿越）。
- **断点续跑**：段落状态机复用 `ai/checkpoint.select_targets()`，但落盘是
  `data/state/weekly_checkpoint/<week>.json`——**不与每日 checkpoint 混目录**，
  否则 `ldst ai status` 的「取最近一份」（按文件名倒序）会被 `2026-W44` 顶掉。
- 消费侧：`GET /api/weekly?week=`（`domain=review`、`topic=weekly_report`，无产物 200 + `missing`）
  与看板 `/weekly`，页面不新算任何数字。

## B. 数据层（7）

| # | 命令 | 作用 | 关键参数 | 输出 / 退出码 |
|---|---|---|---|---|
| 10 | `ldst sync` | 增量同步全部数据集 | — | 部分源失败 8 |
| 11 | `ldst sync --dataset <name>` | 只同步指定数据集 | `daily_k` / `limit_pool` / `dragon_tiger` / … | 同上 |
| 12 | `ldst sync --full` | 强制全量（维护用） | `--full` | 同上 |
| 13 | `ldst sync status` | 水位线与最近同步状态 | — | 表格 / JSON |
| 14 | `ldst sources list` | 生态 peer 与各数据集的工具顺序 | — | 含 peer / tool / primary / fallback / cooldown |
| 15 | `ldst sources validate` | 校验 peer 可达性与主备合法性 | 可选 `--id` | 源不可用 4 |
| 16 | `ldst sources health` | peer 最近失败冷却与降级记录 | 可选 `--since` | 表格 |

**`--codes`（只作用于代码池数据集 `daily_k` / `fund_flow` / `dragon_tiger_seats`）**：
这三个工具的真实 schema 里 `code` 必填，空参回 `QFSD-9001`，所以 `ldst sync` 对它们是
「逐只代码各取一次再合并落库」。池子按 `--codes` → `sync.yaml` 的 `codes` → 本机自选
`watchlist` 取，三处都为空时**不发空参请求**，该数据集直接 `failed`（可选数据集 `skipped`）。
单次上限 100 只，超过判参数错误（退出码 2），不悄悄截断。口径与理由见 `docs/SOURCES.md` §2.3。

```bash
ldst sync --dataset daily_k --codes 600000,000001   # 一次同步两只票，水位增量自动带 start
```

**同库串行（QUOT-234）**：`ldst sync` 一进来先拿 `data/market.duckdb.lock` 这把进程级写锁，
第二个 sync 排队而不是把第一个的 DuckDB 文件锁顶成崩溃。等待上限 `LDST_DB_LOCK_TIMEOUT`
（秒，默认 30）；超时 **10**，一次取数都不发。`sync status` 只读水位文件，不参与排队。

## C. 盘中、提醒与交易日志（7）

| # | 命令 | 作用 | 关键参数 | 输出 / 退出码 |
|---|---|---|---|---|
| 17 | `ldst watch start` | 盘中盯盘：3 分钟涨跌幅阈值、封板/开板检测 | `--date`、`--threshold` | 信号事件流 |
| 18 | `ldst watch status` | 盯盘运行状态与最近信号 | — | 表格 / JSON |
| 19 | `ldst watch stop` | 停止盯盘 | — | 幂等 |
| 20 | `ldst notify test` | 通知链路冒烟（盘前 09:05 提醒同一链路） | `--channel` | 发送结果 |
| 21 | `ldst journal add` | 追加交易日志 | `--code`、`--side`、`--price`、`--qty`、`--note` | **仅写本地 SQLite** |
| 22 | `ldst journal list` | 查询交易日志 | `--from`、`--to`、`--code` | 仅本地，不出本机 |
| 23 | `ldst journal stats` | 纪律复查统计 | `--from`、`--to` | 仅本地 |

## D. AI 编排（3）

| # | 命令 | 作用 | 关键参数 | 输出 / 退出码 |
|---|---|---|---|---|
| 24 | `ldst ai status` | 模型链与最近调用状态 | — | 含 fallback 链顺序 |
| 25 | `ldst ai retry --date` | 重试指定日期失败段落 | `--date`、`--section` | 成功段落不重跑；仍失败 9 |
| 26 | `ldst ai chain test` | 多后端兼容矩阵体检 | 可选 `--model` | 逐模型报告 tool_choice / JSON mode / 截断 |

## D′. 研究管线（R1-1 增量，1）

> **31 条 P0 基线之外的新增命令**，行号从 36 起接排，不动上面的编号。

| # | 命令 | 作用 | 关键参数 | 输出 / 退出码 |
|---|---|---|---|---|
| 36 | `ldst research <code>` | 个股六段研究管线：`company → financial → forecast → valuation → risk → report` 顺序执行，每段落 `data/research/<code>/<date>/<stage>.{json,md}` + 一份 checkpoint（`data/state/research_checkpoint/<code>/<date>.json`，原子写），中断重跑只补缺失段；`--check` 只审计已落盘产物的数字引用（R3） | `--date`（缺省取今日）、`--only <stage>`、`--retry-failed`、`--force`、`--check`、`--output` | `--output json` 载荷见 `ResearchRunResult.as_dict()`（键集合是 R1-2/R1-3/R1-8 的消费契约）；`code` 非法或 `--only` 未知 2；缺 `config/ai.yaml` 3；任一段失败或降级 9。`--check`：审计通过 0、产物不存在 6、有不合格数字 8（不读 `config/ai.yaml`） |

- `--only` / `--retry-failed` / `--force` 的**选择语义与五段复盘编排（`ai retry`）逐字一致**：
  共用 `ai/checkpoint.select_targets()`，已完成段默认跳过，`--only <stage>` 命中已完成段时是
  no-op（要重跑单段就配 `--force`），`--retry-failed` 只重跑 `failed` 段。
- **本段不联网取数**（联网检索属 R4，需人类批准）：`facts` 由调用方注入，默认空；指标进
  AI 上下文时**成对带上出处**（`ResearchPipeline.run(facts=…, provenance=…)`），
  没有出处的数值字段进不了 `verified` 集，渲染时只能挂〔未核实〕；
  未配置模型凭据时六段按占位叙述落盘并如实标 `degraded`（退出 9），用于验证顺序执行与断点续跑。
- `--check`（R3）是**只读审计**，不是重跑：读已落盘的 `<stage>.{json,md}`，判「正文每个数字都带
  角标 `[n]` 或标〔未核实〕、角标指得回去且值对得上」，缺省审 `report` 段、`--only <stage>` 换段；
  一次模型调用与一次取数都不发，所以不需要 `config/ai.yaml`。载荷：`ok` / `status` / `counts` /
  `findings[]`（含 `line` `column` `number` `reason` `excerpt`）/ `problems[]`，`audited` 与 `missing`
  两分支键集合一致。审计形态做成开关而非 `ldst research report <code>` 子命令，理由见下文
  与本节 §0 计数口径（既有叶子加开关不改 41 行 / 39 叶子 / 38 argv）。
- MCP 侧只读工具 `research_status`（`code` 必填、`date` 可选，缺省取该代码最近一次 checkpoint）
  读同一份进度，输出走统一信封；**交易日志与持仓不出现在任何研究产物里**。

## D″. 资讯雷达（R6 增量，2）

> **31 条 P0 基线之外的新增命令**，行号从 44 起接排，不动上面的编号。
> 口径与验收见 `docs/specs/NEWS-RADAR.md`；本任务**不含页面**（四页归 R10）。

| # | 命令 | 作用 | 关键参数 | 输出 / 退出码 |
|---|---|---|---|---|
| 44 | `ldst news <date>` | 一天（或 `--until` 区间）的资讯流：公告 / 研报 / 快讯 / 政策统一 schema，跨源合并去重，规则抽取事件，默认落 `data/news.sqlite` | `<date>`（缺省今日）、`--until`（≤31 天）、`--source`（可重复）、`--code`（可重复，公告类必填）、`--limit`、`--store/--no-store`、`--ai`（**默认关闭**）、`--output` | `--output json` 载荷见 `StreamResult.as_dict()` + `payload.ai`；日期非法 / `--source` 未知 / 窗口超长 2；全部栏目失败 4；部分失败 8；`--ai` 请求了但一条叙述都没成 9 |
| 45 | `ldst events --month YYYY-MM` | 事件日历：财报披露日 / 解禁日 / 股东大会 / 政策发布窗口 | `--month`（必填）、`--code`（解禁栏目必需）、`--limit`、`--fetch/--no-fetch`、`--output` | `--output json` 载荷见 `CalendarResult.as_dict()`；月份格式非法 2；有源小节全挂 4；部分挂 8 |

- **两个命令都不读隐私库**：`journal.sqlite`（交易日志 / 持仓 / 自选）与资讯面完全无关，
  要按代码过滤只能显式 `--code`。隐私字段名在任何出口（stdout / MCP / 导出）递归剥离，
  唯一口径 `core/privacy.py`。
- **`ldst events` 不默认「当月」**：`--month` 必填，避免把月历当今日视图误用。
- **日历的三个缺口是明写的**：`earnings_disclosure` / `shareholder_meeting` / `policy_window`
  返回 `status="no_source"` + `entries=[]` + `reason` + `ask`，**不估算、不编日期**。
- **模型只写叙述**：`--ai` 不加就绝不碰模型；加了也只给规则已抽出的事件补一句 `ai_note`，
  送模型的只有标题 + ≤200 字摘要 + url（`docs/specs/NEWS-RADAR.md` §隐私面）。
- MCP 侧同口径只读工具：`news_stream`（`store` 默认 false，工具被反复调用不留库文件）、
  `event_calendar`（`data_as_of` 用月份首日，缺口在 `source_status` 上如实标 `partial`）。

## E. MCP、配置、调度与体检（8）

| # | 命令 | 作用 | 关键参数 | 输出 / 退出码 |
|---|---|---|---|---|
| 27 | `ldst mcp stdio` | MCP stdio 服务 | — | **stdout 只输出 MCP 协议报文** |
| 28 | `ldst mcp http --port 5951` | MCP Streamable HTTP | `--port 5951`、`--host` | 默认回环监听；非回环见 §F「监听鉴权」 |
| 29 | `ldst mcp tools list` | 枚举 MCP 工具与 schema | `--output` | 契约测试用 |
| 30 | `ldst config validate` | 校验配置与 JSON Schema | 可选 `--file` | 配置错误 3 |
| 31 | `ldst config migrate` | 旧版配置与数据目录迁移 | `--dry-run` | 旧文件保留只读；迁移前自动备份，已是最新则退出 0（幂等） |
| 32 | `ldst schedule list` | 查看定时任务表 | — | 表格 |
| 33 | `ldst schedule run` | 运行一轮启用的定时任务 | `--once` | 任务结果 |
| 34 | `ldst state reset --store <name>` | 重置指定前端持久化分片 | `--store` | 需二次确认 |
| 35 | `ldst doctor` | 环境体检：依赖、端口、缓存目录、数据源 | — | 对齐 `th-cn doctor` 习惯 |
| +1 | `ldst config seed` | 从随包模板补齐缺失配置与 schema（DEC-071） | — | 已存在不覆盖；幂等，输出 `seeded`/`skipped`；不计入上面 31/33/35/32 基线 |

> 表内编号 1–35 为**行号**；去重后为 **33 个叶子路径**（`sync` 的三种形态合并为一条、`validate` 的两级子命令各算一个叶子）。P0 基线口径的 **31 条命令**见第 0 节；`scripts/check_commands.py` 实测覆盖 **32 条可执行 argv**（长驻服务 `run` / `mcp stdio` / `mcp http` 不入矩阵）。四套数字的对应关系见文首「命令计数口径」。
> §D′ 的行 36（`ldst research`）是 R1-1 基线之外的增量，§E″ 的行 37–41（R7 增量 5 条）是 R7 基线之外的增量，§D‴ 的行 42–43（R5 增量 2 条）是 R5 基线之外的增量，§A′ 的行 44（`ldst weekly`）是 R11 基线之外的增量，**都不计入上面的 31/33/35/32**；算上它们则是 44 行 / 42 个叶子 / 41 条可执行 argv。

## E″. 候选池与我的持仓（R7 增量，5）

> 口径来源：`docs/specs/SPEC-CANDIDATE-POOL.md`（打分算式与手算对照）、
> `docs/specs/SPEC-MY-POSITION.md`（字段、本地计算与隐私边界）。
> 这 5 条**不属于** 31 条 P0 基线，测试矩阵按「R7 增量」单独登记。

| # | 命令 | 作用 | 关键参数 | 输出 / 退出码 |
|---|---|---|---|---|
| 37 | `ldst pool` | 当日候选池打分：强度 / 换手 / 地位 / 资金 / 题材五个分项 + 加权总分 | `--date`（缺省今天）、`--limit 30`、`--output` | `LadderEnvelope`（`topic=candidate_pool`），`payload` 平铺打分结果；**只读本机缓存**，涨停池为空 / 无可解析候选时 stdout 仍给完整结果且 `available=false` → **6**；`--limit <= 0` 或日期非法 → 2 |
| 38 | `ldst position add` | 记一条持仓（成本价、数量、建仓日、行业标注、备注） | `--code`、`--cost`、`--qty`、`--date`、`--industry`、`--note` | **仅写本机 `data/journal.sqlite`**；重复 code 提示改用 `update` → 2 |
| 39 | `ldst position list` | 持仓 + 本地计算：浮动盈亏、**已实现盈亏（移动加权，离线复算流水）**、集中度（top1/top3/HHI）、行业分布 | `--code`、`--date`（行情日期）、`--export <path>`、`--output` | 仅本地，载荷带 `local_only=true` 与 `price_lookup` 溯源；现价取不到 → 相应字段 `null` + stderr 说明（仍 0）；流水为空 → `realized.total_pnl=null`（**不给 0**，DEC-038）；**不加 `--export` 不产生任何文件**；导出目标不可写 → 3 |
| 40 | `ldst position update` | 改一条持仓（只改显式传入的字段） | `--code` + `--cost/--qty/--date/--industry/--note` | 一个字段都没给 → 2；code 不在库里 → **6** |
| 41 | `ldst position remove` | 删一条持仓（只删本机记录，不产生文件） | `--code` | code 不在库里 → **6** |

`position` 与 `journal` 同为**本地命令，可打印，不经 MCP**：持仓数据不进 `LadderEnvelope`、
不进 MCP 工具清单、不进 AI 上下文（`docs/MCP_TOOLS.md` §2）。候选池与持仓**互不引用**——
持仓不参与打分，隐私数据不可能经打分结果外溢（护栏见 `tests/integration/test_position_privacy.py`）。

`position list` 的**已实现盈亏**（DEC-038）读的是 `journal` 那份成交流水，口径是**移动加权平均成本**
（券商对账单口径），与 `journal stats` 的 FIFO **不承诺同数**：全部清仓的票逐分不差，
未平仓的票差额 = 两种方法的剩余成本基础之差。它只读、不写回 `positions`，
`--export` 之外不产生任何文件。对账：

```bash
ldst journal stats --from <日> --to <日> --output json   # FIFO realized_pnl
ldst position list --output json                         # realized.total_pnl（移动加权）
```

## D‴. 产业研究（R5 增量，2）

> 口径来源：`docs/specs/SPEC-INDUSTRY-METRICS.md`（五维公式、数据源、缺失策略、手算复核）、
> 生态边界、退出码与分类不混算口径。这 2 条**不属于** 31 条 P0 基线，
> 测试矩阵按「R5 增量」登记（`tests/fixtures/command_matrix.py`）。

| # | 命令 | 作用 | 关键参数 | 输出 / 退出码 |
|---|---|---|---|---|
| 42 | `ldst industry list` | 申万一级行业清单 + 每个行业的缓存可用性（`available` / `constituent_count` / 五维 `dimensions_available` / `data_source_status` / `reason`），并带出窗口参数与公式 | `--output` | 平铺载荷（**不是**信封）；**恒 0**——清单是配置视图，本机一个字节都没同步也照常出，指标缺就逐行显式 `null`；`config/industry.yaml` 读不动 → **3** |
| 43 | `ldst industry show <行业>` | 单行业五维快照：景气 / 资金 / 估值 / 动量 / 拥挤度，每个字段带 R3 溯源 | `--output`；**没有 `--date`**（口径是「本机缓存里的最新一批」，见 SPEC §1.2） | `LadderEnvelope`（`domain=sentiment`、`topic=industry_snapshot`），载荷在 `payload`；五维任一字段算得出 → 0，全空 → **6**（stdout 仍是完整可解析信封）；行业不在清单 → **6** + `candidates`；配置错 → 3 |

- **只读本机缓存、零外呼**：六个输入数据集（`sw_industry_map` / `finance_indicators` /
  `valuation_history` / `fund_flow` / `industry_fund_flow` / `daily_k`）全部经
  `data/archive.py` 回放，取数前先同步（`ldst sync --dataset <数据集> --codes <代码>`）。
- **不估算**：算得出的给值，算不出的给 `null` + `reason`；缺上游工具的字段（行业北向、
  申万指数涨跌幅）是**结构性缺口**，永远显式 `null`，不降级成成分股聚合值。
- **两套分类不混算**：行业归属以申万一级为准；板块资金流的字段只在
  `config/industry.yaml` 配了同名 `board_name`、最新一日只有单一上游口径、
  且同名恰好命中 1 条时才给，并各自标 `caliber="board:<实际 _source>"`。
- **与 `ldst pool` 同一条信封装配路径**（`cli/review._metric_envelope`）：`data_as_of`
  取 `sw_industry_map` 的最新日期，归属数据一条都没有时如实给 `null`。
- **幂等**：同一份缓存 + 同一份配置重跑，除信封的取值时刻 `fetched_at` 之外逐字段一致
  （溯源时间来自缓存的入库时间 `_ingested_at`，公式里没有 `now()`）。
> §D′ 的行 36（`ldst research`）、§E″ 的行 37–41（R7 增量 5 条）、§D‴ 的行 42–43（R5 增量 2 条）、§D″ 的行 44–45（R6 增量 2 条）都在 31 条基线之外，**不计入上面的 31/33/35/32**；全部算上是 45 行 / **43 个叶子** / **42 条可执行 argv**（`scripts/check_commands.py` 现覆盖 42 条）。

## I. 我的自选清单（QUOT-380 增量，3）

> 口径来源：`docs/decisions/DEC-052-watchlist-read-endpoint.md`（自选读面，2026-11-03 拍板）、
> `docs/specs/SPEC-RADAR-PAGES.md` §9.2。这 3 条**不属于** 31 条 P0 基线，
> 测试矩阵按「QUOT-380 增量」单独登记（`tests/fixtures/command_matrix.py`：叶子路径 **45** 个）。
>
> ⚠️ **同一个 `watch` 组里有两类命令**：`start|status|stop` 是**盘中盯盘**（§C，离线单轮信号扫描），
> `add|rm|list` 是**我的自选**（本组，本机隐私库 `watchlist` 表）。组名共用是任务书指定的口径，
> 读写的东西完全不同——`ldst watch --help` 两行帮助里都写明了这一点。

| # | 命令 | 作用 | 关键参数 | 输出 / 退出码 |
|---|---|---|---|---|
| 46 | `ldst watch add` | 加一条自选 | `--code`（必填，6 位数字，可带 `sh`/`sz`/`bj` 前缀） | **仅写本机 `data/journal.sqlite` 的 `watchlist` 表**，不联网、不上传、不经 HTTP；重复 code 更新加入时间 → 0；代码非法 → 2 |
| 47 | `ldst watch rm` | 删一条自选 | `--code` | code 不在清单里 → **6**（「无结果」，不静默成功） |
| 48 | `ldst watch list` | 我的自选清单（按加入时间排序） | `--output` | 0 + 结构化载荷；**空清单也是 0**。**只出 `code` 与 `created_at`，不出 `note`**（DEC-052 §2.2 拍板：自由文本不进这个出口） |

**为什么写面只有 CLI、没有 HTTP**：`watchlist` 表此前只有 schema、没有任何生产者，
数据恒为空——只上 `GET /api/watchlist` 读面就是一张永久空态卡。本组三条就是那个生产者：
它写的是本机 SQLite，正是 DEC-052「自选的增删**不经 HTTP**」要求走的路，所以**不需要新决策**
（HTTP 侧仍然只有那一条只读路由；写面要放开是再一次独立决定）。

自选与 `journal` / `position` 同为**本地数据**：清单不进 `LadderEnvelope`、不进 MCP 工具清单、
不进 AI 上下文。`GET /api/watchlist` 的三条闸门（回环独占 + `Host` 白名单 + `no-store`）
由 `scripts/check_privacy.py` 第 3 组的第三条授权例外钉住，四项判据缺一不可，
反例覆盖「自选模块出现在 MCP / AI 出口」——**白名单不能变成后门**。

## J. 平台看门狗（QUOT-443 增量，1）

> 口径来源：`docs/decisions/DEC-063-platform-watchdog-and-health.md`。
> 这 1 条**不属于** 31 条 P0 基线；与 `run` 一样是长驻进程，**不进** argv 矩阵，
> 但叶子路径 `("watchdog",)` 必须登记在 `tests/fixtures/command_matrix.py`
> （当前 `LEAF_PATHS` 共 **58** 个、矩阵 **52** 条）。

| # | 命令 | 作用 | 关键参数 | 输出 / 退出码 |
|---|---|---|---|---|
| 49 | `ldst watchdog` | 独立看门狗：按 `--probe-interval` 探活 `--api` 的 `/api/health`，对外提供 `GET /platform/health`、`POST /platform/restart` 与一张离线壳页 `GET /` | `--host 127.0.0.1`、`--port 5952`、`--api http://127.0.0.1:5950`、`--restart-task "LadderStock API"`、`--restart-script <绝对 .ps1>`、`--probe-interval 2`、`--output` | 长驻进程；端口占用 / 非回环主机 / 重启脚本不是文件 → **3**（并提示 `--port`）。启动写 `state/watchdog.json` |

**契约**：

- `GET /platform/health` → `{status, uptime_sec, version, db_ok, checked_at, api, watchdog}`，
  `status` 三态 `ok|degraded|offline`，探不到主服务时 `api=null`；`Cache-Control: no-store`。
- `POST /platform/restart`：先杀**当前 5950 的监听者**（不是按 `ldst` 映像名批量杀，
  目标映像名以 `multica` 开头或等于自身 PID 时拒杀），再按
  `--restart-task`（`schtasks /run /tn`）→ `--restart-script` 顺序拉起；
  两级都未配置 → **409**，两级都失败 → **503**；30 秒内重复请求 → **429**；`GET` 无副作用。
- 只绑回环；写方法校验 `Origin` 白名单（5950 / 5952 / 5173 的 `127.0.0.1` 与 `localhost`），
  缺 `Origin` → **403**，不回 `*`；命令参数全部来自启动参数，**不从 HTTP 输入拼接**。
- 离线壳页内联 HTML/CSS/JS，不引用 CDN / 外部脚本；主服务恢复后自动 `location.replace` 回 5950。

**安装常驻**：管理员运行 `scripts/install-service.ps1` 注册两个 S4U 计划任务
（`LadderStock API` → `ldst run`，`LadderStock Watchdog` → `ldst watchdog --restart-task "LadderStock API"`），
并生成 `%LOCALAPPDATA%\LadderStock\restart-api.ps1` 作为脚本兜底。失败重启间隔为 `PT1M`
（任务计划程序的最小值是 1 分钟，`PT30S` 会被注册接口拒绝）。

## L. 单入口 supervisor（QUOT-523 增量，4）

> 口径来源：issue QUOT-523「单 supervisor 单入口接线生产」与 `docs/supervisor/single-entry.md`。
> 这 4 条**不属于** 31 条 P0 基线；叶子路径
> `("supervisor", "start" | "stop" | "restart" | "status")` 已登记
> `tests/fixtures/command_matrix.py`（当前 `LEAF_PATHS` 共 **58** 个）。
> 只动**账本能证明是本入口拉起**的进程，外部进程一律不碰（DEC-014）。

| # | 命令 | 作用 | 关键参数 | 输出 / 退出码 |
|---|---|---|---|---|
| 51 | `ldst supervisor start` | 按注册表顺序全起（单模块失败不挡后面的） | `--only <模块>`（可重复）、`--data-root`、`--output` | 全部成功 0；有模块失败 **8**（`EXIT_PARTIAL`）；不进 argv 矩阵（会真起进程，开环形态由 `tests/unit/cli/test_supervisor.py` 覆盖） |
| 52 | `ldst supervisor stop` | 反向全停；**不带 `--force` 只预演**（列 `would_stop` + `order`，不动进程） | `--force`（确认执行）、`--only`、`--data-root`、`--output` | 预演与成功 0；有模块失败 **8** |
| 53 | `ldst supervisor restart` | 单模块重启，其它模块 pid 不变 | `--only`（**必填**）、`--data-root`、`--output` | 无 `--only` → **3**；全部成功 0；有模块失败 **8**；不进 argv 矩阵（会真起进程） |
| 54 | `ldst supervisor status` | 每模块 state / 归属 / 监听 / 账本 PID | `--data-root`、`--output` | 只读视图，始终 0 + 结构化载荷 |

## K. 助手记忆（QUOT-487 增量，1）

> 口径来源：issue QUOT-487「助手通道接入 Mem0 记忆：召回/写入策略 + 注入边界」。
> 这 1 条**不属于** 31 条 P0 基线；叶子路径 `("ai", "memory-clear")` 已登记
> `tests/fixtures/command_matrix.py`（当前 `LEAF_PATHS` 共 **58** 个）。
> 读/写发生在 web 助手端点（`/api/research/assistant`、`/api/skills/assistant`），
> CLI 只提供清空入口——记忆的写入与召回策略见 `src/ladderstock/ai/memory.py` 模块 docstring。

| # | 命令 | 作用 | 关键参数 | 输出 / 退出码 |
|---|---|---|---|---|
| 50 | `ldst ai memory-clear` | 一键清空助手记忆：只删 `LDST_MEMORY_USER_ID` + `ladderstock-assistant` 名下（mem0 侧 `DELETE /memories?user_id&agent_id`，QUOT-486 实例实测路由）的全部对话记忆，清空动作写日志（`memory_cleared`） | `--yes`（跳过确认）、`--output` | 成功 0 + 结构化载荷 `{cleared, endpoint, user_id, agent_id, removed}`；通道未启用（`LDST_MEMORY_BASE_URL` 为空或被禁用）→ **3**（无事可做，不假装清了）；mem0 不可达 → **9**（未执行清空）。**绝不越命名空间**：不碰其他 user_id / agent_id 的记忆，也绝不连 9000 端口（Multica 的 mem0 后端）

## M. 回测（QUOT-500 增量，3）

> 口径来源：issue QUOT-500「回测 M1：A 股日频策略回测最小闭环」，规格先看
> `docs/specs/SPEC-BACKTEST.md`。这 3 条**不属于** 31 条 P0 基线；
> 叶子路径 `("backtest", "run"|"list"|"show")` 登记在
> `tests/fixtures/command_matrix.py`。只读本机历史库 `daily_k`，运行卡落
> `<data_dir>/backtests/<run_id>/<run_id>.{json,md,html}`。
> 默认成本 / 阈值写在 `config/backtest.yaml`（`config/schema/backtest.schema.json` 管形状），
> 代码里不散落默认值；CLI 旗标只做覆盖，运行卡记录「本次实际生效值」+ 每项来源（`yaml` / `cli` / `fallback`）。

| # | 命令 | 作用 | 关键参数 | 输出 / 退出码 |
|---|---|---|---|---|
| 51 | `ldst backtest run` | 跑一次回测并落盘运行卡（JSON / Markdown / HTML）：撮合信号 t 收盘 → t+1 开盘 ± 滑点成交、佣金双边且单笔取 max(额×率, 最低佣金)、过户费双边、印花税卖出单边、T+1 / 涨停买不进 / 跌停卖不出 / 停牌跳过 | `--strategy ma_cross\|momentum\|breakout`、`--symbol`、`--from` / `--to`、`--commission` / `--slippage` / `--stamp-duty`、`--min-trades`、`--in-sample-ratio`、`--cost-tiers`、`--initial-cash`、`--config`、策略参数 `--short-window` / `--long-window` / `--lookback` / `--threshold`、`--output` | 校验门通过 **0**；校验门未通过 **8**（运行卡与页面标红，仍写出产物）；参数 / 配置非法 **2**；本机无历史库或库不可用 **4** |
| 52 | `ldst backtest list` | 列出本机已落盘的运行卡摘要（`run_id` / 策略 / 区间 / 校验门结论，新的在前，只读目录不重算） | `--limit 20`、`--output` | 始终 **0**（空目录也出结构化空列表） |
| 53 | `ldst backtest show <run_id>` | 按 `run_id` 回读已落盘运行卡摘要（不重算指标） | `run_id`（`bt_[0-9a-f]{8}`）、`--output` | 存在 **0**；`run_id` 不合法 **2**；运行卡不存在 **6** |

**契约**：

- `run_id = "bt_" + sha256(canonical_params(参数) + "\x1f" + 数据指纹)[:8]`；同参数 + 同数据指纹 → 同 `run_id`、同运行卡（仅 `generated_at` 不同），重跑覆盖同一组产物。
- 每项指标调用 calc 层算，`metrics_calc_ids` 逐指标记 `calc_id`（11 项），另有 `calc_ids.run_card / data / gate / benchmark`。
- 校验门三项：最小完整交易次数、样本外收益 > 0（按时间顺序切分）、三档成本敏感性（只替换佣金率重跑）。任一项失败 → `gate.passed=false`，Markdown / HTML 置顶中文红标。
- HTTP 端点（`ldst run` 托管，前缀 `/api/backtest`，QUOT-535）：只读 `GET /runs?limit=`、`GET /latest`、`GET /runs/{run_id}`、`GET /runs/{run_id}/export.{json,md,html}`；触发面 `GET /strategies`（策略清单 + 成本默认值）、`POST /run`（异步，202 + job_id）、`GET /jobs/{job_id}`（`status/run_id/gate_passed/exit_code`）。写面走与本机 CLI 相同的 `run_backtest`，不另写撮合逻辑；写请求需回环 + 合法 `Host` + 来源证据（同源 `Origin` 或 `X-LDST-Client: 1`），否则 **403**；参数非法 **400** + 结构化错误，请求体形状错 **422**，`run_id` 非法 **400**，不存在 **404**，产物损坏 / 配置不可读 **500**。前端只读产物，不重算指标（DEC-074）。

## F. 全局约定

- **stdout / stderr 分离**：结构化结果写 stdout，日志写 stderr；`ldst mcp stdio` 的 stdout 只输出 MCP 协议报文。
- **输出格式**：全局 `--output json|table|csv`；默认 table（交互）/ json（管道）。
- **退出码**：`0` 成功、`2` 参数错误、`3` 配置错误、`4` 数据源不可用、`6` 对象不存在 / 无结果（含 `sentiment show` / `ladder show` 的 `available=false`；含 `research --check` 的产物缺失；含 `weekly` 的整周没有可用单日指标——**6 优先于 9**，此时产物照写）、`8` 部分数据源失败（含 `research --check` 查出引用不合格的数字）、`9` AI 降级完成、`10` **本机历史库被别的进程占着**（`sync` 拿锁超时，或 `duckdb.connect()` 退避重试耗尽；QUOT-234）。
  `10` 不属于对外契约的那 6 个码，也**不复用 4/8**——那两个码的主语是「生态 peer 数据源」，报它们会把排查方向引到换源、重试取数上；库锁争用是本机串行问题，判据见 `docs/decisions/DEC-036-duckdb-cross-process-writes.md`。
- **端口**：看板 `5950`、MCP http `5951`、看门狗 `5952`，默认仅回环监听。**端口被占用直接报错（退出码 3）并提示 `--port`，绝不静默换端口**——IndexedDB 按 origin 隔离，端口漂移会让 M1 状态恢复失效。旧版端口 `5940` / `8770` 仅打印迁移提示。`5952` 只在 `ldst watchdog` 运行时占用，不托管 SPA、不迁前端 origin（QUOT-443 / DEC-063）。
- **监听鉴权（R-1，判据唯一份：`core/listener.py`）**：回环（`127.0.0.1` / `localhost` / `::1`）无需配置；非回环**必须同时**设置 `<前缀>_ALLOW_REMOTE=1` 与 `<前缀>_TOKEN`（≥16 字符），否则拒绝启动、退出码 3，绝不静默对外暴露。前缀：web `LDST_WEB_*`，MCP `LDST_MCP_*`。开启后 web 校验 `/api/*`、MCP 校验 `/mcp`，均要求 `Authorization: Bearer <token>`（web 兼容 `X-Web-Token`），`hmac.compare_digest` 常量时间比较，失败 401 且不回显服务端细节。
- **不撞端口**：Council WebUI `8786` / MCP `8765`、TickHub Admin `8787` / MCP `8000`、TickHub-US MCP `8002`（本机构建无管理端）、Wire `8790` / `8791`。`8788` / `8789` 归 ProxyFleet（REST/管理台、本地转发网关），见 `docs/decisions/DEC-047-peer-port-ownership.md`。
- **控制面**：`ldst run` 启动后看板页可在 `/api/control/*` 查看、安装、启停、配置四个 peer，实现落点 `src/ladderstock/web/control.py`。该族接口**只接受回环来源**（非回环 403），`LDST_CONTROL_ENABLED=0` 时整族返回 404 且数据面 7 个只读接口零影响；**写方法（`POST`/`PUT`/`PATCH`/`DELETE`）必须带正向来源证据**——浏览器同源页面由 `Origin` 自动满足，`curl` / CLI / 脚本须显式加 `X-LDST-Client: 1`（否则 403）；安装/启停一律 `subprocess.run([...], shell=False)` 走白名单命令，端口被占用直接报错、**绝不换端口也不杀占用者**；密钥只写目标 peer **真正加载的那个 dotenv**（CN/US/Senate 为 `.env`、Wire 为 `.env.local`，0600），GET 只回 `{"set": true, "preview": "sk-****abcd"}`，日志同样脱敏。**`GET /api/control/validation/conditions` 是写通道探针**（QUOT-287 / DEC-032）：控制面开启时回 `405` + `Allow: PUT`，不返回数据、不落盘；看板靠它判断 `/validate` 的「后端」模式是否就绪，读条件仍走数据面 `GET /api/validation?date=` 的 `payload.conditions`。

  ```bash
  # 写接口示例：从命令行触发安装（Origin 缺席时必须带 X-LDST-Client）
  curl -X POST http://127.0.0.1:5950/api/control/peers/wire/install \
    -H "X-LDST-Client: 1" -H "Content-Type: application/json" -d '{"dev": false}'
  ```
- **隐私边界**：`journal *` 与 `position *` 的数据只写本机 SQLite（`data/journal.sqlite`，表 `trades` / `positions` / `watchlist`），不进 `LadderEnvelope`、不被 MCP 读取、不注入 AI 上下文。**持仓默认不落文件**：只有 `ldst position list --export <path>` 显式指定才写出，并在 stderr 提示「隐私数据，请自行保管，勿上传」；`ldst pool` 不读隐私库（持仓不参与打分）。
- **兼容别名**：`ladderstock` 为 `ldst` 长别名；旧启动脚本（`scripts/start`、`scripts\start.cmd`）委托 `ldst run` 并打印迁移提示。
- **配置迁移**：`config migrate` 旧文件只读、迁移前自动备份（`data/backups/<时间戳>/`、`journal.sqlite.bak-v<ver>-<时间戳>`），失败按备份回滚；`schema_version` 已最新时 `status=up_to_date` 且不写任何文件。完整契约见 `docs/CONFIG.md` §4。

## G. 验收对照

| 文档验收项 | 对应命令 |
|---|---|
| 刷新后复盘草稿与验证条件可恢复 | 看板 + `state inspect` / `state reset` |
| PDF 中文正常渲染，失败降级 Markdown | `export` |
| AI 失败分类提示、成功段落保留 | `review`、`ai retry`、`ai status` |
| 增量同步仅拉取水位后数据 | `sync`、`sync status` |
| 单源故障自动切备源、UI 标注 | `sources health` + 看板降级标识 |
| MCP 新增工具可调用、带溯源 | `mcp stdio` / `mcp http` / `mcp tools list` |
| 盯盘 / 盘前提醒 / 收盘对账实机验证 | `watch start`、`notify test`、`validate reconcile` |
| 交易日志与持仓不出本机 | `journal *`、`position *`（仅本地 SQLite；持仓只有 `--export` 才落文件） |
| 候选池打分可解释、可复算 | `pool`（分项 + `raw` + `formula` + `weights`，手算对照 `SPEC-CANDIDATE-POOL.md` §5） |
| 行业五维字段要么有值要么显式 `null`、可复算 | `industry list` / `industry show`（每字段带 R3 溯源，手算对照 `SPEC-INDUSTRY-METRICS.md` §7） |
| 周报事实包稳定可复算、AI 失败仍出事实包 + 模板 | `weekly`（`SPEC-WEEKLY.md` §4 / §7 / §8；`--week` 非法 2、整周无读数 6、段落降级 9） |
| 旧启动方式与旧数据目录过渡可用 | `config migrate` + 兼容脚本 |
