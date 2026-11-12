"""自由问答通用叙述 Schema（answer_schema.py）单测。

覆盖：多余键 / 空 narrative / 含指标键 / 叙述带数字被拒，以及与
`structured_output.SECTION_SCHEMA` 的字段集一致性（防两边漂移）。
"""

from __future__ import annotations

import pytest
from jsonschema import Draft202012Validator

from ladderstock.ai.answer_schema import (
    ANSWER_NARRATIVE_FIELDS,
    ANSWER_NARRATIVE_SCHEMA,
    AnswerSchemaError,
    validate_answer_narrative,
)
from ladderstock.ai.structured_output import NARRATIVE_FIELDS, SECTION_SCHEMA


def test_valid_payload_is_cleaned() -> None:
    cleaned = validate_answer_narrative(
        {
            "narrative": ["  主力资金流出，短线承压。  "],
            "highlights": ["放量下跌"],
            "risks": [],
        }
    )
    assert cleaned == {
        "narrative": ["主力资金流出，短线承压。"],
        "highlights": ["放量下跌"],
    }


def test_string_narrative_is_coerced() -> None:
    assert validate_answer_narrative({"narrative": "缩量横盘"}) == {"narrative": ["缩量横盘"]}


@pytest.mark.parametrize("key", ["temperature", "promotion_rate", "seal_rate", "limit_up_count"])
def test_metric_keys_are_rejected(key: str) -> None:
    with pytest.raises(AnswerSchemaError):
        validate_answer_narrative({"narrative": ["横盘"], key: 0.42})


def test_extra_keys_are_rejected() -> None:
    with pytest.raises(AnswerSchemaError, match="非叙述字段"):
        validate_answer_narrative({"narrative": ["横盘"], "summary": "一句话"})


def test_missing_narrative_is_rejected() -> None:
    with pytest.raises(AnswerSchemaError):
        validate_answer_narrative({"highlights": ["放量"]})


@pytest.mark.parametrize("narrative", [[], [""], ["   "], None])
def test_empty_narrative_is_rejected(narrative: object) -> None:
    with pytest.raises(AnswerSchemaError):
        validate_answer_narrative({"narrative": narrative})


def test_digits_in_any_field_are_rejected() -> None:
    with pytest.raises(AnswerSchemaError, match="数字"):
        validate_answer_narrative({"narrative": ["温度 62 度"]})
    with pytest.raises(AnswerSchemaError, match="数字"):
        validate_answer_narrative({"narrative": ["偏冷"], "risks": ["亏 4.3%"]})


def test_non_mapping_is_rejected() -> None:
    with pytest.raises(AnswerSchemaError):
        validate_answer_narrative(["横盘"])  # type: ignore[arg-type]


def test_every_payload_field_is_narrative_only() -> None:
    from ladderstock.core.boundary import AI_WRITABLE

    assert set(ANSWER_NARRATIVE_FIELDS) <= AI_WRITABLE
    assert "narrative" in ANSWER_NARRATIVE_FIELDS


def test_schema_stays_aligned_with_section_schema() -> None:
    """answer_schema 与 structured_output 的段落 Schema 平行定义，字段集必须一致。"""
    assert set(ANSWER_NARRATIVE_SCHEMA["properties"]) == set(SECTION_SCHEMA["properties"])
    assert set(ANSWER_NARRATIVE_FIELDS) == set(NARRATIVE_FIELDS)
    assert ANSWER_NARRATIVE_SCHEMA["required"] == SECTION_SCHEMA["required"] == ["narrative"]
    assert ANSWER_NARRATIVE_SCHEMA["additionalProperties"] is False
    assert ANSWER_NARRATIVE_SCHEMA["properties"]["narrative"]["minItems"] == 1


def test_schema_itself_rejects_metric_keys() -> None:
    """Schema 层面也要挡住指标键，而非只靠字段白名单兜底。"""
    validator = Draft202012Validator(ANSWER_NARRATIVE_SCHEMA)
    assert validator.is_valid({"narrative": ["横盘"]})
    assert not validator.is_valid({"narrative": ["横盘"], "temperature": 62})
