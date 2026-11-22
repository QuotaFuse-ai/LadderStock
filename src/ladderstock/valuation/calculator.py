"""估值计算器（E 包 / DEC-091 §4）：用户面换算工具，**不是研究产物**。

边界（防碰 DEC-025 红线——研究估值段的「数字唯一来源是 facts」）：

- 输入全部由调用方显式给出（CLI 参数 / 页面表单）；本模块**不取数、不写库**；
- 输出恒带 `disclaimer`：「换算器，不是估值判断」；
- 研究管线的 valuation 段照旧：计算器结果不进 facts、不进段内 prompt。

算什么（五类，全部纯函数）：PE / PB / PS、股息率、盈利收益率、两段简化 DCF。
每个函数缺输入就抛用法错误（exit 2），**不补 0、不猜**；分母为 0 / 负价格
同样拒（倍数无意义）。
"""

from __future__ import annotations

from typing import Any

from ladderstock.errors import EXIT_USAGE, LadderStockError

DISCLAIMER = "这是换算器，不是估值判断：数字全部来自你给的输入，不构成投资建议。"

#: DCF 参数护栏（荒谬输入直接拒，不给「算出来但没意义」的数）
MAX_GROWTH = 1.0  # 增速 100%/段 封顶
MAX_DISCOUNT = 0.5
MIN_PERIODS = 1
MAX_PERIODS = 20


def _positive(value: float, name: str) -> float:
    if value <= 0:
        raise LadderStockError(f"{name} 必须为正数，收到 {value}", EXIT_USAGE)
    return value


def _non_negative(value: float, name: str) -> float:
    if value < 0:
        raise LadderStockError(f"{name} 不能为负，收到 {value}", EXIT_USAGE)
    return value


def multiple(price: float, per_share: float, name: str) -> dict[str, Any]:
    """PE / PB / PS 的公共式：price / per_share（每股口径必须同源）。"""
    _positive(price, "price")
    _positive(per_share, f"{name} 分母（每股指标）")
    value = price / per_share
    return {
        "kind": name,
        "price": price,
        "per_share": per_share,
        "value": round(value, 4),
        "disclaimer": DISCLAIMER,
    }


def pe(price: float, eps: float) -> dict[str, Any]:
    return multiple(price, eps, "PE")


def pb(price: float, bps: float) -> dict[str, Any]:
    return multiple(price, bps, "PB")


def ps(price: float, sps: float) -> dict[str, Any]:
    return multiple(price, sps, "PS")


def dividend_yield(price: float, dps: float) -> dict[str, Any]:
    """股息率 = 每股分红 / 价格（百分比口径）。"""
    _positive(price, "price")
    _non_negative(dps, "每股分红")
    rate = dps / price
    return {
        "kind": "股息率",
        "price": price,
        "dps": dps,
        "value": round(rate, 6),
        "value_pct": f"{rate * 100:.2f}%",
        "disclaimer": DISCLAIMER,
    }


def earnings_yield(pe_value: float) -> dict[str, Any]:
    """盈利收益率 = 1 / PE（与债券收益率同口径比较）。"""
    _positive(pe_value, "PE")
    rate = 1.0 / pe_value
    return {
        "kind": "盈利收益率",
        "pe": pe_value,
        "value": round(rate, 6),
        "value_pct": f"{rate * 100:.2f}%",
        "disclaimer": DISCLAIMER,
    }


def dcf_simple(
    base_fcf: float,
    growth: float,
    terminal_growth: float,
    discount: float,
    periods: int,
) -> dict[str, Any]:
    """两段简化 DCF：前 `periods` 年按 `growth` 增长，之后永续 `terminal_growth`。

    - `growth` / `terminal_growth` 是小数（0.08 = 8%）；
    - 终值 = 末年 FCF × (1+g_T) / (r − g_T)，要求 r > g_T（否则永续值无意义）；
    - 输出终值占比——占比 > 60% 的结论（「价值大半在终值」）要在 warnings 里点出来。
    """
    _positive(base_fcf, "基期自由现金流")
    _non_negative(growth, "增长段增速")
    _non_negative(terminal_growth, "终值增速")
    _positive(discount, "折现率")
    if growth > MAX_GROWTH or terminal_growth > MAX_GROWTH:
        raise LadderStockError(
            f"增速超过 {MAX_GROWTH:.0%}/段：护栏拒绝（输入 {growth}/{terminal_growth}）",
            EXIT_USAGE,
        )
    if discount > MAX_DISCOUNT:
        raise LadderStockError(
            f"折现率超过 {MAX_DISCOUNT:.0%}：护栏拒绝（输入 {discount}）", EXIT_USAGE
        )
    if not MIN_PERIODS <= periods <= MAX_PERIODS:
        raise LadderStockError(
            f"期数需在 {MIN_PERIODS}–{MAX_PERIODS} 年（收到 {periods}）", EXIT_USAGE
        )
    if discount <= terminal_growth:
        raise LadderStockError(
            f"折现率（{discount}）必须大于终值增速（{terminal_growth}），否则永续值无意义",
            EXIT_USAGE,
        )

    pv_explicit = 0.0
    flow = base_fcf
    for year in range(1, periods + 1):
        flow = flow * (1.0 + growth)
        pv_explicit += flow / (1.0 + discount) ** year
    terminal = flow * (1.0 + terminal_growth) / (discount - terminal_growth)
    pv_terminal = terminal / (1.0 + discount) ** periods
    total = pv_explicit + pv_terminal
    share = pv_terminal / total if total > 0 else None
    return {
        "kind": "简化两段 DCF",
        "base_fcf": base_fcf,
        "growth": growth,
        "terminal_growth": terminal_growth,
        "discount": discount,
        "periods": periods,
        "pv_explicit": round(pv_explicit, 4),
        "pv_terminal": round(pv_terminal, 4),
        "value": round(total, 4),
        "terminal_share": round(share, 4) if share is not None else None,
        "warnings": ["终值占比超过 60%：结论主要由永续假设驱动——换个增速/折现率再算一次再下结论。"]
        if share is not None and share > 0.6
        else [],
        "disclaimer": DISCLAIMER,
    }


__all__ = [
    "DISCLAIMER",
    "dcf_simple",
    "dividend_yield",
    "earnings_yield",
    "pb",
    "pe",
    "ps",
]
