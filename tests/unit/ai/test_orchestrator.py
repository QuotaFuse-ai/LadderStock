"""五段编排状态机：失败隔离、断点续跑、三级降级（M3 §6.3）。

所有用例都用脚本化假客户端，不拨网络；`root` 指向 tmp_path，
checkpoint 不落进仓库 `data/`。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest

from ladderstock.ai import checkpoint, fallback
from ladderstock.ai.orchestrator import Orchestrator
from ladderstock.ai.structured_output import SECTION_TITLES

DAY = "2026-10-27"
URL_ENV = "TEST_AI_BASE_URL"
KEY_ENV = "TEST_AI_API_KEY"

Script = Callable[[str, str, str, bool], Any]


@dataclass
class ScriptedClient:
    """按 (model, section, json_mode) 返回文本或抛异常。"""

    script: Script
    calls: list[tuple[str, str, bool]] = field(default_factory=list)

    def complete(self, model: str, system: str, user: str, *, json_mode: bool) -> str:
        section = _section_of(user)
        self.calls.append((model, section, json_mode))
        outcome = self.script(model, section, user, json_mode)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def _section_of(user: str) -> str:
    for name, title in SECTION_TITLES.items():
        if title in user:
            return name
    return "?"


def _json_ok(section: str) -> str:
    return json.dumps({"narrative": [f"{section} 叙述"]}, ensure_ascii=False)


def make_config(**overrides: Any) -> fallback.AiConfig:
    sections = {
        name: fallback.SectionConfig(prompt_file=f"prompts/{name}.md", output_schema="")
        for name in checkpoint.SECTIONS
    }
    base: dict[str, Any] = {
        "default_model": "m1",
        "fallback_chain": ("m2",),
        "base_url_env": URL_ENV,
        "api_key_env": KEY_ENV,
        "structured_output": "prefer",
        "on_all_failed": "degrade_to_text",
        "sections": sections,
    }
    base.update(overrides)
    return fallback.AiConfig(**base)


@pytest.fixture()
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(URL_ENV, "http://127.0.0.1:1/v1")
    monkeypatch.setenv(KEY_ENV, "test-key-not-real")


def _metrics() -> dict[str, dict[str, Any]]:
    return {name: {"temperature": 60.0} for name in checkpoint.SECTIONS}


def _all_ok(model: str, section: str, user: str, json_mode: bool) -> str:
    return _json_ok(section)


def test_all_sections_structured_ok(tmp_path, configured) -> None:
    client = ScriptedClient(_all_ok)
    run = Orchestrator(config=make_config(), client=client, root=tmp_path).run(DAY, _metrics())

    assert run.failed == []
    assert run.degraded is False
    assert run.exit_code == 0
    assert run.models == ["m1"]
    assert [item["status"] for item in run.sections] == ["ok"] * 5
    assert all(item["mode"] == "structured" for item in run.sections)
    assert checkpoint.is_complete(checkpoint.load(DAY, tmp_path))
    assert run.checkpoint_path == str(checkpoint.checkpoint_path(DAY, tmp_path))


def test_failed_section_keeps_previous_results(tmp_path, configured) -> None:
    def script(model: str, section: str, user: str, json_mode: bool) -> Any:
        if section == "theme":
            return httpx.ConnectError("boom")
        return _json_ok(section)

    client = ScriptedClient(script)
    orchestrator = Orchestrator(config=make_config(), client=client, root=tmp_path)
    run = orchestrator.run(DAY, _metrics())

    assert run.failed == ["theme"]
    assert run.exit_code == 9
    statuses = {item["name"]: item["status"] for item in run.sections}
    assert statuses == {
        "sentiment": "ok",
        "capital": "ok",
        "theme": "failed",
        "dragon_tiger": "ok",
        "leader": "ok",
    }
    # 前序成果已落盘：失败段不影响其他段
    document = checkpoint.load(DAY, tmp_path)
    assert checkpoint.done_sections(document) == [
        "sentiment",
        "capital",
        "dragon_tiger",
        "leader",
    ]
    assert checkpoint.failed_sections(document) == ["theme"]


def test_retry_failed_only_reruns_failed_section(tmp_path, configured) -> None:
    def failing(model: str, section: str, user: str, json_mode: bool) -> Any:
        if section == "theme":
            return httpx.ConnectError("boom")
        return _json_ok(section)

    Orchestrator(config=make_config(), client=ScriptedClient(failing), root=tmp_path).run(
        DAY, _metrics()
    )

    good = ScriptedClient(_all_ok)
    run = Orchestrator(config=make_config(), client=good, root=tmp_path).run(
        DAY, _metrics(), retry_failed=True
    )

    assert run.generated == ["theme"]
    assert set(run.reused) == {"sentiment", "capital", "dragon_tiger", "leader"}
    assert run.failed == []
    assert run.exit_code == 0
    # 成功段落不重跑：模型只被问过 theme
    assert {section for _model, section, _json in good.calls} == {"theme"}
    assert checkpoint.is_complete(checkpoint.load(DAY, tmp_path))


def test_illegal_json_falls_back_to_plain_text(tmp_path, configured) -> None:
    def script(model: str, section: str, user: str, json_mode: bool) -> str:
        return "{}"

    run = Orchestrator(
        config=make_config(), client=ScriptedClient(script), root=tmp_path
    ).run(DAY, _metrics(), only="sentiment")

    assert run.degraded is True
    assert run.exit_code == 9
    section = run.sections[0]
    assert section["status"] == "degraded"
    assert section["mode"] == "text"
    assert section["narrative"] == ["{}"]
    # 格式错误先同模型重试一次，再走纯文本
    assert [attempt["mode"] for attempt in _attempts(tmp_path)] == [
        "structured",
        "structured",
        "text",
    ]


def test_format_error_does_not_switch_model(tmp_path, configured) -> None:
    def script(model: str, section: str, user: str, json_mode: bool) -> str:
        if model == "m1":
            return "{}" if json_mode else "情绪回暖，注意高位断层。"
        return _json_ok(section)

    client = ScriptedClient(script)
    Orchestrator(config=make_config(), client=client, root=tmp_path).run(
        DAY, _metrics(), only="sentiment"
    )

    models = {model for model, _section, _json in client.calls}
    assert models == {"m1"}, "格式错误不得换模型（只有 network / quota 才换）"


def test_network_error_switches_to_fallback_model(tmp_path, configured) -> None:
    def script(model: str, section: str, user: str, json_mode: bool) -> Any:
        if model == "m1":
            return httpx.ConnectError("connection reset")
        return _json_ok(section)

    client = ScriptedClient(script)
    run = Orchestrator(config=make_config(), client=client, root=tmp_path).run(
        DAY, _metrics(), only="sentiment"
    )

    assert run.exit_code == 0
    assert run.models == ["m2"]
    assert run.sections[0]["model"] == "m2"
    assert client.calls == [("m1", "sentiment", True), ("m2", "sentiment", True)]


def test_all_failed_with_fail_policy(tmp_path, configured) -> None:
    def script(model: str, section: str, user: str, json_mode: bool) -> Any:
        return httpx.ConnectError("down")

    run = Orchestrator(
        config=make_config(on_all_failed="fail"),
        client=ScriptedClient(script),
        root=tmp_path,
    ).run(DAY, _metrics(), only="sentiment")

    assert run.failed == ["sentiment"]
    assert run.exit_code == 9
    section = run.sections[0]
    assert section["status"] == "failed"
    assert section["mode"] == "failed"
    assert run.ai_failures[0]["kind"] == "network"
    assert "重试" in run.ai_failures[0]["action"] or "ai retry" in section["notes"][0]


def test_unconfigured_degrades_without_network(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(URL_ENV, raising=False)
    monkeypatch.delenv(KEY_ENV, raising=False)

    client = ScriptedClient(_all_ok)
    run = Orchestrator(config=make_config(), client=client, root=tmp_path).run(DAY, _metrics())

    assert client.calls == []
    assert run.configured is False
    assert run.degraded is True
    assert run.exit_code == 9
    assert all(item["status"] == "degraded" for item in run.sections)
    assert all("未配置" in item["narrative"][0] for item in run.sections)
    # 密钥名可以出现在提示里，但绝不能回显任何密钥值
    assert "test-key-not-real" not in json.dumps(run.as_dict(), ensure_ascii=False)


def test_resume_reuses_completed_sections(tmp_path, configured) -> None:
    Orchestrator(config=make_config(), client=ScriptedClient(_all_ok), root=tmp_path).run(
        DAY, _metrics(), only="sentiment"
    )

    client = ScriptedClient(_all_ok)
    run = Orchestrator(config=make_config(), client=client, root=tmp_path).run(DAY, _metrics())

    assert run.reused == ["sentiment"]
    assert run.generated == ["capital", "theme", "dragon_tiger", "leader"]
    assert {section for _model, section, _json in client.calls} == {
        "capital",
        "theme",
        "dragon_tiger",
        "leader",
    }


def test_corrupt_checkpoint_does_not_block_rerun(tmp_path, configured) -> None:
    path = checkpoint.checkpoint_path(DAY, tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json", encoding="utf-8")

    run = Orchestrator(
        config=make_config(), client=ScriptedClient(_all_ok), root=tmp_path
    ).run(DAY, _metrics())

    assert run.exit_code == 0
    assert checkpoint.is_complete(checkpoint.load(DAY, tmp_path))


def test_unknown_section_rejected(tmp_path, configured) -> None:
    with pytest.raises(ValueError):
        Orchestrator(config=make_config(), client=ScriptedClient(_all_ok), root=tmp_path).run(
            DAY, _metrics(), only="nope"
        )


def _attempts(root: Any) -> list[dict[str, Any]]:
    document = checkpoint.load(DAY, root)
    return checkpoint.section_state(document, "sentiment")["attempts"]
