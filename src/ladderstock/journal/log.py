"""交易日志写入与查询（仅本机 SQLite）。

隐私边界（DEC-006 / docs/MCP_TOOLS.md §隐私边界）：

- 本模块只依赖 `data.sqlite_store` 与标准库，**不 import** 适配器、MCP、AI；
- 返回值只交给 CLI `emit()` 打到本机终端，绝不包装成 `LadderEnvelope`；
- 不产生任何网络请求，日志里也不出现远端地址。

字段：`code` / `side(buy|sell)` / `price` / `qty` / `note`，可选 `plan_price`
（计划价，供 `journal stats` 统计「偏离计划次数」）与 `trade_date`（缺省今天）。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from ladderstock.data import sqlite_store
from ladderstock.errors import EXIT_USAGE, LadderStockError

SIDES: tuple[str, ...] = ("buy", "sell")

#: 本模块输出的记录字段（隐私测试用它断言不外溢）
TRADE_FIELDS: tuple[str, ...] = (
    "id",
    "trade_date",
    "code",
    "side",
    "price",
    "qty",
    "plan_price",
    "note",
    "created_at",
)


def add_trade(
    code: str,
    side: str,
    price: float,
    qty: float,
    *,
    note: str = "",
    trade_date: str | date | None = None,
    plan_price: float | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """追加一笔成交记录，返回落库后的行。"""
    clean_code = code.strip().upper()
    if not clean_code:
        raise LadderStockError("--code 不能为空", EXIT_USAGE)
    clean_side = side.strip().lower()
    if clean_side not in SIDES:
        raise LadderStockError(f"--side 只接受 buy / sell，收到 {side!r}", EXIT_USAGE)
    if not price or price <= 0:
        raise LadderStockError(f"--price 必须大于 0，收到 {price!r}", EXIT_USAGE)
    if not qty or qty <= 0:
        raise LadderStockError(f"--qty 必须大于 0，收到 {qty!r}", EXIT_USAGE)
    if plan_price is not None and plan_price <= 0:
        raise LadderStockError(f"--plan-price 必须大于 0，收到 {plan_price!r}", EXIT_USAGE)

    day = sqlite_store.parse_day(trade_date) or date.today()
    conn = sqlite_store.connect(root=root, create=True)
    assert conn is not None  # create=True 时不会返回 None
    try:
        cursor = conn.execute(
            "INSERT INTO trades (trade_date, code, side, price, qty, plan_price, note, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                day.isoformat(),
                clean_code,
                clean_side,
                float(price),
                float(qty),
                float(plan_price) if plan_price is not None else None,
                note.strip(),
                sqlite_store.now_iso(),
            ),
        )
        row = conn.execute(
            "SELECT id, trade_date, code, side, price, qty, plan_price, note, created_at"
            " FROM trades WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
    finally:
        sqlite_store.close(conn)
    if row is None:  # pragma: no cover - 插入后立即读不到属于磁盘异常
        raise LadderStockError("写入交易日志失败：插入后读不到记录", EXIT_USAGE)
    return dict(row)


def list_trades(
    *,
    day_from: str | date | None = None,
    day_to: str | date | None = None,
    code: str | None = None,
    limit: int = 200,
    root: Path | None = None,
) -> list[dict[str, Any]]:
    """按日期区间/代码查询交易日志；库不存在时返回空列表（不建库）。"""
    start = sqlite_store.parse_day(day_from)
    end = sqlite_store.parse_day(day_to)
    if start and end and start > end:
        raise LadderStockError("--from 不能晚于 --to", EXIT_USAGE)
    if limit <= 0:
        raise LadderStockError("--limit 必须大于 0", EXIT_USAGE)

    conn = sqlite_store.connect(root=root, create=False)
    if conn is None:
        return []
    try:
        clauses: list[str] = []
        params: list[Any] = []
        if start:
            clauses.append("trade_date >= ?")
            params.append(start.isoformat())
        if end:
            clauses.append("trade_date <= ?")
            params.append(end.isoformat())
        if code and code.strip():
            clauses.append("code = ?")
            params.append(code.strip().upper())
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(int(limit))
        rows = conn.execute(
            "SELECT id, trade_date, code, side, price, qty, plan_price, note, created_at"
            f" FROM trades{where} ORDER BY trade_date, id LIMIT ?",
            params,
        ).fetchall()
        return sqlite_store.rows_to_dicts(rows)
    finally:
        sqlite_store.close(conn)


__all__ = ["SIDES", "TRADE_FIELDS", "add_trade", "list_trades"]
