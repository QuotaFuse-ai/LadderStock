"""R3 数字级出处闸门（QUOT-141 要求 1 + 2）。

钉住四条：

1. `Provenance` 的形状与合法出处族（非法出处族 / 缺引用要素 = 契约错误，直接抛）；
2. **没有 provenance 的数值字段进不了 `verified` 集**（这是 R3 的全部意义）；
3. 出处记录的值与指标值不一致 → 判伪造引用；
4. AI 依旧只能写叙述：`merge_narrative` 不回归，且模型写不出 `provenance` 键。
"""

from __future__ import annotations

import pytest

from ladderstock.core.boundary import (
    AI_WRITABLE,
    PROVENANCE_SOURCES,
    BoundaryViolation,
    Provenance,
    assert_provenance,
    is_provenance_payload,
    merge_narrative,
    numeric_value,
)


def record(**overrides: object) -> Provenance:
    base: dict[str, object] = {
        "value": 18.4,
        "unit": "倍",
        "source": "tickhub-cn",
        "endpoint": "valuation_pe",
        "fetched_at": "2026-10-31T09:00:00+08:00",
        "as_of": "2026-09-30",
        "url": "http://127.0.0.1:8000/mcp",
        "verified": True,
        "metric": "pe",
    }
    base.update(overrides)
    return Provenance(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 1. 结构与出处族
# --------------------------------------------------------------------------- #


def test_provenance_shape_and_source_whitelist() -> None:
    assert list(record().as_dict()) == [
        "value",
        "unit",
        "source",
        "endpoint",
        "fetched_at",
        "as_of",
        "url",
        "verified",
    ]
    assert PROVENANCE_SOURCES == (
        "tickhub-cn",
        "tickhub-us",
        "wire",
        "senate",
        "computed",
        "user",
        "web",
    )


def test_unknown_source_family_is_a_violation() -> None:
    with pytest.raises(BoundaryViolation, match="不在登记清单"):
        record(source="akshare")


@pytest.mark.parametrize(
    ("kwargs", "needle"),
    (
        ({"fetched_at": ""}, "缺引用要素"),
        ({"as_of": "  "}, "缺引用要素"),
        ({"endpoint": None}, "必须点名 endpoint"),
        ({"url": None, "endpoint": None, "source": "computed"}, "无法点回落点"),
    ),
)
def test_verified_needs_full_citation(kwargs: dict[str, object], needle: str) -> None:
    with pytest.raises(BoundaryViolation, match=needle):
        record(**kwargs)


def test_unverified_record_tolerates_missing_citation() -> None:
    """未核实是正常状态，不是契约错误：要素不全也写得进来，只是进不了 verified 集。"""
    loose = record(verified=False, endpoint=None, url=None, fetched_at="", as_of="")
    audit = assert_provenance({"pe": 18.4}, {"pe": loose})
    assert audit.verified == frozenset()
    assert audit.unverified == frozenset({"pe"})


def test_web_source_can_never_be_verified() -> None:
    """联网检索属 R4（DEC-006 硬边界）：web 出处可以登记，但不能背书成已核实。"""
    with pytest.raises(BoundaryViolation, match="未获批准"):
        record(source="web", url="https://example.com/x")
    assert assert_provenance(
        {"pe": 18.4}, {"pe": record(source="web", verified=False)}
    ).unverified == frozenset({"pe"})


# --------------------------------------------------------------------------- #
# 2. 没有出处的数值字段进不了 verified
# --------------------------------------------------------------------------- #


def test_numeric_field_without_provenance_is_unverified() -> None:
    audit = assert_provenance(
        {"pe": 18.4, "pb": 2.1, "revenue_yoy": 37.2},
        {"pe": record()},
    )
    assert audit.verified == frozenset({"pe"})
    assert audit.unverified == frozenset({"pb", "revenue_yoy"})
    assert audit.complete is False


def test_empty_metrics_audit_is_trivially_incomplete() -> None:
    audit = assert_provenance({})
    assert audit.records == {}
    assert audit.verified == frozenset()
    assert audit.unverified == frozenset()


def test_non_numeric_fields_do_not_need_a_source() -> None:
    audit = assert_provenance({"basis": "TTM", "flag": True, "rows": [{"a": 1}]})
    assert audit.non_numeric == frozenset({"basis", "flag", "rows"})
    assert audit.unverified == frozenset()


def test_inlined_record_is_unwrapped_to_plain_metric() -> None:
    """指标值位置直接放记录也认（从 `<stage>.json` 读回来的形态就是 dict）。"""
    audit = assert_provenance({"pe": record(), "pb": 2.1})
    assert audit.metrics == {"pe": 18.4, "pb": 2.1}
    assert audit.verified == frozenset({"pe"})
    assert audit.unverified == frozenset({"pb"})


def test_record_for_unknown_metric_is_rejected() -> None:
    with pytest.raises(BoundaryViolation, match="指向不存在的指标"):
        assert_provenance({"pe": 18.4}, {"PED": record(metric="PED")})


def test_double_written_record_is_rejected() -> None:
    with pytest.raises(BoundaryViolation, match="写了两处"):
        assert_provenance({"pe": record()}, {"pe": record()})


# --------------------------------------------------------------------------- #
# 3. 伪造引用
# --------------------------------------------------------------------------- #


def test_record_value_mismatch_is_forgery() -> None:
    with pytest.raises(BoundaryViolation, match="不一致"):
        assert_provenance({"pe": 99.9}, {"pe": record(value=18.4)})


def test_percent_and_number_are_the_same_value() -> None:
    audit = assert_provenance(
        {"revenue_yoy": "37.2%"},
        {"revenue_yoy": record(value=37.2, unit="%", metric="revenue_yoy")},
    )
    assert audit.verified == frozenset({"revenue_yoy"})
    assert numeric_value("1,234") == 1234.0
    assert numeric_value(True) is None


def test_dict_payload_roundtrip() -> None:
    payload = record().as_dict()
    assert is_provenance_payload(payload)
    audit = assert_provenance({"pe": 18.4}, {"pe": payload})
    assert audit.verified == frozenset({"pe"})


# --------------------------------------------------------------------------- #
# 4. AI 只写叙述：合并不回归，也写不出出处
# --------------------------------------------------------------------------- #


def test_merge_narrative_does_not_regress() -> None:
    metrics = {"pe": 18.4, "pb": 2.1}
    merged = merge_narrative(metrics, {"narrative": ["估值处于中位。"]})
    assert merged["pe"] == 18.4 and merged["pb"] == 2.1
    assert merged["ai"]["narrative"] == ["估值处于中位。"]


def test_ai_cannot_write_provenance() -> None:
    """`provenance` 不在 AI 白名单里：模型给自己发引用 = 非法字段。"""
    assert "provenance" not in AI_WRITABLE
    with pytest.raises(BoundaryViolation, match="非叙述字段"):
        merge_narrative({"pe": 18.4}, {"provenance": {"pe": record().as_dict()}})


def test_ai_still_cannot_overwrite_metrics() -> None:
    with pytest.raises(BoundaryViolation, match="数字必须由程序计算"):
        merge_narrative({"pe": 18.4}, {"pe": 99.9})
    with pytest.raises(BoundaryViolation, match="覆盖程序计算结果"):
        merge_narrative({"highlights": ["程序写的"]}, {"highlights": ["模型改的"]})
