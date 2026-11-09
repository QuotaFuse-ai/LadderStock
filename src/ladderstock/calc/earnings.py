"""财报事件（纯函数，不读文件 / 不联网 / 不读环境变量 / 无全局可变状态）。

两个算子：

- ``earnings_reaction``：财报事件之后的两个交易日涨跌——**事件日**与**次日**都给。
  反应日口径：
    * ``timing="before_open"`` / ``"during"``：发布日当天或之后第一根日 K；
    * ``timing="after_close"``：发布日之后第一根日 K（A 股定期报告多为盘后披露）。
  「事件日」= 上述第一根可反应的 K 线，「次日」= 它后面的下一根 K 线。
  涨跌 = 当日收盘 ÷ 前一交易日收盘 − 1（小数，不扣大盘）。
  任一天缺前收 / 缺次日 → ``not_meaningful``，并说明差几根 K 线。
- ``earnings_surprise``：实际 vs 预期，给**差额**（实际 − 预期）与**差额百分比**
  （差额 ÷ |预期|，小数）；预期为 0 时百分比无意义 → ``not_meaningful``，差额照给。
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any, Final

from ladderstock.calc.fingerprint import data_fingerprint
from ladderstock.calc.indicators import KlineInputError, parse_kline_rows
from ladderstock.calc.result import (
    DENOMINATOR_NON_POSITIVE,
    OUT_OF_DOMAIN,
    CalcResult,
)

RATIO_UNIT: Final = "小数"
TIMINGS: Final = frozenset({"before_open", "during", "after_close"})


def _event_date(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError:
        return None


def _finite_number(name: str, value: object) -> tuple[float | None, str | None]:
    if value is None or isinstance(value, bool):
        return None, f"{name} 必须是数值，收到 {value!r}"
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None, f"{name} 必须是数值，收到 {value!r}"
    if not math.isfinite(number):
        return None, f"{name} 必须是有限数值，收到 {value!r}"
    return number, None


def _missing_bars(
    message: str,
    *,
    required: int,
    actual: int,
    rows: Sequence[Mapping[str, Any]],
    **extra: Any,
) -> CalcResult:
    missing = max(required - actual, 0)
    details: dict[str, Any] = {
        "message": f"{message}：需 {required} 根，实得 {actual} 根，差 {missing} 根",
        "required": required,
        "actual": actual,
        "missing": missing,
        "insufficient_sample": True,
        "data_fp": data_fingerprint(rows),
        **extra,
    }
    return CalcResult.not_meaningful(OUT_OF_DOMAIN, details=details)


def earnings_reaction(
    klines: Sequence[Mapping[str, Any]],
    event_date: str,
    *,
    timing: str = "after_close",
) -> CalcResult:
    """事件日与次日两个交易日的涨跌（小数）。

    ``value`` 是事件日涨跌；``details["event_day"]`` / ``details["next_day"]``
    各给 ``{date, previous_date, previous_close, close, change}``。
    """
    try:
        rows = parse_kline_rows(klines, ("close",))
    except KlineInputError as exc:
        return CalcResult.bad_argument(str(exc))
    if timing not in TIMINGS:
        return CalcResult.bad_argument(
            f"timing 必须是 {' / '.join(sorted(TIMINGS))} 之一，收到 {timing!r}"
        )
    event = _event_date(event_date)
    if event is None:
        return CalcResult.bad_argument(f"event_date 必须是 YYYY-MM-DD，收到 {event_date!r}")

    dates = [str(row["date"]) for row in rows]
    index = bisect_right(dates, event) if timing == "after_close" else bisect_left(dates, event)
    if index >= len(rows):
        return _missing_bars(
            f"反应日还没到（K 线只到 {dates[-1]}）",
            required=1,
            actual=0,
            rows=rows,
            pending=True,
            event_date=event,
            timing=timing,
        )
    if index == 0:
        return _missing_bars(
            "事件日之前没有可比 K 线",
            required=1,
            actual=0,
            rows=rows,
            pending=False,
            event_date=event,
            timing=timing,
            reaction_day=dates[0],
        )

    closes = [float(row["close"]) for row in rows]
    event_change = closes[index] / closes[index - 1] - 1.0
    event_day = {
        "date": dates[index],
        "previous_date": dates[index - 1],
        "previous_close": closes[index - 1],
        "close": closes[index],
        "change": event_change,
    }

    if index + 1 >= len(rows):
        return _missing_bars(
            "缺次日 K 线",
            required=1,
            actual=0,
            rows=rows,
            pending=True,
            event_date=event,
            timing=timing,
            event_day=event_day,
            next_day=None,
        )

    next_day = {
        "date": dates[index + 1],
        "previous_date": dates[index],
        "previous_close": closes[index],
        "close": closes[index + 1],
        "change": closes[index + 1] / closes[index] - 1.0,
    }
    return CalcResult.success(
        event_change,
        unit=RATIO_UNIT,
        details={
            "event_date": event,
            "timing": timing,
            "event_day": event_day,
            "next_day": next_day,
            "bars": {"first": dates[0], "last": dates[-1], "count": len(dates)},
            "data_fp": data_fingerprint(rows),
        },
    )


def earnings_surprise(
    actual: float,
    estimate: float,
    *,
    unit: str = "",
) -> CalcResult:
    """实际 vs 预期：``value`` = 差额百分比（小数），``details.difference`` = 绝对差额。

    ``unit`` 只留痕：实际与预期必须同单位，本函数不做换算。预期为 0 → ``not_meaningful``。
    """
    actual_number, error = _finite_number("actual", actual)
    if error is not None:
        return CalcResult.bad_argument(error)
    estimate_number, error = _finite_number("estimate", estimate)
    if error is not None:
        return CalcResult.bad_argument(error)
    if not isinstance(unit, str):
        return CalcResult.bad_argument(f"unit 必须是字符串，收到 {unit!r}")
    assert actual_number is not None and estimate_number is not None

    difference = actual_number - estimate_number
    details: dict[str, Any] = {
        "actual": actual_number,
        "estimate": estimate_number,
        "difference": difference,
        "unit_in": unit,
    }
    if estimate_number == 0.0:
        return CalcResult.not_meaningful(
            DENOMINATOR_NON_POSITIVE,
            unit=RATIO_UNIT,
            details={
                "message": "预期为 0，差额百分比无意义",
                **details,
            },
        )
    difference_pct = difference / abs(estimate_number)
    return CalcResult.success(
        difference_pct,
        unit=RATIO_UNIT,
        details={**details, "difference_pct": difference_pct},
    )
