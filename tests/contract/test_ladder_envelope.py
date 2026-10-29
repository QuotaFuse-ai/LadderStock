"""LadderEnvelope 契约：字段与生态 DataEnvelope / WireEnvelope 对齐。"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from ladderstock.core.models import LadderEnvelope

ECOSYSTEM_FIELDS = {
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


def _envelope(**overrides):
    base = dict(
        domain="review",
        topic="shortline_review",
        data_as_of=date(2026, 10, 22),
        fetched_at=datetime(2026, 10, 22, 15, 30, tzinfo=UTC),
        payload={"sections": ["sentiment", "capital"]},
    )
    base.update(overrides)
    return LadderEnvelope(**base)


def test_field_set_matches_ecosystem() -> None:
    assert set(LadderEnvelope.model_fields) == ECOSYSTEM_FIELDS


def test_defaults_are_conservative() -> None:
    env = _envelope()
    assert env.source == "ladderstock"
    assert env.freshness == "cached"
    assert env.confidence == "medium"
    assert env.source_status == "ok"
    assert env.event_id is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("domain", "trade_log"),
        ("freshness", "archive"),
        ("confidence", "low"),
        ("source_status", "degraded"),
    ],
)
def test_enums_accept_documented_values(field: str, value: str) -> None:
    assert getattr(_envelope(**{field: value}), field) == value


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("domain", "trade_advice"),
        ("freshness", "live"),
        ("confidence", "certain"),
        ("source_status", "broken"),
    ],
)
def test_enums_reject_unknown_values(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        _envelope(**{field: value})


def test_extra_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        _envelope(price_target=12.5)


def test_envelope_carries_no_privacy_fields() -> None:
    """隐私数据（持仓、交易日志明细）不得成为 envelope 字段。"""
    lowered = {name.lower() for name in LadderEnvelope.model_fields}
    for banned in ("journal", "position", "holding", "cost", "pnl"):
        assert banned not in lowered
