"""估值算子：PE_TTM、扣非×4 PE、前瞻 PE、PEG、PE 消化年数、前瞻 CAGR。

口径约定
--------
- 市值与利润必须同一金额单位、同一币种；跨币种先经 ``growth.convert_amount`` 换算。
  本模块只做除法，不做任何单位或汇率猜测。
- 比率 / 增速入参一律小数（``0.25`` = 25%）；百分数误传按字段阈值拦截，
  ``details["field"]`` 指明字段。``bad_argument`` 在端点层映射为 HTTP 400（QUOT-511）。
- 净利润 / 预期净利 / 扣非年化净利 ``<= 0`` 时 PE 类一律 ``not_meaningful``，
  不硬算出负倍数；基期 ``<= 0`` 时 PEG / 前瞻 CAGR 同样不硬算。
- ``value`` 保留全精度（复算真理源），``display`` 只给展示层用。
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any, Final

from ladderstock.calc.result import (
    DENOMINATOR_NON_POSITIVE,
    OUT_OF_DOMAIN,
    RESULT_LIMITED,
    CalcResult,
)

#: 增速 / 复合增速入参的小数上限；超过即判为百分数误传（如把 25% 传成 25）。
PERCENT_LIMITS: Final[Mapping[str, float]] = {"cagr": 5.0, "ttm_yoy": 20.0}

UNIT_MULTIPLE: Final = "倍"
UNIT_DECIMAL: Final = "小数"
UNIT_YEAR: Final = "年"


def _non_finite(field: str, value: float) -> CalcResult | None:
    if not math.isfinite(value):
        return CalcResult.bad_argument("入参必须是有限数值", field=field, value=repr(value))
    return None


def _reject_percent_like(value: float, *, field: str) -> CalcResult | None:
    """``|value|`` 超过该字段的小数上限时判为百分数误传，返回带 ``field`` 的 bad_argument。"""
    limit = PERCENT_LIMITS[field]
    if abs(value) > limit:
        return CalcResult.bad_argument(
            f"{field}={value!r} 超出小数上限 {limit:g}，疑似百分数误传，请统一传小数（25% → 0.25）",
            field=field,
            value=value,
            limit=limit,
            expected={"unit": UNIT_DECIMAL, "example": "25% → 0.25"},
        )
    return None


def _multiple(value: float, *, method: str, details: Mapping[str, Any]) -> CalcResult:
    return CalcResult.success(
        value,
        unit=UNIT_MULTIPLE,
        display=f"{value:.2f} 倍",
        details={"method": method, **details},
    )


def _pe(*, method: str, market_cap: float, profit: float, profit_field: str) -> CalcResult:
    bad = _non_finite("market_cap", market_cap) or _non_finite(profit_field, profit)
    if bad is not None:
        return bad
    if market_cap <= 0:
        return CalcResult.bad_argument("市值必须为正数", field="market_cap", value=market_cap)
    if profit <= 0:
        return CalcResult.not_meaningful(
            DENOMINATOR_NON_POSITIVE,
            details={
                "field": profit_field,
                "market_cap": market_cap,
                "profit": profit,
                "message_hint": "净利 <= 0 时 PE 无意义",
            },
        )
    value = market_cap / profit
    if not math.isfinite(value):
        return CalcResult.not_meaningful(
            RESULT_LIMITED, details={"market_cap": market_cap, "profit": profit}
        )
    return _multiple(
        value, method=method, details={"market_cap": market_cap, profit_field: profit}
    )


def pe_ttm(*, market_cap: float, net_profit_ttm: float) -> CalcResult:
    """PE_TTM = 市值 / TTM 净利。

    ``market_cap`` 与 ``net_profit_ttm`` 必须同单位同币种（如都以「元」计）。
    """
    return _pe(
        method="pe_ttm",
        market_cap=market_cap,
        profit=net_profit_ttm,
        profit_field="net_profit_ttm",
    )


def pe_deducted_x4(*, market_cap: float, latest_quarter_deducted_profit: float) -> CalcResult:
    """扣非×4 PE = 市值 / (最新单季扣非净利 × 4)。

    用最近一个单季扣非净利年化，适合季节性强、TTM 尚未更新的标的。
    """
    bad = _non_finite("latest_quarter_deducted_profit", latest_quarter_deducted_profit)
    if bad is not None:
        return bad
    if latest_quarter_deducted_profit <= 0:
        return CalcResult.not_meaningful(
            DENOMINATOR_NON_POSITIVE,
            details={
                "field": "latest_quarter_deducted_profit",
                "profit": latest_quarter_deducted_profit,
                "message_hint": "单季扣非 <= 0 时 PE 无意义",
            },
        )
    annualized = latest_quarter_deducted_profit * 4.0
    if not math.isfinite(annualized):
        return CalcResult.not_meaningful(
            RESULT_LIMITED,
            details={"latest_quarter_deducted_profit": latest_quarter_deducted_profit},
        )
    return _pe(
        method="pe_deducted_x4",
        market_cap=market_cap,
        profit=annualized,
        profit_field="annualized_deducted_profit",
    )


def pe_forward(*, market_cap: float, forecast_net_profit: float) -> CalcResult:
    """前瞻 PE = 市值 / 一致预期净利。"""
    return _pe(
        method="pe_forward",
        market_cap=market_cap,
        profit=forecast_net_profit,
        profit_field="forecast_net_profit",
    )


def peg(*, pe: float, ttm_yoy: float) -> CalcResult:
    """PEG = PE / 净利增速（百分点口径）。

    ``ttm_yoy`` 传小数：``0.25``（25%）→ PEG = ``pe / 25``。增速 ``<= 0`` 时无意义。
    """
    bad = _non_finite("pe", pe) or _non_finite("ttm_yoy", ttm_yoy)
    if bad is not None:
        return bad
    bad = _reject_percent_like(ttm_yoy, field="ttm_yoy")
    if bad is not None:
        return bad
    if pe <= 0:
        return CalcResult.bad_argument("PE 必须为正数", field="pe", value=pe)
    if ttm_yoy <= 0:
        return CalcResult.not_meaningful(
            OUT_OF_DOMAIN,
            details={
                "field": "ttm_yoy",
                "value": ttm_yoy,
                "message_hint": "增速 <= 0 时 PEG 无意义",
            },
        )
    growth_percent = ttm_yoy * 100.0
    value = pe / growth_percent
    if not math.isfinite(value):
        return CalcResult.not_meaningful(
            RESULT_LIMITED, details={"pe": pe, "growth_percent": growth_percent}
        )
    return _multiple(value, method="peg", details={"pe": pe, "growth_percent": growth_percent})


def pe_digestion_years(*, pe: float, target_pe: float, cagr: float) -> CalcResult:
    """当前 PE 以 ``cagr`` 增速消化到目标 PE 所需年数：``ln(pe / target_pe) / ln(1 + cagr)``。

    假定股价不变、盈利按 ``cagr`` 复合增长。PE 已不高于目标时返回 0 年。
    """
    bad = _non_finite("pe", pe) or _non_finite("target_pe", target_pe) or _non_finite("cagr", cagr)
    if bad is not None:
        return bad
    bad = _reject_percent_like(cagr, field="cagr")
    if bad is not None:
        return bad
    if pe <= 0:
        return CalcResult.bad_argument("当前 PE 必须为正数", field="pe", value=pe)
    if target_pe <= 0:
        return CalcResult.bad_argument("目标 PE 必须为正数", field="target_pe", value=target_pe)
    if pe <= target_pe:
        return CalcResult.success(
            0.0,
            unit=UNIT_YEAR,
            display="0.00 年",
            details={"pe": pe, "target_pe": target_pe, "cagr": cagr, "already_at_target": True},
        )
    if cagr <= -1.0:
        return CalcResult.not_meaningful(
            OUT_OF_DOMAIN,
            details={"field": "cagr", "value": cagr, "message_hint": "增速 <= -100% 时无法取对数"},
        )
    if cagr <= 0:
        return CalcResult.not_meaningful(
            OUT_OF_DOMAIN,
            details={
                "field": "cagr",
                "value": cagr,
                "message_hint": "非正增速无法把 PE 消化到更低目标",
            },
        )
    years = math.log(pe / target_pe) / math.log1p(cagr)
    if not math.isfinite(years):
        return CalcResult.not_meaningful(
            RESULT_LIMITED, details={"pe": pe, "target_pe": target_pe, "cagr": cagr}
        )
    return CalcResult.success(
        years,
        unit=UNIT_YEAR,
        display=f"{years:.2f} 年",
        details={"pe": pe, "target_pe": target_pe, "cagr": cagr},
    )


def forward_cagr(*, current_profit: float, target_profit: float, years: float) -> CalcResult:
    """前瞻 CAGR = ``(target_profit / current_profit) ** (1 / years) - 1``，返回小数。"""
    bad = (
        _non_finite("current_profit", current_profit)
        or _non_finite("target_profit", target_profit)
        or _non_finite("years", years)
    )
    if bad is not None:
        return bad
    if years <= 0:
        return CalcResult.bad_argument("年数必须为正数", field="years", value=years)
    if current_profit <= 0:
        return CalcResult.not_meaningful(
            DENOMINATOR_NON_POSITIVE,
            details={
                "field": "current_profit",
                "value": current_profit,
                "message_hint": "基期净利 <= 0 时 CAGR 无意义",
            },
        )
    if target_profit <= 0:
        return CalcResult.not_meaningful(
            OUT_OF_DOMAIN,
            details={
                "field": "target_profit",
                "value": target_profit,
                "message_hint": "目标净利 <= 0 时复合增速无意义",
            },
        )
    ratio = target_profit / current_profit
    if not math.isfinite(ratio):
        return CalcResult.not_meaningful(
            RESULT_LIMITED,
            details={"current_profit": current_profit, "target_profit": target_profit},
        )
    value = ratio ** (1.0 / years) - 1.0
    if not math.isfinite(value):
        return CalcResult.not_meaningful(
            RESULT_LIMITED,
            details={"current_profit": current_profit, "target_profit": target_profit},
        )
    return CalcResult.success(
        value,
        unit=UNIT_DECIMAL,
        display=f"{value:.2%}",
        details={
            "current_profit": current_profit,
            "target_profit": target_profit,
            "years": years,
        },
    )
