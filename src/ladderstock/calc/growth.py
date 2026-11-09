"""成长与比率算子：同比 / 环比增速、毛利率 / 费用率 / 负债率、汇率换算。

口径约定
--------
- 比率与增速一律小数（``0.25`` = 25%），返回 ``unit="小数"``，``display`` 才给百分号。
- 基期 ``<= 0`` / 分母 ``<= 0`` 时一律 ``not_meaningful``，不硬算倍数或比率。
- 汇率换算完全复用 ``money`` 的 ``fx_rate`` + ``fx_unit`` 契约：跨币种必须成对给，
  同币种多给汇率同样 ``bad_argument``，本模块不另造一套换算口径。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Final

from ladderstock.calc import money
from ladderstock.calc.result import (
    DENOMINATOR_NON_POSITIVE,
    RESULT_LIMITED,
    CalcResult,
)

UNIT_DECIMAL: Final = "小数"


def _non_finite(field: str, value: float) -> CalcResult | None:
    if not math.isfinite(value):
        return CalcResult.bad_argument("入参必须是有限数值", field=field, value=repr(value))
    return None


def _decimal(value: float, *, method: str, details: Mapping[str, Any]) -> CalcResult:
    return CalcResult.success(
        value, unit=UNIT_DECIMAL, display=f"{value:.2%}", details={"method": method, **details}
    )


def _rate(*, current: float, base: float, base_field: str, method: str) -> CalcResult:
    bad = _non_finite("current", current) or _non_finite(base_field, base)
    if bad is not None:
        return bad
    if base <= 0:
        return CalcResult.not_meaningful(
            DENOMINATOR_NON_POSITIVE,
            details={
                "field": base_field,
                "base": base,
                "message_hint": "基期为 0 或负时增速无意义",
            },
        )
    value = current / base - 1.0
    if not math.isfinite(value):
        return CalcResult.not_meaningful(
            RESULT_LIMITED, details={"current": current, base_field: base}
        )
    return _decimal(value, method=method, details={"current": current, "base": base})


def yoy(*, current: float, prior: float) -> CalcResult:
    """同比增速 = ``current / prior - 1``；``prior`` 为上年同期值。"""
    return _rate(current=current, base=prior, base_field="prior", method="yoy")


def qoq(*, current: float, prior: float) -> CalcResult:
    """环比增速 = ``current / prior - 1``；``prior`` 为上一季度值。"""
    return _rate(current=current, base=prior, base_field="prior", method="qoq")


def _decimal_ratio(
    *,
    numerator: float,
    denominator: float,
    numerator_field: str,
    denominator_field: str,
    method: str,
) -> CalcResult:
    bad = _non_finite(numerator_field, numerator) or _non_finite(denominator_field, denominator)
    if bad is not None:
        return bad
    if denominator <= 0:
        return CalcResult.not_meaningful(
            DENOMINATOR_NON_POSITIVE,
            details={
                "field": denominator_field,
                denominator_field: denominator,
                "message_hint": "分母 <= 0 时比率无意义",
            },
        )
    value = numerator / denominator
    if not math.isfinite(value):
        return CalcResult.not_meaningful(
            RESULT_LIMITED, details={numerator_field: numerator, denominator_field: denominator}
        )
    return _decimal(
        value,
        method=method,
        details={numerator_field: numerator, denominator_field: denominator},
    )


def gross_margin(*, gross_profit: float, revenue: float) -> CalcResult:
    """毛利率 = 毛利 / 营收，返回小数。"""
    return _decimal_ratio(
        numerator=gross_profit,
        denominator=revenue,
        numerator_field="gross_profit",
        denominator_field="revenue",
        method="gross_margin",
    )


def expense_ratio(*, expense: float, revenue: float) -> CalcResult:
    """费用率 = 费用 / 营收，返回小数。"""
    return _decimal_ratio(
        numerator=expense,
        denominator=revenue,
        numerator_field="expense",
        denominator_field="revenue",
        method="expense_ratio",
    )


def debt_ratio(*, liabilities: float, total_assets: float) -> CalcResult:
    """负债率 = 负债 / 总资产，返回小数。"""
    return _decimal_ratio(
        numerator=liabilities,
        denominator=total_assets,
        numerator_field="liabilities",
        denominator_field="total_assets",
        method="debt_ratio",
    )


def convert_amount(
    result: CalcResult,
    *,
    target_currency: str = "CNY",
    display_unit: str | None = None,
    fx_rate: float | None = None,
    fx_unit: str | None = None,
) -> CalcResult:
    """金额结果换币 / 换单位：契约完全复用 ``money.convert``。

    ``result`` 必须带 ``currency`` 元信息（由 ``money.amount`` 产出）；
    本身不是 ok 的结果（含 ``not_meaningful``）原样透传，不被换算成有意义的数。
    """
    return money.convert(
        result,
        target_currency=target_currency,
        display_unit=display_unit,
        fx_rate=fx_rate,
        fx_unit=fx_unit,
    )
