"""A 股日频策略回测最小闭环（QUOT-500 / 规格第五节）。

模块分工（全部自研，不复制/移植参考库代码）：

- ``config``     成本与阈值默认值 + ``config/backtest.yaml`` 覆盖 + CLI 覆盖 + 严格校验
- ``feed``       从本机 ``daily_k`` 读日频 OHLCV（payload 带 ``adjust`` 复权口径）
- ``universe``   单标的 / 手工列表（M1 只支持这两种），并做 A 股可交易域过滤
- ``strategy``   策略协议 + 3 个内置（均线交叉 / 动量 / 突破）
- ``match``      撮合：手续费、滑点、印花税、T+1、涨跌停不可成交、停牌跳过
- ``score``      绩效：调 ``ladderstock.calc`` 计算并记录每个指标的 ``calc_id``
- ``card``       运行卡：JSON（机器读）+ Markdown（人读）+ 自包含 HTML
- ``guard``      校验门：样本内/外切分、三档成本敏感性、最小交易次数阈值
- ``runner``     编排入口：``ldst backtest run`` 与 ``POST /api/backtest/run`` 都走这里
"""

from __future__ import annotations

from ladderstock.backtest.config import BacktestConfig, BacktestConfigError, load_config
from ladderstock.backtest.runner import run_backtest

__all__ = [
    "BacktestConfig",
    "BacktestConfigError",
    "load_config",
    "run_backtest",
]
