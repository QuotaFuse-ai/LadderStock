"""MCP 只读工具 `research_status`：读同一份六段进度，不触发生成，不越隐私边界。"""

from __future__ import annotations

import json
from typing import Any

import pytest

from ladderstock.mcp.server import ToolError, dispatch
from ladderstock.mcp.tools import FORBIDDEN_KEYWORDS, TOOL_SPECS
from ladderstock.research import checkpoint

DAY = "2026-10-27"
CODE = "600000"

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


def _summary(root: Any, code: str = CODE, day: str | None = None) -> dict[str, Any]:
    arguments: dict[str, Any] = {"code": code}
    if day is not None:
        arguments["date"] = day
    return dispatch("research_status", arguments, root=root)


def test_tool_is_registered_read_only() -> None:
    spec = {item.name: item for item in TOOL_SPECS}["research_status"]
    assert spec.read_only is True
    assert spec.input_schema["required"] == ["code"]


def test_missing_checkpoint_is_partial_and_names_the_fix(tmp_path: Any) -> None:
    envelope = _summary(tmp_path, day=DAY)

    assert set(envelope) == ENVELOPE_KEYS
    assert envelope["domain"] == "review" and envelope["topic"] == "research_status"
    assert envelope["data_as_of"] == DAY
    assert envelope["source_status"] == "partial"
    assert envelope["payload"]["data_source_status"] == "missing"
    assert envelope["payload"]["research"]["exists"] is False
    assert envelope["payload"]["research"]["pending"] == list(checkpoint.STAGES)
    assert f"ldst research {CODE} --date {DAY}" in envelope["source_detail"]


def test_partial_progress_reports_exactly_what_is_done(tmp_path: Any) -> None:
    document = checkpoint.fresh(CODE, DAY)
    for name in ("company", "financial"):
        checkpoint.record(
            document, name, status="ok", mode="text", narrative=["…"], model="m1", degraded=False
        )
    checkpoint.record(
        document,
        "forecast",
        status="failed",
        mode="failed",
        narrative=[],
        failure={"kind": "network", "message": "连接超时", "action": "重试", "retryable": True},
    )
    checkpoint.save(document, tmp_path)

    envelope = _summary(tmp_path, day=DAY)
    research = envelope["payload"]["research"]

    assert envelope["source_status"] == "partial"
    assert envelope["payload"]["data_source_status"] == "partial"
    assert research["complete"] is False
    assert research["done"] == ["company", "financial"]
    assert research["failed"] == ["forecast"]
    assert research["pending"] == ["valuation", "risk", "report"]
    assert [item["name"] for item in research["stages"]] == list(checkpoint.STAGES)


def test_completed_run_is_ok(tmp_path: Any) -> None:
    document = checkpoint.fresh(CODE, DAY)
    for name in checkpoint.STAGES:
        checkpoint.record(
            document,
            name,
            status="degraded",
            mode="placeholder",
            narrative=["占位"],
            model=None,
            degraded=True,
        )
    checkpoint.save(document, tmp_path)

    envelope = _summary(tmp_path, day=DAY)
    assert envelope["source_status"] == "ok"
    assert envelope["payload"]["research"]["complete"] is True
    assert envelope["payload"]["data_source_status"] == "ok"
    assert envelope["payload"]["reason"] is None


def test_date_defaults_to_latest_recorded_day(tmp_path: Any) -> None:
    for day in ("2026-10-26", DAY):
        checkpoint.save(checkpoint.fresh(CODE, day), tmp_path)
    assert checkpoint.latest_day(CODE, tmp_path) == DAY

    envelope = _summary(tmp_path)
    assert envelope["payload"]["date"] == DAY


def test_bad_arguments_raise_tool_error(tmp_path: Any) -> None:
    with pytest.raises(ToolError):
        _summary(tmp_path, code="")
    with pytest.raises(ToolError):
        _summary(tmp_path, code="../etc/passwd")
    with pytest.raises(ToolError):
        _summary(tmp_path, day="not-a-date")


def test_envelope_carries_no_private_words(tmp_path: Any) -> None:
    envelope = _summary(tmp_path, day=DAY)
    blob = json.dumps(envelope, ensure_ascii=False).lower()
    for word in FORBIDDEN_KEYWORDS:
        assert word.lower() not in blob, word
