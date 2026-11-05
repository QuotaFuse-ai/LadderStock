"""QUOT-430 / DEC-056：本机账本的读写、校验与旧仓迁移。

钉住几件容易写错的事：

1. 字段白名单与类型校验（B 股、真实日期、每类流水的必填数字）；
2. 删除流水**先备份**；迁移**整批一个事务**，动库前落
   `journal.sqlite.bak-portfolio-<timestamp>`；
3. 旧 `positions` 记录只转成期初流水，**不删、不改旧行**；
4. `pending` → 自动迁；`stale` → 不再重复转；`conflict` → adopt 原地替换，
   不产生第二笔；`skipped` / `dismissed` 各有明确原因；
5. 建仓日为空 → 迁移日暂记 + `undated=1` + `note` 标注。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ladderstock.data import sqlite_store
from ladderstock.errors import EXIT_NOT_FOUND, EXIT_USAGE, LadderStockError
from ladderstock.journal import portfolio as ledger
from ladderstock.journal.position import add_position


def _seed_undated(root: Path, code: str = "000001", qty: float = 200, cost: float = 8) -> int:
    conn = sqlite_store.connect(root=root, create=True)
    assert conn is not None
    try:
        cursor = conn.execute(
            "INSERT INTO positions (code, cost, qty, entry_date, industry, note, updated_at)"
            " VALUES (?, ?, ?, NULL, '', '', ?)",
            (code, cost, qty, sqlite_store.now_iso()),
        )
        return int(cursor.lastrowid or 0)
    finally:
        sqlite_store.close(conn)


def _seed_zero(root: Path, code: str = "000002") -> int:
    conn = sqlite_store.connect(root=root, create=True)
    assert conn is not None
    try:
        cursor = conn.execute(
            "INSERT INTO positions (code, cost, qty, entry_date, industry, note, updated_at)"
            " VALUES (?, 1, 0, NULL, '', '', ?)",
            (code, sqlite_store.now_iso()),
        )
        return int(cursor.lastrowid or 0)
    finally:
        sqlite_store.close(conn)


def _position_count(root: Path) -> int:
    conn = sqlite_store.connect(root=root, create=False)
    assert conn is not None
    try:
        return int(conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0])
    finally:
        sqlite_store.close(conn)


def _set_position(
    root: Path,
    position_id: int,
    *,
    qty: float | None = None,
    cost: float | None = None,
) -> None:
    conn = sqlite_store.connect(root=root, create=True)
    assert conn is not None
    try:
        if qty is not None:
            conn.execute("UPDATE positions SET qty = ? WHERE id = ?", (qty, position_id))
        if cost is not None:
            conn.execute("UPDATE positions SET cost = ? WHERE id = ?", (cost, position_id))
    finally:
        sqlite_store.close(conn)


# --------------------------------------------------------------------------- #
# 单笔流水
# --------------------------------------------------------------------------- #


def test_add_list_delete_round_trip(tmp_path: Path) -> None:
    row = ledger.add_trade(
        {
            "symbol": "sh600519",
            "name": "贵州茅台",
            "type": "buy",
            "trade_date": "2026-01-05",
            "shares": 100,
            "price": 10,
            "fee": 5,
            "note": "首仓",
        },
        root=tmp_path,
    )
    assert row["symbol"] == "600519"
    assert row["date"] == "2026-01-05"
    assert row["shares"] == 100.0
    assert row["price"] == 10.0
    assert row["fee"] == 5.0
    assert row["undated"] is False
    assert row["migrated_from"] is None

    trades = ledger.list_trades(root=tmp_path)
    assert [trade.id for trade in trades] == [row["id"]]

    removed = ledger.delete_trade(int(row["id"]), root=tmp_path)
    backup = tmp_path / str(removed["backup"])
    assert backup.is_file()
    assert ledger.BACKUP_MARK in backup.name
    assert ledger.list_trades(root=tmp_path) == []


def test_delete_missing_trade_is_not_found(tmp_path: Path) -> None:
    ledger.add_trade(
        {"symbol": "600519", "type": "buy", "trade_date": "2026-01-05", "shares": 1, "price": 1},
        root=tmp_path,
    )
    with pytest.raises(LadderStockError) as caught:
        ledger.delete_trade(999, root=tmp_path)
    assert caught.value.code == EXIT_NOT_FOUND


def _body(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "symbol": "600519",
        "type": "buy",
        "trade_date": "2026-01-05",
        "shares": 1,
        "price": 1,
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    ("body", "why"),
    [
        (_body(extra=1), "不认识的字段"),
        (_body(symbol="12345"), "6 位数字"),
        (_body(symbol="900901"), "B 股"),
        (_body(symbol="200002"), "B 股"),
        (_body(type="wat"), "类型"),
        (_body(trade_date="2026-02-30"), "真实存在"),
        (_body(trade_date="2026-13-01"), "真实存在"),
        (_body(shares=0), "大于 0 的数量"),
        (_body(price=0), "大于 0 的成交价"),
        (_body(fee=-1), "费用"),
        (_body(amount=5), "不需要 amount"),
        (
            {"symbol": "600519", "type": "dividend", "trade_date": "2026-01-05", "amount": 0},
            "到账金额",
        ),
        ({"symbol": "600519", "type": "split", "trade_date": "2026-01-05", "ratio": 0}, "比例"),
        (
            {"symbol": "600519", "type": "opening", "trade_date": "2026-01-05", "shares": 1},
            "每股成本",
        ),
        ({"symbol": "600519", "type": "sell", "trade_date": "2026-01-05", "shares": 1}, "成交价"),
        (_body(shares=True), "必须是数字"),
        (_body(trade_date="2026/01/05"), "真实存在"),
    ],
)
def test_add_trade_rejects_bad_input(tmp_path: Path, body: dict[str, object], why: str) -> None:
    with pytest.raises(LadderStockError) as caught:
        ledger.add_trade(body, root=tmp_path)
    assert caught.value.code == EXIT_USAGE
    assert why in caught.value.message
    assert ledger.list_trades(root=tmp_path) == []


def test_opening_cost_may_be_negative(tmp_path: Path) -> None:
    row = ledger.add_trade(
        {
            "symbol": "600519",
            "type": "opening",
            "trade_date": "2026-01-05",
            "shares": 100,
            "price": -2.5,
        },
        root=tmp_path,
    )
    assert row["price"] == -2.5


def test_list_is_ordered_by_replay_order(tmp_path: Path) -> None:
    for day in ("2026-01-03", "2026-01-01", "2026-01-02"):
        ledger.add_trade(
            {"symbol": "600519", "type": "buy", "trade_date": day, "shares": 1, "price": 1},
            root=tmp_path,
        )
    assert [trade.date for trade in ledger.list_trades(root=tmp_path)] == [
        "2026-01-01",
        "2026-01-02",
        "2026-01-03",
    ]


# --------------------------------------------------------------------------- #
# 旧仓分类与迁移
# --------------------------------------------------------------------------- #


def test_legacy_states_before_migration(tmp_path: Path) -> None:
    pid = int(add_position("600519", 10, 100, entry_date="2026-01-05", root=tmp_path)["id"])
    zero = _seed_zero(tmp_path)
    legacy = ledger.legacy_rows(root=tmp_path)
    by_id = {row["id"]: row for row in legacy}
    assert by_id[pid]["state"] == "pending"
    assert by_id[zero]["state"] == "skipped"
    assert "数量是 0" in by_id[zero]["why"]


def test_migrate_pending_writes_opening_and_keeps_positions(tmp_path: Path) -> None:
    dated = int(add_position("600519", 10, 100, entry_date="2026-01-05", root=tmp_path)["id"])
    undated = _seed_undated(tmp_path)
    zero = _seed_zero(tmp_path)
    before = _position_count(tmp_path)

    result = ledger.migrate_legacy(root=tmp_path)
    backup = tmp_path / str(result["backup"])
    assert backup.is_file()
    assert ledger.BACKUP_MARK in backup.name
    assert {item["position_id"] for item in result["migrated"]} == {dated, undated}
    assert result["removed_stale"] == []
    assert result["failed"] == []
    # 旧行一条都没删、没改
    assert _position_count(tmp_path) == before == 3

    trades = {trade.migrated_from: trade for trade in ledger.list_trades(root=tmp_path)}
    assert set(trades) == {dated, undated}
    assert trades[dated].date == "2026-01-05"
    assert trades[dated].undated is False
    assert trades[dated].note == ledger.MIGRATED_NOTE
    assert trades[undated].undated is True
    assert trades[undated].date == date.today().isoformat()
    assert ledger.UNDATED_NOTE in trades[undated].note

    legacy = {row["id"]: row for row in ledger.legacy_rows(root=tmp_path)}
    assert legacy[dated]["state"] == "stale"
    assert legacy[dated]["migrated_trade"] == trades[dated].id
    assert legacy[undated]["state"] == "stale"
    assert legacy[zero]["state"] == "skipped"


def test_second_migration_is_noop_and_does_not_duplicate(tmp_path: Path) -> None:
    add_position("600519", 10, 100, entry_date="2026-01-05", root=tmp_path)
    first = ledger.migrate_legacy(root=tmp_path)
    assert len(first["migrated"]) == 1

    second = ledger.migrate_legacy(root=tmp_path)
    assert second["backup"] is None
    assert second["migrated"] == []
    assert len(ledger.list_trades(root=tmp_path)) == 1


def test_conflict_after_legacy_edit_adopt_replaces_in_place(tmp_path: Path) -> None:
    pid = int(add_position("600519", 10, 100, entry_date="2026-01-05", root=tmp_path)["id"])
    first = ledger.migrate_legacy(root=tmp_path)
    trade_id = first["migrated"][0]["trade_id"]
    _set_position(tmp_path, pid, qty=150, cost=12)

    legacy = {row["id"]: row for row in ledger.legacy_rows(root=tmp_path)}
    assert legacy[pid]["state"] == "conflict"
    assert legacy[pid]["migrated_trade"] == trade_id

    result = ledger.adopt_legacy(pid, root=tmp_path)
    assert result["backup"]
    trades = ledger.list_trades(root=tmp_path)
    assert len(trades) == 1
    assert trades[0].id == trade_id
    assert trades[0].shares == 150.0
    assert trades[0].price == 12.0
    assert trades[0].migrated_from == pid


def test_conflict_because_symbol_has_other_trades(tmp_path: Path) -> None:
    pid = int(add_position("600519", 10, 100, entry_date="2026-01-05", root=tmp_path)["id"])
    ledger.add_trade(
        {"symbol": "600519", "type": "buy", "trade_date": "2026-02-01", "shares": 100, "price": 20},
        root=tmp_path,
    )
    legacy = ledger.legacy_rows(root=tmp_path)
    assert legacy[0]["state"] == "conflict"
    assert "已经有交易流水" in legacy[0]["why"]

    # 用户确认后补一笔期初，不替换用户自己录的那笔
    ledger.adopt_legacy(pid, root=tmp_path)
    trades = ledger.list_trades(root=tmp_path)
    assert len(trades) == 2
    assert {trade.migrated_from for trade in trades} == {None, pid}


def test_adopt_stale_is_rejected(tmp_path: Path) -> None:
    pid = int(add_position("600519", 10, 100, entry_date="2026-01-05", root=tmp_path)["id"])
    ledger.migrate_legacy(root=tmp_path)
    with pytest.raises(LadderStockError) as caught:
        ledger.adopt_legacy(pid, root=tmp_path)
    assert caught.value.code == EXIT_USAGE


def test_adopt_missing_position_is_not_found(tmp_path: Path) -> None:
    with pytest.raises(LadderStockError) as caught:
        ledger.adopt_legacy(999, root=tmp_path)
    assert caught.value.code == EXIT_NOT_FOUND


def test_dismiss_marks_only_and_blocks_migration(tmp_path: Path) -> None:
    pid = int(add_position("600519", 10, 100, entry_date="2026-01-05", root=tmp_path)["id"])
    mark = ledger.dismiss_legacy(pid, root=tmp_path)
    assert mark["position_id"] == pid
    assert mark["dismissed_at"]

    legacy = {row["id"]: row for row in ledger.legacy_rows(root=tmp_path)}
    assert legacy[pid]["state"] == "dismissed"
    assert _position_count(tmp_path) == 1

    result = ledger.migrate_legacy(root=tmp_path)
    assert result["migrated"] == []
    assert ledger.list_trades(root=tmp_path) == []
    assert _position_count(tmp_path) == 1


def test_dismiss_after_migration_is_rejected(tmp_path: Path) -> None:
    pid = int(add_position("600519", 10, 100, entry_date="2026-01-05", root=tmp_path)["id"])
    ledger.migrate_legacy(root=tmp_path)
    with pytest.raises(LadderStockError) as caught:
        ledger.dismiss_legacy(pid, root=tmp_path)
    assert caught.value.code == EXIT_USAGE


def test_multiple_legacy_rows_same_symbol_conflict() -> None:
    """`positions.code` 有唯一约束，库里不可能真出现两行同代码；分类函数仍要兜住。"""
    positions = [
        {
            "id": 1,
            "code": "600519",
            "qty": 100,
            "cost": 10.0,
            "entry_date": "2026-01-05",
            "note": "",
        },
        {
            "id": 2,
            "code": "600519",
            "qty": 200,
            "cost": 10.0,
            "entry_date": "2026-01-05",
            "note": "",
        },
    ]

    legacy = ledger.classify_legacy(positions, set(), [])
    assert [row["state"] for row in legacy] == ["conflict", "conflict"]
    assert "2 条旧持有记录" in legacy[0]["why"]


def test_empty_store_reads_without_creating_database(tmp_path: Path) -> None:
    assert ledger.list_trades(root=tmp_path) == []
    assert ledger.legacy_rows(root=tmp_path) == []
    assert not sqlite_store.db_path(tmp_path).exists()
