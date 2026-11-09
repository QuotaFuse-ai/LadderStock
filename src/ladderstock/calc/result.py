"""统一计算结果对象 CalcResult。

约定：
- ``value`` 是复算真理源，不做四舍五入；``display`` 只给展示层用。
- 返回树任何位置（含 ``details`` 深层）不允许出现 ``inf`` / ``NaN``，
  守卫在构造时执行，构造 ``CalcResult`` 只是这些入口之一。
- 分母 ``<= 0`` / 跨零 / 无定义域时返回 ``ok=False, reason="not_meaningful"``，
  并带 ``details["not_meaningful_kind"]`` 说明原因，不硬算出一个数。
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

OK: Final = "ok"
NOT_MEANINGFUL: Final = "not_meaningful"
BAD_ARGUMENT: Final = "bad_argument"

REASONS: Final = frozenset({OK, NOT_MEANINGFUL, BAD_ARGUMENT})

# 无意义域的细分原因，供 details["not_meaningful_kind"] 使用。
DENOMINATOR_NON_POSITIVE: Final = "denominator_non_positive"
CROSSES_ZERO: Final = "crosses_zero"
OUT_OF_DOMAIN: Final = "out_of_domain"
#: 有限入参算出非有限结果（如比值溢出 float64）
RESULT_LIMITED: Final = "result_limited"

_NOT_MEANINGFUL_KINDS: Final = frozenset(
    {DENOMINATOR_NON_POSITIVE, CROSSES_ZERO, OUT_OF_DOMAIN, RESULT_LIMITED}
)

_REASON_TEXT: Final[dict[str, str]] = {
    DENOMINATOR_NON_POSITIVE: "分母 <= 0，比值无意义",
    CROSSES_ZERO: "区间跨零，分母在此区间内可能为 0，比值无意义",
    OUT_OF_DOMAIN: "输入落在定义域之外，结果无意义",
    RESULT_LIMITED: "计算结果超出 float64 有限范围，无法作为真值使用",
}


class NonFiniteValueError(ValueError):
    """返回树中出现 inf / NaN。"""


def assert_finite(tree: Any, *, _path: str = "") -> None:
    """递归检查任意返回树，发现 inf / NaN 立即抛 ``NonFiniteValueError``。"""
    if isinstance(tree, bool):
        return
    if isinstance(tree, float) and not math.isfinite(tree):
        raise NonFiniteValueError(f"非有限数值 {tree!r} 出现在 {_path or '<root>'}")
    if isinstance(tree, Mapping):
        for key, item in tree.items():
            assert_finite(item, _path=f"{_path}[{key!r}]")
    elif isinstance(tree, Sequence) and not isinstance(tree, str | bytes | bytearray):
        for index, item in enumerate(tree):
            assert_finite(item, _path=f"{_path}[{index}]")


@dataclass(frozen=True, slots=True)
class CalcResult:
    ok: bool
    value: float | int | None = None
    unit: str | None = None
    display: str | None = None
    reason: str = OK
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.reason not in REASONS:
            raise ValueError(f"未知 reason: {self.reason!r}")
        if self.ok != (self.reason == OK):
            raise ValueError(f"ok={self.ok!r} 与 reason={self.reason!r} 不一致")
        if self.ok and self.value is None:
            raise ValueError("ok=True 时必须给出 value")
        if not self.ok and self.value is not None:
            raise ValueError(f"reason={self.reason!r} 时不得给出硬算的 value")
        if self.unit is not None and not self.unit:
            raise ValueError("unit 不许为空字符串")
        assert_finite(self.value, _path="value")
        assert_finite(self.details, _path="details")

    @classmethod
    def success(
        cls,
        value: float | int,
        *,
        unit: str,
        display: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> CalcResult:
        if not unit:
            raise ValueError("金额/量纲结果必须带 unit")
        return cls(
            ok=True,
            value=value,
            unit=unit,
            display=display,
            reason=OK,
            details=details or {},
        )

    @classmethod
    def not_meaningful(
        cls,
        kind: str,
        *,
        unit: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> CalcResult:
        if kind not in _NOT_MEANINGFUL_KINDS:
            raise ValueError(f"未知 not_meaningful 类型: {kind!r}")
        merged: dict[str, Any] = {"not_meaningful_kind": kind, "message": _REASON_TEXT[kind]}
        if details:
            merged.update(details)
        return cls(
            ok=False, value=None, unit=unit, display=None, reason=NOT_MEANINGFUL, details=merged
        )

    @classmethod
    def bad_argument(cls, message: str, **details: Any) -> CalcResult:
        return cls(
            ok=False,
            value=None,
            unit=None,
            display=None,
            reason=BAD_ARGUMENT,
            details={"message": message, **details},
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "value": self.value,
            "unit": self.unit,
            "display": self.display,
            "reason": self.reason,
            "details": dict(self.details),
        }

    def to_json(self) -> str:
        assert_finite(self.to_dict())
        return json.dumps(self.to_dict(), allow_nan=False, ensure_ascii=False, sort_keys=True)


def safe_ratio(numerator: float, denominator: float, *, unit: str = "倍") -> CalcResult:
    """除法统一入口：分母 ``<= 0`` 不硬算，返回 ``not_meaningful``。"""
    if not math.isfinite(numerator) or not math.isfinite(denominator):
        raise NonFiniteValueError("safe_ratio 的入参必须是有限数值")
    if denominator <= 0:
        return CalcResult.not_meaningful(
            DENOMINATOR_NON_POSITIVE,
            details={"numerator": numerator, "denominator": denominator},
        )
    value = numerator / denominator
    if not math.isfinite(value):
        return CalcResult.not_meaningful(
            RESULT_LIMITED,
            details={"numerator": numerator, "denominator": denominator},
        )
    return CalcResult.success(value, unit=unit)


def safe_range_ratio(numerator: float, low: float, high: float, *, unit: str = "倍") -> CalcResult:
    """区间型除法（如 (高-低)/低）：区间跨零或上界 <= 0 时一律不硬算。"""
    if not all(math.isfinite(x) for x in (numerator, low, high)):
        raise NonFiniteValueError("safe_range_ratio 的入参必须是有限数值")
    if high < low:
        return CalcResult.bad_argument("区间上界小于下界", low=low, high=high)
    if low < 0 < high:
        return CalcResult.not_meaningful(
            CROSSES_ZERO, details={"numerator": numerator, "low": low, "high": high}
        )
    if low <= 0 <= high:
        return CalcResult.not_meaningful(
            DENOMINATOR_NON_POSITIVE,
            details={"numerator": numerator, "low": low, "high": high},
        )
    # low/2 + high/2 避免 low+high 在接近 float64 上限时溢出
    return safe_ratio(numerator, low / 2 + high / 2, unit=unit)
