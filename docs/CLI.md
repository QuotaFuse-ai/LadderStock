# LadderStock CLI 命令基线（ldst）

> 依据《LadderStock 升级文档 v1.0》第五、七、十节整理。
> **基线状态：P0 已定稿，共 31 条命令**（2026-10-28 决策，见 `docs/decisions/DEC-003-cli-baseline.md`）。
> 19 条来自文档明文，12 条为文档已定义能力但未给命令名的缺口，按建议命名补齐并定为 P0。
> 测试工程师按本表逐条对比，实现方不得改名；确需改名需另开决策记录。
>
> **命令计数口径（四套数字，各有其用，勿混用）**：
> - **31 条命令** —— P0 定稿的**命令**数（DEC-003 决策口径，测试逐条对比以此为准）；
> - **35 行** —— 下方 §A–E 表格行号（`sync` 三种形态、`validate` 两级子命令各占一行）；
> - **33 个叶子路径** —— 表格去重后的命令叶子（`sync` 三种形态合并为一条）；
> - **32 条可执行 argv** —— `scripts/check_commands.py` 实测矩阵（33 个叶子减去长驻服务 `run` / `mcp stdio` / `mcp http`，另加 `sync --dataset` / `sync --full` 两种形态各跑一次）。
>
> **R1-1 增量（QUOT-190，2026-10-30）**：基线 31 条口径**不变**，在其之上新增 1 条命令
> `ldst research <code>`（个股研究六段管线）。加它之后：叶子 **34** 个、可执行 argv **33** 条。
> 见 §0「研究管线」行与 §D′。
>
> **前置约束（DEC-006）**：所有取数命令只向生态 peer 取数——TickHub-CN（只消费）、QuotaFuse Wire（可选消费）、StockSenate（只读消费）。`sources` / `sync` 里的"源"指 **peer 的工具**，不是行情供应商；主备必须同 peer，peer 整体不可用时回放本地缓存，绝不启用自建采集。

## 0. 总览（31 条）

> 口径说明：本节按 **P0 基线 31 条命令**统计（DEC-003）；下列分组明细展开后为 **35 行**表格、去重 **33 个叶子路径**，`check_commands.py` 实测 **32 条可执行 argv**（长驻服务 `run` / `mcp stdio` / `mcp http` 不入矩阵）。四套数字的关系见文首「命令计数口径」。

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
| MCP（3） | `mcp stdio`、`mcp http`、`mcp tools list` |
| 配置与调度（4） | `config validate`、`config migrate`、`schedule list`、`schedule run` |
| 体检（1） | `doctor` |

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

> **31 条 P0 基线之外的新增命令**（QUOT-139 / QUOT-190），行号从 36 起接排，不动上面的编号。

| # | 命令 | 作用 | 关键参数 | 输出 / 退出码 |
|---|---|---|---|---|
| 36 | `ldst research <code>` | 个股六段研究管线：`company → financial → forecast → valuation → risk → report` 顺序执行，每段落 `data/research/<code>/<date>/<stage>.{json,md}` + 一份 checkpoint（`data/state/research_checkpoint/<code>/<date>.json`，原子写），中断重跑只补缺失段 | `--date`（缺省取今日）、`--only <stage>`、`--retry-failed`、`--force`、`--output` | `--output json` 载荷见 `ResearchRunResult.as_dict()`（键集合是 R1-2/R1-3/R1-8 的消费契约）；`code` 非法或 `--only` 未知 2；缺 `config/ai.yaml` 3；任一段失败或降级 9 |

- `--only` / `--retry-failed` / `--force` 的**选择语义与五段复盘编排（`ai retry`）逐字一致**：
  共用 `ai/checkpoint.select_targets()`，已完成段默认跳过，`--only <stage>` 命中已完成段时是
  no-op（要重跑单段就配 `--force`），`--retry-failed` 只重跑 `failed` 段。
- **本段不联网取数**（联网检索属 R4，需人类批准）：`facts` 由调用方注入，默认空；
  未配置模型凭据时六段按占位叙述落盘并如实标 `degraded`（退出 9），用于验证顺序执行与断点续跑。
- MCP 侧只读工具 `research_status`（`code` 必填、`date` 可选，缺省取该代码最近一次 checkpoint）
  读同一份进度，输出走统一信封；**交易日志与持仓不出现在任何研究产物里**。

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
> §D′ 的行 36（`ldst research`）是 R1-1 基线之外的增量，**不计入上面的 31/33/35/32**；算上它则是 36 行 / 34 个叶子 / 33 条可执行 argv。

## F. 全局约定

- **stdout / stderr 分离**：结构化结果写 stdout，日志写 stderr；`ldst mcp stdio` 的 stdout 只输出 MCP 协议报文。
- **输出格式**：全局 `--output json|table|csv`；默认 table（交互）/ json（管道）。
- **退出码**：`0` 成功、`2` 参数错误、`3` 配置错误、`4` 数据源不可用、`6` 对象不存在 / 无结果（含 `sentiment show` / `ladder show` 的 `available=false`，见 DEC-012）、`8` 部分数据源失败、`9` AI 降级完成。
- **端口（DEC-004）**：看板 `5950`、MCP http `5951`，默认仅回环监听。**端口被占用直接报错（退出码 3）并提示 `--port`，绝不静默换端口**——IndexedDB 按 origin 隔离，端口漂移会让 M1 状态恢复失效。旧版端口 `5940` / `8770` 仅打印迁移提示。
- **监听鉴权（R-1，判据唯一份：`core/listener.py`）**：回环（`127.0.0.1` / `localhost` / `::1`）无需配置；非回环**必须同时**设置 `<前缀>_ALLOW_REMOTE=1` 与 `<前缀>_TOKEN`（≥16 字符），否则拒绝启动、退出码 3，绝不静默对外暴露。前缀：web `LDST_WEB_*`，MCP `LDST_MCP_*`。开启后 web 校验 `/api/*`、MCP 校验 `/mcp`，均要求 `Authorization: Bearer <token>`（web 兼容 `X-Web-Token`），`hmac.compare_digest` 常量时间比较，失败 401 且不回显服务端细节。
- **不撞端口**：Council WebUI `8788` / MCP `8765`、TickHub Admin `8787` / MCP `8000`、TickHub-US MCP `8002` / Admin `8789`、Wire `8790` / `8791`。
- **控制面（DEC-014）**：`ldst run` 启动后看板页可在 `/api/control/*` 查看、安装、启停、配置四个 peer，实现落点 `src/ladderstock/web/control.py`。该族接口**只接受回环来源**（非回环 403），`LDST_CONTROL_ENABLED=0` 时整族返回 404 且数据面 7 个只读接口零影响；**写方法（`POST`/`PUT`/`PATCH`/`DELETE`）必须带正向来源证据**——浏览器同源页面由 `Origin` 自动满足，`curl` / CLI / 脚本须显式加 `X-LDST-Client: 1`（否则 403，QUOT-178）；安装/启停一律 `subprocess.run([...], shell=False)` 走白名单命令，端口被占用直接报错、**绝不换端口也不杀占用者**（DEC-004）；密钥只写目标 peer **真正加载的那个 dotenv**（CN/US/Senate 为 `.env`、Wire 为 `.env.local`，0600），GET 只回 `{"set": true, "preview": "sk-****abcd"}`，日志同样脱敏。

  ```bash
  # 写接口示例：从命令行触发安装（Origin 缺席时必须带 X-LDST-Client）
  curl -X POST http://127.0.0.1:5950/api/control/peers/wire/install \
    -H "X-LDST-Client: 1" -H "Content-Type: application/json" -d '{"dev": false}'
  ```
- **隐私边界**：`journal *` 与持仓数据只写本机 SQLite（`data/journal.sqlite`），不进 `LadderEnvelope`、不被 MCP 读取、不注入 AI 上下文。
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
| 交易日志与持仓不出本机 | `journal *`（仅本地 SQLite） |
| 旧启动方式与旧数据目录过渡可用 | `config migrate` + 兼容脚本 |
