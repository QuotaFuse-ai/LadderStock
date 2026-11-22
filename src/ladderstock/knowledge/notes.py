"""用户笔记的存储层（E 包 / DEC-091）：`data/knowledge.sqlite` 的 notes 三表。

为什么不是 `journal.sqlite`：那是隐私库（trades/positions/watchlist，
`core/privacy.py` 硬边界），把笔记塞进去只会扩大被扫描的面。笔记是
「用户对标的的看法」——与档案同族（本地知识资产），单独建库。

表结构（幂等迁移）：

- `notes`：正文 + 标的 + 标签 + 创建/更新时间；
- `note_tags`：标签去重表（`tag COLLATE NOCASE`，中文原样保留）；
- `note_note_tags`：多对多。

口径：

- 标的入 `market:symbol` 形态（与档案 key 同一族），可空——「不针对某只票的
  随手记」也合法；
- 删除是硬删但**逐标签留痕**（`note_events` 不建：页面检索走 `list` 就够，
  不引入审计面）；单条笔记长度上限 20000 字符（markdown 全文，防把库当文件盘）；
- 标签由**服务端从正文归一**（客户端只传字符串数组，逐条 strip、丢空、
  限 24 条 × 32 字符）——不信前端。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from ladderstock.cli.paths import data_dir
from ladderstock.errors import EXIT_NOT_FOUND, EXIT_USAGE, LadderStockError
from ladderstock.knowledge.model import normalize_market, normalize_symbol

DB_NAME = "knowledge.sqlite"

MAX_BODY_CHARS = 20000
MAX_TAGS = 24
MAX_TAG_CHARS = 32

_SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    market     TEXT NOT NULL DEFAULT '',
    symbol     TEXT NOT NULL DEFAULT '',
    title      TEXT NOT NULL DEFAULT '',
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_symbol ON notes (market, symbol);
CREATE TABLE IF NOT EXISTS note_tags (
    tag_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    tag     TEXT NOT NULL COLLATE NOCASE UNIQUE
);
CREATE TABLE IF NOT EXISTS note_note_tags (
    note_id INTEGER NOT NULL REFERENCES notes (id) ON DELETE CASCADE,
    tag_id  INTEGER NOT NULL REFERENCES note_tags (tag_id) ON DELETE CASCADE,
    PRIMARY KEY (note_id, tag_id)
);
"""


def db_path(root: Path | None = None) -> Path:
    return (root if root is not None else data_dir()) / DB_NAME


def connect(root: Path | None = None, *, create: bool = True) -> sqlite3.Connection | None:
    path = db_path(root)
    if not path.exists() and not create:
        return None
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    return conn


def close(conn: sqlite3.Connection | None) -> None:
    if conn is not None:
        conn.close()


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _fail(message: str, code: int = EXIT_USAGE) -> LadderStockError:
    return LadderStockError(message, code)


def _split_target(target: str | None) -> tuple[str, str]:
    """`SH:600000` / `600000` → (market, symbol)；不校验存在性（笔记可以记未研究票）。"""
    text = str(target or "").strip().upper()
    if not text:
        return "", ""
    if ":" in text:
        head, _, tail = text.partition(":")
        return normalize_market(head), normalize_symbol(tail)
    return "", normalize_symbol(text)


def clean_tags(raw: list[str] | None) -> list[str]:
    """服务端归一：strip、丢空、去重（忽略大小写，保留首个形态）、截长度与数量。"""
    folded: set[str] = set()
    out: list[str] = []
    for item in raw or []:
        tag = str(item).strip()
        if not tag:
            continue
        tag = tag[:MAX_TAG_CHARS]
        if tag.casefold() not in folded:
            folded.add(tag.casefold())
            out.append(tag)
    return out[:MAX_TAGS]


# --------------------------------------------------------------------------- #
# CRUD
# --------------------------------------------------------------------------- #


def _row_to_note(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    note = dict(row)
    tags = [
        str(item[0])
        for item in conn.execute(
            "SELECT t.tag FROM note_tags t JOIN note_note_tags m ON m.tag_id = t.tag_id"
            " WHERE m.note_id = ? ORDER BY t.tag",
            (row["id"],),
        ).fetchall()
    ]
    note["tags"] = tags
    note["target"] = (
        f"{note['market']}:{note['symbol']}"
        if note["market"] and note["symbol"]
        else note["symbol"]
    )
    return note


def add_note(
    body: str,
    *,
    target: str | None = None,
    title: str = "",
    tags: list[str] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """新增一条笔记；body 非空是唯一硬要求（空笔记没有信息量）。"""
    text = str(body or "").strip()
    if not text:
        raise _fail("笔记正文不能为空")
    if len(text) > MAX_BODY_CHARS:
        raise _fail(f"笔记正文超过 {MAX_BODY_CHARS} 字上限（当前 {len(text)}）")
    market, symbol = _split_target(target)
    heading = str(title or "").strip()[:200]
    clean = clean_tags(tags)
    stamp = _now()
    conn = connect(root)
    assert conn is not None
    try:
        cursor = conn.execute(
            "INSERT INTO notes (market, symbol, title, body, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?)",
            (market, symbol, heading, text, stamp, stamp),
        )
        note_id = int(cursor.lastrowid or 0)
        for tag in clean:
            conn.execute("INSERT OR IGNORE INTO note_tags (tag) VALUES (?)", (tag,))
            tag_id = conn.execute("SELECT tag_id FROM note_tags WHERE tag = ?", (tag,)).fetchone()[
                0
            ]
            conn.execute(
                "INSERT OR IGNORE INTO note_note_tags (note_id, tag_id) VALUES (?,?)",
                (note_id, tag_id),
            )
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        return _row_to_note(conn, row)
    finally:
        close(conn)


def list_notes(
    *,
    root: Path | None = None,
    target: str | None = None,
    tag: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """按标的 / 标签过滤；limit 上限 500。空库返回空列表（不建库）。"""
    conn = connect(root, create=False)
    if conn is None:
        return []
    try:
        clauses: list[str] = []
        params: list[Any] = []
        if target:
            market, symbol = _split_target(target)
            if market:
                clauses.append("market = ? AND symbol = ?")
                params.extend((market, symbol))
            elif symbol:
                clauses.append("symbol = ?")
                params.append(symbol)
        if tag:
            wanted = str(tag).strip()[:MAX_TAG_CHARS]
            clauses.append(
                "id IN (SELECT m.note_id FROM note_note_tags m"
                " JOIN note_tags t ON t.tag_id = m.tag_id WHERE t.tag = ? COLLATE NOCASE)"
            )
            params.append(wanted)
        where = f" WHERE {' OR '.join(clauses)}" if clauses else ""
        bounded = max(1, min(int(limit), 500))
        rows = conn.execute(
            f"SELECT * FROM notes{where} ORDER BY updated_at DESC, id DESC LIMIT {bounded}",
            params,
        ).fetchall()
        return [_row_to_note(conn, row) for row in rows]
    finally:
        close(conn)


def get_note(note_id: int, *, root: Path | None = None) -> dict[str, Any]:
    conn = connect(root, create=False)
    if conn is None:
        raise _fail(f"笔记不存在：{note_id}", EXIT_NOT_FOUND)
    try:
        row = conn.execute("SELECT * FROM notes WHERE id = ?", (int(note_id),)).fetchone()
        if row is None:
            raise _fail(f"笔记不存在：{note_id}", EXIT_NOT_FOUND)
        return _row_to_note(conn, row)
    finally:
        close(conn)


def update_note(
    note_id: int,
    *,
    root: Path | None = None,
    body: str | None = None,
    title: str | None = None,
    tags: list[str] | None = None,
    target: str | None = None,
) -> dict[str, Any]:
    """改笔记；只有传入的字段才动（tags 传了就是整体替换）。"""
    current = get_note(note_id, root=root)
    new_body = str(body).strip() if body is not None else current["body"]
    if not new_body:
        raise _fail("笔记正文不能为空")
    if len(new_body) > MAX_BODY_CHARS:
        raise _fail(f"笔记正文超过 {MAX_BODY_CHARS} 字上限")
    new_title = str(title).strip()[:200] if title is not None else current["title"]
    if target is not None:
        market, symbol = _split_target(target)
    else:
        market, symbol = current["market"], current["symbol"]
    new_tags = clean_tags(tags) if tags is not None else current["tags"]

    conn = connect(root)
    assert conn is not None
    try:
        found = conn.execute("SELECT 1 FROM notes WHERE id = ?", (int(note_id),)).fetchone()
        if found is None:
            raise _fail(f"笔记不存在：{note_id}", EXIT_NOT_FOUND)
        conn.execute(
            "UPDATE notes SET market=?, symbol=?, title=?, body=?, updated_at=? WHERE id=?",
            (market, symbol, new_title, new_body, _now(), int(note_id)),
        )
        if tags is not None:
            conn.execute("DELETE FROM note_note_tags WHERE note_id = ?", (int(note_id),))
            for tag in new_tags:
                conn.execute("INSERT OR IGNORE INTO note_tags (tag) VALUES (?)", (tag,))
                tag_id = conn.execute(
                    "SELECT tag_id FROM note_tags WHERE tag = ?", (tag,)
                ).fetchone()[0]
                conn.execute(
                    "INSERT OR IGNORE INTO note_note_tags (note_id, tag_id) VALUES (?,?)",
                    (int(note_id), tag_id),
                )
    finally:
        close(conn)
    return get_note(note_id, root=root)


def remove_note(note_id: int, *, root: Path | None = None) -> dict[str, Any]:
    removed = get_note(note_id, root=root)
    conn = connect(root)
    assert conn is not None
    try:
        conn.execute("DELETE FROM notes WHERE id = ?", (int(note_id),))
    finally:
        close(conn)
    return {"removed": True, "note": removed}


def list_tags(*, root: Path | None = None) -> list[dict[str, Any]]:
    """标签清单 + 各自笔记数（页面做标签筛选器）。"""
    conn = connect(root, create=False)
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT t.tag, COUNT(m.note_id) AS notes FROM note_tags t"
            " LEFT JOIN note_note_tags m ON m.tag_id = t.tag_id"
            " GROUP BY t.tag ORDER BY notes DESC, t.tag"
        ).fetchall()
        return [{"tag": str(row[0]), "notes": int(row[1])} for row in rows]
    finally:
        close(conn)


__all__ = [
    "DB_NAME",
    "MAX_BODY_CHARS",
    "add_note",
    "clean_tags",
    "close",
    "connect",
    "db_path",
    "get_note",
    "list_notes",
    "list_tags",
    "remove_note",
    "update_note",
]
