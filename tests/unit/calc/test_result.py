from __future__ import annotations

import json
import math
import random

import pytest

from ladderstock.calc.result import (
    BAD_ARGUMENT,
    CROSSES_ZERO,
    DENOMINATOR_NON_POSITIVE,
    NOT_MEANINGFUL,
    OK,
    RESULT_LIMITED,
    CalcResult,
    NonFiniteValueError,
    assert_finite,
    safe_range_ratio,
    safe_ratio,
)


def _walk_finite(node: object) -> None:
    if isinstance(node, bool) or node is None or isinstance(node, str | int):
        return
    if isinstance(node, float):
        assert math.isfinite(node), f"非有限值 {node!r}"
        return
    if isinstance(node, dict):
        for v in node.values():
            _walk_finite(v)
        return
    if isinstance(node, list | tuple):
        for v in node:
            _walk_finite(v)
        return
    raise AssertionError(f"未知节点类型 {type(node)!r}")


# ---------------------------------------------------------------- 成功路径


def test_success_keeps_full_precision_and_serializes() -> None:
    r = CalcResult.success(1 / 3, unit="倍")
    assert r.ok and r.reason == OK
    assert r.value == 1 / 3
    payload = json.loads(r.to_json())
    assert payload["value"] == 1 / 3
    assert payload["unit"] == "倍"


def test_success_requires_unit() -> None:
    with pytest.raises(ValueError):
        CalcResult.success(1.0, unit="")


def test_ok_and_reason_must_agree() -> None:
    with pytest.raises(ValueError):
        CalcResult(ok=True, value=1.0, reason=NOT_MEANINGFUL)
    with pytest.raises(ValueError):
        CalcResult(ok=False, reason=OK)


def test_ok_result_must_have_value() -> None:
    with pytest.raises(ValueError):
        CalcResult(ok=True, unit="倍")


# ---------------------------------------------------------------- 无意义域


def test_zero_denominator_is_not_meaningful() -> None:
    r = safe_ratio(5.0, 0.0)
    assert not r.ok and r.reason == NOT_MEANINGFUL
    assert r.value is None
    assert r.details["not_meaningful_kind"] == DENOMINATOR_NON_POSITIVE


@pytest.mark.parametrize("denominator", [-1e-12, -3.0, 0.0])
def test_non_positive_denominator_never_computed(denominator: float) -> None:
    r = safe_ratio(10.0, denominator)
    assert r.reason == NOT_MEANINGFUL and r.value is None


def test_range_crossing_zero_is_not_meaningful() -> None:
    r = safe_range_ratio(1.0, -2.0, 3.0)
    assert r.reason == NOT_MEANINGFUL
    assert r.details["not_meaningful_kind"] == CROSSES_ZERO


def test_not_meaningful_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError):
        CalcResult.not_meaningful("whatever")


# ---------------------------------------------------------------- 参数非法


def test_bad_argument_carries_message_and_no_value() -> None:
    r = CalcResult.bad_argument("fx 缺失", fx_rate=None)
    assert not r.ok and r.reason == BAD_ARGUMENT and r.value is None
    assert r.details["message"] == "fx 缺失"


def test_invalid_reason_rejected() -> None:
    with pytest.raises(ValueError):
        CalcResult(ok=False, reason="oops")


def test_unknown_reason_text_not_displayed() -> None:
    r = CalcResult.bad_argument("x")
    assert r.display is None and r.unit is None


def test_safe_ratio_rejects_non_finite_input() -> None:
    with pytest.raises(NonFiniteValueError):
        safe_ratio(math.inf, 1.0)


# ---------------------------------------------------------------- 数值守卫


@pytest.mark.parametrize("bad", [math.inf, -math.inf, math.nan])
def test_constructor_blocks_non_finite_value(bad: float) -> None:
    with pytest.raises(NonFiniteValueError):
        CalcResult(ok=True, value=bad, unit="倍")


@pytest.mark.parametrize("bad", [math.inf, math.nan])
def test_constructor_blocks_non_finite_in_deep_details(bad: float) -> None:
    with pytest.raises(NonFiniteValueError):
        CalcResult.success(1.0, unit="倍", details={"a": [{"b": ({"c": bad},)}]})


def test_assert_finite_walks_trees() -> None:
    assert_finite({"a": [1, 2.5, None, "x", True]})
    with pytest.raises(NonFiniteValueError):
        assert_finite({"a": [{"b": math.nan}]})


def test_finite_inputs_overflowing_to_inf_are_not_meaningful() -> None:
    # 两个有限数相除溢出 float64：不许抛 inf，也不许硬算
    r = safe_ratio(1.7e308, 1e-308)
    assert r.reason == NOT_MEANINGFUL and r.value is None
    assert r.details["not_meaningful_kind"] == RESULT_LIMITED


def test_range_ratio_near_float_max_does_not_overflow() -> None:
    r = safe_range_ratio(1.0, 1.6e308, 1.7e308)
    assert r.ok and r.value is not None and math.isfinite(r.value)


def test_money_overflow_is_bad_argument() -> None:
    from ladderstock.calc.money import amount

    r = amount(1.7e308, currency="CNY", unit="亿元")
    assert r.reason == BAD_ARGUMENT and r.value is None


# ---------------------------------------------------------------- 参数化模糊测试

_EDGE = [
    0.0,
    -0.0,
    1.0,
    -1.0,
    1e-300,
    -1e-300,
    1e300,
    -1e300,
    1.7976931348623157e308,
    -1.7976931348623157e308,
    5e-324,
    -5e-324,
]


def _random_floats(rng: random.Random, n: int) -> list[float]:
    out = []
    for _ in range(n):
        mode = rng.randrange(4)
        if mode == 0:
            value = rng.uniform(-1e6, 1e6)
        elif mode == 1:
            value = rng.uniform(-1, 1) * 10 ** rng.randint(-300, 300)
        elif mode == 2:
            value = float(rng.randint(-(10**6), 10**6))
        else:
            value = rng.choice(_EDGE)
        if math.isfinite(value):
            out.append(value)
    return out


@pytest.mark.parametrize("seed", range(40))
def test_fuzz_safe_ratio_and_range_never_emit_nan_or_inf(seed: int) -> None:
    rng = random.Random(seed)
    for a in _random_floats(rng, 20):
        for b in _random_floats(rng, 5):
            for r in (safe_ratio(a, b), safe_range_ratio(a, b / 2, b)):
                _walk_finite(json.loads(r.to_json()))
                _walk_finite(dict(r.details))
                if r.ok:
                    assert r.value is not None and math.isfinite(r.value)
                else:
                    assert r.value is None


def test_fuzz_payload_roundtrip_is_strict_json() -> None:
    rng = random.Random(7)
    for _ in range(200):
        a, b = _random_floats(rng, 2)
        r = safe_ratio(a, b)
        # allow_nan=False：任何 nan/inf 都会抛 ValueError
        json.loads(r.to_json(), parse_constant=lambda c: pytest.fail(f"非法常量 {c}"))
