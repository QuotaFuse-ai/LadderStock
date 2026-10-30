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
    # R1-1（QUOT-190）新增：issue 里工具名定死为 `research_status`，
    # 不带 `ladder_` 前缀是有意的偏差（与 CLI `ldst research` 同名，便于跨面引用）
    "research_status",
    # R7 新增（QUOT-145）
    "candidate_pool",
}

#: 按**证券代码**定位进度的工具：`date` 可选（缺省取该代码最近一次 checkpoint），
#: 所以不适用下面「取数类工具必须以 date 定位快照」这条不变量。
CODE_SCOPED_TOOLS = {"research_status"}


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
        if spec.domain == "meta" or spec.name in CODE_SCOPED_TOOLS:
            continue
        assert spec.input_schema.get("required") == ["date"], spec.name


def test_code_scoped_tools_require_code_and_keep_date_optional() -> None:
    """反向钉住偏差：`research_status` 必须 required=["code"]、date 可选且只读。"""
    by_name = {spec.name: spec for spec in TOOL_SPECS}
    assert CODE_SCOPED_TOOLS <= set(by_name)
    for name in CODE_SCOPED_TOOLS:
        spec = by_name[name]
        assert spec.input_schema["required"] == ["code"], name
        assert spec.input_schema["properties"]["date"]["type"] == "string", name
        assert spec.read_only is True, name


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


def test_candidate_pool_tool_missing_is_partial(tmp_path: Any) -> None:
    """R7：候选池工具在缓存为空时如实 partial + `available=false`，不伪造候选。"""
    envelope = dispatch("candidate_pool", {"date": DAY.isoformat()}, root=tmp_path)

    _assert_envelope(envelope)
    assert envelope["domain"] == "sentiment"
    assert envelope["topic"] == "candidate_pool"
    assert envelope["source_status"] == "partial"
    assert envelope["payload"]["data_source_status"] == "missing"
    pool = envelope["payload"]["pool"]
    assert pool["available"] is False
    assert pool["candidates"] == []
    # 口径随结果一起出：权重与算式必须让调用方能自己复算
    assert pool["weights"] == {
        "strength": 0.3,
        "turnover": 0.1,
        "standing": 0.25,
        "capital": 0.2,
        "theme": 0.15,
    }
    assert "Σ(weight_i × part_i)" in pool["formula"]
    blob = json.dumps(envelope, ensure_ascii=False).lower()
    for word in FORBIDDEN_KEYWORDS:
        assert word.lower() not in blob, word


def test_candidate_pool_tool_scores_and_stays_privacy_clean(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R7：缓存可用时给出分项与总分；「地位」的键名是 `standing`，不踩隐私关键字。

    手算（`docs/specs/SPEC-CANDIDATE-POOL.md` §5）：600001 三板、当日最高 3 板、
    题材板块 +4% → 强度 50.0（3/6）、地位 100.0（3/3）、题材 80.0（4/5），
    换手与资金取不到数，权重重归一到 0.70 → (0.30×50 + 0.25×100 + 0.15×80)/0.70 = 74.3。
    """
    from ladderstock.core import pool as pool_module

    rows = [
        {"code": "600001", "name": "甲", "boards": 3, "hot_topic": "机器人"},
        {"code": "600002", "name": "乙", "boards": 1, "hot_topic": "中药"},
    ]

    def fake_snapshot(dataset: str, day: Any = None, root: Any = None) -> tuple[list, str]:
        if dataset == "limit_pool":
            return rows, "given"
        if dataset == "concept_board":
            return [{"name": "机器人", "change_pct": 4.0}], "given"
        return [], "none"

    monkeypatch.setattr(pool_module, "dataset_snapshot", fake_snapshot)

    envelope = dispatch("candidate_pool", {"date": DAY.isoformat()}, root=tmp_path)

    _assert_envelope(envelope)
    assert envelope["source_status"] == "ok"
    assert envelope["payload"]["data_source_status"] == "ok"
    pool = envelope["payload"]["pool"]
    assert pool["available"] is True
    assert pool["height"] == 3
    top = pool["candidates"][0]
    assert top["code"] == "600001"
    assert set(top["parts"]) == {"strength", "turnover", "standing", "capital", "theme"}
    assert top["parts"] == {
        "strength": 50.0,
        "turnover": None,
        "standing": 100.0,
        "capital": None,
        "theme": 80.0,
    }
    assert top["absent_parts"] == ["turnover", "capital"]
    assert top["score"] == 74.3
    assert "候选池" in envelope["payload"]["summary"]
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
