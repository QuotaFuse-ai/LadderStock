"""组合统计（B 包）：把「为什么赚 / 为什么亏」算成可复盘的数。

模块划分：

- `engine` —— 全部计算（组合指标 / 基准对比 / 交易归因 / 行为统计 / 错题本），
  纯本地、只读；口径见模块 docstring 的表（页面与 CLI 共用一份）。

入口面：CLI `ldst analytics`、Web `GET /api/analytics/summary`（DEC-089 授权面）。
"""

from ladderstock.analytics import engine

__all__ = ["engine"]
