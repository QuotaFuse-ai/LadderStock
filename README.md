<h1 align="center">LadderStock · 天梯股复盘台</h1>

<p align="center">
  <b>面向 A 股短线交易者的本地化复盘与跟踪工作台</b><br>
  盯盘 → 复盘 → 明日验证 → 交易日志，闭环跑在本机
</p>

<p align="center">
  程序算数 · AI 解读 · 状态可恢复 · 数据可追溯 · 多源可降级
</p>

<p align="center">
  复盘辅助与交易日志，不是投资建议 · 不接实盘 · 不托管账户
</p>

---

LadderStock 是 QuotaFuse 产品线里的**短线复盘工作台**：情绪温度、晋级率、连板天梯、梯队断层这些指标由确定性代码直出，AI 只负责把数字串成叙事；交易日志、持仓、自选只存本机 SQLite，不进 MCP、不进 AI 上下文。

它不做行情采集，也不做多角色投研。行情与资金由 `TickHub-CN` 提供，资讯由 `QuotaFuse Wire` 提供，多角色分析与辩论交给 `StockSenate`；LadderStock 只负责短线交易者的日常复盘与纪律执行，并用统一 `LadderEnvelope` 与生态对齐。

- **程序算数、AI 解读**：指标全部确定性计算，AI 失败只降级叙事，不污染数字。
- **状态可恢复**：Zustand persist → IndexedDB，刷新不丢复盘草稿与未提交验证条件。
- **数据可增量**：DuckDB + Parquet 按日期分区，水位线增量同步，盘中快照与历史复盘冷热分离。
- **数据源可兜底**：Provider Registry 主源 → 备源 → 降级，单源故障不中断复盘，UI 标注当前数据源。
- **AI 可降级**：五段式复盘状态机，逐段持久化；结构化输出 → fallback 链 → 纯文本降级，错误四分类可行动。
- **本地优先**：默认只监听回环地址，零强制云端账户，隐私数据不出本机。

> **边界**：不输出交易信号或买卖建议；不接实盘自动交易；不重复实现行情与资讯采集；不把程序计算交给 AI。

---

## 与 QuotaFuse 生态的分工

| 能力 | LadderStock | TickHub-CN（StockData） | QuotaFuse Wire | StockSenate（Council） |
|---|---|---|---|---|
| 盘中盯盘、复盘、明日验证对账 | ✅ | ❌ | ❌ | ❌ |
| 交易日志与纪律复查 | ✅ | ❌ | ❌ | ❌ |
| 行情、财报、公告、资金 | 消费（可本地兜底） | ✅ | ❌ | 消费 |
| 新闻、政策、产业链资讯 | 消费（可选） | ❌ | ✅ | 消费 |
| 多角色分析、辩论、回测 | ❌ | ❌ | ❌ | ✅ |
| MCP 工具 | 复盘 / 情绪 / 验证类 | 数据类 | 资讯类 | 分析类 |
| 隐私数据本地存储 | ✅ 仅本机 | ❌ | ❌ | ❌ |

数据、资讯、决策、复盘四层通过统一 envelope 与 MCP 工具解耦，互不重复建设。

### 边界是可执行的，不只是写在文档里

LadderStock 是**第四层：复盘与纪律执行层**，只消费前三层。这条边界由代码和 CI 强制，不是口头约定：

| 闸门 | 实现 | 拦什么 |
|---|---|---|
| 归属目录 | `core/boundary.py: DATASET_CATALOG` | 未登记 peer+tool 的数据集不许同步（= 不许自建采集） |
| 唯一入口 | `data/adapters/{tickhub,wire,senate}.py` | 三种信封只能经对应 adapter 进入，且校验数据集归属 |
| 配置契约 | `config/sources.yaml` / `sync.yaml` + `audit_sources()` | 只写 peer+tool，不写供应商；备源跨 peer 直接违规 |
| 静态禁令 | `scan_no_collection()` | 源码出现 `akshare`/`tushare` 等采集库或 `push2.eastmoney.com` 等行情站点即失败 |
| AI 只写叙述 | `assert_narrative_only()` / `merge_narrative()` | AI 输出与指标键重名、或出现白名单外的键即报错，数字不可能被覆盖 |

降级规则只有一条：**peer 不可用 → 同 peer 备工具 → 本地缓存回放**（`freshness=cached`、`source_status=degraded`），**永远没有"我们自己抓"这个分支**。

```bash
python -X utf8 -m pytest tests/contract/test_ecosystem_boundary.py -q   # 29 条边界契约
ldst doctor --output json     # boundary:no_collection / boundary:datasets
```

## 架构

```text
┌─────────────────────────────────────────────────────┐
│ 调用层：CLI(ldst) │ 本地 Web 看板 │ MCP Server │ Council │
└─────────────────────────────────────────────────────┘
                          │
┌─────────────────────────────────────────────────────┐
│ LadderStock 核心                                     │
│ 盘中盯盘 │ 收盘复盘 │ 情绪计算 │ 明日验证对账 │ 交易日志 │
│ AI 编排状态机 │ 错误分类反馈 │ 降级提示 │ 断点恢复      │
└─────────────────────────────────────────────────────┘
                          │
┌─────────────────────────────────────────────────────┐
│ 数据层                                               │
│ Provider Registry（主源 → 备源 → 降级）               │
│ DuckDB + Parquet（日线 / 指标 / 事件，水位线增量）    │
│ SQLite（交易日志 / 持仓 / 配置，本地隐私）            │
│ 可选：TickHub-CN / Wire / 本地缓存                    │
└─────────────────────────────────────────────────────┘
                          │
┌─────────────────────────────────────────────────────┐
│ 前端状态层                                           │
│ Zustand persist → IndexedDB（分片、版本迁移、恢复语义）│
└─────────────────────────────────────────────────────┘
```

前端形态与生态内看板保持一致：**React + Vite 本地浏览器 UI**（由 `ldst run` 在本机托管），不引入 Electron；Tauri 2.0 仅作为 M4 的可选打包壳。

## 安装

需要 **Python ≥ 3.11**（与 TickHub-CN / StockSenate 保持一致），Node ≥ 22 仅用于前端构建。

```bash
pip install ladderstock           # 核心
pip install "ladderstock[all]"    # 全功能（mcp / web / pdf）
pip install -e ".[dev]"           # 开发
```

可选 extras：`mcp`（MCP Server）、`web`（本地看板）、`pdf`（PDF 导出与中文字体）、`all`。

开发环境建议使用 Python 3.12 虚拟环境；CI 以 Python 3.11 为基线，两者都受支持。

## 快速开始

```bash
ldst config migrate        # 首次启动：只读迁移旧配置与旧数据目录
ldst sync                  # 增量同步（水位线之后的数据）
ldst review --date 2026-10-22
ldst validate --date 2026-10-22
ldst run                   # 打开本地工作台 http://127.0.0.1:5950
```

结构化结果走 stdout，日志走 stderr；`--output json|table|csv` 三档可选。

端口固定 `5950`（看板）/ `5951`（MCP http），默认只监听 `127.0.0.1`；**端口被占用时直接报错并提示 `--port`，不静默换端口**——IndexedDB 按 origin 隔离，端口漂移会丢前端状态。

## 命令行

命令基线 **31 条，已全部定为 P0**（19 条文档明文 + 12 条补齐缺口），完整参数与退出码见 [docs/CLI.md](docs/CLI.md)，包名与模块映射见 [docs/PACKAGES.md](docs/PACKAGES.md)，分层与数据流见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)，配置与旧版迁移见 [docs/CONFIG.md](docs/CONFIG.md)。

```bash
ldst run [--port 5950]            # 启动本地工作台
ldst review --date 2026-10-22     # 重新生成指定日期复盘
ldst review show --date 2026-10-22
ldst validate --date 2026-10-22   # 明日验证条件与对账结果
ldst validate reconcile --date 2026-10-22
ldst sentiment show --date 2026-10-22
ldst ladder show --date 2026-10-22
ldst sync [--dataset daily_k] [--full]
ldst sync status                  # 水位线与最近同步状态
ldst sources list|validate|health
ldst watch start|status|stop      # 盘中盯盘
ldst notify test                  # 盘前提醒链路冒烟
ldst journal add|list|stats       # 交易日志（仅本地 SQLite）
ldst export --format pdf|md       # PDF 失败自动降级 Markdown
ldst state inspect|reset
ldst ai status|retry|chain test
ldst mcp stdio | ldst mcp http --port 5951 | ldst mcp tools list
ldst config validate | ldst config migrate
ldst schedule list|run
ldst doctor                       # 依赖、端口、缓存目录、数据源体检
```

## MCP 工具

沿用 7 个只读工具 + 1 个发起研究工具，并新增：

| 工具 | 说明 |
|---|---|
| `ladder_get_review(date)` | 某日复盘结构化结果 |
| `ladder_get_validation(date)` | 明日验证条件与对账结果 |
| `ladder_get_sentiment(date)` | 情绪温度、晋级率、梯队断层 |
| `industry_snapshot(industry)` | 申万一级行业五维画像：景气 / 资金 / 估值 / 动量 / 拥挤度（R5） |

输出统一 `LadderEnvelope`；隐私数据（交易日志、持仓、自选）永不出现在 MCP 输出与 AI 上下文中。

## 配置

| 文件 | 作用 |
|---|---|
| `config/sources.yaml` | 数据源与主备顺序、超时、重试、冷却、权重 |
| `config/sync.yaml` | 数据集与增量模式（append / replace）、冷热分离 |
| `config/ai.yaml` | 默认模型、fallback 链、分段 prompt 与输出 schema |
| `config/industry.yaml` | 申万一级行业清单与窗口参数（R5，口径见 `docs/specs/SPEC-INDUSTRY-METRICS.md`） |
| `config/state.yaml` | 前端持久化分片、TTL、schema 版本 |

校验：`ldst config validate`；JSON Schema 位于 `config/schema/`。

## 里程碑

| 里程碑 | 内容 | 验收 |
|---|---|---|
| M1 状态连续性与体验修复 | IndexedDB 持久化；PDF 中文字体；AI 错误分类 | 刷新可恢复；PDF 中文正常；错误可行动 |
| M2 增量数据层与源韧性 | DuckDB / Parquet 增量；Provider Registry；冷热分离 | 同步提速；单源故障不中断；历史可查 |
| M3 AI 编排与生态集成 | 状态机；fallback 链；MCP 新工具；Envelope 对齐 | AI 可降级可恢复；MCP 可调用；字段对齐 |
| M4 验证门禁与产品化 | 时间敏感实机清单；跨平台验收；Docker / 只读 API | 关键场景实机通过；一键部署；本机监听 |

## 目录结构

```text
ladderstock/
├── config/            # sources / sync / ai / state + schema
├── prompts/           # 五段式复盘 prompt
├── docs/              # 架构、配置、CLI、包名、MCP、迁移、发布清单
├── src/ladderstock/   # core / data / ai / watch / review / journal / mcp / cli / web / config
├── frontend/          # stores / persist（IndexedDB）
├── compatibility/     # 旧启动方式委托新版
├── data/              # 运行数据（gitignore）：market.duckdb / journal.sqlite / archive/
├── tests/             # unit / integration / contract / state / compat
└── scripts/           # 源校验、数据迁移、发布冒烟
```

## 兼容与迁移

| 项 | 新值 | 兼容策略 |
|---|---|---|
| 仓库 | LadderStock | README 标注“已升级为 LadderStock”，旧仓库名保留过渡 |
| PyPI / 导入包 | `ladderstock` | 单包，不发布 quotafuse 前缀包 |
| CLI | `ldst` | 注册长别名 `ladderstock` |
| 启动方式 | `ldst run` | 旧启动脚本委托新版并打印提示 |
| 端口 | 5950（看板）/ 5951（MCP http） | 被占用直接报错不换端口；旧 5940 / 8770 仅打印迁移提示 |
| 配置与环境变量 | `~/.ladderstock/`、`LDST_*` | 首次启动只读迁移，兼容读取旧前缀 |

## 设计取舍

下列口径是当前实现的既定约束，详细取舍背景见仓库历史：

| 主题 | 口径 |
|---|---|
| 开发位置 | 先在本地目录开发，成熟后再接入工作区仓库 |
| 开发环境 | Python 3.12 虚拟环境；CI 以 3.11 为基线 |
| CLI 基线 | 31 条命令全部 P0 |
| 端口 | 占用直接报错，不自动换端口 |
| 前端 | React + Vite 本地浏览器 UI；Tauri 2.0 仅作可选打包壳 |
| 生态边界 | 四层结构：不采集，只消费 peer；主源 → 同 peer 备源 → 缓存重放 |
| 数据集归属 | 归属唯一真源 + 幂等增量同步（水位只进不退） |
| 数据源配置 | `sources.yaml` 链路与门禁目录对齐，非法配置在 `doctor` / `sources validate` 直接报红 |
| 配置迁移 | `config migrate` 只读旧文件、先备份、失败回滚、版本上限拒绝；旧入口委托 `ldst run` |

## 开发

```bash
python -m venv .venv && . .venv/Scripts/activate   # Windows；Python 3.12
pip install -e ".[dev]"
python -X utf8 -m pytest -q                        # Windows 控制台建议 -X utf8
ruff check .
mypy src
ldst doctor                                        # 23 项体检：解释器 / 依赖 / 端口 / 配置 / 边界
```

CI 实际执行的 job、与 workflow 逐字对齐的本地命令（含 Windows / POSIX 的 venv 路径差异）以及 required checks 现状，见 [docs/CI.md](docs/CI.md)。

测试不依赖网络与 API Key：全部走本地 fixture 与 offline provider。当前基线 **614 passed**（另有 4 个旧版兼容性用例由 xfail 转 XPASS）；31 条命令已全部注册可执行，施工期占位（退出码 `70`）已随 `config migrate` 落地而退役（见 `docs/CLI.md` §F、`docs/CONFIG.md`）。

## License

Apache License 2.0。
