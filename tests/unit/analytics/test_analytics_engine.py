"""analytics 引擎单测：纯函数口径（回撤 / 夏普 / 胜负统计 / 行为分档）。

`build_summary` 端到端读隐私库与 DuckDB，属集成路径——这里只钉**纯口径**，
不造库、不碰磁盘（缺数语义由 `null` / 空列表断言）。
"""

from __future__ import annotations

import pytest

from ladderstock.analytics.engine import (
    bucket_position,
    daily_returns,
    max_drawdown,
    sharpe,
    summarize_realized,
)
from ladderstock.core.portfolio import RealizedRow


def test_max_drawdown_peak_to_trough() -> None:
    # 1.0 → 1.2 → 0.9 → 1.05：峰值 1.2 回撤到 0.9 = 25%
    assert max_drawdown([1.0, 1.2, 0.9, 1.05]) == pytest.approx(0.25)
    # 单调上涨没有回撤
    assert max_drawdown([1.0, 1.1, 1.2]) == 0.0
    # 不足两个点 → 不给值
    assert max_drawdown([1.0]) is None
    assert max_drawdown([]) is None


def test_daily_returns_skip_non_positive() -> None:
    assert daily_returns([100.0, 110.0, 99.0]) == pytest.approx([0.1, -0.1])
    assert daily_returns([0.0, 1.0]) == []


def test_sharpe_needs_sample_and_variance() -> None:
    # 19 个点（18 个收益）不足 20 → None
    values = [1.0 + 0.001 * index for index in range(19)]
    assert sharpe(values) == (None, None)
    # 20+ 个点但零波动 → None（std=0）
    flat = [1.0 for _ in range(30)]
    assert sharpe(flat) == (None, None)
    # 有波动的序列给 Sharpe 与年化波动
    wobble = [1.0 + (0.01 if index % 2 else -0.01) for index in range(60)]
    value, vol = sharpe(wobble)
    assert value is not None and vol is not None
    assert vol > 0


def _row(pnl: float) -> RealizedRow:
    return RealizedRow(
        trade_id=1,
        symbol="600000",
        date="2026-11-01",
        shares=100.0,
        proceeds=1000.0,
        cost=1000.0,
        pnl=pnl,
    )


def test_summarize_realized_win_rate_and_profit_factor() -> None:
    stats = summarize_realized([_row(100.0), _row(200.0), _row(-50.0), _row(-150.0)])
    assert stats["count"] == 4
    assert stats["wins"] == 2 and stats["losses"] == 2
    assert stats["win_rate"] == pytest.approx(0.5)
    assert stats["profit_factor"] == pytest.approx(1.5)  # 300 / 200
    assert stats["avg_win"] == pytest.approx(150.0)
    assert stats["avg_loss"] == pytest.approx(-100.0)

    # 平局不进分母：胜率只看有方向的
    tie = summarize_realized([_row(0.0), _row(100.0)])
    assert tie["count"] == 2 and tie["wins"] == 1
    assert tie["win_rate"] == pytest.approx(1.0)

    # 没有亏损 → 盈亏比 None（分母为 0 不编数）
    only_wins = summarize_realized([_row(100.0)])
    assert only_wins["profit_factor"] is None
    # 没有已实现 → 胜率 None
    assert summarize_realized([])["win_rate"] is None


def test_bucket_position_boundaries() -> None:
    # 买在振幅上沿 90%（>0.8）→ 追高
    assert bucket_position(19.0, 10.0, 20.0) == "chase"
    # 买在振幅下沿 10%（<0.2）→ 低吸
    assert bucket_position(11.0, 10.0, 20.0) == "dip"
    # 中间 → 中性
    assert bucket_position(15.0, 10.0, 20.0) == "neutral"
    # 振幅为 0（一字板）→ 判不了
    assert bucket_position(10.0, 10.0, 10.0) is None
