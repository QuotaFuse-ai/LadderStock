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
| 39 | `ldst position list` | 持仓 + 本地计算：浮动盈亏、集中度（top1/top3/HHI）、行业分布 | `--code`、`--date`（行情日期）、`--export <path>`、`--output` | 仅本地，载荷带 `local_only=true` 与 `price_lookup` 溯源；现价取不到 → 相应字段 `null` + stderr 说明（仍 0）；**不加 `--export` 不产生任何文件**；导出目标不可写 → 3 |
| 40 | `ldst position update` | 改一条持仓（只改显式传入的字段） | `--code` + `--cost/--qty/--date/--industry/--note` | 一个字段都没给 → 2；code 不在库里 → **6** |
| 41 | `ldst position remove` | 删一条持仓（只删本机记录，不产生文件） | `--code` | code 不在库里 → **6** |

`position` 与 `journal` 同为**本地命令，可打印，不经 MCP**：持仓数据不进 `LadderEnvelope`、
不进 MCP 工具清单、不进 AI 上下文（`docs/MCP_TOOLS.md` §2）。候选池与持仓**互不引用**——
持仓不参与打分，隐私数据不可能经打分结果外溢（护栏见 `tests/integration/test_position_privacy.py`）。

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

## F. 全局约定

- **stdout / stderr 分离**：结构化结果写 stdout，日志写 stderr；`ldst mcp stdio` 的 stdout 只输出 MCP 协议报文。
- **输出格式**：全局 `--output json|table|csv`；默认 table（交互）/ json（管道）。
- **退出码**：`0` 成功、`2` 参数错误、`3` 配置错误、`4` 数据源不可用、`6` 对象不存在 / 无结果（含 `sentiment show` / `ladder show` 的 `available=false`；含 `research --check` 的产物缺失；含 `weekly` 的整周没有可用单日指标——**6 优先于 9**，此时产物照写）、`8` 部分数据源失败（含 `research --check` 查出引用不合格的数字）、`9` AI 降级完成、`10` **本机历史库被别的进程占着**（`sync` 拿锁超时，或 `duckdb.connect()` 退避重试耗尽；QUOT-234）。
  `10` 不属于对外契约的那 6 个码，也**不复用 4/8**——那两个码的主语是「生态 peer 数据源」，报它们会把排查方向引到换源、重试取数上；库锁争用是本机串行问题，判据见 `docs/decisions/DEC-036-duckdb-cross-process-writes.md`。
- **端口**：看板 `5950`、MCP http `5951`，默认仅回环监听。**端口被占用直接报错（退出码 3）并提示 `--port`，绝不静默换端口**——IndexedDB 按 origin 隔离，端口漂移会让 M1 状态恢复失效。旧版端口 `5940` / `8770` 仅打印迁移提示。
- **监听鉴权（R-1，判据唯一份：`core/listener.py`）**：回环（`127.0.0.1` / `localhost` / `::1`）无需配置；非回环**必须同时**设置 `<前缀>_ALLOW_REMOTE=1` 与 `<前缀>_TOKEN`（≥16 字符），否则拒绝启动、退出码 3，绝不静默对外暴露。前缀：web `LDST_WEB_*`，MCP `LDST_MCP_*`。开启后 web 校验 `/api/*`、MCP 校验 `/mcp`，均要求 `Authorization: Bearer <token>`（web 兼容 `X-Web-Token`），`hmac.compare_digest` 常量时间比较，失败 401 且不回显服务端细节。
- **不撞端口**：Council WebUI `8788` / MCP `8765`、TickHub Admin `8787` / MCP `8000`、TickHub-US MCP `8002` / Admin `8789`、Wire `8790` / `8791`。
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
