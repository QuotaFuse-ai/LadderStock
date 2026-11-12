# SPEC-AI-CHAT 对话通道：一轮问答端点

- 版本：v0.3（2026-11-13，QUOT-573 补 §1.1；v0.2 2026-11-08，QUOT-473；v0.1 首版 2026-11-07）
- 关联：DEC-067（本规格的决策记录）、QUOT-454（页级助手）、DEC-021（AI 边界）、
  DEC-018（受控联网）、`ai/error_taxonomy.py`（`FailureKind` 四类）
- 实现：`src/ladderstock/web/chat.py`、`src/ladderstock/web/control.py`
  （`POST /api/control/ai/chat`）、`src/ladderstock/ai/cli_provider.py`
  （`CliProvider.acomplete` / `_run_async`）

## 1. 目标与非目标

**目标**：给首页研究入口与页级助手提供**一轮一问一答**的 HTTP 端点：回答正文 +
来源清单 + 待确认的研究建议；复用既有 AI 通道；失败按可读原因分类，绝不返回
mock / 写死回答。

**非目标**（本规格明确不做）：

- 不新起模型调用链路：通道选择仍由 `ai/fallback.py` + `ai/cli_provider.py` 决定，
  不硬编码模型名 / 密钥；
- 不改 `POST /api/control/ai/generate` / `POST /api/control/ai/retry` 的 job 型契约；
- 不做前端页面改动；
- 不做研究任务的编排与调度：`pending_research[]` 只返回建议，后端不自动启动；
- 服务端不主动抓持仓、不抓论点正文，`context` 只接受调用方白名单字段。

### 1.1 对首页的适用性（2026-11-13，QUOT-573 / DEC-083）

本端点是**控制面**端点。首页是数据面，**首页对话不走本端点**，改走 `POST /api/page/assistant`：

- 控制面总开关 `LDST_CONTROL_ENABLED=0` 时 `/api/control/*` 整族 404（`server.py:1033` `control_switch`），
  首页对话会整块失败，而首页其余数据照常——数据面不该被写面开关连坐（`research.py:15` 同口径）。
- 首页通道定为**单轮无状态**：不建会话。本规格 §4 的会话记忆（内存 TTL 30 min / 6 轮）**不适用于首页**；
  多轮会话由任务书阶段 C 的 `chat_session / chat_turn`（`chat.sqlite`，D1）承接。
- 本规格产出的「来源校验 + `used_at`」与「服务端 `_source_note`」两项能力按 D3（能力迁进现役）
  迁入 `assistant.py` 链路（阶段 B/C），首页届时经页面助手端点获得，**不经本端点**。
- `pending_research[]` 首页不返回；若将来某通道返回，允许不渲染、**禁止渲染成起任务按钮**（SPEC-HOME-V2 §12-L08）。
- 本端点保留、不删、不挂载（D3 定稿），留作对照与后续合并。

## 2. 端点

```http
POST /api/control/ai/chat
Content-Type: application/json
X-LDST-Client: 1            # 或带同源 Origin（控制面写方法要求）
```

### 2.1 请求

```json
{
  "message": "600000 最近怎么样？",
  "session_id": "acc-http-0001",
  "timeout_sec": 30,
  "context": {
    "page": "research/600000",
    "data": {"quotes": {"close": 10.1}},
    "sources": [
      {
        "name": "本地 /api/control/peers（工作区状态）",
        "fetched_at": "2026-11-07T09:49:31+00:00",
        "url": "http://127.0.0.1:5963/api/control/peers"
      }
    ]
  }
}
```

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `message` | 是 | 1–4000 字；空 / 超长 → 400 |
| `session_id` | 否 | 8–64 位 `[A-Za-z0-9_-]`；缺省生成 `secrets.token_urlsafe(12)`；非法 → 400 |
| `context.page` | 否 | 页面标识，最多 80 字进 prompt |
| `context.data` | 否 | 页面已展示数据的只读摘要；JSON 序列化后 ≤ 12000 字、嵌套 ≤ 6 层 |
| `context.sources` | 否 | 调用方**本轮真实取数记录**，最多 20 条；见 §3 |
| `timeout_sec` | 否 | 只能**压低**服务端上限，不能抬高；非法 → 400 |

三个模型都声明了 `extra="forbid"`：多传字段直接 422，不静默丢弃。

### 2.2 成功响应（200）

```json
{
  "ok": true,
  "session_id": "acc-http-0001",
  "reply": "……",
  "model": "openai:user-model",
  "channel": "openai",
  "sources": [
    {
      "name": "本地 /api/control/peers（工作区状态）",
      "fetched_at": "2026-11-07T09:49:31+00:00",
      "url": "http://127.0.0.1:5963/api/control/peers",
      "used_at": "2026-11-07T09:49:34+00:00",
      "kind": "page_context"
    }
  ],
  "pending_research": [
    {
      "kind": "stock_research",
      "code": "600000",
      "label": "个股深挖 600000",
      "reason": "问题涉及个股研究，确认后由前端调用既有研究接口启动",
      "confirm_required": true
    }
  ],
  "elapsed_ms": 114,
  "fetched_at": "2026-11-07T09:49:34+00:00"
}
```

- `reply`：模型正文；`sources` 为空时在末尾追加**服务端固定文案**说明
  （`（说明：本轮没有任何可核实的数据来源……）`），不由模型生成；
- `model`：既有配置解析出的标签（`backend:model`），不硬编码；
- `channel`：`cli` / `openai` / `anthropic`；
- `sources[]`：见 §3，`kind` 当前固定 `page_context`；
- `pending_research[]`：仅当问题同时命中「6 位代码」与「研究类触发词」时给出，
  最多 3 条；只含 `kind` / `code` / `label` / `reason` / `confirm_required`；
- 响应头 `Cache-Control: no-store`。

### 2.3 失败响应

统一形状（失败也回 `session_id`，便于前端续聊）：

```json
{
  "ok": false,
  "session_id": "acc-network-01",
  "reply": "",
  "sources": [],
  "pending_research": [],
  "code": "ai_network",
  "detail": "AI 请求未送达（网络/代理）：……",
  "retryable": true,
  "failure": {
    "kind": "network",
    "reason": "network",
    "message": "……",
    "action": "检查网络或代理后重试……",
    "retryable": true,
    "detail": "……",
    "channel_state": null
  }
}
```

`failure.kind` 对齐 `ai/error_taxonomy.FailureKind`（`network` / `quota` /
`format` / `truncated` / `internal`），`failure.reason` 是给前端区分场景的细分值。

## 3. 来源（sources）口径

**服务端不编造来源。** `sources[]` 只来自调用方随本轮 `context.sources`
交来的真实取数记录，并在服务端做三道校验：

1. `name` 非空（截断 80 字）；
2. `fetched_at` 必须是可解析的 ISO 8601；**晚于当前时间 5 分钟以上判为编造 → 400**；
3. `url` 只保留 `http(s)` 的 scheme / host / path，**查询串整段丢弃**
   （可能带 token）。

通过校验的条目补 `used_at`（UTC，本轮实际使用时间）与 `kind="page_context"`，
按 `name + fetched_at` 去重，最多 20 条。一条都没有 → `sources: []`，正文末尾
追加服务端说明文案。

**本轮来源不跨轮记忆**：`sources` 只对当轮请求生效，不写入会话历史；
服务端也不从上下文里反推来源。

## 4. 会话记忆

- 进程内 `OrderedDict`，TTL 30 分钟、最多 64 个会话、每个会话保留最近 6 轮
  （12 条消息）；
- **只在本轮成功后落账**；失败 / 中止不写历史；
- 历史以「用户：… / 助手：…」形式放进 prompt 的 `【历史对话】` 段；
- 进程重启即清空（本版不做持久化，见 DEC-067）。

## 5. 通道选择与超时

### 5.1 通道

`ai/fallback.load_config()` 按 `config/ai.yaml` + `ai.env` 解析默认模型：

- `kind == "cli"`：先用 `cli_provider.probe` 判四态；非 `ok` 直接 503
  `ai_no_channel`（带 `channel_state` 与登录提示），**不退化成 HTTP、不编回答**；
- HTTP 通道：缺 `base_url` / `api_key` 环境变量 → 503 `ai_no_channel`，并列出缺哪些；
- 调用本身仍走既有实现：CLI → `CliProvider.acomplete`，HTTP →
  `build_chat_client(config).complete`。

### 5.2 超时

- 默认 60s，`LDST_AI_CHAT_TIMEOUT` 可覆盖（夹在 5–300s）；
- 请求里的 `timeout_sec` 只能向下压；
- 超时统一 504 `ai_timeout`（`failure.kind = "network"`, `reason = "timeout"`），
  文案区分「本机 CLI」与「上游模型」；
- 上游返回 5xx 时单独归为 `ai_upstream`（502，`kind=network`，可重试）：
  请求已经送达、是上游自己报错，不能和「本地请求没送出去」混在一起说。

### 5.3 中止（前端 abort）

- 端点每 0.2s 轮询 `Request.is_disconnected()`；发现断连即取消本轮；
- CLI 通道：`asyncio.create_subprocess_exec` 启动，取消时 Windows 用
  `taskkill /PID <pid> /T /F`、POSIX 用 `killpg(SIGKILL)`，**整棵进程树一并终止**，
  不留孤儿进程；
- 中止不写会话历史，响应码 499 `ai_aborted`（客户端已断开，通常看不到）；
- HTTP 通道没有子进程，取消任务后底层线程由 httpx 自身超时兜底。

## 6. 错误码

| HTTP | code | reason | 前端可见文案（`detail` / `failure.message`） | retryable |
| --- | --- | --- | --- | --- |
| 400 | `empty_message` | `invalid_request` | `message 不能为空` | 否 |
| 400 | `message_too_long` | `invalid_request` | `message 过长（上限 4000 字）` | 否 |
| 400 | `invalid_session` | `invalid_request` | `session_id 需要 8–64 位字母 / 数字 / 下划线 / 连字符` | 否 |
| 400 | `invalid_timeout` | `invalid_request` | `timeout_sec 需要数字（秒）` / `timeout_sec 必须大于 0` | 否 |
| 400 | `invalid_source` | `invalid_request` | 来源名为空 / 时间不可解析 / 时间晚于当前 | 否 |
| 400 | `context_too_long` | `invalid_request` | `页内上下文过长（上限 12000 字）……` | 否 |
| 400 | `context_too_deep` | `invalid_request` | `context.data 嵌套超过 6 层` | 否 |
| 499 | `ai_aborted` | `aborted` | `本轮已被前端中止` | 否 |
| 503 | `ai_no_channel` | `no_channel` | 未配置通道 / CLI 未安装 / 未登录 / 上游订阅不可用，附 `action` 与 `channel_state` | 是 |
| 504 | `ai_timeout` | `timeout` | `AI 响应超时（Ns）：本机 CLI / 上游模型没有在时限内返回` | 是 |
| 502 | `ai_upstream` | `upstream` | `上游模型返回 5xx：……`（请求已送达，上游自身报错） | 是 |
| 502 | `ai_network` | `network` | `AI 请求未送达（网络/代理）：……` | 是 |
| 429 | `ai_quota` | `quota` | 额度 / 限流文案（来自 `error_taxonomy`） | 是 |
| 502 | `ai_empty` | `empty` | `AI 返回了空回答（模型未产出内容），请重试。` | 是 |
| 502 | `ai_truncated` | `truncated` | 输出截断文案 | 否 |
| 502 | `ai_format` | `format` | 输出格式不符文案 | 是 |
| 502 | `ai_internal` | `internal` | 兜底文案（异常分类后的可读原因） | 否 |

**前端三态映射建议**：`ai_no_channel`（503）→「没有可用通道」，引导去设置/登录；
`ai_network`（502）→「网络/代理不通」；`ai_timeout`（504）→「超时」；
`ai_upstream`（502）→「上游模型报错」（与 `ai_network` 同为 `kind=network`、
可重试，但文案明确是上游 5xx，不是本地没送达）；其余按 `retryable` 提供重试按钮。

## 7. 安全与隐私

- prompt 内容与密钥不进响应、不进日志；失败文案经 `sanitize()` 截断；
- `context` 只接受调用方裁剪后的只读字段，服务端不补数据、不抓持仓、不抓论点正文；
- `url` 查询串一律丢弃，避免把 `token=` 之类带进响应与 prompt；
- 控制面既有闸门（回环来源、Host 白名单、写方法来源证据、`Sec-Fetch-Site`）
  全部照旧生效，本端点不另开旁路。

## 8. 变更记录

| 日期 | 版本 | 变更 |
| --- | --- | --- |
| 2026-11-07 | v0.1 | 首版：端点、来源口径、会话记忆、三态降级、中止与错误码 |
| 2026-11-08 | v0.2 | 上游 5xx 细分为 `ai_upstream`（502）；补真实调用、三态降级与 `model` 回显验收 |
| 2026-11-13 | v0.3 | 新增 §1.1「对首页的适用性」：首页不走本控制面端点，改走 `POST /api/page/assistant`；首页单轮无状态、§4 会话记忆不适用；`pending_research` 首页不返回（DEC-083 / QUOT-573） |
