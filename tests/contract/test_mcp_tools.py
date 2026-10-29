"""MCP 工具契约：数量、只读白名单、隐私边界、输出信封与降级状态。"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest

from ladderstock.core import validation as validation_module
from ladderstock.mcp.server import (
    ENVELOPE_OUTPUT_SCHEMA,
    ToolError,
    dispatch,
    tool_definitions,
)
from ladderstock.mcp.tools import FORBIDDEN_KEYWORDS, TOOL_SPECS, tool_listing
from ladderstock.review import pipeline

DAY = date(2026, 10, 27)
NEW_TOOLS = ("ladder_get_sentiment", "ladder_get_validation", "ladder_get_review")
ENVELOPE_KEYS = {
    "domain",
    "topic",
    "event_id",
    "data_as_of",
    "payload",
    "source",
    "source_detail",
    "fetched_at",
    "freshness",
    "confidence",
    "source_status",
}

DOCUMENTED_TOOLS = {
    # 原有 7 只读 + 1 发起研究
    "ladder_get_watch",
    "ladder_get_sentiment_series",
    "ladder_get_limit_ladder",
    "ladder_get_theme_rotation",
    "ladder_get_dragon_tiger",
    "ladder_get_leader",
    "ladder_get_sources_health",
    "ladder_start_research",
    # M3 新增
    "ladder_get_sentiment",
    "ladder_get_validation",
    "ladder_get_review",
}


def test_tool_set_matches_document() -> None:
    assert {spec.name for spec in TOOL_SPECS} == DOCUMENTED_TOOLS


def test_only_research_tool_is_write() -> None:
    writes = {spec.name for spec in TOOL_SPECS if not spec.read_only}
    assert writes == {"ladder_start_research"}


def test_no_privacy_keywords_leak_into_mcp() -> None:
    blob = json.dumps(tool_listing(), ensure_ascii=False).lower()
    for word in FORBIDDEN_KEYWORDS:
        assert word.lower() not in blob, f"MCP 工具暴露了隐私关键字：{word}"


def test_date_tools_require_date() -> None:
    for spec in TOOL_SPECS:
        if spec.domain == "meta":
            continue
        assert spec.input_schema.get("required") == ["date"], spec.name


# --------------------------------------------------------------------------- #
# 输出 schema 与信封
# --------------------------------------------------------------------------- #

def test_new_tools_carry_input_and_output_schema() -> None:
    by_name = {spec.name: spec for spec in TOOL_SPECS}
    for name in NEW_TOOLS:
        spec = by_name[name]
        assert spec.input_schema["properties"]["date"]["type"] == "string", name
        assert spec.input_schema["required"] == ["date"], name

    definitions = {tool.name: tool for tool in tool_definitions()}
    assert set(definitions) == DOCUMENTED_TOOLS
    for name in NEW_TOOLS:
        assert definitions[name].output_schema == ENVELOPE_OUTPUT_SCHEMA, name
        assert definitions[name].input_schema == by_name[name].input_schema, name


def test_every_tool_declares_envelope_output_schema() -> None:
    for tool in tool_definitions():
        assert tool.output_schema == ENVELOPE_OUTPUT_SCHEMA, tool.name
        assert tool.input_schema, tool.name


def test_tool_listing_json_includes_schemas() -> None:
    rows = {row["name"]: row for row in tool_listing()}
    for name in NEW_TOOLS:
        assert rows[name]["input_schema"]["required"] == ["date"], name
        assert rows[name]["read_only"] is True, name


# --------------------------------------------------------------------------- #
# 分发：信封形状、降级状态、隐私
# --------------------------------------------------------------------------- #

def _assert_envelope(envelope: dict[str, Any]) -> None:
    assert set(envelope) == ENVELOPE_KEYS
    assert envelope["source"] == "ladderstock"
    assert isinstance(envelope["source_detail"], str) and envelope["source_detail"]
    assert envelope["data_as_of"] == DAY.isoformat()
    assert envelope["source_status"] in {"ok", "degraded", "partial"}
    assert isinstance(envelope["payload"], dict)
    # 每个 payload 都显式带数据状态，缺失/降级一眼可见
    assert envelope["payload"]["data_source_status"] in {"ok", "missing", "partial", "degraded"}


def test_sentiment_tool_envelope_marks_missing_data(tmp_path: Any) -> None:
    envelope = dispatch("ladder_get_sentiment", {"date": DAY.isoformat()}, root=tmp_path)

    _assert_envelope(envelope)
    assert envelope["domain"] == "sentiment"
    assert envelope["source_status"] == "partial"
    assert envelope["confidence"] == "low"
    sentiment = envelope["payload"]["sentiment"]
    assert sentiment["available"] is False
    assert "涨停池为空" in sentiment["reason"]


def test_validation_tool_partial_before_reconcile(tmp_path: Any) -> None:
    validation_module.write_document(
        validation_module.validation_path(DAY, tmp_path),
        {"date": DAY.isoformat(), "conditions": [{"id": "x"}], "reconcile": None},
    )

    envelope = dispatch("ladder_get_validation", {"date": DAY.isoformat()}, root=tmp_path)

    _assert_envelope(envelope)
    assert envelope["domain"] == "validation"
    assert envelope["source_status"] == "partial"
    assert "尚未对账" in envelope["source_detail"]
    assert envelope["payload"]["conditions"] == [{"id": "x"}]


def test_validation_tool_ok_after_reconcile(tmp_path: Any) -> None:
    validation_module.write_document(
        validation_module.validation_path(DAY, tmp_path),
        {
            "date": DAY.isoformat(),
            "conditions": [],
            "reconcile": {"status": "ok", "next_session": "2026-10-28", "results": []},
        },
    )

    envelope = dispatch("ladder_get_validation", {"date": DAY.isoformat()}, root=tmp_path)

    _assert_envelope(envelope)
    assert envelope["source_status"] == "ok"
    assert "2026-10-28" in envelope["source_detail"]
    assert envelope["payload"]["data_source_status"] == "ok"


def test_review_tool_missing_is_partial_and_says_how_to_fix(tmp_path: Any) -> None:
    envelope = dispatch("ladder_get_review", {"date": DAY.isoformat()}, root=tmp_path)

    _assert_envelope(envelope)
    assert envelope["domain"] == "review"
    assert envelope["source_status"] == "partial"
    assert envelope["payload"]["review"] is None
    assert "ldst review" in envelope["source_detail"]


def test_review_tool_strips_private_fields(tmp_path: Any) -> None:
    path = pipeline.review_path(DAY, tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "date": DAY.isoformat(),
                "source_status": "ok",
                "freshness": "cached",
                "sections": [{"name": "sentiment", "narrative": ["情绪回暖"]}],
                "journal": {"trades": [{"code": "600000"}]},
                "positions": [{"code": "600000"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    envelope = dispatch("ladder_get_review", {"date": DAY.isoformat()}, root=tmp_path)

    _assert_envelope(envelope)
    assert envelope["source_status"] == "ok"
    blob = json.dumps(envelope, ensure_ascii=False).lower()
    for word in FORBIDDEN_KEYWORDS:
        assert word.lower() not in blob, word


def test_dispatch_rejects_bad_usage(tmp_path: Any) -> None:
    with pytest.raises(ToolError):
        dispatch("ladder_no_such_tool", {"date": DAY.isoformat()}, root=tmp_path)
    with pytest.raises(ToolError):
        dispatch("ladder_get_review", {}, root=tmp_path)
    with pytest.raises(ToolError):
        dispatch("ladder_get_review", {"date": "not-a-date"}, root=tmp_path)
