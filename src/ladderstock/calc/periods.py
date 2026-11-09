"""报告期算子：累计转单季（含非自然财年）、TTM、TTM 同比。

口径约定
--------
- 累计值按同一财年内报告期递增给出：单季 = 本期累计 − 上期累计，首期单季即首期累计。
- 财年起始月支持 1–12；4 月起始（4-3-31 财年）按起始月推算每期截止日，
  不假设自然年（12-31）财年。
- ``None`` 表示该报告期缺数：缺数时算不出单季 / TTM，返回 ``not_meaningful``，不硬算。
- 金额结果的单位由调用方给定（``unit``），本模块不猜单位也不做币种换算。
"""

from __future__ import annotations

import calendar
import math
from collections.abc import Sequence
from typing import Any, Final

from ladderstock.calc.result import (
    DENOMINATOR_NON_POSITIVE,
    OUT_OF_DOMAIN,
    RESULT_LIMITED,
    CalcResult,
)

MIN_FISCAL_YEAR: Final = 1900
MAX_FISCAL_YEAR: Final = 2999
#: 一个财年的报告期数量（一季报 / 中报 / 三季报 / 年报）
PERIODS_PER_YEAR: Final = 4

UNIT_DECIMAL: Final = "小数"


def _non_finite(field: str, value: float) -> CalcResult | None:
    if not math.isfinite(value):
        return CalcResult.bad_argument("入参必须是有限数值", field=field, value=repr(value))
    return None


def _quarter_end(fiscal_year: int, fiscal_start_month: int, period_index: int) -> str:
    """财年内第 ``period_index`` 期的截止日（含跨年）。"""
    months_from_january = (fiscal_start_month - 1) + 3 * period_index - 1
    year = fiscal_year + months_from_january // 12
    month = months_from_january % 12 + 1
    day = calendar.monthrange(year, month)[1]
    return f"{year:04d}-{month:02d}-{day:02d}"


def cumulative_to_single(
    *,
    fiscal_year: int,
    fiscal_start_month: int = 1,
    cumulative: Sequence[float | None],
    unit: str = "元",
) -> CalcResult:
    """把同一财年的累计值还原成单季值，返回最后一期单季值。

    ``value`` 是最后一期单季值；完整序列在 ``details["single_quarters"]`` 里，
    每项带期次、截止日、累计值与单季值。支持 4 月起始等非自然财年。
    """
    if not unit:
        return CalcResult.bad_argument("unit 不能为空", field="unit")
    if not MIN_FISCAL_YEAR <= fiscal_year <= MAX_FISCAL_YEAR:
        return CalcResult.bad_argument(
            f"财年超出 {MIN_FISCAL_YEAR}-{MAX_FISCAL_YEAR}", field="fiscal_year", value=fiscal_year
        )
    if not 1 <= fiscal_start_month <= 12:
        return CalcResult.bad_argument(
            "财年起始月必须在 1-12", field="fiscal_start_month", value=fiscal_start_month
        )
    if not 1 <= len(cumulative) <= PERIODS_PER_YEAR:
        return CalcResult.bad_argument(
            f"累计值需要 1-{PERIODS_PER_YEAR} 期", field="cumulative", count=len(cumulative)
        )
    missing = [index for index, value in enumerate(cumulative) if value is None]
    if missing:
        return CalcResult.not_meaningful(
            OUT_OF_DOMAIN,
            details={
                "field": f"cumulative[{missing[0]}]",
                "missing_indices": missing,
                "message_hint": "缺报告期累计值，无法还原单季",
            },
        )

    values = [float(value) for value in cumulative if value is not None]
    for index, value in enumerate(values):
        bad = _non_finite(f"cumulative[{index}]", value)
        if bad is not None:
            return bad

    rows: list[dict[str, Any]] = []
    previous = 0.0
    for index, value in enumerate(values, start=1):
        single = value - previous
        if not math.isfinite(single):
            return CalcResult.not_meaningful(
                RESULT_LIMITED,
                details={"field": f"cumulative[{index - 1}]", "value": value},
            )
        rows.append(
            {
                "period_index": index,
                "quarter_end": _quarter_end(fiscal_year, fiscal_start_month, index),
                "cumulative": value,
                "single": single,
            }
        )
        previous = value

    return CalcResult.success(
        rows[-1]["single"],
        unit=unit,
        details={
            "fiscal_year": fiscal_year,
            "fiscal_start_month": fiscal_start_month,
            "single_quarters": rows,
        },
    )


def ttm(*, single_quarters: Sequence[float | None], unit: str = "元") -> CalcResult:
    """TTM = 最近四个单季之和；``None`` 表示缺该季，缺一季即不硬算。"""
    if not unit:
        return CalcResult.bad_argument("unit 不能为空", field="unit")
    if len(single_quarters) != PERIODS_PER_YEAR:
        return CalcResult.bad_argument(
            f"TTM 需要最近 {PERIODS_PER_YEAR} 个单季",
            field="single_quarters",
            count=len(single_quarters),
        )
    missing = [index for index, value in enumerate(single_quarters) if value is None]
    if missing:
        return CalcResult.not_meaningful(
            OUT_OF_DOMAIN,
            details={
                "field": f"single_quarters[{missing[0]}]",
                "missing_indices": missing,
                "message_hint": "TTM 窗口缺季，不硬算",
            },
        )

    values = [float(value) for value in single_quarters if value is not None]
    for index, value in enumerate(values):
        bad = _non_finite(f"single_quarters[{index}]", value)
        if bad is not None:
            return bad

    try:
        total = math.fsum(values)
    except OverflowError:
        return CalcResult.not_meaningful(RESULT_LIMITED, details={"single_quarters": values})
    if not math.isfinite(total):
        return CalcResult.not_meaningful(RESULT_LIMITED, details={"single_quarters": values})

    return CalcResult.success(
        total,
        unit=unit,
        details={"method": "ttm", "window": PERIODS_PER_YEAR, "single_quarters": values},
    )


def ttm_yoy(*, current_ttm: float, prior_ttm: float) -> CalcResult:
    """TTM 同比 = ``current_ttm / prior_ttm - 1``，返回小数；上期 TTM ``<= 0`` 时无意义。"""
    bad = _non_finite("current_ttm", current_ttm) or _non_finite("prior_ttm", prior_ttm)
    if bad is not None:
        return bad
    if prior_ttm <= 0:
        return CalcResult.not_meaningful(
            DENOMINATOR_NON_POSITIVE,
            details={
                "field": "prior_ttm",
                "prior_ttm": prior_ttm,
                "message_hint": "上期 TTM <= 0 时同比无意义",
            },
        )
    value = current_ttm / prior_ttm - 1.0
    if not math.isfinite(value):
        return CalcResult.not_meaningful(
            RESULT_LIMITED, details={"current_ttm": current_ttm, "prior_ttm": prior_ttm}
        )
    return CalcResult.success(
        value,
        unit=UNIT_DECIMAL,
        display=f"{value:.2%}",
        details={"method": "ttm_yoy", "current_ttm": current_ttm, "prior_ttm": prior_ttm},
    )
