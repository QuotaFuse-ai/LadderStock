# 配置与迁移（CONFIG）

本文是配置目录、环境变量与旧版迁移的**唯一权威说明**。改动前请先看
`docs/decisions/DEC-001-local-first-development.md`（本地优先）与
`docs/decisions/DEC-009-config-migration-and-legacy-entry.md`（迁移口径）。

## 1. 目录解析顺序

配置目录与数据目录都不写死在代码里，按下列顺序解析（`src/ladderstock/cli/paths.py`）：

| 优先级 | 配置目录 | 数据目录 |
|---|---|---|
| 1 | `LDST_CONFIG_DIR` | `LDST_DATA_DIR` |
| 2 | 当前工作目录下的 `config/` | 当前工作目录下的 `data/` |
| 3 | `~/.ladderstock/config` | `~/.ladderstock/data` |

`config/` 与 `data/` 进仓库、可提交；`data/` 已在 `.gitignore` 中——里面有本机隐私库
（`data/journal.sqlite`）与迁移备份，**绝不入库**。

## 2. 环境变量

| 变量 | 作用 |
|---|---|
| `LDST_CONFIG_DIR` / `LDST_DATA_DIR` | 覆盖配置 / 数据目录 |
| `LDST_LEGACY_CONFIG_DIR` | 显式指定旧版配置文件（`vibe-research.config.json`）所在路径 |
| `LDST_LEGACY_DATA_DIR` | 显式指定旧版数据目录（`data.js`、`review/`），迁移时只读复制 |
| `LDST_SKIP_AUTO_MIGRATE` | `1` 关闭 `ldst run` 首启动的自动迁移（排障用） |

**旧前缀兼容**：过渡期继续读 `VRA_*` 与 `VIBE_ASTOCK_*`（旧版 vibe-astock 命名），
按此顺序兜底；`LDST_*` 只要存在就永远优先，旧变量绝不覆盖新值，也从不反向写入。

密钥只走环境变量（如 provider token），**不落配置文件、不进日志**——
`scripts/check_privacy.py` 的 `config-secrets` 组会扫描 `config/*.yaml` 里的明文凭据。

## 3. 配置文件清单

| 文件 | 内容 | Schema |
|---|---|---|
| `config/sources.yaml` | 生态 peer 与数据集登记（DEC-006 边界） | `config/schema/sources.schema.json` |
| `config/sync.yaml` | 增量水位与同步策略 | `config/schema/sync.schema.json` |
| `config/ai.yaml` | AI provider 顺序、超时、降级策略 | `config/schema/ai.schema.json` |
| `config/state.yaml` | 前端持久化分片清单（`schema_version: 2`） | `config/schema/state.schema.json` |
| `config/schedule.yaml` | 定时任务表 | `config/schema/schedule.schema.json` |

`ldst config validate` 逐文件校验并给出 `/a/b: 错误` 形式的路径；任一失败退出码 3。

## 4. `ldst config migrate`

实现落在 `src/ladderstock/config/migrate.py`，CLI 只做参数解析与结构化输出。
四件事：

1. **`config/state.yaml` v1 → v2**：分片从单块拆成 `watch_snapshot` / `review_draft` /
   `validation_draft` 三类，并保留 `journal_draft` 作为**预留分片**——它只在配置里声明、没有前端
   消费者（journal 域只走 `ldst journal` CLI + 本机 SQLite，`web/server.py` 不提供日志只读接口），
   预留理由见 `docs/STATE_PERSISTENCE.md §2`；同时补齐缺项、校验 `watch_snapshot.ttl_sec`；
2. **`data/journal.sqlite` 结构升级**：交给 `ladderstock.data.sqlite_store.migrate()`；
3. **旧目录只读迁移**：把旧 `data.js` / `review/` 复制进 `data/legacy/import-<时间戳>/`，
   旧目录不移动、不删除；旧配置文件只告警（模型与 provider 必须人工确认，不猜、不拷凭据）；
4. **迁移标记**：写入 `data/state/migration.json`，记录版本、步骤与备份路径。

输出契约（`--output json`）：

| 字段 | 说明 |
|---|---|
| `dry_run` | 是否只出计划 |
| `status` | `planned`（有步骤待执行）/ `up_to_date`（无事可做）/ `applied`（已执行） |
| `schema` | `{from, to, supported}` |
| `steps` | `[{id, action, detail, files}]`，`id` 稳定：`config:state.yaml`、`sqlite:journal.sqlite`、`data:legacy` |
| `backups` | 本次真实落盘的备份文件绝对路径（`dry_run` 恒为空） |
| `warnings` | 需要人工确认的事项 |
| `legacy` | `{config: [...], data: 路径或 null}` |
| `marker` | 迁移标记路径 |

**三条硬约束**：

- **先备份后改动**：`data/backups/<时间戳>/state.yaml`、`data/journal.sqlite.bak-v<ver>-<时间戳>`；
  任一步失败按备份回滚（`state.yaml` 还原、`journal.sqlite` 覆盖回去、新建目录删除），退出码 3；
- **幂等**：已是 v2 时 `status=up_to_date`、`steps=[]`、不写任何文件（包括备份与标记），
  因此放进 CI 矩阵是安全的；
- **不猜未来**：`state.yaml` 或隐私库版本高于程序支持值时直接拒绝（退出码 3），不降级、不改写。

`--dry-run` 只读：不建 `data/backups/`、不写标记、不动任何文件。

## 5. 首启动自动迁移

`ldst run` 在抢端口之前调用 `ensure_migrated()`：有活可干才迁移，失败只告警、绝不
阻断启动（旧路径仍然可用）。想彻底关掉：`LDST_SKIP_AUTO_MIGRATE=1`。

## 6. 旧版启动方式（兼容入口）

| 旧入口 | 现在做什么 |
|---|---|
| `scripts/start`（sh） | 打印迁移提示到 stderr，`exec ldst run "$@"` |
| `scripts/start.cmd`（cmd） | 同上，找不到 `ldst` 时回落 `python -m ladderstock run` |
| `compatibility/start.py` | 翻译旧前缀环境变量后把参数原样交给 `ldst`；首个参数不是已知子命令时补 `run` |

旧端口 `5940` / `8770` 只打印迁移提示，**不接管、不回退**（DEC-004）；这两个数字只允许
出现在文档的迁移说明里，`src/**/*.py` 中不得出现（`tests/compat/test_legacy_entry.py` 会扫）。
