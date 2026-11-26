# LadderStock 项目功能总览 · 缺陷清单 · Opus 5.5 整改要求

> 生成：2026-11-19 · 复核基线：`D:\workplace\LadderStock`（分支 `agent/agentic-research`，HEAD `f7dcc57`，工作区另有 5 个未提交改动）
> **证据等级约定**：`【实测】`=本轮真跑命令/接口得到的结果；`【代码】`=读源码确认；`【待核实】`=没验证，**不要当事实用**。

---

## 1. 这个文件夹是什么项目

**LadderStock（天梯股复盘台）** —— 面向 A 股短线交易者的**本地化复盘与跟踪工作台**。定位是"复盘辅助 + 交易日志"，**不接实盘、不托管账户、不构成投资建议**。

### 1.1 规模【实测】

| 维度 | 数量 |
|---|---|
| Python 代码 | **83,829 行**，22 个功能域（`ai` `backtest` `calc` `cli` `config` `core` `data` `industry` `journal` `knowledge` `mcp` `modules` `news` `research` `review` `service` `supervisor` `watch` `web` `weekly` …） |
| CLI 顶级命令 | **33 个**（`run` `watchdog` `weekly` `backfill` `pool` `export` `research` `news` `events` `doctor` `review` `validate` `sentiment` `ladder` `industry` `sync` `sources` `watch` `notify` `journal` `position` `state` `ai` `mcp` `config` `schedule` `symbol` `backtest` `supervisor` `knowledge` `service` …） |
| Web API 模块 | **28 个**（`assistant` `backtest` `calc` `chat` `control` `ensure` `financecoffer_api` `inbox` `industry` `journal_write` `knowledge` `peer_registry` `platform` `port_diagnostics` `preferences_api` `qa_fetch` `radar` `research` `schedule_api` `schedule_runner` `senate_actions` `senate_roles` `server` `setup_api` `state_index` `supervisor_api` `symbols`） |
| 前端 | `frontend/src` 241 个文件（`screens` `components` `charts` `api` `lib` `styles`） |
| 托管子模块 | **8 个**：tickhub / tickhub_us / wire / senate / qdrant / financecoffer / proxyfleet / watch（+ 按需 proxypool） |

### 1.2 架构三层

```
① 工作台本体（Python 单体 + Vite 前端 + 本机 SQLite/DuckDB）
   └─ CLI 33 命令  ·  Web API 28 模块  ·  前端看板
② 子模块编排层（supervisor + web/control 两条通道）
   └─ 端口台账（prod 5950 / test 5951，子模块 +10000）
   └─ 进程托管（spawn / adopt / stop / restart / 自愈）
   └─ 端口冲突处置（避让 + 本方残留清理 + 外部占用拒绝）
③ 子模块实现（各自独立仓库，安装到 file/modules/<name>）
   └─ TickHub-CN（A股数据）  TickHub-US（美股）  Wire（资讯）
   └─ StockSenate（多空辩论）  FinanceCoffer（财务）  ProxyFleet（代理池）
   └─ Qdrant（向量库，本地单体二进制 + MCP）
```

### 1.3 三种交付形态

| 形态 | 入口 | 状态 |
|---|---|---|
| 源码运行 | `python -m ladderstock run --port 5950/5951` | 可用【实测】 |
| 便携包 | `packaging/`（Windows installer / Linux AppImage / macOS） | 存在【代码】 |
| 服务化 | `python -m ladderstock service`（三平台原生服务 + 托盘） | 存在【代码】，未实测 |

---

## 2. 已实现的真实功能（可用）

> 判定标准：有 CLI/HTTP 入口 + 有落地实现 + 本轮或近期实测跑通。

| # | 功能 | 入口 | 实现位置 | 证据 |
|---|---|---|---|---|
| 1 | 本地工作台启动 + 看板 | `ladderstock run` | `web/server.py` + `frontend/` | 【实测】5951 起 13–18s，`/api/health` 200 |
| 2 | 全模块启停 / 单模块重启 | `supervisor start-all|stop-all|restart` | `supervisor/engine.py` | 【实测】qdrant 起 1.3s 就位 |
| 3 | 端口台账（prod/test 双环境） | `supervisor/modules.py` | `module_ports()` | 【实测】prod/test 端口表正确、无冲突 |
| 4 | 端口诊断与归属分类 | `GET /api/control/ports` | `web/port_diagnostics.py` | 【实测】classify_owner 口径三处统一 |
| 5 | 端口自愈（本方残留先清再起） | `POST /api/control/ports/{peer}/heal` | `web/control.py` + `engine._reap_stale` | 【实测】残留 pid 2964 被清、新实例接管 16333/16334 |
| 6 | 控制面任务队列（异步 job） | `POST /api/control/peers/{id}/start` → `/jobs/{id}` | `web/control.py` | 【实测】job status=ok |
| 7 | 子模块进程托管（8 模块） | `supervisor` | `supervisor/module_adapters.py` | 【实测】8 模块端口全在听 |
| 8 | A 股数据（TickHub-CN） | MCP 150 工具 / CLI / Python 库 | 子模块 | 【代码】`skills/tickhub-cn/SKILL.md` 完整 |
| 9 | 复盘主链路 | `pool` `review` `validate` `sentiment` `ladder` | `src/ladderstock/{calc,review,watch}` | 【代码】33 命令已注册 |
| 10 | 六段研究管线 | `research`（company→financial→forecast→valuation→risk→report） | `research/` | 【代码】 |
| 11 | 资讯雷达 / 事件日历 | `news` `events` | `news/` | 【代码】 |
| 12 | 行业五维研究 | `industry list|show` | `industry/` | 【代码】 |
| 13 | 交易日志 / 持仓（本机 SQLite，不经 MCP） | `journal` `position` | `journal/` + `web/journal_write.py` | 【代码】 |
| 14 | AI 编排（模型链、重试、记忆清理） | `ai status|retry|memory-clear` `ai chain test` | `ai/`（22 文件） | 【代码】 |
| 15 | 知识库（qdrant 投影 + 检索） | `knowledge sync-announcements|status` | `knowledge/` | 【代码】+【实测】qdrant 端口可用 |
| 16 | 回测（A股日频策略） | `backtest run|list|show` | `backtest/` | 【代码】 |
| 17 | 周度复盘 | `weekly` | `weekly/` | 【代码】 |
| 18 | 历史回补（qfq + 缺口报表） | `backfill` | `data/` | 【代码】 |
| 19 | 导出（PDF→Markdown 降级） | `export` | `cli/` | 【代码】 |
| 20 | 环境体检 | `doctor` | `cli/doctor` | 【代码】 |
| 21 | 定时调度 | `schedule` + `web/schedule_api.py` | `service/` | 【代码】 |
| 22 | 跨平台服务/自启/托盘 | `service` | `service/` | 【代码】，**未实测** |
| 23 | 配置校验/补种/迁移 | `config validate|seed|migrate` | `config/` | 【代码】 |
| 24 | 旧版兼容入口 | `python compatibility/start.py` | `compatibility/start.py` | 【代码】真实做参数翻译 + 委派，非空壳 |
| 25 | 前端持久化（Zustand → IndexedDB） | `/api/state` | `web/state_index.py` + `frontend/stores` | 【代码】 |
| 26 | 助手问答 / 会话 | `/api/assistant/*` | `web/assistant.py` | 【代码】 |
| 27 | 偏好记忆 | `/api/preferences` | `web/preferences_api.py` | 【代码】 |
| 28 | 前端无损重启 | `/platform/restart-*` | `web/platform.py` | 【代码】 |

---

## 3. 假完成 —— 声明上"有"，实际没跑通

> 这是最需要 Opus 处理的类别：**用户看到的是"已就绪"，实际是空的或错的。**

| # | 项 | 声明/表象 | 实际 | 证据 | 影响 |
|---|---|---|---|---|---|
| F1 | **Qdrant 模块状态机** | 端口在听、`health=ok`，页面应显示"运行中" | `state=stopped`、`ownership=external`、`evidence=cmdline_mismatch`、`stop_reason=suspected_pid_reuse_on_reconcile` | 【实测 2026-11-19】state 接口原文 | 页面显示异常；**停止/启动按钮判据失真** |
| F2 | **ProxyFleet 子模块** | `.gitmodules` 声明为 submodule | `git submodule status` 显示 `-db5fd7c…`（**未 init**），目录为空 | 【实测】 | 源码不在仓库内；`ProxyFleet/` 空目录本身无意义 |
| F3 | **FinanceCoffer embedding 组件** | `install_state.json`：`"state":"ready"`、`with_embedding:true`、`components` 含 `embedding` | 端口 **18010 从未监听**；`monitor.py` 自述"形态 2 下 embedding 不再被拉起" | 【实测】+【代码】 | 声明与实际形态矛盾，安装状态不可信 |
| F4 | **minio 停止** | `stop_all()` 返回"已停止" | 19000/19001 仍在监听（本轮 pid 32600） | 【实测】 | 停止不彻底，重启撞端口 |
| F5 | **名称搜索** | `GET /symbols/search?q=600202&limit=10` | **5000ms 超时** | 【实测（历史多轮复现）】 | 用户只能输 6 位代码 |
| F6 | **「问 AI」金融取数** | 应给出涨跌 + 指标 | 返回"这一块没有取到数据"、"本轮没有取到可核实的指标数据""TIMEOUT"，只剩四指数表 | 【实测】【截图证据】 | 核心问答能力形同虚设 |
| F7 | **`.gitignore` 的 `file/` 规则** | 声称"子模块容器不进库" | `file` 实际 **TRACKED**（`file/ops/start_board.cmd` 被跟踪），规则对已跟踪文件无效 | 【实测】`git ls-files -s file` | 规则失效，子模块目录可能被误提交 |
| F8 | **全量测试"全绿"** | CI 绿 | 本机全量 pytest 有 1 条红：`tests/state/test_persist_recover.py::test_frontend_persistence_suite_passes`（vitest 用例过，2 个 tinypool worker 崩，退出码 2147483651） | 【实测】 | 前端持久化链路未被真实验证 |
| F9 | **正式服（5950 口径）FinanceCoffer** | 应能起在 8100/8101 | 8100 被 `wslhost.exe`（WSL 遗留）占用 | 【实测】pid 21072 | 正式服 fc 起不来 |

---

## 4. 欠妥 —— 能跑，但设计/口径有问题

| # | 项 | 问题 | 证据 | 建议 |
|---|---|---|---|---|
| D1 | `GET /api/supervisor/state` | **48 秒**返回；reconcile 逐模块串行 probe，fc/pg 慢则整体阻塞 | 【实测】state OK in 48s | 并行 probe（已有 `ldst-probe` 线程池，未在此处用）或返回缓存 + 异步刷新 |
| D2 | 端口自愈"三套机制"历史包袱 | 已收敛口径，但残留死代码：`stop_all()` 里 `if record.adopted_peer: skipped` 恒不触发；`_start_module_locked` 的 `claimed` 分支与 `_reap_stale` 场景重叠 | 【代码】 | 清理死分支，避免后人误读 |
| D3 | `config/sources.yaml` | financecoffer 段注释仍写"服务跑在 WSL（8100），控制面只读观测不接管启停" | 【代码】 | 与现状（bundle 形态、可托管）不符，改注释 + 改 `managed` 语义 |
| D4 | `file/` 双份 | `D:\workplace\file\modules\<name>`（旧）与 `D:\workplace\LadderStock\file\modules\<name>`（新，fc 仍是 junction 指向旧）并存 | 【实测】 | 统一到安装根，junction 清零 |
| D5 | 临时产物无门禁 | `tmp_*`、`*.bak_*`、`*_final`、`*_clean` 曾直接躺在仓库根/D 盘根（本轮已人工归集 1345 MB / 46,828 文件） | 【实测】 | 加 `scripts/check_repo_layout.py` + CI |
| D6 | `docs/tasks/*` 白名单例外 | `.gitignore` 里 20+ 条 `!docs/tasks/QUOT-xxx/...` 逐条白名单，越加越乱 | 【代码】 | 改为"默认忽略 + 提交时显式 `git add -f`"或统一目录约定 |
| D7 | `_archive` 无归属 | `D:\workplace\_archive\2026-11-19\` 不属于任何仓库，无保留策略 | 【实测】 | 定 TTL（如 30 天）或纳入备份流程 |
| D8 | 前端备份 `frontend.bak_*` / `src.bak_*` | 已在仓库根存在 2 周，代码 0 引用 | 【实测】已归档 | 长期看应在 CI 禁止 |
| D9 | `state/` `supervisor/` `control/` 三处运行时目录并列 | 语义重叠（都在存"运行态"） | 【代码】 | 统一到 `var/` 下分域 |

---

## 5. 目录职责表（修正版）

> ⚠️ 修正上一版文档的错误：`file/` **不是**"完全被 .gitignore 覆盖"，它有被跟踪文件（`file/ops/start_board.cmd`）。

### 5.1 仓库根（`D:\workplace\LadderStock`）

| 路径 | 职责 | git 状态【实测】 |
|---|---|---|
| `src/ladderstock/` | 主源码包（22 功能域 / 83,829 行） | TRACKED |
| `tests/` | 单测 + 契约 + 集成 + fixtures | TRACKED |
| `frontend/` | Vite 前端（241 文件）；`dist/` 为构建产物 | TRACKED（`dist/` IGNORED） |
| `docs/` | `decisions/`（DEC-0xx）`specs/` `ops/` `supervisor/` `tasks/` `verification/` `screenshots/` | TRACKED（`tasks/*` 白名单） |
| `config/` | `ports.yaml` `ai.yaml` `sources.yaml` `schema/` 等运行配置 | TRACKED |
| `content/skills/` | 14 个技能定义 | TRACKED |
| `prompts/` | AI 提示词模板 | TRACKED |
| `scripts/` | 运维/校验脚本（privacy、nav-citations、install-service…） | TRACKED |
| `tools/` | 技能与工具目录审计脚本（与 `scripts/` 职能重叠） | TRACKED |
| `packaging/` | 三平台打包（Windows/Linux/macOS） | TRACKED |
| `compatibility/start.py` | 旧入口（`python compatibility/start.py`）→ 翻译成 `ldst run` | TRACKED |
| `ops/host/` | Windows 主机侧 PowerShell 适配器与预检 | TRACKED |
| `file/` | 子模块运行时容器：`modules/`（8.5 GB 真实安装）`config/` `data/` `logs/` `models/` `ops/` `wip-backup-*` | **TRACKED 部分**（`file/ops/*` 入库）+ 其余忽略 |
| `data/` `state/` `supervisor/` `control/` `var/` `logs/` `python/` `build/` `dist/` `.venv/` `*_cache/` | 运行数据 / 运行时 / 构建 / 环境 | IGNORED |
| `ProxyFleet/` | submodule 声明（**未 init**，空） | TRACKED（gitlink） |
| `README.md` `NOTICE` `LICENSE` `pyproject.toml` `requirements.lock` `.env.example` `.gitattributes` `.gitignore` `.gitmodules` | 标准仓库文件 | TRACKED |

**仓库根健康度【实测】**：`UNCLASSIFIED`（既未跟踪也未忽略）只有 `.git` 一项 → **无临时噪音**。

### 5.2 工作区根（`D:\workplace`）

| 路径 | 职责 |
|---|---|
| `LadderStock/` | **主仓库**（唯一交付仓库） |
| `TickHub-CN/` `TickHub-US/` `Wire/` `StockSenate/` `FinanceCoffer/` `ProxyFleet/` | 各子模块**上游源码仓库**（独立 git） |
| `file/` | 子模块运行时容器（旧位置，fc 仍指向这里） |
| `ruanjian/python312/` | 统一 Python 工具链（ruff/mypy 与 CI 同版本） |
| `pypibao/` `quatofuse/` `LadderStock-app/` `backups/` `data/` `logs/` `ops/` `testllm/` `th369-e2e/` `disk-warden*/` `AgentCuria/` `bt-cards/` `adplugpro/` | 其它项目 / 工具 / 待确认（明细见上一份《目录说明与整理指引》§2） |
| `_archive/2026-11-19/` | 本次归集的临时产物（665.7 + 380.6 MB） |
| `.multica/` `.qoder/` `.workbuddy/` | 工具链本地数据 |

---

## 6. 多次修改没改对的功能（重点）

| # | 症状 | 改过几轮 | 每轮做了什么 | 为什么还没对 |
|---|---|---|---|---|
| R1 | **Qdrant「假 down」** | ≥3 轮 | ① `f7dcc57`：取消 `adopted_peer` 永久跳过、`_reap_stale` 清残留；② 本轮：control 层 `_own_residual_pid` 先清再起；③ 本轮：`_repoint_to_live_listener`（账本 pid 死后改指真实监听者） | 真实根因是**账本 pid 仍活着但 `cmdline_hash` 对不上** → reconcile 判 `suspected_pid_reuse`。我第③轮假设"pid 已死"，前提就错了，所以代码写了但没生效【实测】 |
| R2 | **端口被自己孤儿占着 → 关不掉 → 起不来** | ≥4 轮 | 端口避让(+1) → S4 杀重启 → adopt 认领 → 本轮"本方残留先清再起" | 现在**部分生效**（qdrant 2964 已清、proxyfleet/tickhub heal 成功），但状态机仍失真（见 R1），用户看到的仍是"异常" |
| R3 | **`/api/control/peers` 超时** | ≥2 轮 | 探测并发化（`ldst-probe` 线程池 8） | `peers` 已从 15s 超时 → **5s**【实测】；但 `state` 仍是 **48s**【实测】——只修了一处，同源问题没顺手全修 |
| R4 | **FinanceCoffer 起不来** | ≥3 轮 | WSL docker compose → bundle 形态 → `_start_bundle` 返回真实 cmdline → pg 日志按端口 | 现在 test 侧能起（冷启 29s，stop 29s）；**正式服 8100 仍被 WSL 遗留占用**【实测】 |
| R5 | **financecoffer junction** | 提了 ≥2 轮 | 只做到"识别出来"，从未真正迁移 | 5.41 GB；迁移必须停自愈，否则 supervisor 立刻把 fc 拉起来（本轮实测：停服清进程后 8 秒内又被拉起） |

---

## 7. 需要完善（按优先级）

**P0（影响用户可见正确性）**
1. qdrant/各模块**状态机与端口实际一致**（R1）
2. `state` 接口降到 3s 内（D1）
3. 「问 AI」金融取数（F6）+ 名称搜索（F5）

**P1（数据/进程正确性）**
4. minio 停止不彻底（F4）
5. `install_state.json` 与实际组件一致（F3）
6. 8100 WSL 残留清理（F9）
7. fc junction → 真实目录（R5）

**P2（工程卫生）**
8. `check_repo_layout.py` 门禁 + CI（D5）
9. `_gitignore` 的 `file/` 规则修正（F7）
10. 清理死代码分支（D2）、过期注释（D3）
11. 全量 pytest 1 条红（F8）
12. `docs/tasks` 白名单机制收敛（D6）

---

## 8. 给 Opus 5.5 的执行要求

> 规则：**每完成一项，把对应方框改成 `[x]`，并在行尾写一行证据**（命令 + 输出摘要）。没验证过的不许打勾。

### 8.1 状态机正确性（最高优先）

- [ ] **修 Qdrant「假 down」**：让 `state` 与端口实况一致
  实现位置：`src/ladderstock/supervisor/engine.py`（`reconcile` / `_repoint_to_live_listener` / `mark_spawned`）、`src/ladderstock/supervisor/module_adapters.py`（`QdrantAdapter`）
  验收命令：`POST /api/control/peers/qdrant/start` → `GET /api/supervisor/state`，期望 `state=running, ownership=own_child, evidence=cmdline_match, listening=true`
  提示：账本 pid 活着时不要走 repoint；应改为 `mark_spawned` 用 **psutil 实测 cmdline** 记账，或 reconcile 在「端口在听 + 监听者 exe 落在本模块安装根」时认领为 own。

- [ ] **`state` 接口 ≤3s**
  实现位置：`src/ladderstock/supervisor/engine.py`（`_await_probe` / `state` 视图）、`src/ladderstock/web/supervisor_api.py`
  验收：`curl -w '%{time_total}' /api/supervisor/state`，连续 3 次 < 3s

### 8.2 进程与端口

- [ ] **minio 停止彻底**：`stop_all()` 后 19000/19001 必须 free
  实现位置：`src/ladderstock/modules/financecoffer/services.py`（`stop_all` / `_stop_bundle`）
  验收：`stop_all()` → 5s 后 `Get-NetTCPConnection -LocalPort 19000` 返回空

- [ ] **financecoffer junction → 真实目录**（5.41 GB）
  前置：**先停自愈**（`LDST_SUPERVISOR_OWN_PROCESSES=0` 或摘掉 fc 模块），否则拉起会写坏
  步骤：`rmdir` 删链接 → `robocopy /E /COPYALL` → 校验文件数/字节数 → `Get-Item -Force` 的 `LinkType` 必须为空
  验收：`file/modules/financecoffer` `LinkType` 空、`Attributes=Directory`；fc 能正常起

- [ ] **8100 WSL 残留清理**
  验收：8100 不再由 `wslhost.exe` 持有，正式服 fc 能在 8100 起

### 8.3 用户可见功能

- [ ] **名称搜索**：`GET /symbols/search?q=600202&limit=10` 在 1s 内返回
  实现位置：`src/ladderstock/web/symbols.py` + `data/` 索引
- [ ] **「问 AI」金融取数**：指数 + 指标不再返回"没有取到数据"
  实现位置：`src/ladderstock/web/assistant.py` + `src/ladderstock/ai/`
  验收：问"今天大盘怎么样"，回答含指数涨跌幅与至少 3 项指标，且每项有来源
- [ ] **`install_state.json` 与实际一致**：不起 embedding 就不要声明 `with_embedding: true`
  实现位置：`src/ladderstock/modules/financecoffer/`（安装器写状态处）

### 8.4 工程卫生

- [ ] **新增 `scripts/check_repo_layout.py`** + 接进 `.github/workflows/ci.yml`
  检查：仓库根出现 `tmp_*` / `_*` / `*.bak_*` / `*_final` / `*_clean` / 未跟踪杂物 / `file/modules/*` 为 junction → 退出码 1
  验收：造 3 个违规样本报 3 条，删掉后返回 0；CI 绿
- [ ] **修 `.gitignore` 的 `file/` 规则**：要么 `git rm --cached file/ops/*` 真正脱离跟踪，要么删掉 `file/` 规则并写清"只忽略 `file/{modules,data,logs,models}`"
  验收：`git ls-files file | wc -l` 结果符合预期，且 `git status` 不因 `file/` 抖动
- [ ] **清理死代码**：`stop_all()` 的 `adopted_peer skipped` 分支、`_start_module_locked` 的 `claimed` 分支（已被 `_reap_stale` 覆盖）
  验收：删除后 `pytest tests/unit/supervisor` 全绿
- [ ] **修正 `config/sources.yaml` 里 fc 的 WSL 注释**与 `managed` 语义
- [ ] **全量 pytest 零红**：修 `test_frontend_persistence_suite_passes`（vitest worker 崩溃）
- [ ] **`docs/tasks` 白名单收敛**：改成目录约定 + 显式 `git add -f`，移除逐条 `!` 规则

### 8.5 收尾

- [ ] **版本号 bump（0.2.2 → 0.2.3+）**，`pyproject.toml`
- [ ] **提交并推 PR #273**，正文写清本轮所有修复点与证据
- [ ] **更新本文件**：把已打勾项的"证据"补成真实命令输出

---

## 9. 不要做的事（红线）

- ❌ 不要 `taskkill wslhost.exe` / `wsl --shutdown`（会打断用户其它 WSL 工作）
- ❌ 不要终止"可执行文件不在对应 peer 安装目录内"的进程（DEC-014）
- ❌ 不要往 junction 里写/复制（会直接写回源目录）——必须 `rmdir` 删链接后重建真实目录
- ❌ 不要删除 `D:\workplace\_archive\2026-11-19\`（用户证据，等确认）
- ❌ 不要把 git 跟踪文件当垃圾搬走（本轮已犯过：`ProxyFleet` submodule、`.commitmsg.txt`、`pr-body-354.md`）；搬前必须 `git ls-files` + `git check-ignore` 逐个核对
- ❌ 未验证的功能不要打勾

---

## 10. 复现命令

```powershell
# 仓库根健康度（期望：UNCLASSIFIED 仅 .git）
Set-Location D:\workplace\LadderStock
$tracked = git ls-files | ForEach-Object { ($_ -split '/')[0] } | Sort-Object -Unique
foreach($i in Get-ChildItem . -Force){ $ig = git check-ignore $i.Name 2>$null; if(-not ($tracked -contains $i.Name) -and -not $ig){ "UNCLASS " + $i.Name } }

# qdrant 状态 vs 端口实况
(Invoke-RestMethod http://127.0.0.1:5951/api/supervisor/state).modules | Where-Object module -eq qdrant | Select-Object state,ownership,evidence,pid,listening
Get-NetTCPConnection -LocalPort 16333 -State Listen

# 接口耗时
Measure-Command { Invoke-RestMethod http://127.0.0.1:5951/api/supervisor/state } | Select-Object TotalSeconds

# junction 清单（期望：fc 之外为空）
Get-ChildItem D:\workplace\LadderStock\file\modules -Force | Where-Object LinkType | Select-Object Name,LinkType,Target
```
