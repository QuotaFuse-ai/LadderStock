"""公式层单测（SPEC-INDUSTRY-METRICS.md §2–§6）：每个期望值都是手算，可复核。

这一层不碰缓存、不碰 peer，所以断言直接对着算式写：

- 空集给 `None` 不给 0；任一分量缺失让整条指标为 `None`；
- 取整只在最后一步，且是**十进制四舍五入**（`0.125 → 0.13`），不是银行家舍入；
- 分位是经验分位（`<= 当前值`的天数占比），不做插值。
"""

from __future__ import annotations

import pytest

from ladderstock.industry import metrics

# --------------------------------------------------------------------------- #
# 取整与数值过滤
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("value", "digits", "want"),
    [
        (0.125, 2, 0.13),  # 十进制四舍五入：内置 round(0.125, 2) 给 0.12
        (0.135, 2, 0.14),
        (2.5, 0, 3.0),
        (-2.5, 0, -3.0),  # 远离 0，不是就近取偶
        (1 / 3, 4, 0.3333),
        (85.0, 4, 85.0),
        (82.35294117647058, 4, 82.3529),
    ],
)
def test_round_half_up_is_decimal(value: float, digits: int, want: float) -> None:
    assert metrics.round_half_up(value, digits) == want


def test_finite_numbers_rejects_bool_and_text() -> None:
    """`bool` 不算数（上游偶尔给 `true` 占位），文本容忍尾随 `%`，NaN/inf 丢掉。"""
    values = [1, True, None, "2.5", "3%", "abc", float("nan"), float("inf"), -4]
    assert metrics.finite_numbers(values) == [1.0, 2.5, 3.0, -4.0]


# --------------------------------------------------------------------------- #
# 中位数 / 平均 / 分位
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("series", "want"),
    [
        ([3.0, 1.0, 2.0], 2.0),  # 奇数个：升序取中间
        ([1.0, 2.0, 3.0, 4.0], 2.5),  # 偶数个：中间两数算术平均
        ([7.0], 7.0),
        ([], None),
        ([None, None], None),
        ([5.0, None, 1.0], 3.0),
    ],
)
def test_median(series: list[object], want: float | None) -> None:
    assert metrics.median(series, digits=1) == want


def test_mean_and_sum_none_rules() -> None:
    assert metrics.mean([1.0, 2.0, 6.0], digits=2) == 3.0
    assert metrics.mean([], digits=2) is None
    # 合计：缺一个分量就整体 None，不拿部分样本冒充全样本
    assert metrics.sum_values([1.0, 2.0, None], digits=2) is None
    assert metrics.sum_values([], digits=2) is None
    assert metrics.sum_values([1.0, 2.0], digits=2) == 3.0
    assert metrics.sum_values([0.005, 0.0], digits=2) == 0.01  # 边界同样四舍五入


@pytest.mark.parametrize(
    ("series", "value", "want"),
    [
        ([1.0, 2.0, 3.0, 4.0], 4.0, 1.0),  # 最大值 → 全部 <= 它
        ([1.0, 2.0, 3.0, 4.0], 1.0, 0.25),  # 含等号：同值算在本期之下
        ([1.0, 2.0, 3.0, 4.0], 0.5, 0.0),
        ([1.0, 1.0, 1.0], 1.0, 1.0),
        ([], 1.0, None),
    ],
)
def test_percentile_rank_is_empirical(
    series: list[float], value: float, want: float | None
) -> None:
    assert metrics.percentile_rank(series, value) == want


def test_percentile_rank_never_interpolates() -> None:
    """经验分位：2 个样本里较小那个给 0.5，插值分位会给出别的数。"""
    assert metrics.percentile_rank([10.0, 20.0], 10.0) == 0.5


# --------------------------------------------------------------------------- #
# 比率与窗口
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("numerator", "denominator", "want"),
    [
        (1500.0, 3000.0, 50.0),
        (0.0, 100.0, 0.0),
        (1.0, 3.0, 33.33),
        (1.0, 0.0, None),  # 分母 0：不除零
        (1.0, -5.0, None),  # 分母为负：不取绝对值
        (None, 10.0, None),
        (1.0, None, None),
    ],
)
def test_ratio_share(
    numerator: float | None, denominator: float | None, want: float | None
) -> None:
    assert metrics.ratio_share(numerator, denominator) == want


def test_window_series_sorts_then_trims() -> None:
    daily = {"2026-01-05": 1.0, "2026-01-07": 3.0, "2026-01-06": 2.0, "2025-12-31": 0.0}
    days, values = metrics.window_series(daily, lookback_days=3)
    assert days == ["2026-01-05", "2026-01-06", "2026-01-07"]
    assert values == [1.0, 2.0, 3.0]
    assert metrics.window_series({}, lookback_days=3) == ([], [])
    # 窗口比样本长：给全部，不补值
    assert metrics.window_series(daily, lookback_days=999)[1] == [0.0, 1.0, 2.0, 3.0]


def test_equal_weight_change_is_mean_of_stock_changes() -> None:
    """等权：先算每只的涨跌幅再平均，不是用合计收盘价算「加权」涨跌幅。

    600001: 10 → 12 即 +20%；600002: 20 → 18 即 -10%；等权 = (20 - 10)/2 = +5.0%。
    """
    closes = {"600001": [10.0, 11.0, 12.0], "600002": [20.0, 19.0, 18.0]}
    assert metrics.equal_weight_change(closes, 2) == (5.0, 2)


@pytest.mark.parametrize(
    ("closes", "window", "want"),
    [
        # K 线不足 window+1 根的股票直接剔除，不补 0，也不改分母
        ({"600001": [10.0, 12.0], "600002": [20.0, 19.0, 18.0]}, 2, (-10.0, 1)),
        # 基期为 0 无法算比率，同样剔除
        ({"600001": [0.0, 1.0, 2.0]}, 2, (None, 0)),
        ({}, 2, (None, 0)),
        ({"600001": [None, None, None]}, 2, (None, 0)),
    ],
)
def test_equal_weight_change_samples(
    closes: dict[str, list[object]], window: int, want: tuple
) -> None:
    assert metrics.equal_weight_change(closes, window) == want
