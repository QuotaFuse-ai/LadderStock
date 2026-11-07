# DEC-067 对话通道：复用既有 AI 栈、来源只认真实取数记录、可中止的一轮问答

- 日期：2026-11-07
- 提出：后端架构师（QUOT-473，承接 QUOT-454 页级助手）
- 状态：已采纳（实现见 `src/ladderstock/web/chat.py`、`POST /api/control/ai/chat`）
- 关联：QUOT-473（任务）、QUOT-454（页级助手）、DEC-021（AI 边界与受控工具）、
  DEC-018（受控联网）、`docs/specs/SPEC-AI-CHAT.md` v0.1
- 生效版本：`docs/specs/SPEC-AI-CHAT.md` v0.1

## 1. 背景

首页研究入口需要「问一句、得一段正文 + 来源 + 待确认的研究建议」的通道。
仓库里已经有两条成熟链路：`ai/fallback.py`（配置与后端选择）、
`ai/cli_provider.py`（订阅版 CLI）与 `ai/orchestrator.HttpChatClient`（HTTP 模型），
以及 `web/assistant.py`（QUOT-454 页级助手，同步、无来源、无会话）。

如果为对话另起一套调用栈，会同时破坏三件事：模型/密钥只在一处配置、
错误分类只在 `error_taxonomy` 一处维护、审计只认一套日志。因此本决策的核心是
**只加端点，不加调用链路**。

## 2. 决定

### 2.1 复用既有通道，不新起模型调用

- 通道解析：`fallback.load_config()` → `config.resolve(default_model)`；
  CLI 走 `CliProvider.acomplete`，HTTP 走 `build_chat_client(config).complete`；
- 不硬编码模型名 / 密钥；CLI 探针非 `ok` 时直接 503 `ai_no_channel`，
  **不退化成 HTTP 通道、不返回假回答**；
- 请求里的 `timeout_sec` 只能压低服务端上限（默认 60s，`LDST_AI_CHAT_TIMEOUT`
  可覆盖，夹在 5–300s），防止调用方绕过保护。

### 2.2 来源只认真实取数记录，服务端不编

`context.sources[]` 是唯一来源入口：调用方交来本轮真实取数记录
（`name` + `fetched_at` + 可选 `url`），服务端只做校验、去查询串、补 `used_at`。

- 时间不可解析或晚于当前 5 分钟以上 → 400（视为编造）；
- 一条都没有 → `sources: []`，正文末尾追加**服务端固定文案**说明；
- 服务端不主动抓持仓 / 论点正文，也不从上下文反推来源（DEC-021 边界）。

### 2.3 CLI 调用可中止，且整棵进程树一起终止

同步 `subprocess.run` 无法被前端 abort 打断，会留下孤儿 codex 进程。为此：

- 新增 `CliProvider.acomplete` + `_run_async`（`asyncio.create_subprocess_exec`）；
- 取消 / 超时时：Windows `taskkill /PID <pid> /T /F`，POSIX `killpg(SIGKILL)`；
- 端点每 0.2s 轮询 `Request.is_disconnected()`，断连即取消本轮；
- HTTP 通道无子进程，取消后由 httpx 自身超时兜底（本版不做连接级中断）。

### 2.4 会话记忆放进程内，且只记成功轮次

- TTL 30 分钟、最多 64 个会话、每会话最近 6 轮；
- 只在本轮成功后落账；失败 / 中止不写历史，避免把半截对话带进下一次；
- 本版不做持久化：对话不是 canonical 数据，重启清空可接受（与 DEC-021 的
  「AI 写的不是 canonical」一致）。

### 2.5 `pending_research` 只建议、不启动

问题同时命中 6 位代码与研究类触发词时返回建议（最多 3 条，
`confirm_required: true`）。后端不建任务、不调研究接口，编排权留在前端与用户确认。

## 3. 后果

**收益**

- 配置、错误分类、审计仍只有一套；`generate` / `retry` 契约零改动；
- 无通道时明确 503，不产生「看起来像回答」的假内容；
- 前端 abort 不再留下 codex 孤儿进程（QUOT-473 验收要求）。

**代价 / 限制**

- 会话记忆在进程内：多 worker 或重启后会丢；本版单进程回环服务可接受；
- HTTP 通道的中止只能取消等待，底层请求线程要等 httpx 超时才结束；
- `sources[]` 依赖调用方诚实提供取数记录；服务端只能校验时间与链接形状，
  无法验证「这条记录是否真的来自数据源」——这属于前端取数层的契约。

**后续**

- 若对话需要跨重启保留，再评估落 `data/state/` 的持久化方案，不在此决策内；
- CLI 上游订阅不可用时，端点会如实 503（见 TASK-QUOT-473 验收记录）。
