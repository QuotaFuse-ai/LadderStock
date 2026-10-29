"""结构化输出与两级补偿：非法 JSON / 越界字段 / 围栏（M3 §6.3 第 1–2 级）。"""

from __future__ import annotations

import json

import pytest

from ladderstock.ai import structured_output
from ladderstock.ai.structured_output import StructuredOutputError


def test_parse_json_accepts_fenced_output() -> None:
    text = '```json\n{"narrative": ["情绪回暖"]}\n```'
    assert structured_output.parse_json(text) == {"narrative": ["情绪回暖"]}


def test_parse_json_tolerates_explanation_around_object() -> None:
    text = '复盘如下：{"narrative": ["涨停家数回升"]} 以上。'
    assert structured_output.parse_json(text)["narrative"] == ["涨停家数回升"]


def test_parse_json_rejects_plain_text() -> None:
    with pytest.raises(StructuredOutputError):
        structured_output.parse_json("今天情绪不错，但没有 JSON。")


def test_parse_json_rejects_non_object() -> None:
    with pytest.raises(StructuredOutputError):
        structured_output.parse_json("[1, 2, 3]")


def test_validate_payload_rejects_metric_keys() -> None:
    with pytest.raises(StructuredOutputError) as excinfo:
        structured_output.validate_payload("sentiment", {"narrative": ["x"], "temperature": 62})
    assert "非叙述字段" in str(excinfo.value)


def test_validate_payload_requires_narrative() -> None:
    with pytest.raises(StructuredOutputError):
        structured_output.validate_payload("sentiment", {"highlights": ["x"]})
    with pytest.raises(StructuredOutputError):
        structured_output.validate_payload("sentiment", {"narrative": []})


def test_validate_payload_cleans_allowed_fields() -> None:
    payload = structured_output.validate_payload(
        "capital", {"narrative": [" 资金回流 "], "risks": [" 缩量 "]}
    )
    assert payload == {"narrative": ["资金回流"], "risks": ["缩量"]}


def test_compensate_extracts_narrative_from_partial_json() -> None:
    text = '模型多说了一句 {"narrative": ["尾盘拉起"], "risks": ["炸板"]}'
    assert structured_output.compensate(text) == {
        "narrative": ["尾盘拉起"],
        "risks": ["炸板"],
    }


def test_compensate_splits_paragraphs() -> None:
    text = "第一段：情绪修复。\n\n第二段：但高位断层。"
    payload = structured_output.compensate(text)
    assert payload is not None
    assert len(payload["narrative"]) == 2


def test_compensate_gives_up_on_empty_or_tiny_text() -> None:
    assert structured_output.compensate("") is None
    assert structured_output.compensate("{}") is None
    assert structured_output.compensate("  \n ") is None


def test_plain_payload_keeps_whole_text() -> None:
    assert structured_output.plain_payload("情绪回暖，注意高位风险。") == {
        "narrative": ["情绪回暖，注意高位风险。"]
    }
    assert structured_output.plain_payload("") is None


def test_schema_contains_no_metric_keys() -> None:
    allowed = set(structured_output.SECTION_SCHEMA["properties"])
    assert allowed <= structured_output.MODEL_WRITABLE
    assert "temperature" not in allowed


def test_schema_for_unknown_section_raises() -> None:
    with pytest.raises(StructuredOutputError):
        structured_output.schema_for("unknown", "")


def test_schema_for_named_schema() -> None:
    schema = structured_output.schema_for("sentiment", "SentimentSection")
    assert schema is structured_output.SECTION_SCHEMA


def test_messages_carry_metrics_snapshot() -> None:
    system, user = structured_output.build_messages(
        "sentiment", "2026-10-27", {"temperature": 62.5}, "只写叙述"
    )
    assert "只写叙述" in system
    assert "temperature" in user
    assert "2026-10-27" in user


def test_load_prompt_falls_back_when_missing(tmp_path) -> None:
    text = structured_output.load_prompt(tmp_path / "nope.md")
    assert "只写叙述" in text


def test_narrative_must_be_array() -> None:
    """Schema 违例同样抛 StructuredOutputError（上层按 format 分类）。"""
    with pytest.raises(StructuredOutputError):
        structured_output.validate_payload(
            "sentiment", json.loads('{"narrative": "不是数组"}')
        )
