"""journal.sqlite schema v3：旧库原样升上来，新表建好，重复迁移不改数据（QUOT-426）。

契约来自 DEC-056 / DEC-058：

- v2 → v3 只新增表与索引，`trades` / `positions` / `watchlist` 一字节不动；
- 迁移前必须留 `journal.sqlite.bak-v2-<时间戳>`；
- 版本号同时写进 `PRAGMA user_version` 与 `schema_meta.schema_version`；
- 再跑一次 `migrate()` 不报错、不重复建表、不改数据、不再备份。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ladderstock.data import sqlite_store

#: 手写一份 v2 库结构，不依赖实现里的 `_SCHEMA_V*`（否则测试自己证明自己）
V2_DDL = """
CREATE TABLE schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date  TEXT NOT NULL,
    code        TEXT NOT NULL,
    side        TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    price       REAL NOT NULL CHECK (price > 0),
    qty         REAL NOT NULL CHECK (qty > 0),
    plan_price  REAL CHECK (plan_price IS NULL OR plan_price > 0),
    note        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE INDEX idx_trades_trade_date ON trades (trade_date);
CREATE INDEX idx_trades_code_date ON trades (code, trade_date);
CREATE INDEX idx_trades_created_at ON trades (created_at);
CREATE TABLE positions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL UNIQUE,
    qty         REAL NOT NULL CHECK (qty >= 0),
    cost        REAL NOT NULL CHECK (cost >= 0),
    updated_at  TEXT NOT NULL,
    entry_date  TEXT,
    industry    TEXT NOT NULL DEFAULT '',
    note        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX idx_positions_updated_at ON positions (updated_at);
CREATE INDEX idx_positions_entry_date ON positions (entry_date, code);
CREATE TABLE watchlist (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL UNIQUE,
    note        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE INDEX idx_watchlist_created_at ON watchlist (created_at);
INSERT INTO schema_meta (key, value) VALUES ('schema_version', '2');
"""

V1_DDL = """
CREATE TABLE schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE positions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL UNIQUE,
    qty         REAL NOT NULL CHECK (qty >= 0),
    avg_price   REAL NOT NULL CHECK (avg_price >= 0),
    updated_at  TEXT NOT NULL
);
INSERT INTO schema_meta (key, value) VALUES ('schema_version', '1');
"""


def _make_database(path: Path, version: int) -> None:
    """造一个带真实行的旧库：交易纪律日志 + 旧持仓各一行。"""
    raw = sqlite3.connect(str(path))
    try:
        raw.executescript(V1_DDL if version == 1 else V2_DDL)
        raw.execute(f"PRAGMA user_version = {version}")
        if version == 1:
            raw.execute(
                "INSERT INTO positions (code, qty, avg_price, updated_at)"
                " VALUES ('600519', 100, 1500.0, '2026-10-01T09:00:00+08:00')"
            )
        else:
            raw.execute(
                "INSERT INTO positions (code, qty, cost, updated_at, entry_date)"
                " VALUES ('600519', 100, 1500.0, '2026-10-01T09:00:00+08:00', '2026-09-01')"
            )
            raw.execute(
                "INSERT INTO trades (trade_date, code, side, price, qty, note, created_at)"
                " VALUES ('2026-10-02', '600519', 'buy', 1500.0, 100, '哨兵-TRADE',"
                " '2026-10-02T10:00:00+08:00')"
            )
        raw.commit()
    finally:
        raw.close()


def _rows(conn: sqlite3.Connection, table: str) -> list[dict[str, object]]:
    return [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id")]


@pytest.fixture()
def v2_db(tmp_path: Path) -> Path:
    path = tmp_path / sqlite_store.DB_FILENAME
    _make_database(path, version=2)
    return path


def test_v2_upgrades_to_v3_with_timestamped_backup(
    tmp_path: Path, v2_db: Path
) -> None:
    before_trades = None
    raw = sqlite3.connect(str(v2_db))
    raw.row_factory = sqlite3.Row
    try:
        before_trades = [dict(row) for row in raw.execute("SELECT * FROM trades")]
        before_positions = [dict(row) for row in raw.execute("SELECT * FROM positions")]
    finally:
        raw.close()

    conn = sqlite_store.connect(root=tmp_path, create=True)
    assert conn is not None
    try:
        assert sqlite_store.SCHEMA_VERSION == 3
        assert sqlite_store.schema_version(conn) == 3
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 3
        meta = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]
        assert int(meta) == 3, "PRAGMA 与 schema_meta 两个口径必须一致"

        names = set(sqlite_store.table_names(conn))
        assert {"portfolio_trades", "portfolio_legacy_marks", "inbox"} <= names
        assert set(sqlite_store.TABLES) <= names

        # 旧表行数 / 内容一字节不变
        assert _rows(conn, "trades") == [dict(row) for row in before_trades]
        assert _rows(conn, "positions") == [dict(row) for row in before_positions]
    finally:
        sqlite_store.close(conn)

    backups = list(tmp_path.glob(f"{sqlite_store.DB_FILENAME}.bak-v2-*"))
    assert len(backups) == 1, "v2 → v3 必须留下一份带版本与时间戳的备份"
    backup = sqlite3.connect(str(backups[0]))
    try:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 2
        assert backup.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1
    finally:
        backup.close()


def test_second_migrate_is_a_noop(tmp_path: Path, v2_db: Path) -> None:
    conn = sqlite_store.connect(root=tmp_path, create=True)
    assert conn is not None
    try:
        snapshot = sqlite_store.table_names(conn)
        indexes_before = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
        trades_before = _rows(conn, "trades")
        positions_before = _rows(conn, "positions")

        assert sqlite_store.migrate(conn) == 3
        assert sqlite_store.migrate(conn) == 3

        assert sqlite_store.table_names(conn) == snapshot
        assert {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        } == indexes_before
        assert _rows(conn, "trades") == trades_before
        assert _rows(conn, "positions") == positions_before
    finally:
        sqlite_store.close(conn)

    assert len(list(tmp_path.glob(f"{sqlite_store.DB_FILENAME}.bak-*"))) == 1


def test_new_tables_have_the_contract_columns(tmp_path: Path, v2_db: Path) -> None:
    """Stage 2 的 A / C 直接依赖这些列名，字段名不得再改。"""
    conn = sqlite_store.connect(root=tmp_path, create=True)
    assert conn is not None
    try:
        columns = {
            table: [str(row[1]) for row in conn.execute(f"PRAGMA table_info ({table})")]
            for table in ("portfolio_trades", "portfolio_legacy_marks", "inbox")
        }
    finally:
        sqlite_store.close(conn)

    assert columns["portfolio_trades"] == [
        "id", "symbol", "name", "type", "trade_date", "shares", "price", "fee",
        "amount", "ratio", "note", "migrated_from", "undated", "created_at",
    ]
    assert columns["portfolio_legacy_marks"] == ["position_id", "dismissed_at"]
    assert columns["inbox"] == [
        "id", "job", "job_label", "kind", "title", "lines_json", "link",
        "dedupe_key", "read_at", "created_at",
    ]


def test_v1_upgrades_all_the_way_to_v3(tmp_path: Path) -> None:
    path = tmp_path / sqlite_store.DB_FILENAME
    _make_database(path, version=1)

    conn = sqlite_store.connect(root=tmp_path, create=True)
    assert conn is not None
    try:
        assert sqlite_store.schema_version(conn) == 3
        row = conn.execute("SELECT * FROM positions").fetchone()
        assert row is not None
        assert dict(row)["code"] == "600519"
        assert dict(row)["cost"] == 1500.0  # 原 avg_price 的值没丢
        assert {"portfolio_trades", "portfolio_legacy_marks", "inbox"} <= set(
            sqlite_store.table_names(conn)
        )
    finally:
        sqlite_store.close(conn)

    assert list(tmp_path.glob(f"{sqlite_store.DB_FILENAME}.bak-v1-*"))


def test_migrated_from_is_unique_and_nullable(tmp_path: Path, v2_db: Path) -> None:
    """旧持仓只能迁出一笔期初流水；`migrated_from` 为空的行不受唯一索引约束。"""
    conn = sqlite_store.connect(root=tmp_path, create=True)
    assert conn is not None
    try:
        insert = (
            "INSERT INTO portfolio_trades"
            " (symbol, type, trade_date, shares, price, migrated_from, created_at)"
            " VALUES (?, 'opening', '2026-01-01', 100, ?, ?, '2026-01-01T00:00:00+08:00')"
        )
        conn.execute(insert, ("600519", 10, 7))
        conn.execute(insert, ("600520", 10, None))
        conn.execute(insert, ("600521", 10, None))
        # 期初持仓的每股成本允许为负（分红 / 送转摊薄后的真实成本）
        conn.execute(insert, ("600522", -2.5, None))
        assert conn.execute(
            "SELECT price FROM portfolio_trades WHERE symbol = '600522'"
        ).fetchone()[0] == -2.5
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, ("600519", 10, 7))
    finally:
        sqlite_store.close(conn)
