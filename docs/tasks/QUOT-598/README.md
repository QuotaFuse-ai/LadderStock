# QUOT-598 战略 C2 注册表工具调用环

C1 的注册表（`ai/tool_registry.py`，跨 peer 发现 `ToolEntry`）此前只被用来拼接工具说明，
没有任何一条路径真正"调用"它：board/research 的模型拿到的 `tools.execute` 只认
`ai/tools.py` 里的常量工具表。本单把 **read_only 白名单内的注册表工具**接进同一条
工具循环，并让每一次真实调用在 SSE 台账与 cog 事件总线上各留一条痕。

```text
模型 -> tool_descriptors(含注册表条目) -> tools.execute
     -> [白名单/namespace 闸] -> RegistryTools.execute
     -> HttpMcpTransport.call(peer, tool, params) -> 文本化 -> 审计 + cog StageSample
```

## 改动

| 文件 | 内容 |
|---|---|
| `src/ladderstock/ai/registry_tools.py`（新增） | `callable_entries()` 只留 `read_only is True`（`None`/`False` 一律拒）；`registry_descriptors()` 与 `tools.tool_descriptors()` 同形；`check_not_future()` 把 `date/until/month` 夹到 `min(分析基准日, 今天)`；`build_for_stage()` 校验 `ToolNamespace(agent_id, kb_scope)`；`RegistryTools.execute()` 经 `HttpMcpTransport.call()` 执行并返回文本；`sample_for()` 生成 `module_class="cog"`、`phase="tool_call"` 的 `StageSample` |
| `src/ladderstock/ai/tools.py` | `ToolContext.registry`；`tool_descriptors/tool_instruction/descriptors_from_ctx` 接受 registry；`execute()` 对非常量名走 `_execute_registry`，拒绝与成功都走原有 `_audit`（9 键同形），**没有第二套鉴权/审计** |
| `src/ladderstock/web/chat.py` | `stream_turn(registry=, event_bus=)`；每次真实调用在 `event: status` 台账里追加一对 `action:"tool"`（running/done），`item_id=tool:<stage>/<tool>`，拒绝帧带 `rejected:true` 与原因码 |
| `src/ladderstock/web/control.py` | 仅在流式问答且开关打开时构建 `RegistryTools(stage="chat")`，透传 `plane.selfheal.event_bus`；构建异常 → `None`，问答照常 |
| `src/ladderstock/research/pipeline.py` | `_laddered()` 在开关打开时并入注册表工具名；`_on_registry_call` → `sample_for` → `publish_sample(event_bus)` |
| `src/ladderstock/research/ladder.py` | 1 行：`descriptors_from_ctx(ctx, ...)`，`_tool_loop` 骨架未动 |
| `src/ladderstock/research/stages.py` | `build_messages(registry=)` 转发给 `tool_instruction` |

`assistant.py` / `answer_blocks.py` 未改；role_llms 语义未动；**StockSenate 零改动**。

## 闸门与红线

- **namespace 闸**：快照的 `ToolNamespace` 必须等于当前 `(agent_id, kb_scope)`，否则整批不可用；
- **stage 白名单闸**：名字不在 `ctx.allowed` 内（含模型编造的名字）走 `tools.execute` 原有越权路径
  （审计 + `UnauthorizedToolCall`），不重试；
- **不看未来**：`date/until/month` 超过 `min(基准日, 今天)` → 拒绝 + 审计，原因码 `future_date`；
- **不盘内再采**：`EXCLUDED_TOOLS`（`ladder_get_watch`/`news_stream`/`event_calendar`/`proxyfleet_status`）
  即使 read_only 也排除，测试钉死；
- **开关默认关**：`LDST_REGISTRY_TOOLS` 关闭时描述符与提示词逐字节不变（测试比对）；
- **事件只来自真实结果**：拒绝帧只发 `warn` + 原因码，不伪造成功；零调用零帧。

## 开关

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `LDST_REGISTRY_TOOLS` | 关 | 打开后 board/research/chat 暴露 read_only 注册表工具 |
| `LDST_REGISTRY_NAMESPACE` | `ladderstock-board/*` | `<agent_id>/<kb_scope>`，两者都支持 `*` 通配 |

## 验证

- `tests/unit/ai/test_registry_tools.py`（26 例）+ `tests/unit/web/test_chat_stream.py`（32 例）全绿；
- `tests/unit/ai` + `tests/unit/research` + `tests/unit/web` 全部 exit 0；
- `ruff check src tests` All checks passed；mypy 7 个改动文件无问题；
- 全程注入 `FakeTransport`，测试不触网。
