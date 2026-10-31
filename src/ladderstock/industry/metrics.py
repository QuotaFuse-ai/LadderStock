"""公式层：纯函数，不做 I/O（SPEC-INDUSTRY-METRICS.md §2–§6）。

这一层的每个函数都可以拿夹具手算复核，也是单测的直接对象：
输入是**已经取好的数**，输出是口径算完的数，中间不碰缓存、不碰 peer、不调模型。

三条贯穿全层的规则：

1. **空集回 `None`**，不回 0——0 是一个数值，`None` 才是「没有这个数」；
2. **任一分量为 `None` 就让整条指标为 `None`**（比率/分位类），
   宁可缺，也不拿部分样本冒充全样本；分母 ≤ 0 同样给 `None`；
3. **取整只在最后一步**：`round_half_up`（四舍五入，远离 0），
   比率与分位保留 4 位，百分数与金额保留 2 位——与 spec 的表一字不差。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

#: 保留位数（SPEC-INDUSTRY-METRICS.md §1.3）
DIGITS_RATIO = 4
DIGITS_PERCENT = 2
DIGITS_AMOUNT = 2


def round_half_up(value: float, digits: int) -> float:
    """十进制四舍五入（远离 0），不用 Python 内置 `round()` 的银行家舍入。

    金标准夹具里的手算值按十进制四舍五入写，实现必须同一口径，否则
    `x.5` 这类边界值会让测试与文档各说各话。
    """
    quantum = Decimal(1).scaleb(-digits)
    return float(Decimal(repr(float(value))).quantize(quantum, rounding=ROUND_HALF_UP))


def finite_numbers(values: Iterable[Any]) -> list[float]:
    """过滤出可解析为有限数值的项（`bool` 不算数），保持输入顺序。"""
    out: list[float] = []
    for value in values:
        if value is None or isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            number = float(value)
        else:
            text = str(value).strip().rstrip("%")
            try:
                number = float(text)
            except ValueError:
                continue
        if number == number and number not in (float("inf"), float("-inf")):
            out.append(number)
    return out


def median(values: Iterable[Any], *, digits: int = DIGITS_PERCENT) -> float | None:
    """中位数：升序后取中间；偶数个取中间两数的算术平均。空集给 `None`。"""
    numbers = sorted(finite_numbers(values))
    count = len(numbers)
    if count == 0:
        return None
    middle = count // 2
    if count % 2:
        value = numbers[middle]
    else:
        value = (numbers[middle - 1] + numbers[middle]) / 2.0
    return round_half_up(value, digits)


def percentile_rank(
    series: Sequence[float], value: float, *, digits: int = DIGITS_RATIO
) -> float | None:
    """分位 = 序列中 `<= value` 的个数 / 序列长度，值域 `(0, 1]`。

    含 `<=`（历史同值算在本期之下），空序列给 `None`。这是「当前值在窗口内
    处于什么位置」的经验分位，不是插值分位——不做插值正是任务硬约束。
    """
    numbers = finite_numbers(series)
    if not numbers:
        return None
    below = sum(1 for item in numbers if item <= value)
    return round_half_up(below / len(numbers), digits)


def mean(values: Iterable[Any], *, digits: int = DIGITS_PERCENT) -> float | None:
    """等权算术平均；空集 `None`。"""
    numbers = finite_numbers(values)
    if not numbers:
        return None
    return round_half_up(sum(numbers) / len(numbers), digits)


def sum_values(values: Iterable[Any], *, digits: int = DIGITS_AMOUNT) -> float | None:
    """合计；只要有一个分量缺失就整体 `None`（不拿部分样本冒充全样本）。"""
    items = list(values)
    numbers = finite_numbers(items)
    if not items or len(numbers) != len(items):
        return None
    return round_half_up(sum(numbers), digits)


def ratio_share(
    numerator: float | None, denominator: float | None, *, digits: int = DIGITS_PERCENT
) -> float | None:
    """占比 = 分子 / 分母 × 100；任一侧缺失或分母 ≤ 0 给 `None`（不除零、不取绝对值）。"""
    if numerator is None or denominator is None:
        return None
    if denominator <= 0:
        return None
    return round_half_up(numerator / denominator * 100.0, digits)


def window_series(
    daily: Mapping[str, float], *, lookback_days: int
) -> tuple[list[str], list[float]]:
    """把「日期 → 数值」按日期升序排列并截取最近 `lookback_days` 个交易日。

    返回 `(dates, values)`，两者等长；空映射给 `([], [])`。
    """
    days = sorted(daily)
    if lookback_days > 0:
        days = days[-lookback_days:]
    return days, [daily[day] for day in days]


def equal_weight_change(
    closes: Mapping[str, Sequence[Any]], window: int
) -> tuple[float | None, int]:
    """成分股**等权**涨跌幅（%）：`(收盘价[-1] / 收盘价[-1-window] - 1) × 100` 的算术平均。

    返回 `(均值, 参与股票数)`。`window` 需要 `window + 1` 根 K 线，不足的股票
    不计入（也不补 0），所以调用方必须把 `参与股票数` 一起输出——样本口径要可见。
    全无样本时给 `(None, 0)`。
    """
    changes: list[float] = []
    for code in sorted(closes):
        numbers = finite_numbers(closes[code])
        if len(numbers) < window + 1:
            continue
        base = numbers[-1 - window]
        if base == 0:
            continue
        changes.append((numbers[-1] / base - 1.0) * 100.0)
    if not changes:
        return None, 0
    return round_half_up(sum(changes) / len(changes), DIGITS_PERCENT), len(changes)
