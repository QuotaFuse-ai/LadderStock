"""研究段的三级降级阶梯（R1-2）+ 受控工具循环（R2）。

`结构化 JSON + Schema → 文本补偿 → 纯文本`，mode 取值与五段复盘**完全同名**
（`structured` / `parsed` / `text`），下游（`research_status`、R8 页面）只需要认一套词表。

与 `ai/orchestrator.py` 那条阶梯的唯一差别：**格式错误不同模型重试一次**。
这里每个模型在结构化一级只请求一次，解析失败就立刻从同一段返回文本里抢救，
抢救不动才落到纯文本。理由：个股六段每段都要消耗额度，格式重试换不来更高的
成功率，只会让 `--only` 单段重跑变慢；验收也只要求三级可区分、末级标 `degraded`。

R2（QUOT-140）：调用方传入 `ctx`（本段的工具上下文）且客户端实现了
`complete_with_tools` 时，结构化一级改走**工具循环**——模型只能看到本段白名单里
的工具，越权调用第一次就抛 `UnauthorizedToolCall`，该段直接判 `failed`，
**不重试、不换模型、也不进纯文本降级**（白名单是边界，边界不靠重试解决）。
不传 `ctx` 时这条阶梯与 R2 之前逐字一致。

段落名与五段复盘不同，`structured_output.schema_for()` 认不出研究段，
所以这里的校验是本模块的 `_validate`，Schema 仍复用同一个 `SECTION_SCHEMA`。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from ladderstock.ai import fallback, structured_output, tools
from ladderstock.ai.error_taxonomy import AiFailure, classify_error
from ladderstock.ai.orchestrator import ChatClient, ToolChatClient, supports_tools
from ladderstock.ai.providers import ToolCall, ToolTurn
from ladderstock.research import checkpoint, stages

#: 一次段内生成最多几轮工具。到顶就改用不带工具的单次补全收口，
#: 不给模型「在工具里反复打转」的机会（每轮都烧额度，且对成文没有增益）。
MAX_TOOL_TURNS = 3


def generate(
    client: ChatClient,
    config: fallback.AiConfig,
    stage: str,
    *,
    messages: tuple[str, str],
    plain: tuple[str, str],
    ctx: tools.ToolContext | None = None,
) -> dict[str, Any]:
    """跑一段的三级阶梯，返回段结果（未过边界闸门，由调用方 merge）。"""
    models = fallback.model_chain(config)
    attempts: list[dict[str, Any]] = []
    last_failure: AiFailure | None = None

    try:
        if config.structured_output != "off":
            payload, failure = _structured_chain(
                client, config, stage, models, messages, attempts, ctx=ctx
            )
            if payload is not None:
                return _ok(payload, attempts)
            last_failure = failure

        if config.on_all_failed == "degrade_to_text":
            payload, failure = _plain_chain(client, models, plain, attempts)
            if payload is not None:
                note = "纯文本降级：结构化一级未通过，本段改用纯文本生成并标记 degraded"
                if last_failure is not None:
                    note = f"{note}（一级失败原因：{last_failure.kind}）"
                return {
                    "status": "degraded",
                    "mode": "text",
                    "model": attempts[-1]["model"] if attempts else None,
                    "degraded": True,
                    "narrative": payload["narrative"],
                    "extras": _extras(payload),
                    "notes": [note],
                    "failure": None,
                    "attempts": attempts,
                }
            last_failure = failure or last_failure
    except tools.UnauthorizedToolCall as exc:
        # 越权 = 碰了本段白名单之外的工具：该段失败，序列其余段不受影响
        return _unauthorized(stage, exc, attempts)

    failure = last_failure or AiFailure(
        kind="internal",
        message=f"{stage} 段未产出可用叙述",
        action="检查模型可用性与提示词，再 ldst research --only 重跑该段",
        retryable=True,
    )
    return {
        "status": "failed",
        "mode": "failed",
        "model": None,
        "degraded": False,
        "narrative": [],
        "extras": {},
        "notes": [],
        "failure": failure,
        "attempts": attempts,
    }


def _unauthorized(
    stage: str, exc: tools.UnauthorizedToolCall, attempts: list[dict[str, Any]]
) -> dict[str, Any]:
    """越权尝试的段结果：`failed` + 不可重试 + 点名允许范围。"""
    title = checkpoint.STAGE_TITLES.get(stage, stage)
    failure = AiFailure(
        kind="internal",
        message=str(exc),
        action=(
            f"该段提示词或白名单需要复核：{title}段只允许"
            f"{'、'.join(exc.allowed) or '没有'}这些工具，其余一律不可调用"
        ),
        retryable=False,
        detail=f"tool={exc.tool} stage={exc.stage}",
    )
    return {
        "status": "failed",
        "mode": "failed",
        "model": None,
        "degraded": False,
        "narrative": [],
        "extras": {},
        "notes": [f"越权工具调用：{exc.tool}（审计见 tool_calls.jsonl）"],
        "failure": failure,
        "attempts": attempts,
    }


# --------------------------------------------------------------------------- #
# 第一、二级：结构化 JSON，失败就地从同一份返回文本里抢救
# --------------------------------------------------------------------------- #


def _structured_chain(
    client: ChatClient,
    config: fallback.AiConfig,
    stage: str,
    models: list[str],
    messages: tuple[str, str],
    attempts: list[dict[str, Any]],
    *,
    ctx: tools.ToolContext | None = None,
) -> tuple[dict[str, Any] | None, AiFailure | None]:
    system, user = messages
    last: AiFailure | None = None
    for model in models:
        try:
            if ctx is not None and ctx.allowed and supports_tools(client):
                text = _tool_loop(client, model, system, user, ctx)
            else:
                text = client.complete(model, system, user, json_mode=True)
        except tools.UnauthorizedToolCall:
            raise
        except Exception as exc:  # noqa: BLE001 - 分类后按段隔离
            failure = classify_error(exc)
            attempts.append(_attempt(model, "structured", failure))
            last = failure
            # 只有网络 / 额度错误才换模型；格式错误交给第二级抢救
            if not fallback.should_switch(failure):
                break
            continue

        try:
            payload = _validate(stage, structured_output.parse_json(text))
        except structured_output.StructuredOutputError as exc:
            failure = classify_error(exc, schema=stages.STAGE_SCHEMA)
            attempts.append(_attempt(model, "structured", failure))
            compensated = _compensate(text, stage)
            if compensated is not None:
                attempts.append(_attempt_ok(model, "parsed"))
                return compensated, None
            # 抢救不回来就到此为止：格式错误换模型也修不好，只多烧额度
            return None, failure
        attempts.append(_attempt_ok(model, "structured"))
        return payload, None
    return None, last


def _tool_loop(
    client: ToolChatClient,
    model: str,
    system: str,
    user: str,
    ctx: tools.ToolContext,
) -> str:
    """带白名单工具的结构化一级：请求→执行→回喂，直到模型出正文。

    越权调用由 `tools.execute` 抛 `UnauthorizedToolCall`，这里不吞、不重试。
    """
    history: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    descriptors = tools.tool_descriptors(sorted(ctx.allowed))
    ctx.model = model
    for _ in range(MAX_TOOL_TURNS):
        turn = client.complete_with_tools(model, history, descriptors, json_mode=True)
        if not turn.tool_calls:
            return turn.text
        history.append(_assistant_turn(turn))
        for request in turn.tool_calls:
            outcome = tools.execute(ctx, request.name, request.arguments)
            history.append(
                {"role": "tool", "tool_call_id": request.id, "content": outcome["content"]}
            )
    # 轮次用尽仍只想调工具：回到不带工具的单次补全收口，别把这段吊死
    return client.complete(model, system, user, json_mode=True)


def _assistant_turn(turn: ToolTurn) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": turn.text,
        "tool_calls": [_tool_call_payload(call) for call in turn.tool_calls],
    }


def _tool_call_payload(call: ToolCall) -> dict[str, Any]:
    arguments = call.arguments
    if not isinstance(arguments, str):
        clean = arguments if isinstance(arguments, Mapping) else {}
        arguments = json.dumps(clean, ensure_ascii=False)
    return {
        "id": call.id,
        "type": "function",
        "function": {"name": call.name, "arguments": arguments},
    }


def _plain_chain(
    client: ChatClient,
    models: list[str],
    plain: tuple[str, str],
    attempts: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, AiFailure | None]:
    system, user = plain
    last: AiFailure | None = None
    for model in models:
        try:
            text = client.complete(model, system, user, json_mode=False)
        except Exception as exc:  # noqa: BLE001
            failure = classify_error(exc)
            attempts.append(_attempt(model, "text", failure))
            last = failure
            if not fallback.should_switch(failure):
                break
            continue
        payload = structured_output.plain_payload(text)
        if payload is not None:
            attempts.append(_attempt_ok(model, "text"))
            return payload, None
    return None, last


# --------------------------------------------------------------------------- #
# 校验与抢救
# --------------------------------------------------------------------------- #


def _validate(stage: str, payload: dict[str, Any]) -> dict[str, Any]:
    """叙述字段白名单 + Schema；越界即抛，交给第二级抢救。"""
    from jsonschema import Draft202012Validator

    illegal = set(payload) - structured_output.MODEL_WRITABLE
    if illegal:
        raise structured_output.StructuredOutputError(
            "AI 输出含非叙述字段：" + ", ".join(sorted(illegal)) + "（数字必须由程序计算）"
        )
    errors = sorted(
        Draft202012Validator(stages.STAGE_SCHEMA).iter_errors(dict(payload)),
        key=lambda err: list(err.path),
    )
    if errors:
        raise structured_output.StructuredOutputError(
            f"不符合 {stage} Schema：{errors[0].message}"
        )
    narrative = _strings(payload.get("narrative"))
    if not narrative:
        raise structured_output.StructuredOutputError("narrative 不能为空")
    cleaned: dict[str, Any] = {"narrative": narrative}
    cleaned.update(_extras(payload))
    return cleaned


def _compensate(text: str, stage: str) -> dict[str, Any] | None:
    """第二级：从解析失败的返回文本里抢救叙述，抢救结果同样要过校验。"""
    compensated = structured_output.compensate(text)
    if compensated is None:
        return None
    try:
        return _validate(stage, compensated)
    except structured_output.StructuredOutputError:
        return None


def _extras(payload: dict[str, Any]) -> dict[str, Any]:
    extras: dict[str, Any] = {}
    for key in ("highlights", "risks", "evidence"):
        values = _strings(payload.get(key))
        if values:
            extras[key] = values
    return extras


def _strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value)]


def _ok(payload: dict[str, Any], attempts: list[dict[str, Any]]) -> dict[str, Any]:
    mode = "parsed" if attempts and attempts[-1].get("mode") == "parsed" else "structured"
    return {
        "status": "ok",
        "mode": mode,
        "model": attempts[-1]["model"] if attempts else None,
        "degraded": False,
        "narrative": payload["narrative"],
        "extras": _extras(payload),
        "notes": [],
        "failure": None,
        "attempts": attempts,
    }


def _attempt(model: str, mode: str, failure: AiFailure) -> dict[str, Any]:
    return {"model": model, "mode": mode, "kind": failure.kind, "message": failure.message}


def _attempt_ok(model: str, mode: str) -> dict[str, Any]:
    return {"model": model, "mode": mode, "kind": None}


__all__ = ["generate"]
