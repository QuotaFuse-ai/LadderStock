"""Provider 适配层（QUOT-236）：协议差异 + 错误归类。

只验证「适配层翻译是否准确」：请求形状 / 鉴权头 / 截断标记 / HTTP 状态码
能被 `error_taxonomy.classify_error` 归到正确类别。重试与换模型策略不在这里。
"""

from __future__ import annotations

import json

import httpx
import pytest

from ladderstock.ai import fallback
from ladderstock.ai.error_taxonomy import classify_error
from ladderstock.ai.fallback import BackendConfig, ModelTarget
from ladderstock.ai.providers import (
    UnconfiguredError,
    provider_for,
)

URL_ENV = "TEST_PROVIDER_BASE_URL"
KEY_ENV = "TEST_PROVIDER_API_KEY"


@pytest.fixture()
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(URL_ENV, "https://example.test")
    monkeypatch.setenv(KEY_ENV, "test-key")


def _target(kind: str = "openai", model: str = "m-1") -> ModelTarget:
    backend = BackendConfig(
        id=kind,
        kind=kind,
        base_url_env=URL_ENV,
        api_key_env=KEY_ENV,
        models=(model,),
    )
    return ModelTarget(backend=backend, model=model, label=f"{kind}:{model}")


def _anthropic_reply(text: str, *, stop_reason: str = "end_turn") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "stop_reason": stop_reason,
            "content": [{"type": "text", "text": text}],
        },
    )


def _openai_reply(text: str, *, finish_reason: str = "stop") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "choices": [
                {"index": 0, "finish_reason": finish_reason, "message": {"content": text}}
            ]
        },
    )


# --------------------------------------------------------------------------- #
# Anthropic 协议
# --------------------------------------------------------------------------- #

def test_anthropic_request_shape_and_json_instruction(env: None) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _anthropic_reply("ok")

    text = provider_for("anthropic").complete(
        _target("anthropic", "claude-x"),
        "系统提示",
        "用户输入",
        json_mode=True,
        transport=httpx.MockTransport(handler),
    )

    assert text == "ok"
    request = seen[0]
    assert request.url == "https://example.test/v1/messages"
    assert request.headers["x-api-key"] == "test-key"
    assert request.headers["anthropic-version"] == "2023-06-01"
    body = json.loads(request.content)
    assert body["model"] == "claude-x"
    assert body["system"].startswith("系统提示")
    assert "JSON" in body["system"]  # JSON 模式靠 system 指令，Anthropic 没有 response_format
    assert "response_format" not in body
    assert body["messages"] == [{"role": "user", "content": "用户输入"}]
    assert body["max_tokens"] > 0


def test_anthropic_base_url_already_v1_is_not_duplicated(
    env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(URL_ENV, "https://example.test/v1")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _anthropic_reply("ok")

    provider_for("anthropic").complete(
        _target("anthropic"),
        "s",
        "u",
        json_mode=False,
        transport=httpx.MockTransport(handler),
    )
    assert seen[0].url == "https://example.test/v1/messages"


def test_anthropic_max_tokens_is_classified_truncated(env: None) -> None:
    transport = httpx.MockTransport(
        lambda request: _anthropic_reply("半截", stop_reason="max_tokens")
    )
    with pytest.raises(Exception) as caught:
        provider_for("anthropic").complete(
            _target("anthropic"), "s", "u", json_mode=False, transport=transport
        )
    assert classify_error(caught.value).kind == "truncated"


# --------------------------------------------------------------------------- #
# OpenAI 协议
# --------------------------------------------------------------------------- #

def test_openai_request_shape(env: None) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return _openai_reply("ok")

    text = provider_for("openai").complete(
        _target("openai"),
        "s",
        "u",
        json_mode=True,
        transport=httpx.MockTransport(handler),
    )
    assert text == "ok"
    request = seen[0]
    assert request.url == "https://example.test/chat/completions"
    assert request.headers["Authorization"] == "Bearer test-key"
    body = json.loads(request.content)
    assert body["response_format"] == {"type": "json_object"}
    assert [item["role"] for item in body["messages"]] == ["system", "user"]


def test_openai_length_finish_is_classified_truncated(env: None) -> None:
    transport = httpx.MockTransport(
        lambda request: _openai_reply("半截", finish_reason="length")
    )
    with pytest.raises(Exception) as caught:
        provider_for("openai").complete(
            _target("openai"), "s", "u", json_mode=False, transport=transport
        )
    assert classify_error(caught.value).kind == "truncated"


# --------------------------------------------------------------------------- #
# 错误映射
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("status", "kind"),
    [(429, "quota"), (503, "network"), (500, "network"), (401, "internal")],
)
def test_http_status_maps_to_failure_kind(env: None, status: int, kind: str) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(status, json={"error": {"message": "nope"}})
    )
    with pytest.raises(Exception) as caught:
        provider_for("openai").complete(
            _target("openai"), "s", "u", json_mode=False, transport=transport
        )
    failure = classify_error(caught.value)
    assert failure.kind == kind
    # 错误信息里不出现密钥
    assert "test-key" not in failure.message


def test_missing_credentials_is_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(URL_ENV, raising=False)
    monkeypatch.delenv(KEY_ENV, raising=False)
    with pytest.raises(UnconfiguredError):
        provider_for("openai").complete(
            _target("openai"), "s", "u", json_mode=False, transport=httpx.MockTransport(
                lambda request: _openai_reply("never")
            )
        )


def test_unknown_provider_kind_rejected() -> None:
    with pytest.raises(ValueError):
        provider_for("workbuddy")


# --------------------------------------------------------------------------- #
# 配置解析：backends[] / 链式标签 / 旧配置兼容
# --------------------------------------------------------------------------- #

def _write_ai(tmp_path, body: str) -> None:
    (tmp_path / "ai.yaml").write_text(body, encoding="utf-8")


SECTIONS = """
  sections:
    sentiment: {prompt_file: p/s.md, output_schema: S}
    capital: {prompt_file: p/c.md, output_schema: C}
    theme: {prompt_file: p/t.md, output_schema: T}
    dragon_tiger: {prompt_file: p/d.md, output_schema: D}
    leader: {prompt_file: p/l.md, output_schema: L}
"""


def test_multi_backend_chain_labels_and_resolution(tmp_path, monkeypatch) -> None:
    _write_ai(
        tmp_path,
        """
ai:
  backends:
    - id: oa
      type: openai
      base_url_env: OA_URL
      api_key_env: OA_KEY
      models: [m-small, m-big]
    - id: an
      type: anthropic
      base_url_env: AN_URL
      api_key_env: AN_KEY
      models: [claude-x]
  default_model: oa:m-small
  fallback_chain: [oa:m-big, an:claude-x, oa:m-small]
"""
        + SECTIONS,
    )
    config = fallback.load_config(tmp_path)
    assert config.models == ("oa:m-small", "oa:m-big", "an:claude-x")

    target = config.resolve("an:claude-x")
    assert target.backend.kind == "anthropic"
    assert target.model == "claude-x"
    # 裸模型名按声明顺序落到第一个包含它的后端
    assert config.resolve("claude-x").label == "an:claude-x"

    monkeypatch.setenv("OA_URL", "https://oa.test/v1")
    monkeypatch.setenv("OA_KEY", "k")
    # 只有 openai 凭据齐全：Anthropic 档位静默跳过
    assert fallback.model_chain(config) == ["oa:m-small", "oa:m-big"]
    assert fallback.is_configured(config) is True
    assert set(fallback.missing_env(config)) == {"AN_URL", "AN_KEY"}


def test_backends_none_configured_is_not_configured(tmp_path, monkeypatch) -> None:
    _write_ai(
        tmp_path,
        """
ai:
  backends:
    - {id: an, type: anthropic, models: [claude-x]}
  default_model: an:claude-x
  fallback_chain: []
"""
        + SECTIONS,
    )
    monkeypatch.delenv("LDST_LLM_ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("LDST_LLM_ANTHROPIC_API_KEY", raising=False)
    config = fallback.load_config(tmp_path)
    assert fallback.model_chain(config) == []
    assert fallback.is_configured(config) is False
    assert "LDST_LLM_ANTHROPIC_API_KEY" in fallback.missing_env(config)


def test_legacy_config_still_readable(tmp_path, monkeypatch) -> None:
    _write_ai(
        tmp_path,
        """
ai:
  default_model: user-model
  fallback_chain: [model-a]
  base_url_env: LEGACY_URL
  api_key_env: LEGACY_KEY
"""
        + SECTIONS,
    )
    monkeypatch.setenv("LEGACY_URL", "https://legacy.test/v1")
    monkeypatch.setenv("LEGACY_KEY", "k")
    config = fallback.load_config(tmp_path)
    # 旧配置的标签保持裸模型名，不改变既有行为
    assert config.models == ("user-model", "model-a")
    assert fallback.model_chain(config) == ["user-model", "model-a"]
    assert config.resolve("model-a").backend.kind == "openai"


@pytest.mark.parametrize(
    "bad",
    [
        "ai: {fallback_chain: [], sections: {}}",
        "ai: {backends: [{id: x, type: wizard, models: [m]}], fallback_chain: []}",
        "ai: {backends: [{id: x, type: openai, models: []}], fallback_chain: []}",
        "ai: {backends: [{id: x, type: openai, models: [m]}],"
        " default_model: nope:m, fallback_chain: []}",
    ],
)
def test_invalid_config_raises_boundary_violation(tmp_path, bad: str) -> None:
    from ladderstock.core.boundary import BoundaryViolation

    _write_ai(tmp_path, bad)
    with pytest.raises(BoundaryViolation):
        fallback.load_config(tmp_path)


def test_status_document_never_echoes_secret(tmp_path, monkeypatch) -> None:
    _write_ai(
        tmp_path,
        """
ai:
  backends:
    - {id: oa, type: openai, models: [m]}
  default_model: oa:m
  fallback_chain: []
"""
        + SECTIONS,
    )
    monkeypatch.setenv("LDST_LLM_BASE_URL", "https://oa.test/v1")
    monkeypatch.setenv("LDST_LLM_API_KEY", "sk-super-secret")
    document = fallback.status_document(fallback.load_config(tmp_path))
    assert document["backends"][0]["api_key_set"] is True
    assert "sk-super-secret" not in json.dumps(document, ensure_ascii=False)
