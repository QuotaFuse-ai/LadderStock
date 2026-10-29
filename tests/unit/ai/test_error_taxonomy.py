"""AI 失败四分类：network / quota / format / truncated（M1 验收）。

每个用例都断言三件事：分类正确、提示可行动、摘要脱敏（不含 key、不含完整 prompt）。
"""

from __future__ import annotations

import json

import httpx
import pytest

from ladderstock.ai.error_taxonomy import (
    FAILURE_KINDS,
    AiFailure,
    classify_error,
    sanitize,
)

SECRET = "sk-proj-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
PROMPT = "【完整提示词】你是 A 股复盘助手，请阅读以下 3 万字原始材料……" * 50


def test_network_timeout_exception() -> None:
    failure = classify_error(httpx.ConnectTimeout("connect timeout"))
    assert failure.kind == "network"
    assert failure.retryable is True
    assert "网络" in failure.action


def test_network_from_plain_text_message() -> None:
    """`str(exc)` 这种只剩消息文本的入参也要能认出超时。"""
    failure = classify_error("Connection timed out after 30s")
    assert failure.kind == "network"
    assert "timed out" in failure.detail


def test_network_http_503() -> None:
    failure = classify_error({"status_code": 503, "error": {"message": "upstream unavailable"}})
    assert failure.kind == "network"
    assert failure.retryable is True


def test_quota_429() -> None:
    failure = classify_error(
        {"status_code": 429, "error": {"message": "Rate limit exceeded, please retry later"}}
    )
    assert failure.kind == "quota"
    assert failure.retryable is True
    # 可行动提示：换 fallback 模型 / 稍后重试
    assert "fallback" in failure.action or "稍后重试" in failure.action


def test_quota_message_without_status_code() -> None:
    failure = classify_error(RuntimeError("insufficient_quota: 账户额度已用尽"))
    assert failure.kind == "quota"


def test_format_invalid_json() -> None:
    failure = classify_error('{"temperature": 62, "narrative": "情绪回升"')
    assert failure.kind == "format"
    assert failure.retryable is True
    assert "JSON" in failure.action or "Schema" in failure.action


def test_format_schema_mismatch() -> None:
    content = json.dumps({"narrative": "情绪回升"}, ensure_ascii=False)
    failure = classify_error({"content": content}, required_keys=["narrative", "highlights"])
    assert failure.kind == "format"
    assert "Schema" in failure.message


def test_format_json_schema_violation() -> None:
    schema = {
        "type": "object",
        "required": ["temperature"],
        "properties": {"temperature": {"type": "number"}},
    }
    failure = classify_error({"content": '{"temperature": "冷"}'}, schema=schema)
    assert failure.kind == "format"


def test_truncated_finish_reason_length() -> None:
    response = {"choices": [{"finish_reason": "length", "message": {"content": '{"a": 1'}}]}
    failure = classify_error(response)
    assert failure.kind == "truncated"
    # 原样重试只会再截断：不可重试，提示调整参数
    assert failure.retryable is False
    assert "max_tokens" in failure.action


def test_truncated_by_output_text() -> None:
    failure = classify_error("finish_reason=length")
    assert failure.kind == "truncated"


def test_unknown_failure_is_internal_not_misfiled() -> None:
    failure = classify_error(ValueError("boom"))
    assert failure.kind == "internal"
    assert failure.retryable is False


@pytest.mark.parametrize("kind", FAILURE_KINDS)
def test_every_kind_has_action_and_message(kind: str) -> None:
    samples = {
        "network": "request timed out",
        "quota": "HTTP 429 Too Many Requests",
        "format": "not json at all",
        "truncated": "finish_reason=length",
    }
    failure = classify_error(samples[kind])
    assert failure.kind == kind
    assert failure.action and failure.message
    assert failure.as_dict()["kind"] == kind


# --------------------------------------------------------------------------- #
# 脱敏
# --------------------------------------------------------------------------- #

def test_message_never_leaks_api_key() -> None:
    failure = classify_error(RuntimeError(f"401 unauthorized: api_key={SECRET}"))
    assert failure.kind == "internal"
    assert SECRET not in failure.message
    assert SECRET not in failure.detail
    assert "***" in failure.detail


def test_prompt_body_never_enters_summary() -> None:
    # 响应对象里带着完整 prompt 和超长正文，摘要只允许取错误描述
    response = {
        "status_code": 502,
        "prompt": PROMPT,
        "error": {"message": "bad gateway"},
        "choices": [{"message": {"content": PROMPT}}],
    }
    failure = classify_error(response)
    assert failure.kind == "network"
    assert "完整提示词" not in failure.message
    assert len(failure.detail) <= 240


def test_sanitize_masks_tokens_and_url_query() -> None:
    text = (
        f"call failed api_key={SECRET}&token=deadbeefdeadbeef "
        "Authorization: Bearer abcdefgh12345678 "
        "https://api.example.com/v1/chat?key=secretvalue"
    )
    cleaned = sanitize(text)
    assert SECRET not in cleaned
    assert "deadbeef" not in cleaned
    assert "abcdefgh12345678" not in cleaned
    assert "secretvalue" not in cleaned
    assert "***" in cleaned


def test_sanitize_collapses_and_truncates() -> None:
    cleaned = sanitize("第一行\n\n第二行\t第三行 " + "长" * 400, max_len=40)
    assert "\n" not in cleaned
    assert len(cleaned) <= 40
    assert cleaned.endswith("…")


# --------------------------------------------------------------------------- #
# 不得误伤正常复盘文本
# --------------------------------------------------------------------------- #

def test_market_narrative_with_429_is_not_quota() -> None:
    """模型正文里的“429 家下跌”不能被当成额度错误。"""
    response = {"content": "今日上涨 429 家、下跌 3100 家，情绪温度 62。"}
    assert classify_error(response).kind != "quota"


def test_theme_name_with_network_is_not_network_failure() -> None:
    response = {"content": "网络游戏板块领涨，龙头 3 连板。"}
    failure = classify_error(response, required_keys=["temperature"])
    assert failure.kind == "format"


def test_ai_failure_round_trips_as_json() -> None:
    failure = classify_error(httpx.ReadTimeout("read timeout"))
    assert isinstance(failure, AiFailure)
    payload = json.loads(json.dumps(failure.as_dict(), ensure_ascii=False))
    assert set(payload) == {"kind", "message", "action", "retryable", "detail"}
