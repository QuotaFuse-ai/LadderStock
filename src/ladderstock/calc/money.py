"""金额与币种：同一币种内的 元 / 万元 / 亿元 换算，跨币种显式汇率换算。

汇率协议：``fx_unit`` 形如 ``CNY/HKD``，表示「1 基准币 = ? 报价币」，
``fx_rate`` 即该比值。跨币种缺 ``fx_rate`` 或 ``fx_unit`` 任一 → ``bad_argument``；
币种相同却给了汇率 → 同样 ``bad_argument``（多给就是错），绝不猜汇率。
"""

from __future__ import annotations

import math
from typing import Final

from ladderstock.calc.result import CalcResult

CURRENCIES: Final = frozenset({"CNY", "HKD", "USD"})

#: 单位 -> 元 的倍率
UNIT_TO_YUAN: Final[dict[str, float]] = {"元": 1.0, "万元": 1e4, "亿元": 1e8}

#: 展示用小数位（仅作用于 display，value 不四舍五入）
_UNIT_DECIMALS: Final[dict[str, int]] = {"元": 2, "万元": 4, "亿元": 6}


def _fmt(value: float, decimals: int) -> str:
    if value == 0:
        return f"{0:.{decimals}f}"
    magnitude = abs(value)
    if magnitude >= 1e15 or magnitude < 10 ** (-decimals):
        return f"{value:.{decimals}e}"
    return f"{value:,.{decimals}f}"


def _display(value: float, currency: str, unit: str) -> str:
    return f"{_fmt(value, _UNIT_DECIMALS[unit])} {unit} {currency}"


def _parse_fx_unit(fx_unit: str) -> tuple[str, str]:
    base, sep, quote = fx_unit.partition("/")
    if not sep or not base or not quote:
        raise ValueError(f"fx_unit 形如 'CNY/HKD'，收到 {fx_unit!r}")
    return base, quote


def _rate_multiplier(currency: str, target: str, fx_rate: float, fx_unit: str) -> float | None:
    """「1 个 currency = ? 个 target」的乘数；给出的汇率与本次换算两端不匹配时返回 None。"""
    base, quote = _parse_fx_unit(fx_unit)
    if base == currency and quote == target:
        return fx_rate
    if quote == currency and base == target:
        return 1.0 / fx_rate
    return None


def _check_fx(
    currency: str, target: str, fx_rate: float | None, fx_unit: str | None
) -> float | str:
    """返回「1 个 currency = ? 个 target」的乘数；非法时返回错误说明。"""
    if currency == target:
        if fx_rate is not None or fx_unit is not None:
            return f"币种相同（{currency}）却给了汇率，多给就是错"
        return 1.0
    if fx_rate is None or fx_unit is None:
        return f"{currency} → {target} 跨币种换算必须同时给 fx_rate 与 fx_unit"
    if not math.isfinite(fx_rate) or fx_rate <= 0:
        return f"fx_rate 必须是正的有限数，收到 {fx_rate!r}"
    try:
        base, quote = _parse_fx_unit(fx_unit)
    except ValueError as exc:
        return str(exc)
    if base not in CURRENCIES or quote not in CURRENCIES or base == quote:
        return f"fx_unit={fx_unit!r} 不合法（形如 'CNY/HKD' 且两端为已知不同币种）"
    multiplier = _rate_multiplier(currency, target, fx_rate, fx_unit)
    if multiplier is None:
        return f"fx_unit={fx_unit!r} 与 {currency} → {target} 不匹配"
    if not math.isfinite(multiplier) or multiplier <= 0:
        return f"汇率换算结果非法: {multiplier!r}"
    return multiplier


def amount(
    value: float,
    *,
    currency: str,
    unit: str,
    display_unit: str | None = None,
    target_currency: str | None = None,
    fx_rate: float | None = None,
    fx_unit: str | None = None,
) -> CalcResult:
    """构造一个金额结果：可直接换算显示单位（元/万元/亿元），也可跨币种换算。"""
    if currency not in CURRENCIES:
        return CalcResult.bad_argument(f"未知币种 {currency!r}", supported=sorted(CURRENCIES))
    if unit not in UNIT_TO_YUAN:
        return CalcResult.bad_argument(f"未知单位 {unit!r}", supported=sorted(UNIT_TO_YUAN))
    if display_unit is not None and display_unit not in UNIT_TO_YUAN:
        return CalcResult.bad_argument(f"未知展示单位 {display_unit!r}")
    if not math.isfinite(value):
        return CalcResult.bad_argument("金额必须是有限数值", value=repr(value))
    if target_currency is not None and target_currency not in CURRENCIES:
        return CalcResult.bad_argument(f"未知目标币种 {target_currency!r}")

    target = target_currency or currency
    multiplier = _check_fx(currency, target, fx_rate, fx_unit)
    if isinstance(multiplier, str):
        return CalcResult.bad_argument(multiplier)

    yuan = value * UNIT_TO_YUAN[unit] * multiplier
    if not math.isfinite(yuan):
        return CalcResult.bad_argument("金额换算溢出 float64 有限范围", value=repr(value))

    out_unit = display_unit or unit
    result_value = yuan / UNIT_TO_YUAN[out_unit]
    details: dict[str, object] = {
        "currency": target,
        "source_currency": currency,
        "source_unit": unit,
    }
    if currency != target:
        details["fx_rate"] = fx_rate
        details["fx_unit"] = fx_unit
    return CalcResult.success(
        result_value,
        unit=out_unit,
        display=_display(result_value, target, out_unit),
        details=details,
    )


def convert(
    result: CalcResult,
    *,
    target_currency: str = "CNY",
    display_unit: str | None = None,
    fx_rate: float | None = None,
    fx_unit: str | None = None,
) -> CalcResult:
    """把带币种信息的金额结果换算到目标币种 / 展示单位。"""
    if not result.ok:
        return result
    meta = result.details
    currency = meta.get("currency")
    unit = result.unit
    if currency not in CURRENCIES or unit not in UNIT_TO_YUAN or result.value is None:
        return CalcResult.bad_argument(
            "该结果不带可换算的金额元信息（需 currency 与 元/万元/亿元 单位）",
            got_currency=currency,
            got_unit=unit,
        )
    if target_currency not in CURRENCIES:
        return CalcResult.bad_argument(f"未知目标币种 {target_currency!r}")
    return amount(
        float(result.value),
        currency=currency,
        unit=unit,
        display_unit=display_unit or unit,
        target_currency=target_currency,
        fx_rate=fx_rate,
        fx_unit=fx_unit,
    )
