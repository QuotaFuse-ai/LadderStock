"""技术指标与筹码集中度（纯函数，不读文件 / 不联网 / 不读环境变量 / 无全局可变状态）。

全部按「最新一根 K 线」取值，输入是调用方传入的有序行序列
``{date, close[, high, low, volume]}``；乱序会自动按日期升序，重复日期视为非法。

口径
----
- MA：``period`` 日简单算术平均。
- RSI：简单平均（不是 Wilder 平滑）。用最近 ``period`` 个涨跌幅，取值 0–100；
  平均跌幅为 0 时按惯例记 100（只对齐口径，不解读超买超卖）。
- MACD：``dif = EMA(fast) - EMA(slow)``，``dea = EMA(signal)(dif)``，
  ``hist = (dif - dea) * 2``。**EMA 以序列第一个值为种子（seed = first value）**，
  平滑系数 ``k = 2 / (n + 1)``。另一种常见约定是用前 n 日 SMA 作初值；本模块不用，
  因为首值种子在足够长的序列上收敛，长期结果无差异，写清楚是为了可复算。
- ATR：真实波幅 ``TR = max(高 - 低, |高 - 昨收|, |低 - 昨收|)``，
  取最近 ``period`` 个 TR 的简单平均（不做 Wilder 平滑）。
- BOLL：中轨 = ``period`` 日简单均线；标准差用**总体标准差**（除以 N，不是 N-1）；
  上下轨 = 中轨 ± ``num_std`` × 标准差。
- 筹码集中度：按**成交量加权的收盘价分布**计算，不按换手率逐日衰减重建持仓成本
  （本模块输入只有 OHLCV，没有流通股本）。每根 K 线的收盘价为价位、成交量为权重，
  归一化后取加权 ``low_quantile`` / ``high_quantile`` 分位价，集中度 =
  ``(P_high - P_low) / (P_high + P_low)``。取值 0–1，**越小越集中**（0 表示所有成交同价）。
- 样本不足：一律返回 ``not_meaningful``，``details`` 带 ``required`` / ``actual`` /
  ``missing`` 和「需 N 根，实得 M 根，差 K 根」；不截短窗口、不用前值填充。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any, Final

from ladderstock.calc.fingerprint import data_fingerprint
from ladderstock.calc.result import (
    DENOMINATOR_NON_POSITIVE,
    OUT_OF_DOMAIN,
    CalcResult,
)

PRICE_UNIT: Final = "元"
RATIO_UNIT: Final = "小数"
RSI_UNIT: Final = "指数"
DEFAULT_MA_PERIODS: Final = (5, 10, 20, 60)
MIN_CHIP_BARS: Final = 20

_PRICE_FIELDS: Final = frozenset({"open", "high", "low", "close"})


class KlineInputError(ValueError):
    """K 线行序列不合法（缺列 / 非数 / 非有限 / 价格非正 / 重复日期）。"""


def parse_kline_rows(
    rows: Sequence[Mapping[str, Any]], fields: Sequence[str]
) -> list[dict[str, Any]]:
    """校验并规范化 K 线行：``fields`` 必填，按日期升序返回，重复日期报错。

    价格类字段必须为正且 OHLC 自洽（``high >= low``，``open`` / ``close`` 落在区间内）；
    ``volume`` 必须非负。返回新列表，不改入参。
    """
    if isinstance(rows, str | bytes | bytearray) or not isinstance(rows, Sequence):
        raise KlineInputError("K 线必须是行列表")
    if not rows:
        raise KlineInputError("K 线不能为空")
    required = tuple(dict.fromkeys(fields))
    if not required:
        raise KlineInputError("至少需要一个字段")
    unknown = sorted(set(required) - _PRICE_FIELDS - {"volume"})
    if unknown:
        raise KlineInputError(f"未知 K 线字段: {unknown}")

    parsed: list[dict[str, Any]] = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, Mapping):
            raise KlineInputError(f"K 线[{index}] 不是对象")
        date = raw.get("date")
        if not isinstance(date, str) or not date.strip():
            raise KlineInputError(f"K 线[{index}] 缺 date")
        row: dict[str, Any] = {"date": date.strip()}
        for field in required:
            value = raw.get(field)
            if value is None or isinstance(value, bool):
                raise KlineInputError(f"K 线[{index}]({row['date']})缺 {field} 或不是数值")
            try:
                number = float(value)
            except (TypeError, ValueError) as exc:
                raise KlineInputError(
                    f"K 线[{index}]({row['date']}){field} 非数: {value!r}"
                ) from exc
            if not math.isfinite(number):
                raise KlineInputError(f"K 线[{index}]({row['date']}){field} 非有限数")
            row[field] = number
        for field in _PRICE_FIELDS & set(required):
            if row[field] <= 0:
                raise KlineInputError(f"K 线[{index}]({row['date']}){field} 必须为正")
        if "volume" in required and row["volume"] < 0:
            raise KlineInputError(f"K 线[{index}]({row['date']})volume 不能为负")
        if "high" in required and "low" in required:
            if row["high"] < row["low"]:
                raise KlineInputError(f"K 线[{index}]({row['date']}) high < low")
            for field in ("open", "close"):
                if field in required and not (row["low"] <= row[field] <= row["high"]):
                    raise KlineInputError(
                        f"K 线[{index}]({row['date']}) {field} 不在 [low, high] 内"
                    )
        parsed.append(row)

    parsed.sort(key=lambda item: item["date"])
    for previous, current in zip(parsed, parsed[1:], strict=False):
        if previous["date"] == current["date"]:
            raise KlineInputError(f"K 线有重复日期 {current['date']}")
    return parsed


def _fingerprint(rows: Sequence[Mapping[str, Any]]) -> str:
    return data_fingerprint(rows)


def _insufficient(rows: Sequence[Mapping[str, Any]], required: int) -> CalcResult:
    actual = len(rows)
    missing = required - actual
    return CalcResult.not_meaningful(
        OUT_OF_DOMAIN,
        details={
            "message": f"样本不足：需 {required} 根，实得 {actual} 根，差 {missing} 根",
            "required": required,
            "actual": actual,
            "missing": missing,
            "insufficient_sample": True,
            "data_fp": _fingerprint(rows),
        },
    )


def _validated_period(name: str, value: object, *, minimum: int = 1) -> str | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        return f"{name} 必须是 >= {minimum} 的整数，收到 {value!r}"
    return None


def moving_average(
    rows: Sequence[Mapping[str, Any]],
    period: int = 5,
    *,
    unit: str = PRICE_UNIT,
) -> CalcResult:
    """最新一根的 ``period`` 日简单均线；样本少于 ``period`` 根时不硬算。"""
    try:
        parsed = parse_kline_rows(rows, ("close",))
    except KlineInputError as exc:
        return CalcResult.bad_argument(str(exc))
    error = _validated_period("period", period)
    if error is not None:
        return CalcResult.bad_argument(error)
    if not unit:
        return CalcResult.bad_argument("unit 不能为空")
    if len(parsed) < period:
        return _insufficient(parsed, period)
    closes = [float(row["close"]) for row in parsed]
    value = math.fsum(closes[-period:]) / period
    return CalcResult.success(
        value,
        unit=unit,
        details={
            "period": period,
            "actual": len(parsed),
            "window": [parsed[-period]["date"], parsed[-1]["date"]],
            "data_fp": _fingerprint(parsed),
        },
    )


def rsi(
    rows: Sequence[Mapping[str, Any]],
    period: int = 14,
) -> CalcResult:
    """最新一根的 RSI(``period``)，0–100；简单平均，平均跌幅为 0 时记 100。"""
    try:
        parsed = parse_kline_rows(rows, ("close",))
    except KlineInputError as exc:
        return CalcResult.bad_argument(str(exc))
    error = _validated_period("period", period)
    if error is not None:
        return CalcResult.bad_argument(error)
    required = period + 1
    if len(parsed) < required:
        return _insufficient(parsed, required)
    closes = [float(row["close"]) for row in parsed]
    changes = [current - previous for previous, current in zip(closes, closes[1:], strict=False)]
    window = changes[-period:]
    average_gain = math.fsum(max(change, 0.0) for change in window) / period
    average_loss = math.fsum(max(-change, 0.0) for change in window) / period
    if average_loss == 0.0:
        value = 100.0
    else:
        value = 100.0 - 100.0 / (1.0 + average_gain / average_loss)
    return CalcResult.success(
        value,
        unit=RSI_UNIT,
        details={
            "period": period,
            "actual": len(parsed),
            "window": [parsed[-required]["date"], parsed[-1]["date"]],
            "average_gain": average_gain,
            "average_loss": average_loss,
            "data_fp": _fingerprint(parsed),
        },
    )


def _ema(values: Sequence[float], period: int) -> list[float]:
    # 初值约定：seed = 第一个值（不是 SMA 初值），k = 2/(n+1)。
    factor = 2.0 / (period + 1.0)
    output = [values[0]]
    for value in values[1:]:
        output.append(value * factor + output[-1] * (1.0 - factor))
    return output


def macd(
    rows: Sequence[Mapping[str, Any]],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
    *,
    unit: str = PRICE_UNIT,
) -> CalcResult:
    """最新一根的 MACD(fast, slow, signal)。

    ``value`` 是柱值 ``hist``，``details`` 另给 ``dif`` / ``dea``。
    EMA 初值用序列首值（见模块 docstring），样本少于 ``slow`` 根时不硬算。
    """
    try:
        parsed = parse_kline_rows(rows, ("close",))
    except KlineInputError as exc:
        return CalcResult.bad_argument(str(exc))
    for name, value in (("fast", fast), ("slow", slow), ("signal", signal)):
        error = _validated_period(name, value)
        if error is not None:
            return CalcResult.bad_argument(error)
    if fast >= slow:
        return CalcResult.bad_argument(f"fast({fast}) 必须小于 slow({slow})")
    if not unit:
        return CalcResult.bad_argument("unit 不能为空")
    if len(parsed) < slow:
        return _insufficient(parsed, slow)

    closes = [float(row["close"]) for row in parsed]
    fast_ema = _ema(closes, fast)
    slow_ema = _ema(closes, slow)
    dif = [
        fast_value - slow_value for fast_value, slow_value in zip(fast_ema, slow_ema, strict=True)
    ]
    dea = _ema(dif, signal)
    hist = (dif[-1] - dea[-1]) * 2.0
    return CalcResult.success(
        hist,
        unit=unit,
        details={
            "fast": fast,
            "slow": slow,
            "signal": signal,
            "dif": dif[-1],
            "dea": dea[-1],
            "hist": hist,
            "actual": len(parsed),
            "window": [parsed[0]["date"], parsed[-1]["date"]],
            "ema_seed": "first_value",
            "data_fp": _fingerprint(parsed),
        },
    )


def atr(
    rows: Sequence[Mapping[str, Any]],
    period: int = 14,
    *,
    unit: str = PRICE_UNIT,
) -> CalcResult:
    """最新一根的 ATR(``period``)；真实波幅的简单平均，需要 ``period + 1`` 根 K 线。"""
    try:
        parsed = parse_kline_rows(rows, ("high", "low", "close"))
    except KlineInputError as exc:
        return CalcResult.bad_argument(str(exc))
    error = _validated_period("period", period)
    if error is not None:
        return CalcResult.bad_argument(error)
    if not unit:
        return CalcResult.bad_argument("unit 不能为空")
    required = period + 1
    if len(parsed) < required:
        return _insufficient(parsed, required)

    true_ranges: list[float] = []
    for previous, current in zip(parsed, parsed[1:], strict=False):
        true_ranges.append(
            max(
                float(current["high"]) - float(current["low"]),
                abs(float(current["high"]) - float(previous["close"])),
                abs(float(current["low"]) - float(previous["close"])),
            )
        )
    value = math.fsum(true_ranges[-period:]) / period
    return CalcResult.success(
        value,
        unit=unit,
        details={
            "period": period,
            "actual": len(parsed),
            "window": [parsed[-required]["date"], parsed[-1]["date"]],
            "data_fp": _fingerprint(parsed),
        },
    )


def bollinger(
    rows: Sequence[Mapping[str, Any]],
    period: int = 20,
    num_std: float = 2.0,
    *,
    unit: str = PRICE_UNIT,
) -> CalcResult:
    """最新一根的布林带(``period``, ``num_std`` 倍总体标准差)。

    ``value`` 是中轨，``details`` 另给 ``upper`` / ``lower`` / ``std`` / ``bandwidth``。
    """
    try:
        parsed = parse_kline_rows(rows, ("close",))
    except KlineInputError as exc:
        return CalcResult.bad_argument(str(exc))
    error = _validated_period("period", period)
    if error is not None:
        return CalcResult.bad_argument(error)
    if isinstance(num_std, bool) or not isinstance(num_std, int | float):
        return CalcResult.bad_argument(f"num_std 必须是数值，收到 {num_std!r}")
    if not math.isfinite(num_std) or num_std <= 0:
        return CalcResult.bad_argument(f"num_std 必须是正的有限数，收到 {num_std!r}")
    if not unit:
        return CalcResult.bad_argument("unit 不能为空")
    if len(parsed) < period:
        return _insufficient(parsed, period)

    closes = [float(row["close"]) for row in parsed]
    window = closes[-period:]
    middle = math.fsum(window) / period
    variance = math.fsum((value - middle) ** 2 for value in window) / period
    std = math.sqrt(variance)
    upper = middle + num_std * std
    lower = middle - num_std * std
    return CalcResult.success(
        middle,
        unit=unit,
        details={
            "period": period,
            "num_std": float(num_std),
            "upper": upper,
            "middle": middle,
            "lower": lower,
            "std": std,
            "bandwidth": (upper - lower) / middle,
            "actual": len(parsed),
            "window": [parsed[-period]["date"], parsed[-1]["date"]],
            "data_fp": _fingerprint(parsed),
        },
    )


def _weighted_quantile(
    pairs: Sequence[tuple[float, float]],
    quantile: float,
) -> float:
    """按权重归一化后的分位价：在跨越分位点的两根 K 线之间线性插值。"""
    total = math.fsum(weight for _, weight in pairs)
    cumulative = 0.0
    previous_price = pairs[0][0]
    for price, weight in pairs:
        previous_cumulative = cumulative
        cumulative += weight / total
        if cumulative >= quantile:
            if weight <= 0.0:
                return price
            fraction = (quantile - previous_cumulative) / (weight / total)
            return previous_price + (price - previous_price) * fraction
        previous_price = price
    return pairs[-1][0]


def chip_concentration(
    rows: Sequence[Mapping[str, Any]],
    *,
    low_quantile: float = 0.15,
    high_quantile: float = 0.85,
) -> CalcResult:
    """成交量加权收盘价分布的筹码集中度（0–1，越小越集中）。

    ``value = (P_high - P_low) / (P_high + P_low)``；``details`` 给 ``low_price`` /
    ``high_price`` / ``vwap``。成交量全为 0 时分位价无定义，返回 ``not_meaningful``。
    """
    try:
        parsed = parse_kline_rows(rows, ("close", "volume"))
    except KlineInputError as exc:
        return CalcResult.bad_argument(str(exc))
    for name, value in (("low_quantile", low_quantile), ("high_quantile", high_quantile)):
        if isinstance(value, bool) or not isinstance(value, int | float):
            return CalcResult.bad_argument(f"{name} 必须是数值，收到 {value!r}")
        if not math.isfinite(value) or not 0 < value < 1:
            return CalcResult.bad_argument(f"{name} 必须落在 (0, 1)，收到 {value!r}")
    if not low_quantile < high_quantile:
        return CalcResult.bad_argument(
            f"low_quantile({low_quantile}) 必须小于 high_quantile({high_quantile})"
        )
    if len(parsed) < MIN_CHIP_BARS:
        return _insufficient(parsed, MIN_CHIP_BARS)

    pairs = sorted(
        ((float(row["close"]), float(row["volume"])) for row in parsed),
        key=lambda pair: pair[0],
    )
    pairs = [(price, volume) for price, volume in pairs if volume > 0.0]
    total_volume = math.fsum(volume for _, volume in pairs)
    if total_volume <= 0.0:
        return CalcResult.not_meaningful(
            DENOMINATOR_NON_POSITIVE,
            details={
                "message": "成交量为 0，成交量加权分布无定义",
                "actual": len(parsed),
                "data_fp": _fingerprint(parsed),
            },
        )
    low_price = _weighted_quantile(pairs, float(low_quantile))
    high_price = _weighted_quantile(pairs, float(high_quantile))
    vwap = math.fsum(price * volume for price, volume in pairs) / total_volume
    return CalcResult.success(
        (high_price - low_price) / (high_price + low_price),
        unit=RATIO_UNIT,
        details={
            "low_quantile": float(low_quantile),
            "high_quantile": float(high_quantile),
            "low_price": low_price,
            "high_price": high_price,
            "vwap": vwap,
            "actual": len(parsed),
            "window": [parsed[0]["date"], parsed[-1]["date"]],
            "data_fp": _fingerprint(parsed),
        },
    )
