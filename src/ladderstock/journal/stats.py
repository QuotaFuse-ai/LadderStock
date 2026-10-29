"""纪律复查统计：胜率、盈亏比、偏离计划次数（仅本机）。

口径（可复现、可解释）：

| 指标 | 定义 |
|---|---|
| 配对 | 按 `code` 先进先出（FIFO）：卖出依次吃掉最早的买入批次 |
| 闭环 | 一次「有配对数量」的卖出 = 一个闭环；没有可配对的卖出只计入 `unmatched_sell_qty` |
| 胜率 | 盈利闭环 / 全部闭环（百分比，1 位小数） |
| 盈亏比 | 平均盈利 / 平均亏损绝对值；没有亏损样本时为 `null`（不拿 0 或 1 冒充） |
| 偏离计划 | `plan_price` 存在且 `|price - plan_price| / plan_price > threshold` 的成交笔数 |

隐私：只读本机 `journal.sqlite`，返回值仅用于本机终端输出，不进信封 / MCP / AI。
"""

from __future__ import annotations

from collections import deque
from datetime import date
from pathlib import Path
from typing import Any

from ladderstock.errors import EXIT_USAGE, LadderStockError
from ladderstock.journal.log import list_trades

#: 默认偏离阈值（相对计划价的 1%）
DEFAULT_DEVIATION_THRESHOLD = 0.01

_EPSILON = 1e-9


def compute_stats(
    *,
    day_from: str | date | None = None,
    day_to: str | date | None = None,
    deviation_threshold: float = DEFAULT_DEVIATION_THRESHOLD,
    root: Path | None = None,
) -> dict[str, Any]:
    """计算区间内的纪律统计；无交易时返回明确的空态。"""
    if deviation_threshold < 0:
        raise LadderStockError("--deviation 必须大于等于 0", EXIT_USAGE)

    trades = list_trades(
        day_from=day_from,
        day_to=day_to,
        limit=1_000_000,  # 统计口径要覆盖整个区间，不截断
        root=root,
    )

    lots: dict[str, deque[list[float]]] = {}
    gross_win = 0.0
    gross_loss = 0.0
    realized = 0.0
    wins = losses = flats = closed = 0
    unmatched_sell_qty = 0.0
    deviations: list[dict[str, Any]] = []

    for trade in trades:
        code = str(trade["code"])
        price = float(trade["price"])
        qty = float(trade["qty"])

        plan_price = trade.get("plan_price")
        if plan_price is not None:
            plan = float(plan_price)
            if plan > 0 and abs(price - plan) / plan > deviation_threshold:
                deviations.append(
                    {
                        "id": trade["id"],
                        "trade_date": trade["trade_date"],
                        "code": code,
                        "side": trade["side"],
                        "price": price,
                        "plan_price": plan,
                        "deviation_pct": round(abs(price - plan) / plan * 100.0, 2),
                    }
                )

        if trade["side"] == "buy":
            lots.setdefault(code, deque()).append([qty, price])
            continue

        remaining = qty
        matched_cost = 0.0
        matched_qty = 0.0
        queue = lots.get(code)
        while remaining > _EPSILON and queue:
            lot = queue[0]
            take = min(remaining, lot[0])
            matched_cost += take * lot[1]
            matched_qty += take
            lot[0] -= take
            remaining -= take
            if lot[0] <= _EPSILON:
                queue.popleft()
        if remaining > _EPSILON:
            # 没有对应买入的卖出（例如日志从持仓中途开始记）：如实计数，不编造成本
            unmatched_sell_qty += remaining
        if matched_qty <= _EPSILON:
            continue

        pnl = (price - matched_cost / matched_qty) * matched_qty
        realized += pnl
        closed += 1
        if pnl > _EPSILON:
            wins += 1
            gross_win += pnl
        elif pnl < -_EPSILON:
            losses += 1
            gross_loss += pnl
        else:
            flats += 1

    avg_win = gross_win / wins if wins else None
    avg_loss = abs(gross_loss) / losses if losses else None
    ratio = (avg_win / avg_loss) if (avg_win is not None and avg_loss) else None

    return {
        "local_only": True,
        "store": "local_sqlite",
        "data_source_status": "ok" if trades else "missing",
        "range": {
            "from": str(day_from) if day_from else None,
            "to": str(day_to) if day_to else None,
        },
        "total_trades": len(trades),
        "buy_count": sum(1 for t in trades if t["side"] == "buy"),
        "sell_count": sum(1 for t in trades if t["side"] == "sell"),
        "symbols": sorted({str(t["code"]) for t in trades}),
        "closed_rounds": closed,
        "wins": wins,
        "losses": losses,
        "flats": flats,
        "win_rate": round(wins / closed * 100.0, 1) if closed else None,
        "avg_win": round(avg_win, 2) if avg_win is not None else None,
        "avg_loss": round(avg_loss, 2) if avg_loss is not None else None,
        "profit_loss_ratio": round(ratio, 2) if ratio is not None else None,
        "realized_pnl": round(realized, 2),
        "unmatched_sell_qty": round(unmatched_sell_qty, 4),
        "deviation_review": {
            "threshold_pct": round(deviation_threshold * 100.0, 2),
            "count": len(deviations),
            "trades": deviations,
        },
    }


__all__ = ["DEFAULT_DEVIATION_THRESHOLD", "compute_stats"]
