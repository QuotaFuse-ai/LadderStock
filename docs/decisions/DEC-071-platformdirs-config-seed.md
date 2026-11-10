# DEC-071: 路径/配置独立 + 首跑 seed（platformdirs 用户数据目录，QUOT-524）

日期：2026-11-10 · 状态：已定稿 · 关联：QUOT-524（拆自 QUOT-519 第 3 柱）、QUOT-471（美股指数源）、QUOT-525（打包）

## 1. 问题

移植生产时暴露两个耦合点：

1. **路径耦合**：安装态落点是硬写的 `~/.ladderstock`，打包/迁移到别的机器、
   别的 OS 时要么改代码、要么手工搬目录；服务脚本 `install-service.ps1` 还
   自己复刻了一遍路径优先级（`~\.ladderstock\data`），两处一旦不同步就各自漂。
2. **配置漂移**：运行时只解析目录、**从不落文件**。用户目录里缺 `sources.yaml`
   或里面少了后来新增的源（QUOT-471 的 `tickhub_us_index_quote`），首页美股两格
   就静默缺数，代码侧无从发现——这就是「①美股指数源漂移」的根因。

## 2. 决策

### 2.1 用户数据目录交给 platformdirs

安装态目录一律由 `platformdirs.user_data_dir("LadderStock", "QuotaFuse")` 推出：

| 平台 | 落点 |
|---|---|
| Windows | `%LOCALAPPDATA%\QuotaFuse\LadderStock` |
| macOS | `~/Library/Application Support/LadderStock` |
| Linux | `$XDG_DATA_HOME/LadderStock`（默认 `~/.local/share/LadderStock`） |

配置、数据分别为其下 `config/`、`data/` 子目录。代码里**不再出现盘符或
`~/.ladderstock` 常量**；服务安装脚本改为同口径（`%LOCALAPPDATA%\QuotaFuse\LadderStock\data`）。

解析优先级（`cli/paths.py`）不变，只在最后一级换成 platformdirs：

1. `LDST_CONFIG_DIR` / `LDST_DATA_DIR`（显式覆盖，最高）
2. `cwd/config`、`cwd/data`（源码开发态）
3. platformdirs 用户数据目录（安装态）

旧前缀 `VRA_*` / `VIBE_ASTOCK_*` 仍作兜底读，`LDST_*` 永远优先。

### 2.2 首跑从 bundle 模板 seed 完整配置

- 真源是随包的模板：源仓库 `config/`（含 `schema/`）同步一份到包内
  `src/ladderstock/assets/config/`，由 `[tool.setuptools.package-data]` 打进 wheel；
  `bundle_config_dir()` 优先取包内副本，源码树里没有包内副本时退回工程根 `config/`。
- `ldst run` 启动时、自动迁移**之前**调用 `config.seed.seed_if_needed()`：
  只有解析到**用户数据目录**（安装态）且存在缺失文件时才 seed；`cwd/config`
  开发态与 `LDST_CONFIG_DIR` 显式目录**都不 seed**（程序不往用户自己的目录塞文件）。
- seed 只复制**缺失**文件，已存在的一律跳过——用户改过的配置永远优先，
  升级不回滚；`schema/*.json` 同步补齐，保证干净目录 `config validate` 能跑。
- 新增 `ldst config seed` 作为手动补齐入口（幂等，输出 `seeded`/`skipped`）。

顺序：**先 seed 再 migrate**——迁移要读 `state.yaml`，seed 之前它不存在。

### 2.3 漂移的修法边界

seed 修的是「文件整体缺失」这一最常见漂移态。若用户已有 `sources.yaml` 但
缺某个新增源，seed **不覆盖**（保留用户手改）——这是有意的：自动改动用户
写过的配置比缺一个源风险更大。这类「同文件内增量漂移」交给 `config validate`
与 `boundary_audit` 报出（`sources.yaml` 与 `DATASET_CATALOG` 的双向一致性检查），
用户按提示补登记，不做静默改写。

### 2.4 密钥不进模板

seed 只复制字节、不注入任何凭据；模板 `ai.yaml` 只写环境变量**名**
（`api_key_env` / `base_url_env`），不含明文。`scripts/check_privacy.py` 的
`config-secrets` 组继续守这条线，新增单测 `test_config_templates_carry_no_plaintext_secrets`
做二次兜底。

## 3. 验收对照（QUOT-524）

| 验收项 | 落点 |
|---|---|
| 干净目录首跑自动 seed 完整配置 | `test_seed_fills_clean_dir` / `test_seed_if_needed_only_in_install_mode`；CLI 实测 `config validate` 六文件全 True |
| US2 指数源 seed 后存在 | `test_seed_clean_dir_sources_contains_us_index` / `test_seed_repairs_missing_sources_file` |
| grep 无硬编码生产路径 | `test_bundle_config_has_no_absolute_drive_paths`；`install-service.ps1`、`paths.py` 已改 |
| 密钥仍只进 env | `config-secrets` PASS + `test_config_templates_carry_no_plaintext_secrets` |

## 4. 影响面与后续

- 新依赖 `platformdirs>=4,<5`（进 `pyproject.toml` 与 `requirements.lock`）。
- 旧 `~/.ladderstock` 不做自动搬迁（本单范围外）；用户若需要，`LDST_CONFIG_DIR`/
  `LDST_DATA_DIR` 指过去即可，或后续单补一次性迁移。
- 模板随包发布（`assets/config/` + `package-data`），wheel 装进 site-packages 后
  `bundle_config_dir()` 仍能找到；仓库 `config/` 与包内副本由
  `test_packaged_templates_match_repo_config` 守同步，改一份漏一份会红。
  QUOT-525 打包只需确认 wheel 含 `ladderstock/assets/config/**`（已由单测覆盖）。
