"""本机隐私库：交易日志 / 持仓 / 自选（SQLite，DEC-006 隐私边界）。

布局::

    <data_dir>/journal.sqlite     # 隐私库；data/ 已在 .gitignore

三条硬约束：

1. **只落本机**：本模块只读写本地文件，没有任何网络分支；
2. **不出本机**：数据不得进入 `LadderEnvelope`、MCP 工具结果或 AI prompt
   （见 `docs/MCP_TOOLS.md` §隐私边界）；
3. **版本可控**：迁移版本号与 `config/schema/*.json` 同口径——整数单调递增，
   不兼容变更必须 +1；打开旧库自动迁移，打开更高版本的库直接拒绝
   （退出码 3），绝不「猜」未来 schema。

版本号同时写进 `PRAGMA user_version` 与 `schema_meta` 表，二者必须一致，
方便外部工具（备份/审计）不依赖 Python 也能读出当前版本。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ladderstock.cli.paths import data_dir
from ladderstock.errors import EXIT_CONFIG, LadderStockError

DB_FILENAME = "journal.sqlite"

#: 隐私库 schema 版本；与 config/schema 的整数版本号同口径
SCHEMA_VERSION = 1

SCHEMA_META_TABLE = "schema_meta"

TABLES: tuple[str, ...] = ("trades", "positions", "watchlist")


def db_path(root: Path | None = None) -> Path:
    base = Path(root) if root is not None else data_dir()
    return base / DB_FILENAME


def schema_version(conn: sqlite3.Connection) -> int:
    """当前库版本：优先 `PRAGMA user_version`，没有则看 `schema_meta`。"""
    row = conn.execute("PRAGMA user_version").fetchone()
    version = int(row[0]) if row and row[0] else 0
    if version:
        return version
    if not _has_table(conn, SCHEMA_META_TABLE):
        return 0
    row = conn.execute(
        f"SELECT value FROM {SCHEMA_META_TABLE} WHERE key = 'schema_version'"
    ).fetchone()
    try:
        return int(row[0]) if row and row[0] is not None else 0
    except (TypeError, ValueError):
        return 0


def table_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return sorted(str(row[0]) for row in rows)


def connect(root: Path | None = None, *, create: bool = True) -> sqlite3.Connection | None:
    """打开隐私库。

    `create=False` 且文件不存在时返回 `None`——`journal list` / `stats` 不该因为
    只是查一下就在磁盘上留下一个空库。打开后自动 `migrate()`。
    """
    target = db_path(root)
    if not target.is_file() and not create:
        return None
    if create:
        target.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(target), timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")   # WAL：读写互不阻塞
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        migrate(conn)
    except Exception:
        conn.close()
        raise
    return conn


def close(conn: sqlite3.Connection | None) -> None:
    if conn is None:
        return
    try:
        conn.close()
    except Exception:  # noqa: BLE001 - 关闭失败不影响主流程
        pass


# --------------------------------------------------------------------------- #
# 迁移
# --------------------------------------------------------------------------- #

def migrate(conn: sqlite3.Connection, *, backup: bool = True) -> int:
    """幂等迁移到 `SCHEMA_VERSION`；返回迁移后的版本号。

    `backup=True`（默认）时，**在动结构之前**先落一份带版本与时间戳的副本
    （`journal.sqlite.bak-v<ver>-<时间戳>`，审计 R-6b）。已有库（version > 0）
    才备份；空库/新建库没有可丢的数据。`ldst config migrate` 自己会先备份，
    再以 `backup=False` 复用本函数，避免同一版本重复备份两份。
    """
    version = schema_version(conn)
    if version > SCHEMA_VERSION:
        raise LadderStockError(
            f"本机隐私库版本 {version} 高于当前程序支持的 {SCHEMA_VERSION}；"
            "请升级 LadderStock（不会自动降级或改写数据）",
            EXIT_CONFIG,
        )
    if version < SCHEMA_VERSION:
        # 有内容的库才需要备份：`user_version` 已写或是里面已经有表（老库可能没写版本号）
        if backup and (version > 0 or table_names(conn)):
            _backup_before_migration(conn, version)
        conn.execute("BEGIN IMMEDIATE")
        try:
            _apply(conn)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    return schema_version(conn)


def _backup_before_migration(conn: sqlite3.Connection, version: int) -> None:
    """迁移前备份；拿不到库文件路径（`:memory:`）时跳过。"""
    path = _connection_path(conn)
    if path is None:
        return
    from ladderstock.config.migrate import backup_sqlite_file

    try:
        backup_sqlite_file(conn, path, version=version)
    except OSError as exc:
        raise LadderStockError(
            f"隐私库迁移前备份失败（{path}）：{exc}；为避免无备份改结构，本次迁移已中止",
            EXIT_CONFIG,
        ) from exc


def _connection_path(conn: sqlite3.Connection) -> Path | None:
    row = conn.execute("PRAGMA database_list").fetchone()
    value = str(row[2]) if row and row[2] else ""
    return Path(value) if value else None


def _apply(conn: sqlite3.Connection) -> None:
    """v1 结构；`IF NOT EXISTS` 保证重复执行不改变已有数据。

    注意：这里逐条 `execute()` 而不用 `executescript()`——后者会先隐式提交，
    把外层 `BEGIN IMMEDIATE` 事务拆散，导致随后的 `COMMIT` 报
    “cannot rollback - no transaction is active”。
    """
    for statement in _split_statements(_SCHEMA_V1):
        conn.execute(statement)
    conn.execute(
        f"INSERT INTO {SCHEMA_META_TABLE} (key, value) VALUES ('schema_version', ?) "
        f"ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(SCHEMA_VERSION),),
    )


def _split_statements(script: str) -> list[str]:
    """把 DDL 脚本切成单条语句（DDL 里没有字符串含分号，去掉行注释后按 `;` 切）。"""
    body = "\n".join(
        line.split("--", 1)[0] for line in script.splitlines()
    )
    return [chunk.strip() for chunk in body.split(";") if chunk.strip()]


_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trades (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date  TEXT NOT NULL,                       -- YYYY-MM-DD
    code        TEXT NOT NULL,
    side        TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    price       REAL NOT NULL CHECK (price > 0),
    qty         REAL NOT NULL CHECK (qty > 0),
    plan_price  REAL CHECK (plan_price IS NULL OR plan_price > 0),  -- 计划价（纪律复查）
    note        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL                        -- ISO-8601（带时区）
);
CREATE INDEX IF NOT EXISTS idx_trades_trade_date ON trades (trade_date);
CREATE INDEX IF NOT EXISTS idx_trades_code_date ON trades (code, trade_date);
CREATE INDEX IF NOT EXISTS idx_trades_created_at ON trades (created_at);

CREATE TABLE IF NOT EXISTS positions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL UNIQUE,
    qty         REAL NOT NULL CHECK (qty >= 0),
    avg_price   REAL NOT NULL CHECK (avg_price >= 0),
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_positions_updated_at ON positions (updated_at);

CREATE TABLE IF NOT EXISTS watchlist (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL UNIQUE,
    note        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_watchlist_created_at ON watchlist (created_at);
"""


# --------------------------------------------------------------------------- #
# 小工具（journal.log / journal.stats 共用）
# --------------------------------------------------------------------------- #

def parse_day(value: str | date | None) -> date | None:
    """把 `YYYY-MM-DD` / `date` 归一成 `date`；空值返回 None，非法值抛用法错误。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        raise LadderStockError(f"日期需要 YYYY-MM-DD，收到 {value!r}") from None


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


__all__ = [
    "DB_FILENAME",
    "SCHEMA_VERSION",
    "TABLES",
    "close",
    "connect",
    "db_path",
    "migrate",
    "now_iso",
    "parse_day",
    "rows_to_dicts",
    "schema_version",
    "table_names",
]
