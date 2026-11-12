"""自由问答的通用叙述 Schema（任务书 §3.3 / §10 阶段 A）。

`structured_output.SECTION_SCHEMA` 只服务 5 个固定复盘段落（`sentiment / capital /
theme / dragon_tiger / leader`，由 `SCHEMA_BY_NAME` 按 `ai.yaml` 的
`output_schema` 名选中）。自由问答没有固定段落，`schema_for()` 会因「未知段落」
直接抛错，所以这里补一个与段落无关的通用叙述 Schema。

**平行定义，不复用 `SECTION_SCHEMA` 对象。** 两条理由：

1. `SECTION_SCHEMA` 的消费者是编排层（`validate_payload(section, ...)`），
   它的键集合是复盘口径的一部分；让问答模块直接持有同一个 `dict` 对象，
   任何一边的调整都会悄悄改掉另一边的行为，两条链路的演进节奏不同；
2. 问答链路需要在 `SECTION_SCHEMA` 之上再加一条段落 Schema 不关心的约束：
   `narrative` 的每条都必须是非空字符串（已由 Schema 表达）且**不含数字**
   （红线 4）。这条约束挂在验证函数 `validate_answer_narrative()` 里，
   不塞进 JSON Schema，免得复盘链路的错误信息跟着变。

代价是两份 Schema 可能漂移，所以 `answer_schema` 与 `structured_output`
之间有一条测试锁死键集合一致（`tests/unit/ai/test_answer_schema.py`）。
字段仍限 `narrative / highlights / risks / evidence`，`additionalProperties: False`，
**不含任何指标键**：AI 写数字即越界（DEC-006 闸门 5）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ladderstock.ai.answer_blocks import contains_number
from ladderstock.ai.structured_output import NARRATIVE_FIELDS

#: 自由问答通用叙述 Schema。字段与 `SECTION_SCHEMA` 逐一对应，但刻意是独立对象。
ANSWER_NARRATIVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["narrative"],
    "additionalProperties": False,
    "properties": {
        "narrative": {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "minLength": 1},
        },
        "highlights": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "evidence": {"type": "array", "items": {"type": "string"}},
    },
}

#: 与 `SECTION_SCHEMA` 对账用的键集合（有测试断言两边相等）。
ANSWER_NARRATIVE_FIELDS: frozenset[str] = frozenset(ANSWER_NARRATIVE_SCHEMA["properties"])

#: 与 `NARRATIVE_FIELDS` 同序，方便与 `structured_output` 对表。
FIELD_ORDER: tuple[str, ...] = tuple(NARRATIVE_FIELDS)


class AnswerSchemaError(ValueError):
    """问答叙述不合通用 Schema，或叙述里出现了数字。"""


def validate_answer_narrative(payload: Any) -> dict[str, Any]:
    """按通用 Schema 校验 AI 叙述；返回清洗后的 `{narrative, highlights, risks, evidence}`。

    拒绝：多余键（含任何指标键）、`narrative` 缺失或为空、非字符串项、
    空字符串项、叙述里出现数字（红线 4：数字只能走 `metric` 块）。
    """
    from jsonschema import Draft202012Validator

    if not isinstance(payload, Mapping):
        raise AnswerSchemaError("叙述 payload 必须是对象")
    extra = set(payload) - ANSWER_NARRATIVE_FIELDS
    if extra:
        raise AnswerSchemaError(
            "叙述含非叙述字段：" + ", ".join(sorted(extra)) + "（数字必须由程序计算）"
        )
    normalized = _normalize(dict(payload))
    errors = sorted(
        Draft202012Validator(ANSWER_NARRATIVE_SCHEMA).iter_errors(normalized),
        key=lambda err: list(err.path),
    )
    if errors:
        raise AnswerSchemaError(f"不符合问答叙述 Schema：{errors[0].message}")
    cleaned: dict[str, Any] = {}
    for field in ("narrative", "highlights", "risks", "evidence"):
        values = normalized.get(field) or []
        for text in values:
            if contains_number(text):
                raise AnswerSchemaError(f"{field} 段出现数字（红线 4）：{text!r}")
        if field == "narrative" or values:
            cleaned[field] = values
    return cleaned


def _normalize(payload: dict[str, Any]) -> dict[str, Any]:
    """把单个字符串与 `None` 统一成数组/缺席，再交给 Schema 判空。"""
    normalized: dict[str, Any] = {}
    for key, value in payload.items():
        if value is None:
            continue
        if isinstance(value, str):
            normalized[key] = [value.strip()]
        elif isinstance(value, (list, tuple)):
            normalized[key] = [item.strip() if isinstance(item, str) else item for item in value]
        else:
            normalized[key] = value
    return normalized


__all__ = [
    "ANSWER_NARRATIVE_FIELDS",
    "ANSWER_NARRATIVE_SCHEMA",
    "AnswerSchemaError",
    "FIELD_ORDER",
    "validate_answer_narrative",
]
