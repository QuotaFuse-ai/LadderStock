"""已实现盈亏：移动加权平均成本，离线复算成交流水（DEC-038，仅本机）。

只读复算，不新增真相：本模块不写库、不建表、不改 `positions`，
输入就是 `journal.log.list_trades()` 的形状，输出交给 `journal/position.py::compute_metrics()`
投影进 `ldst position list` 与 `GET /api/positions`（同一份计算结果，两处不同各算一遍）。

为什么是移动加权平均，不是 FIFO：`journal stats` 的 FIFO 配对服务纪律复盘
（逐笔「闭环」，胜率与盈亏比按它算）；用户问「这只票卖出的得失」要的是
券商对账单口径——部分卖出后**剩余持仓的成本单价不变**，能和账户对上。
两者吃的是同一份流水，不是两份数据；差额有恒等式可解释（DEC-038 §2.3）。

口径（可复现、可解释）：

- **回放顺序**：按传入顺序回放；生产路径给 `list_trades` 的 `(trade_date, id)` 升序
- **买入**：数量累加、成本额 `+= 数量 × 成交价`；成本单价 = 成本额 ÷ 在手数量
- **卖出**：结转成本 = **卖出时点**的成本单价 × 成交数量；
  已实现 = 卖出金额 − 结转成本
- **部分卖出**：结转后剩余的成本单价不变（这就是与「吃掉最早一批」的 FIFO 的区别）
- **卖出多于在手**：只结转在手部分，多出来的记进 `unmatched_sell_qty`，**不编造成本**
- **手续费**：`trades` 表没有费用列，所以口径是**不含费用**的毛额；不为此新增假设字段
- **无成交**：`total_pnl` 与该 code 的 `realized_pnl` 给 `null` 不给 `0`——
  「没记过」不等于「不赚不亏」
- **只买未卖**：有成交但没有卖出 → `0.0`（实现了 0，和「没有数据」是两件事）

舍入纪律与 `SPEC-MY-POSITION` §4 相同：本模块给**未舍入的原值**，舍入只在出口做一次，
不逐条舍入再相加（那样会凑出多余的分）。

隐私：输入来自本机 `journal.sqlite`，输出只交给本机 CLI 与回环独占的
`GET /api/positions`；不进 `LadderEnvelope`、不进 MCP 工具清单、不进 AI 上下文（DEC-006）。
"""

from __future__ import annotations

from typing import Any

#: 成本法标识，出口字段 `realized.method` 的值（与 `journal stats` 的 FIFO 区分开）
METHOD = "moving_weighted_average"

#: 成交流水缺费用列，毛额口径必须在出口写明，不让人误当成对账单最终数
FEE_INCLUDED = False

_EPSILON = 1e-9

#: 空态要说清「没记过」而不是「不赚不亏」，与 `valuation.unrealized_pnl` 的 null 纪律一致
NO_TRADES_REASON = "本机成交流水为空：没有成交记录时已实现盈亏不成立，给 null 不给 0"


def _new_state() -> dict[str, float]:
    return {
        "qty": 0.0,  # 在手数量
        "cost_total": 0.0,  # 在手成本额（÷ qty 就是移动加权成本单价）
        "pnl": 0.0,  # 已实现盈亏累计
        "matched": 0.0,  # 卖出里结转过成本的数量
        "unmatched": 0.0,  # 卖出里没有成本可结转的数量
        "buys": 0.0,
        "sells": 0.0,
    }


def compute_realized(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """按顺序回放成交流水，算出每个 code 的已实现盈亏与合计。

    `trades` 需要是 `journal.log.list_trades()` 的行（`code` / `side` / `price` / `qty`）；
    本模块既不读库也不重排，顺序由调用方保证，这样生产路径与 `journal stats`
    吃到的就是同一条时间线（DEC-038 §2.1）。

    返回 `codes` 是以 code 为键的原值表（未舍入），供调用方投影；
    其余键是可以直接进出口字段的聚合值。
    """
    states: dict[str, dict[str, float]] = {}

    for trade in trades:
        code = str(trade["code"])
        price = float(trade["price"])
        qty = float(trade["qty"])
        lot = states.setdefault(code, _new_state())

        if str(trade["side"]) == "buy":
            lot["buys"] += 1.0
            lot["qty"] += qty
            lot["cost_total"] += qty * price
            continue

        lot["sells"] += 1.0
        matched = min(lot["qty"], qty)
        if matched > _EPSILON:
            unit_cost = lot["cost_total"] / lot["qty"]
            consumed = unit_cost * matched
            lot["pnl"] += price * matched - consumed
            lot["matched"] += matched
            lot["qty"] -= matched
            lot["cost_total"] -= consumed
        leftover = qty - matched
        if leftover > _EPSILON:
            # 流水从持仓中途开始记，或卖出数量超过在手：如实计数，不编造成本
            lot["unmatched"] += leftover
        if lot["qty"] <= _EPSILON:
            # 清仓后残余的浮点尾巴归零，避免下一次买入接着一个 -1e-16 的成本额
            lot["qty"] = 0.0
            lot["cost_total"] = 0.0

    codes = {
        code: {
            "realized_pnl": value["pnl"],
            "buy_count": int(value["buys"]),
            "sell_count": int(value["sells"]),
            "matched_sell_qty": value["matched"],
            "unmatched_sell_qty": value["unmatched"],
        }
        for code, value in states.items()
    }

    return {
        "method": METHOD,
        "fee_included": FEE_INCLUDED,
        "status": "ok" if trades else "missing",
        "reason": None if trades else NO_TRADES_REASON,
        "buy_count": sum(int(value["buys"]) for value in states.values()),
        "sell_count": sum(int(value["sells"]) for value in states.values()),
        "matched_sell_qty": sum(value["matched"] for value in states.values()),
        "unmatched_sell_qty": sum(value["unmatched"] for value in states.values()),
        "total_pnl": sum(value["pnl"] for value in states.values()) if trades else None,
        "codes": codes,
    }


__all__ = ["FEE_INCLUDED", "METHOD", "NO_TRADES_REASON", "compute_realized"]
