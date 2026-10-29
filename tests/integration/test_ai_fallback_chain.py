"""AI fallback 链与三级降级（M3 §6.3 / issue R2、R3）。

这里走**真实的 `HttpChatClient`**，只把网络层换成 `httpx.MockTransport`：
换模型的判定、同模型重试、纯文本降级、断点续跑都按生产路径执行。
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from ladderstock.ai import checkpoint, fallback
from ladderstock.ai.orchestrator import HttpChatClient, Orchestrator
from ladderstock.ai.structured_output import SECTION_TITLES
from ladderstock.errors import EXIT_AI_DEGRADED, EXIT_OK

DAY = "2026-10-27"
URL_ENV = "TEST_CHAIN_BASE_URL"
KEY_ENV = "TEST_CHAIN_API_KEY"


# --------------------------------------------------------------------------- #
# 假上游
# --------------------------------------------------------------------------- #

def make_config(**overrides: Any) -> fallback.AiConfig:
    sections = {
        name: fallback.SectionConfig(prompt_file=f"prompts/{name}.md", output_schema="")
        for name in checkpoint.SECTIONS
    }
    base: dict[str, Any] = {
        "default_model": "m1",
        "fallback_chain": ("m2", "m3"),
        "base_url_env": URL_ENV,
        "api_key_env": KEY_ENV,
        "structured_output": "prefer",
        "on_all_failed": "degrade_to_text",
        "sections": sections,
    }
    base.update(overrides)
    return fallback.AiConfig(**base)


def _reply(text: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json={"choices": [{"message": {"content": text}}]})


def _json_ok(section: str) -> str:
    return json.dumps({"narrative": [f"{section} 叙述"]}, ensure_ascii=False)


def _section_of(body: dict[str, Any]) -> str:
    user = body["messages"][-1]["content"]
    for name, title in SECTION_TITLES.items():
        if title in user:
            return name
    return "?"


def _orchestrator(handler: Any, config: fallback.AiConfig, root: Any) -> Orchestrator:
    client = HttpChatClient(config, transport=httpx.MockTransport(handler))
    return Orchestrator(config=config, client=client, root=root)


def _metrics() -> dict[str, dict[str, Any]]:
    return {name: {"temperature": 60.0} for name in checkpoint.SECTIONS}


@pytest.fixture()
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(URL_ENV, "http://llm.test/v1")
    monkeypatch.setenv(KEY_ENV, "chain-test-key-not-real")


# --------------------------------------------------------------------------- #
# 用例
# --------------------------------------------------------------------------- #

def test_network_then_quota_then_success(tmp_path: Any, configured: None) -> None:
    """m1 网络失败 → m2 额度受限 → m3 成功：只在 network/quota 时沿链前进。"""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body["model"])
        if body["model"] == "m1":
            return _reply("", status=503)
        if body["model"] == "m2":
            return _reply("", status=429)
        return _reply(_json_ok(_section_of(body)))

    run = _orchestrator(handler, make_config(), tmp_path).run(
        DAY, _metrics(), only="sentiment"
    )

    assert calls == ["m1", "m2", "m3"]
    assert run.exit_code == EXIT_OK
    assert run.models == ["m3"]
    section = run.sections[0]
    assert section["status"] == "ok"
    assert section["mode"] == "structured"
    assert section["model"] == "m3"
    # 指标原样保留，AI 只写进 ai 段
    assert section["metrics"] == {"temperature": 60.0}
    assert section["ai"]["narrative"] == ["sentiment 叙述"]


def test_format_error_retries_same_model_once(tmp_path: Any, configured: None) -> None:
    """格式错误不换模型：同模型重试一次，第二次合法即成功。"""
    calls: list[tuple[str, bool]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        json_mode = "response_format" in body
        calls.append((body["model"], json_mode))
        if json_mode:
            # 第一次给空对象（不可补偿），第二次给合法 JSON
            if len([item for item in calls if item[1]]) == 1:
                return _reply("{}")
            return _reply(_json_ok(_section_of(body)))
        return _reply("纯文本不该被用到")

    run = _orchestrator(handler, make_config(), tmp_path).run(
        DAY, _metrics(), only="sentiment"
    )

    assert calls == [("m1", True), ("m1", True)]
    assert run.exit_code == EXIT_OK
    assert run.sections[0]["status"] == "ok"
    assert run.sections[0]["mode"] == "structured"
    modes = [item["mode"] for item in checkpoint.section_state(
        checkpoint.load(DAY, tmp_path), "sentiment"
    )["attempts"]]
    assert modes == ["structured", "structured"]


def test_all_fail_degrades_to_plain_text(tmp_path: Any, configured: None) -> None:
    """结构化全失败且 on_all_failed=degrade_to_text → 纯文本降级，退出码 9。"""
    calls: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        json_mode = "response_format" in body
        calls.append(json_mode)
        if json_mode:
            return _reply("{}")  # 两次都不可解析，格式错误不换模型
        return _reply("情绪回暖，高位股有分歧。")

    run = _orchestrator(handler, make_config(), tmp_path).run(
        DAY, _metrics(), only="sentiment"
    )

    assert calls == [True, True, False]
    assert run.exit_code == EXIT_AI_DEGRADED
    assert run.degraded is True
    section = run.sections[0]
    assert section["status"] == "degraded"
    assert section["mode"] == "text"
    assert section["degraded"] is True
    assert section["narrative"] == ["情绪回暖，高位股有分歧。"]
    assert run.ai_failures == []


def test_all_fail_policy_fail_marks_section_failed(tmp_path: Any, configured: None) -> None:
    """on_all_failed=fail：整链失败直接记 failed，不伪造叙述。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return _reply("", status=503)

    run = _orchestrator(handler, make_config(on_all_failed="fail"), tmp_path).run(
        DAY, _metrics(), only="sentiment"
    )

    assert run.exit_code == EXIT_AI_DEGRADED
    assert run.failed == ["sentiment"]
    assert run.sections[0]["status"] == "failed"
    assert run.sections[0]["narrative"] == []
    assert run.ai_failures[0]["kind"] == "network"


def test_retry_failed_only_fills_missing_sections(tmp_path: Any, configured: None) -> None:
    """theme 失败后重跑：只补 theme，其余四段从 checkpoint 复用。"""
    state = {"fail_theme": True}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        section = _section_of(body)
        if state["fail_theme"] and section == "theme":
            return _reply("", status=502)
        return _reply(_json_ok(section))

    config = make_config(on_all_failed="fail")
    first = _orchestrator(handler, config, tmp_path).run(DAY, _metrics())

    assert first.failed == ["theme"]
    assert first.exit_code == EXIT_AI_DEGRADED
    assert checkpoint.done_sections(checkpoint.load(DAY, tmp_path)) == [
        "sentiment",
        "capital",
        "dragon_tiger",
        "leader",
    ]

    state["fail_theme"] = False
    asked: list[str] = []

    def recording(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        asked.append(_section_of(body))
        return _reply(_json_ok(_section_of(body)))

    second = _orchestrator(recording, config, tmp_path).run(
        DAY, _metrics(), retry_failed=True
    )

    assert asked == ["theme"]
    assert second.generated == ["theme"]
    assert set(second.reused) == {"sentiment", "capital", "dragon_tiger", "leader"}
    assert second.failed == []
    assert second.exit_code == EXIT_OK


def test_unconfigured_never_opens_socket(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """没配密钥：不构造请求、不崩溃，只做纯文本降级。"""
    monkeypatch.delenv(URL_ENV, raising=False)
    monkeypatch.delenv(KEY_ENV, raising=False)

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - 不应被调用
        raise AssertionError("未配置凭据时不应该发起网络请求")

    run = _orchestrator(handler, make_config(), tmp_path).run(DAY, _metrics())

    assert run.configured is False
    assert run.exit_code == EXIT_AI_DEGRADED
    assert run.degraded is True
    assert all(item["status"] == "degraded" for item in run.sections)
    assert all("未配置" in item["narrative"][0] for item in run.sections)
    blob = json.dumps(run.as_dict(), ensure_ascii=False)
    assert "chain-test-key-not-real" not in blob
