"""周度流水线：五段编排复用、降级补模板、断点续跑、产物落盘（QUOT-149 要求 2/4）。

全部用脚本化假客户端，不拨网络；`root` 指向 tmp_path，产物不落进仓库 `data/`。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from fixtures import weekly_cache

from ladderstock.ai import checkpoint as daily_checkpoint
from ladderstock.ai import fallback
from ladderstock.weekly import checkpoint as weekly_checkpoint
from ladderstock.weekly import facts as facts_module
from ladderstock.weekly import pipeline

WEEK = weekly_cache.WEEK
URL_ENV = "TEST_WEEKLY_AI_BASE_URL"
KEY_ENV = "TEST_WEEKLY_AI_API_KEY"

Script = Callable[[str, str, str, bool], Any]


@dataclass
class ScriptedClient:
    """按 (model, section, json_mode) 返回文本或抛异常，并记录调用次数。"""

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
    for name, title in weekly_checkpoint.SECTION_TITLES.items():
        if title in user:
            return name
    return "?"


def _json_ok(section: str) -> str:
    return json.dumps({"narrative": [f"{section} 周度叙述"]}, ensure_ascii=False)


def make_config(**overrides: Any) -> fallback.AiConfig:
    sections = {
        name: fallback.SectionConfig(prompt_file=f"prompts/{name}.md", output_schema="")
        for name in daily_checkpoint.SECTIONS
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


@pytest.fixture()
def seeded(tmp_path: Path) -> Path:
    return weekly_cache.seed_week(tmp_path)


def _all_ok(model: str, section: str, user: str, json_mode: bool) -> str:
    return _json_ok(section)


# --------------------------------------------------------------------------- #
# 成功路径
# --------------------------------------------------------------------------- #

def test_generate_all_sections_ok(seeded, configured) -> None:
    client = ScriptedClient(_all_ok)
    report, run, pack = pipeline.generate(WEEK, root=seeded, config=make_config(), client=client)

    assert run.failed == []
    assert run.degraded is False
    assert run.exit_code == 0
    assert [item["status"] for item in report["sections"]] == ["ok"] * 5
    assert [item["name"] for item in report["sections"]] == list(daily_checkpoint.SECTIONS)
    assert all(item["mode"] == "structured" for item in report["sections"])
    assert report["ai"] == {"model": "m1", "degraded": False}
    assert report["source_status"] == "ok"
    assert report["week"] == WEEK
    assert weekly_checkpoint.load(WEEK, seeded)


def test_fact_pack_has_no_model_output(seeded, configured) -> None:
    client = ScriptedClient(_all_ok)
    report, _, pack = pipeline.generate(WEEK, root=seeded, config=make_config(), client=client)

    # 报告里的 facts 就是纯事实包本身：逐字节相同
    assert report["facts"] == pack
    assert facts_module.build_facts(WEEK, root=seeded) == pack
    dumped = json.dumps(report["facts"], ensure_ascii=False)
    for section in report["sections"]:
        assert section["narrative"][0] not in dumped
    assert weekly_cache.CHECKPOINT_NARRATIVE not in dumped
    assert '"narrative"' not in dumped


def test_section_metrics_kept_verbatim(seeded, configured) -> None:
    """AI 只追加 `ai` 块：段落里的指标快照必须与 `section_snapshots()` 原样一致。"""
    client = ScriptedClient(_all_ok)
    report, _, pack = pipeline.generate(WEEK, root=seeded, config=make_config(), client=client)

    snapshots = pipeline.section_snapshots(pack)
    for section in report["sections"]:
        name = section["name"]
        assert section["metrics"] == snapshots[name]
        assert section["metrics"]["week"] == WEEK
        assert "narrative" not in section["metrics"]
        assert "highlights" not in section["metrics"]


# --------------------------------------------------------------------------- #
# 降级路径（要求 4）
# --------------------------------------------------------------------------- #

def test_model_failure_still_writes_both_artifacts(seeded, configured) -> None:
    client = ScriptedClient(lambda model, section, user, json_mode: httpx.ConnectError("boom"))
    report, run, pack = pipeline.generate(WEEK, root=seeded, config=make_config(), client=client)

    assert run.exit_code == 9
    assert report["ai"]["degraded"] is True
    assert report["source_status"] == "partial"
    assert len(report["sections"]) == 5
    for section in report["sections"]:
        assert section["mode"] == "template"
        assert section["degraded"] is True
        assert section["narrative"], "降级段落也要有文字"
        assert section["notes"][0] == pipeline.TEMPLATE_NOTE
    assert pack["available"] is True
    assert pipeline.weekly_path(WEEK, seeded).is_file()
    assert pipeline.markdown_path(WEEK, seeded).is_file()


def test_partial_failure_degrades_only_that_section(seeded, configured) -> None:
    def script(model: str, section: str, user: str, json_mode: bool) -> Any:
        if section == "theme":
            return httpx.ConnectError("boom")
        return _json_ok(section)

    report, run, _ = pipeline.generate(
        WEEK, root=seeded, config=make_config(), client=ScriptedClient(script)
    )

    assert run.failed == ["theme"]
    assert run.exit_code == 9
    by_name = {item["name"]: item for item in report["sections"]}
    assert by_name["theme"]["mode"] == "template"
    assert by_name["theme"]["degraded"] is True
    assert by_name["sentiment"]["mode"] == "structured"
    assert by_name["sentiment"]["degraded"] is False


def test_empty_week_skips_model_and_still_writes(seeded, configured) -> None:
    """整周没有可用读数：不调模型（省额度、不写幻觉），五段仍补模板，产物照落盘。"""
    empty = seeded / "empty"
    client = ScriptedClient(_all_ok)
    report, run, pack = pipeline.generate(WEEK, root=empty, config=make_config(), client=client)

    assert pack["available"] is False
    assert client.calls == []
    assert [item["mode"] for item in report["sections"]] == ["template"] * 5
    assert all(item["degraded"] for item in report["sections"])
    assert report["source_status"] == "partial"
    assert run.exit_code == 0  # 6 由 CLI 判（无数据优先于降级）
    assert (empty / "weekly" / f"{WEEK}.json").is_file()
    assert (empty / "weekly" / f"{WEEK}.md").is_file()


# --------------------------------------------------------------------------- #
# 断点续跑
# --------------------------------------------------------------------------- #

def test_second_run_reuses_checkpoint(seeded, configured) -> None:
    first = ScriptedClient(_all_ok)
    pipeline.generate(WEEK, root=seeded, config=make_config(), client=first)
    assert len(first.calls) == 5

    second = ScriptedClient(_all_ok)
    _, run, _ = pipeline.generate(WEEK, root=seeded, config=make_config(), client=second)
    assert second.calls == []
    assert sorted(run.reused) == sorted(daily_checkpoint.SECTIONS)

    forced = ScriptedClient(_all_ok)
    pipeline.generate(WEEK, root=seeded, config=make_config(), client=forced, force=True)
    assert len(forced.calls) == 5


# --------------------------------------------------------------------------- #
# Markdown 渲染
# --------------------------------------------------------------------------- #

def test_render_markdown_covers_every_section(seeded, configured) -> None:
    client = ScriptedClient(_all_ok)
    report, _, _ = pipeline.generate(WEEK, root=seeded, config=make_config(), client=client)
    text = pipeline.render_markdown(report)

    for name, title in weekly_checkpoint.SECTION_TITLES.items():
        assert title in text
        assert name in text
    for heading in (
        "## 本周概览",
        "## 情绪与天梯序列",
        "## 涨跌分布",
        "## 主线题材持续天数",
        "## 待验证项结算",
        "## 本周每日复盘 AI 完成度",
        "## 口径",
        "## 数据来源",
    ):
        assert heading in text


def test_render_markdown_survives_empty_week(tmp_path: Path) -> None:
    """空周的每个字段都是 None：渲染不能抛异常，也不得留空段落。"""
    pack = facts_module.build_facts(WEEK, root=tmp_path)
    assert pack["available"] is False
    run = pipeline.RunResult(date=WEEK)
    report = pipeline.build_report(WEEK, run, pack, pipeline.section_snapshots(pack))
    text = pipeline.render_markdown(report)

    assert len(report["sections"]) == 5
    assert all(section["narrative"] for section in report["sections"])
    assert "数据缺失" in text
