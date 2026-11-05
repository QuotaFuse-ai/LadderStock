"""持仓账本：`portfolio_trades` 的读写 + 旧 `positions` 记录的一次性迁移（DEC-056）。

三条硬约束（与 `journal/position.py` 同一条）：

1. **只落本机**：只依赖 `data/sqlite_store` 与本模块要用的纯算法
   （`core/portfolio.py`）+ 标准库。不 import 适配器、行情缓存、MCP、AI，
   不产生任何网络请求。
2. **不出本机**：返回值只交给本机页面 / CLI，不进 `LadderEnvelope`、不进 MCP、
   不进 AI 上下文（`core/privacy.py` 的 `PRIVATE_KEYS` 也要求出口键名避开
   `holdings` / `positions` / `trades`）。
3. **旧记录只读不删**：迁移把 `positions` 的每一行转成一笔「期初持仓」流水，
   `migrated_from` 记住原点；**不删、不改 `positions` 行**。回滚只需删掉
   `portfolio_trades` 里 `migrated_from` 对应的流水，旧数据原样还在。

迁移在**一个事务**里完成，动库之前先用 SQLite backup API 落一份
`journal.sqlite.bak-portfolio-<timestamp>`；中途任何一步失败整批回滚。

旧记录没写建仓日的，按迁移日暂记，并在 `note` 末尾写明、`undated=1`——
**不把迁移日当成真实建仓日而不标注**。
"""

from __future__ import annotations

import math
import re
import sqlite3
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ladderstock.core import portfolio as core_portfolio
from ladderstock.core.portfolio import B_SHARE, B_SHARE_UNSUPPORTED, Trade
from ladderstock.data import sqlite_store
from ladderstock.errors import EXIT_CONFIG, EXIT_NOT_FOUND, EXIT_USAGE, LadderStockError

#: `POST /api/portfolio/ledger` 的字段白名单（多一个键就 400，不做模糊匹配）
TRADE_WRITE_FIELDS: tuple[str, ...] = (
    "symbol",
    "name",
    "type",
    "trade_date",
    "shares",
    "price",
    "fee",
    "amount",
    "ratio",
    "note",
)

#: 每一类流水真正要落的数字字段（其余字段即使传了也拒收）
FIELDS_OF: dict[str, tuple[str, ...]] = {
    "opening": ("shares", "price"),
    "buy": ("shares", "price", "fee"),
    "sell": ("shares", "price", "fee"),
    "dividend": ("amount",),
    "split": ("ratio",),
}

#: 旧记录迁移进 `note` 的标注
MIGRATED_NOTE = "从旧持有记录迁移"
UNDATED_NOTE = "原记录未写建仓日，按迁移日暂记"

#: 备份文件名前缀（`journal.sqlite` → `journal.sqlite.bak-portfolio-<timestamp>`）
BACKUP_MARK = "bak-portfolio-"

NOTE_MAX = 1000
NAME_MAX = 40

_NUMERIC_FIELDS: tuple[str, ...] = ("shares", "price", "fee", "amount", "ratio")
_SYMBOL_PATTERN = re.compile(r"^(?:sh|sz|bj)?(\d{6})$", re.IGNORECASE)

_TRADE_COLUMNS = (
    "id, symbol, name, type, trade_date, shares, price, fee, amount, ratio, "
    "note, migrated_from, undated, created_at"
)
_TRADE_SELECT = (
    f"SELECT {_TRADE_COLUMNS} FROM portfolio_trades"
    " ORDER BY trade_date, CASE WHEN type = 'split' THEN 0 ELSE 1 END, created_at, id"
)
_INSERT_TRADE = (
    "INSERT INTO portfolio_trades"
    " (symbol, name, type, trade_date, shares, price, fee, amount, ratio, note,"
    "  migrated_from, undated, created_at)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


# --------------------------------------------------------------------------- #
# 校验
# --------------------------------------------------------------------------- #

def _usage(message: str) -> LadderStockError:
    return LadderStockError(message, EXIT_USAGE)


def clean_symbol(raw: Any) -> str:
    """归一成裸 6 位 A 股代码；B 股与其它写法明确拒收。"""
    text = str(raw or "").strip().upper()
    if not text:
        raise _usage("代码不能为空")
    match = _SYMBOL_PATTERN.match(text)
    if match is None:
        raise _usage(f"代码要 6 位数字（可带 sh/sz/bj 前缀），收到 {raw!r}")
    symbol = match.group(1)
    if B_SHARE.match(symbol):
        raise _usage(B_SHARE_UNSUPPORTED)
    return symbol


def _number(raw: Any, key: str) -> float | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise _usage(f"{key} 必须是数字，收到 {raw!r}")
    value = float(raw)
    if not math.isfinite(value):
        raise _usage(f"{key} 必须是有限数字，收到 {raw!r}")
    return value


def _text(raw: Any, key: str, limit: int) -> str:
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise _usage(f"{key} 必须是文本")
    return raw.strip()[:limit]


def _clean_type(raw: Any) -> str:
    kind = str(raw or "").strip().lower()
    if kind not in core_portfolio.TRADE_TYPES:
        raise _usage(
            f"类型只能是 {' / '.join(core_portfolio.TRADE_TYPES)}，收到 {raw!r}"
        )
    return kind


def _clean_date(raw: Any) -> str:
    text = str(raw or "").strip()
    if not core_portfolio.real_day(text):
        raise _usage(f"日期要是真实存在的 YYYY-MM-DD，收到 {raw!r}")
    return text


def trade_write_fields(raw: Any) -> dict[str, Any]:
    """把请求体收敛成可直接落库的字段；未知键 / 类型不对 / 缺必填都抛用法错误。"""
    if not isinstance(raw, Mapping):
        raise _usage("请求体必须是一条流水（JSON 对象）")
    unknown = sorted(str(key) for key in raw if str(key) not in TRADE_WRITE_FIELDS)
    if unknown:
        raise _usage(f"不认识的字段：{', '.join(unknown)}（只收 {', '.join(TRADE_WRITE_FIELDS)}）")

    symbol = clean_symbol(raw.get("symbol"))
    kind = _clean_type(raw.get("type"))
    trade_date = _clean_date(raw.get("trade_date"))
    allowed = FIELDS_OF[kind]

    values: dict[str, float | None] = {}
    for key in _NUMERIC_FIELDS:
        value = _number(raw.get(key), key)
        if key not in allowed and value is not None:
            raise _usage(f"{kind} 流水不需要 {key}")
        values[key] = value if key in allowed else None

    if kind == "opening":
        if values["shares"] is None or values["shares"] <= 0:
            raise _usage("期初持仓要写大于 0 的数量")
        if values["price"] is None:
            raise _usage("期初持仓要写每股成本（可以是负数）")
    elif kind in ("buy", "sell"):
        label = "买入" if kind == "buy" else "卖出"
        if values["shares"] is None or values["shares"] <= 0:
            raise _usage(f"{label}要写大于 0 的数量")
        if values["price"] is None or values["price"] <= 0:
            raise _usage(f"{label}要写大于 0 的成交价")
        if values["fee"] is not None and values["fee"] < 0:
            raise _usage("费用不能是负数（没有就留空）")
    elif kind == "dividend":
        if values["amount"] is None or values["amount"] <= 0:
            raise _usage("现金分红要写大于 0 的到账金额")
    elif values["ratio"] is None or values["ratio"] <= 0:
        raise _usage("送转拆股要写大于 0 的比例（每 1 股变成几股，如 10 转 5 写 1.5）")

    return {
        "symbol": symbol,
        "name": _text(raw.get("name"), "name", NAME_MAX),
        "type": kind,
        "trade_date": trade_date,
        **values,
        "note": _text(raw.get("note"), "note", NOTE_MAX),
    }


# --------------------------------------------------------------------------- #
# 读
# --------------------------------------------------------------------------- #

def _trade_from_row(row: sqlite3.Row) -> Trade:
    return Trade(
        id=int(row["id"]),
        symbol=str(row["symbol"]),
        name=str(row["name"] or ""),
        type=str(row["type"]),
        date=str(row["trade_date"]),
        shares=row["shares"],
        price=row["price"],
        fee=row["fee"],
        amount=row["amount"],
        ratio=row["ratio"],
        note=str(row["note"] or ""),
        migrated_from=int(row["migrated_from"]) if row["migrated_from"] is not None else None,
        undated=bool(row["undated"]),
        created_at=str(row["created_at"] or ""),
    )


def trade_as_dict(trade: Trade) -> dict[str, Any]:
    """对外投影：字段名与参考实现一致（`date` 而不是 `trade_date`）。"""
    return {
        "id": trade.id,
        "symbol": trade.symbol,
        "name": trade.name,
        "type": trade.type,
        "date": trade.date,
        "shares": trade.shares,
        "price": trade.price,
        "fee": trade.fee,
        "amount": trade.amount,
        "ratio": trade.ratio,
        "note": trade.note,
        "migrated_from": trade.migrated_from,
        "undated": trade.undated,
        "created_at": trade.created_at,
    }


def list_trades(*, root: Path | None = None) -> list[Trade]:
    """按重放顺序读出全部流水；库不存在时返回空列表（不建库）。"""
    conn = sqlite_store.connect(root=root, create=False)
    if conn is None:
        return []
    try:
        return [_trade_from_row(row) for row in conn.execute(_TRADE_SELECT)]
    finally:
        sqlite_store.close(conn)


def _position_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, code, qty, cost, entry_date, note FROM positions ORDER BY id"
    )
    return [dict(row) for row in rows]


def _mark_ids(conn: sqlite3.Connection) -> set[int]:
    rows = conn.execute("SELECT position_id FROM portfolio_legacy_marks")
    return {int(row[0]) for row in rows}


def _snapshot(
    *, root: Path | None = None
) -> tuple[list[dict[str, Any]], set[int], list[Trade]]:
    conn = sqlite_store.connect(root=root, create=False)
    if conn is None:
        return [], set(), []
    try:
        return (
            _position_rows(conn),
            _mark_ids(conn),
            [_trade_from_row(row) for row in conn.execute(_TRADE_SELECT)],
        )
    finally:
        sqlite_store.close(conn)


# --------------------------------------------------------------------------- #
# 旧仓分类
# --------------------------------------------------------------------------- #

def classify_legacy(
    positions: list[dict[str, Any]],
    marks: set[int],
    trades: list[Trade],
) -> list[dict[str, Any]]:
    """每条旧 `positions` 记录归哪一类（纯函数）。

    - `pending`：可用、无冲突 → 下次自动转期初流水；
    - `stale`：已经按它转过、数量 / 成本一致 → 不再重复转换，页面显示「已迁移」；
    - `conflict`：转过但旧记录后来改了，或同代码已有别的流水 / 多条旧记录 → 用户 adopt；
    - `skipped`：无代码、数量 ≤ 0、无成本 → 说明原因；
    - `dismissed`：用户已点「不要这条」。
    """
    by_origin: dict[int, Trade] = {}
    for trade in trades:
        if trade.migrated_from is not None:
            by_origin.setdefault(trade.migrated_from, trade)

    def symbol_of(position: dict[str, Any]) -> str:
        return str(position.get("code") or "").strip().upper()

    def shares_of(position: dict[str, Any]) -> float | None:
        value = position.get("qty")
        return float(value) if value is not None else None

    def cost_of(position: dict[str, Any]) -> float | None:
        value = position.get("cost")
        return float(value) if value is not None else None

    def usable(position: dict[str, Any]) -> bool:
        return (
            int(position["id"]) not in by_origin
            and bool(symbol_of(position))
            and (shares_of(position) or 0.0) > 0
            and cost_of(position) is not None
        )

    same_symbol: dict[str, int] = {}
    for position in positions:
        if usable(position):
            code = symbol_of(position)
            same_symbol[code] = same_symbol.get(code, 0) + 1

    out: list[dict[str, Any]] = []
    for position in positions:
        pid = int(position["id"])
        symbol = symbol_of(position)
        shares = shares_of(position)
        cost = cost_of(position)
        opened_at = str(position.get("entry_date") or "").strip()
        entry: dict[str, Any] = {
            "id": pid,
            "symbol": symbol,
            "name": "",
            "shares": shares,
            "cost": cost,
            "opened_at": opened_at,
            "state": "pending",
            "why": "",
            "migrated_trade": None,
        }

        done = by_origin.get(pid)
        if done is not None:
            same = (
                done.symbol == symbol
                and done.shares == shares
                and done.price == cost
            )
            entry["migrated_trade"] = done.id
            if same:
                entry["state"] = "stale"
                entry["why"] = "已经转成期初持仓流水，旧记录保留作对照"
            else:
                entry["state"] = "conflict"
                entry["why"] = (
                    "已经按它转过一笔期初持仓，但旧记录之后的数量或成本和那笔对不上："
                    "可以按旧记录现在的数替换那笔，或保留那笔、不要这条"
                )
            out.append(entry)
            continue

        if pid in marks:
            entry["state"] = "dismissed"
            entry["why"] = "你已确认不要这条旧记录"
            out.append(entry)
            continue

        if not symbol:
            entry["state"] = "skipped"
            entry["why"] = "没有代码"
            out.append(entry)
            continue
        if shares is None or shares <= 0:
            entry["state"] = "skipped"
            entry["why"] = "数量是 0，不算持有；不需要可以直接标成不要"
            out.append(entry)
            continue
        if cost is None:
            entry["state"] = "skipped"
            entry["why"] = "没有成本"
            out.append(entry)
            continue

        other_trades = [
            trade
            for trade in trades
            if trade.symbol == symbol and trade.migrated_from != pid
        ]
        if other_trades:
            entry["state"] = "conflict"
            entry["why"] = (
                "这个代码已经有交易流水：这条可能是重复录入，"
                "请确认要不要按它补一笔期初持仓"
            )
            out.append(entry)
            continue
        if same_symbol.get(symbol, 0) > 1:
            entry["state"] = "conflict"
            entry["why"] = (
                f"这个代码有 {same_symbol[symbol]} 条旧持有记录："
                "可能是重复录入，请选一条补期初持仓、其余标成不要"
            )
            out.append(entry)
            continue

        out.append(entry)
    return out


def legacy_rows(*, root: Path | None = None) -> list[dict[str, Any]]:
    """旧记录 + 分类；库不存在时返回空列表（不建库）。"""
    positions, marks, trades = _snapshot(root=root)
    return classify_legacy(positions, marks, trades)


# --------------------------------------------------------------------------- #
# 备份
# --------------------------------------------------------------------------- #

def _backup(conn: sqlite3.Connection, *, root: Path | None = None) -> str:
    """用 SQLite backup API 落一份带时间戳的副本，返回文件名。"""
    target = sqlite_store.db_path(root)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = f"{target.name}.{BACKUP_MARK}{stamp}"
    candidate = target.with_name(base)
    index = 2
    while candidate.exists():
        candidate = target.with_name(f"{base}-{index}")
        index += 1
    try:
        dest = sqlite3.connect(str(candidate))
        try:
            conn.backup(dest)
        finally:
            dest.close()
    except (OSError, sqlite3.Error) as exc:
        raise LadderStockError(
            f"备份本机账本失败，本次没有写入：{exc}", EXIT_CONFIG
        ) from None
    return candidate.name


# --------------------------------------------------------------------------- #
# 写：单笔流水
# --------------------------------------------------------------------------- #

def _read_trade(conn: sqlite3.Connection, trade_id: int) -> sqlite3.Row | None:
    return conn.execute(
        f"SELECT {_TRADE_COLUMNS} FROM portfolio_trades WHERE id = ?", (trade_id,)
    ).fetchone()


def add_trade(raw: Any, *, root: Path | None = None) -> dict[str, Any]:
    """新增一笔流水（只写 `portfolio_trades`），返回落库后的行。"""
    fields = trade_write_fields(raw)
    conn = sqlite_store.connect(root=root, create=True)
    assert conn is not None  # create=True 时不会返回 None
    try:
        cursor = conn.execute(
            _INSERT_TRADE,
            (
                fields["symbol"],
                fields["name"],
                fields["type"],
                fields["trade_date"],
                fields["shares"],
                fields["price"],
                fields["fee"],
                fields["amount"],
                fields["ratio"],
                fields["note"],
                None,
                0,
                sqlite_store.now_iso(),
            ),
        )
        row = _read_trade(conn, int(cursor.lastrowid or 0))
    finally:
        sqlite_store.close(conn)
    if row is None:  # pragma: no cover - 插入后读不到属于磁盘异常
        raise _usage("写入流水失败：插入后读不到记录")
    return trade_as_dict(_trade_from_row(row))


def delete_trade(trade_id: int, *, root: Path | None = None) -> dict[str, Any]:
    """硬删一笔流水；删之前先备份。不存在就是 6（无结果）。"""
    conn = sqlite_store.connect(root=root, create=False)
    if conn is None:
        raise LadderStockError(f"本机账本里没有这笔流水（id={trade_id}）", EXIT_NOT_FOUND)
    try:
        row = _read_trade(conn, trade_id)
        if row is None:
            raise LadderStockError(f"本机账本里没有这笔流水（id={trade_id}）", EXIT_NOT_FOUND)
        removed = trade_as_dict(_trade_from_row(row))
        backup = _backup(conn, root=root)
        conn.execute("DELETE FROM portfolio_trades WHERE id = ?", (trade_id,))
    finally:
        sqlite_store.close(conn)
    return {"removed": removed, "backup": backup}


# --------------------------------------------------------------------------- #
# 旧仓迁移
# --------------------------------------------------------------------------- #

def _opening_values(
    position: dict[str, Any], *, day: str
) -> tuple[list[Any], bool]:
    """旧记录 → 期初流水的插入值；返回 (参数, 是否没有建仓日)。"""
    opened = str(position.get("entry_date") or "").strip()
    undated = not opened
    trade_date = opened or day
    base_note = str(position.get("note") or "").strip()
    tag = MIGRATED_NOTE if not undated else f"{MIGRATED_NOTE}；{UNDATED_NOTE}"
    note = f"{base_note}\n{tag}" if base_note else tag
    note = note[:NOTE_MAX]
    values = [
        str(position.get("code") or "").strip().upper(),
        "",
        "opening",
        trade_date,
        float(position["qty"]),
        float(position["cost"]),
        None,
        None,
        None,
        note,
        int(position["id"]),
        1 if undated else 0,
        sqlite_store.now_iso(),
    ]
    return values, undated


def _empty_migration() -> dict[str, Any]:
    return {"backup": None, "migrated": [], "removed_stale": [], "failed": []}


def migrate_legacy(
    *, root: Path | None = None, now: datetime | None = None
) -> dict[str, Any]:
    """自动迁移：`pending` 的旧记录转成期初流水。

    没有任何 `pending` 时什么都不写（也不备份）。`stale` 已经在账本里，
    本任务**不删 `positions` 行**，所以这里也不动它。整批在一个事务里，
    中途失败回滚并抛出。
    """
    positions, marks, trades = _snapshot(root=root)
    todo = [
        item
        for item in classify_legacy(positions, marks, trades)
        if item["state"] == "pending"
    ]
    if not todo:
        return _empty_migration()

    day = (now or datetime.now()).date().isoformat()
    by_id = {int(position["id"]): position for position in positions}
    conn = sqlite_store.connect(root=root, create=True)
    assert conn is not None
    try:
        backup = _backup(conn, root=root)
        migrated: list[dict[str, Any]] = []
        try:
            conn.execute("BEGIN IMMEDIATE")
            for item in todo:
                position = by_id[int(item["id"])]
                values, _ = _opening_values(position, day=day)
                cursor = conn.execute(_INSERT_TRADE, values)
                migrated.append(
                    {
                        "position_id": int(item["id"]),
                        "trade_id": int(cursor.lastrowid or 0),
                        "symbol": item["symbol"],
                    }
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        sqlite_store.close(conn)
    return {
        "backup": backup,
        "migrated": migrated,
        "removed_stale": [],
        "failed": [],
    }


def _require_position(position_id: int, *, root: Path | None = None) -> dict[str, Any]:
    positions, _, _ = _snapshot(root=root)
    for position in positions:
        if int(position["id"]) == position_id:
            return position
    raise LadderStockError(
        f"没有这条旧持有记录（id={position_id}，可能已删除）", EXIT_NOT_FOUND
    )


def adopt_legacy(
    position_id: int, *, root: Path | None = None, now: datetime | None = None
) -> dict[str, Any]:
    """用户确认：把冲突 / 待定旧记录转成期初流水。

    已经按它转过一笔的，**原地改写那一笔**（不新增第二笔）；写过 dismiss 标记的，
    这次确认会把标记清掉。
    """
    position = _require_position(position_id, root=root)
    positions, marks, trades = _snapshot(root=root)
    info = classify_legacy([position], marks, trades)[0]
    if info["state"] == "skipped":
        raise _usage(f"这条旧记录没法转：{info['why']}")
    if info["state"] == "stale":
        raise _usage("这条已经转成期初流水了，不用再转")

    previous = [trade for trade in trades if trade.migrated_from == position_id]
    day = (now or datetime.now()).date().isoformat()
    values, _ = _opening_values(position, day=day)
    conn = sqlite_store.connect(root=root, create=True)
    assert conn is not None
    try:
        backup = _backup(conn, root=root)
        try:
            conn.execute("BEGIN IMMEDIATE")
            if previous:
                trade_id = previous[0].id
                conn.execute(
                    "UPDATE portfolio_trades SET symbol = ?, name = ?, type = 'opening',"
                    " trade_date = ?, shares = ?, price = ?, fee = NULL, amount = NULL,"
                    " ratio = NULL, note = ?, undated = ?, created_at = ? WHERE id = ?",
                    (
                        values[0],
                        values[1],
                        values[3],
                        values[4],
                        values[5],
                        values[9],
                        values[11],
                        values[12],
                        trade_id,
                    ),
                )
                for extra in previous[1:]:
                    conn.execute(
                        "DELETE FROM portfolio_trades WHERE id = ?", (extra.id,)
                    )
            else:
                cursor = conn.execute(_INSERT_TRADE, values)
                trade_id = int(cursor.lastrowid or 0)
            conn.execute(
                "DELETE FROM portfolio_legacy_marks WHERE position_id = ?",
                (position_id,),
            )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        sqlite_store.close(conn)
    return {
        "backup": backup,
        "migrated": [
            {
                "position_id": position_id,
                "trade_id": trade_id,
                "symbol": info["symbol"],
            }
        ],
        "removed_stale": [],
        "failed": [],
    }


def dismiss_legacy(
    position_id: int, *, root: Path | None = None, now: datetime | None = None
) -> dict[str, Any]:
    """用户确认不要这条旧记录：只写 `portfolio_legacy_marks`，不删 `positions` 行。"""
    position = _require_position(position_id, root=root)
    _, marks, trades = _snapshot(root=root)
    info = classify_legacy([position], marks, trades)[0]
    if info["state"] == "stale":
        raise _usage("这条已经转成期初流水了，不能标成不要")

    stamp = (now or datetime.now()).astimezone().isoformat(timespec="seconds")
    conn = sqlite_store.connect(root=root, create=True)
    assert conn is not None
    try:
        conn.execute(
            "INSERT INTO portfolio_legacy_marks (position_id, dismissed_at)"
            " VALUES (?, ?)"
            " ON CONFLICT(position_id) DO UPDATE SET dismissed_at = excluded.dismissed_at",
            (position_id, stamp),
        )
    finally:
        sqlite_store.close(conn)
    return {"position_id": position_id, "dismissed_at": stamp}


def today_iso() -> str:
    return date.today().isoformat()


__all__ = [
    "BACKUP_MARK",
    "FIELDS_OF",
    "MIGRATED_NOTE",
    "TRADE_WRITE_FIELDS",
    "UNDATED_NOTE",
    "add_trade",
    "adopt_legacy",
    "classify_legacy",
    "clean_symbol",
    "delete_trade",
    "dismiss_legacy",
    "legacy_rows",
    "list_trades",
    "migrate_legacy",
    "today_iso",
    "trade_as_dict",
    "trade_write_fields",
]
