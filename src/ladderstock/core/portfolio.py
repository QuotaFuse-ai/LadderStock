"""持仓 = **交易流水按日期重放**的结果（DEC-056）。

台账只存用户写下的每一笔（期初持仓 / 买入 / 卖出 / 现金分红 / 送转拆股），
持有多少、成本多少、已实现盈亏多少都在这里算出来，不另存一份——存两份就会有两个真相。

四条口径（与参考实现 `orchestrator/src/finance/holdings.ts` 同一套）：

1. **两种成本法**：先进先出（`fifo`）按买入批次逐批扣；移动加权平均（`average`）
   卖出不改均价。持有数量两者相同，成本与已实现盈亏不同——界面要写明用的哪一种。
2. **分红单独记收入**，不改持仓成本；费用计入买入成本、从卖出所得里扣。
3. **数据不对的流水逐条写原因、不参与计算**，不猜也不补。跳过一笔卖出会让之后
   的持有数偏大，所以问题清单必须摆在持仓明细旁边，不能藏。
4. **同一天先送转**（除权日的成交已按新股本计价），其余按录入顺序（`created_at`）
   再按 `id`，保证同输入同输出。

期初持仓的成本**可以是负数**：分红 / 送转摊薄之后真实存在。

本模块是纯函数：不读文件、不取行情、不碰数据库。读写台账与迁移旧记录在
`journal/portfolio.py`，行情拼接在 `web/radar.py`。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

CostMethod = Literal["fifo", "average"]

#: 成本法 → 中文标签（界面直接展示，后端与前端用同一份）
COST_METHODS: dict[CostMethod, str] = {"average": "移动加权平均", "fifo": "先进先出"}

#: 合法流水类型（与 `portfolio_trades.type` 的 CHECK 一致）
TRADE_TYPES: tuple[str, ...] = ("opening", "buy", "sell", "dividend", "split")

#: 浮点余量：卖出 100 股、买入时是 33.333…×3 这类，差一点点不该报「卖超」
EPS = 1e-8

#: B 股（沪 900、深 200 开头）按美元 / 港元计价，六位代码在这里一律按 A 股、
#: 人民币算，记进来会把外币金额直接加进人民币合计。支持之前先明确拒收。
B_SHARE = re.compile(r"^(?:900|200)\d{3}$")
B_SHARE_UNSUPPORTED = (
    "B 股（900、200 开头）暂不支持：按美元、港元计价，并进人民币合计会算错"
)

_SIX_DIGITS = re.compile(r"^\d{6}$")
_DAY_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SPLIT_ORDER = 0
_OTHER_ORDER = 1


@dataclass(frozen=True)
class Trade:
    """一笔台账流水（字段与 `portfolio_trades` 一一对应）。"""

    id: int
    symbol: str
    name: str = ""
    type: str = ""
    date: str = ""
    shares: float | None = None
    price: float | None = None
    fee: float | None = None
    amount: float | None = None
    ratio: float | None = None
    note: str = ""
    migrated_from: int | None = None
    undated: bool = False
    created_at: str = ""


@dataclass(frozen=True)
class TradeIssue:
    """不参与计算的一笔流水 + 原因。"""

    id: int
    symbol: str
    date: str
    type: str
    why: str


@dataclass
class Lot:
    """先进先出的一个买入批次；`unit_cost` 已含买入费用、已按送转换算。"""

    trade_id: int
    date: str
    shares: float
    unit_cost: float


@dataclass
class HoldingRow:
    symbol: str
    name: str
    shares: float
    unit_cost: float
    cost_total: float
    opened_at: str
    lots: list[Lot]
    realized_pnl: float
    dividends: float
    fees: float
    trade_count: int


@dataclass(frozen=True)
class ClosedRow:
    """已经卖光、但有过已实现盈亏或分红的代码。"""

    symbol: str
    name: str
    realized_pnl: float
    dividends: float
    fees: float
    last_date: str


@dataclass(frozen=True)
class RealizedRow:
    trade_id: int
    symbol: str
    date: str
    shares: float
    proceeds: float
    cost: float
    pnl: float


@dataclass(frozen=True)
class DividendRow:
    trade_id: int
    symbol: str
    date: str
    amount: float


@dataclass
class ReplayResult:
    method: CostMethod
    holdings: list[HoldingRow] = field(default_factory=list)
    closed: list[ClosedRow] = field(default_factory=list)
    realized: list[RealizedRow] = field(default_factory=list)
    dividends: list[DividendRow] = field(default_factory=list)
    issues: list[TradeIssue] = field(default_factory=list)


def round4(value: float) -> float:
    """输出金额 / 股数统一四舍五入到 4 位。"""
    return round(float(value), 4)


def real_day(raw: str) -> bool:
    """`YYYY-MM-DD` 且日历上真实存在（`2025-02-30` / `2025-13-01` 都算非法）。"""
    text = str(raw or "").strip()
    if not _DAY_PATTERN.match(text):
        return False
    try:
        return date.fromisoformat(text).isoformat() == text
    except ValueError:
        return False


def valid_symbol(raw: str) -> bool:
    """6 位数字、非 B 股。"""
    text = str(raw or "").strip().upper()
    return bool(_SIX_DIGITS.match(text)) and not B_SHARE.match(text)


def trade_problem(trade: Trade) -> str | None:
    """单笔流水自身的字段是否够算（不看前后文）。够算返回 `None`，否则返回原因。"""
    if not trade.symbol:
        return "没有代码"
    if B_SHARE.match(trade.symbol):
        return B_SHARE_UNSUPPORTED
    if trade.type not in TRADE_TYPES:
        return f"类型「{trade.type or '空'}」不认识"
    if not real_day(trade.date):
        return f"日期「{trade.date or '空'}」不是有效日期"
    if trade.type == "opening":
        if trade.shares is None or trade.shares <= 0:
            return "期初持仓要写大于 0 的数量"
        if trade.price is None:
            return "期初持仓要写每股成本"
        return None
    if trade.type in ("buy", "sell"):
        label = "买入" if trade.type == "buy" else "卖出"
        if trade.shares is None or trade.shares <= 0:
            return f"{label}要写大于 0 的数量"
        if trade.price is None or trade.price <= 0:
            return f"{label}要写大于 0 的成交价"
        if trade.fee is not None and trade.fee < 0:
            return "费用不能是负数"
        return None
    if trade.type == "dividend":
        if trade.amount is None or trade.amount <= 0:
            return "现金分红要写大于 0 的到账金额"
        return None
    if trade.ratio is None or trade.ratio <= 0:
        return "送转拆股要写大于 0 的比例（每 1 股变成几股，如 10 转 5 写 1.5）"
    return None


def replay_order_key(trade: Trade) -> tuple[str, int, str, int]:
    """重放顺序：日期 → 同日先送转 → 录入时间 → id。"""
    return (
        trade.date,
        _SPLIT_ORDER if trade.type == "split" else _OTHER_ORDER,
        trade.created_at,
        trade.id,
    )


@dataclass
class _Book:
    symbol: str
    name: str = ""
    lots: list[Lot] = field(default_factory=list)
    shares: float = 0.0
    cost: float = 0.0
    opened_at: str = ""
    realized: float = 0.0
    dividends: float = 0.0
    fees: float = 0.0
    trades: int = 0
    last: str = ""


def _held(book: _Book, method: CostMethod) -> float:
    if method == "fifo":
        return sum(lot.shares for lot in book.lots)
    return book.shares


def replay(trades: list[Trade], method: CostMethod) -> ReplayResult:
    """按日期重放一批流水，返回持仓 / 清仓 / 已实现 / 分红 / 问题清单。"""
    if method not in COST_METHODS:
        raise ValueError(f"成本法只能是 fifo 或 average，收到 {method!r}")

    books: dict[str, _Book] = {}
    realized: list[RealizedRow] = []
    dividends: list[DividendRow] = []
    issues: list[TradeIssue] = []

    def issue(trade: Trade, why: str) -> None:
        issues.append(
            TradeIssue(
                id=trade.id,
                symbol=trade.symbol,
                date=trade.date,
                type=trade.type,
                why=why,
            )
        )

    for trade in sorted(trades, key=replay_order_key):
        bad = trade_problem(trade)
        if bad:
            issue(trade, bad)
            continue

        book = books.get(trade.symbol)
        if book is None:
            book = _Book(symbol=trade.symbol)
            books[trade.symbol] = book
        held = _held(book, method)

        if trade.type in ("opening", "buy"):
            shares = trade.shares or 0.0
            fee = (trade.fee or 0.0) if trade.type == "buy" else 0.0
            total = shares * (trade.price or 0.0) + fee
            if held <= EPS:
                book.opened_at = trade.date
                book.lots = []
                book.shares = 0.0
                book.cost = 0.0
            book.lots.append(
                Lot(
                    trade_id=trade.id,
                    date=trade.date,
                    shares=shares,
                    unit_cost=total / shares,
                )
            )
            book.shares += shares
            book.cost += total
            book.fees += fee
        elif trade.type == "sell":
            shares = trade.shares or 0.0
            fee = trade.fee or 0.0
            if shares > held + EPS:
                issue(
                    trade,
                    f"卖出 {shares:g} 股，超过当时持有的 {round4(held):g} 股"
                    "（之前的买入或送转可能没录）",
                )
                continue
            cost = 0.0
            if method == "fifo":
                left = shares
                while left > EPS and book.lots:
                    lot = book.lots[0]
                    take = min(lot.shares, left)
                    cost += take * lot.unit_cost
                    lot.shares -= take
                    left -= take
                    if lot.shares <= EPS:
                        book.lots.pop(0)
            else:
                cost = (book.cost / book.shares) * shares if book.shares > EPS else 0.0
                book.cost -= cost
            book.shares -= shares
            proceeds = shares * (trade.price or 0.0) - fee
            realized.append(
                RealizedRow(
                    trade_id=trade.id,
                    symbol=trade.symbol,
                    date=trade.date,
                    shares=round4(shares),
                    proceeds=round4(proceeds),
                    cost=round4(cost),
                    pnl=round4(proceeds - cost),
                )
            )
            book.realized += proceeds - cost
            book.fees += fee
            if _held(book, method) <= EPS or book.shares <= EPS:
                book.lots = []
                book.shares = 0.0
                book.cost = 0.0
        elif trade.type == "dividend":
            amount = trade.amount or 0.0
            dividends.append(
                DividendRow(
                    trade_id=trade.id,
                    symbol=trade.symbol,
                    date=trade.date,
                    amount=round4(amount),
                )
            )
            book.dividends += amount
        else:  # split
            ratio = trade.ratio or 0.0
            for lot in book.lots:
                lot.shares *= ratio
                lot.unit_cost /= ratio
            if held > EPS:
                book.shares *= ratio

        if trade.name:
            book.name = trade.name
        book.trades += 1
        book.last = trade.date

    holdings: list[HoldingRow] = []
    closed: list[ClosedRow] = []
    for book in sorted(books.values(), key=lambda item: item.symbol):
        shares = _held(book, method)
        if shares > EPS:
            cost_total = (
                sum(lot.shares * lot.unit_cost for lot in book.lots)
                if method == "fifo"
                else book.cost
            )
            lots = (
                [
                    Lot(
                        trade_id=lot.trade_id,
                        date=lot.date,
                        shares=round4(lot.shares),
                        unit_cost=round4(lot.unit_cost),
                    )
                    for lot in book.lots
                ]
                if method == "fifo"
                else []
            )
            holdings.append(
                HoldingRow(
                    symbol=book.symbol,
                    name=book.name,
                    shares=round4(shares),
                    unit_cost=round4(cost_total / shares),
                    cost_total=round4(cost_total),
                    opened_at=book.opened_at,
                    lots=lots,
                    realized_pnl=round4(book.realized),
                    dividends=round4(book.dividends),
                    fees=round4(book.fees),
                    trade_count=book.trades,
                )
            )
        elif book.realized != 0 or book.dividends != 0:
            closed.append(
                ClosedRow(
                    symbol=book.symbol,
                    name=book.name,
                    realized_pnl=round4(book.realized),
                    dividends=round4(book.dividends),
                    fees=round4(book.fees),
                    last_date=book.last,
                )
            )

    return ReplayResult(
        method=method,
        holdings=holdings,
        closed=closed,
        realized=realized,
        dividends=dividends,
        issues=issues,
    )


__all__ = [
    "B_SHARE",
    "B_SHARE_UNSUPPORTED",
    "COST_METHODS",
    "EPS",
    "TRADE_TYPES",
    "ClosedRow",
    "CostMethod",
    "DividendRow",
    "HoldingRow",
    "Lot",
    "RealizedRow",
    "ReplayResult",
    "Trade",
    "TradeIssue",
    "real_day",
    "replay",
    "replay_order_key",
    "round4",
    "trade_problem",
    "valid_symbol",
]
