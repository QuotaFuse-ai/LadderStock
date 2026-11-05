"""本机提醒收件箱：`inbox` 表读写（DEC-058）。

三条硬约束（与 `journal/watchlist.py` / `journal/portfolio.py` 同一条）：

1. **只落本机**：只依赖 `data/sqlite_store` + 标准库，不 import 适配器 / 行情 / MCP / AI，
   不产生任何网络请求。
2. **不出本机**：返回值只交给本机页面（回环独占的 `GET /api/inbox`）与定时任务，
   不进 `LadderEnvelope`、不进 MCP、不进 AI 上下文。
3. **一条摘要只占一行**：`dedupe_key = f"{job}:{trade_day}"` 命中唯一索引时**更新**那条
   （标题 / 正文 / 时间刷新），`read_at` 原样保留——同一交易日补跑不会给用户刷出第二条，
   也不会把「已读」悄悄改回「未读」。

只读函数在库不存在时返回空结果**且不建库**（`create=False`），与 `list_watch` 同一取舍：
页面被访问一下不该在磁盘上留一个空文件。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from ladderstock.data import sqlite_store
from ladderstock.errors import EXIT_NOT_FOUND, EXIT_USAGE, LadderStockError

INBOX_TABLE = "inbox"

#: 单条摘要最多几行正文（`lines[]`），超出截断——正文是给人看的，不做无界增长
MAX_LINES = 40

#: 单行最多多少字符
MAX_LINE_CHARS = 500

#: `GET /api/inbox?limit=` 的默认值与上限
DEFAULT_LIMIT = 50
MAX_LIMIT = 200

_COLUMNS = (
    "id, job, job_label, kind, title, lines_json, link, dedupe_key, read_at, created_at"
)


def _usage(message: str) -> LadderStockError:
    return LadderStockError(message, EXIT_USAGE)


def _not_found(item_id: str) -> LadderStockError:
    return LadderStockError(f"本机收件箱里没有这条提醒（id={item_id}）", EXIT_NOT_FOUND)


def _clean_lines(lines: Sequence[str] | None) -> list[str]:
    """正文归一：逐行 strip、丢空行、限行数 / 行长。"""
    out: list[str] = []
    for raw in lines or []:
        if not isinstance(raw, str):
            raise _usage(f"摘要正文必须是文本行，收到 {raw!r}")
        text = raw.strip()
        if not text:
            continue
        out.append(text[:MAX_LINE_CHARS])
        if len(out) >= MAX_LINES:
            break
    return out


def _clean_optional(raw: Any, name: str) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise _usage(f"{name} 必须是文本")
    text = raw.strip()
    return text or None


def _item_from_row(row: sqlite3.Row) -> dict[str, Any]:
    try:
        lines = json.loads(row["lines_json"] or "[]")
    except (TypeError, ValueError):
        lines = []
    if not isinstance(lines, list):
        lines = []
    read_at = row["read_at"]
    return {
        "id": str(row["id"]),
        "job": str(row["job"]),
        "job_label": str(row["job_label"] or ""),
        "kind": str(row["kind"] or "digest"),
        "title": str(row["title"]),
        "lines": [str(line) for line in lines],
        "link": row["link"],
        "is_read": read_at is not None,
        "read_at": read_at,
        "created_at": str(row["created_at"]),
    }


def _select(conn: sqlite3.Connection, item_id: str) -> sqlite3.Row | None:
    return conn.execute(
        f"SELECT {_COLUMNS} FROM {INBOX_TABLE} WHERE id = ?", (item_id,)
    ).fetchone()


def _connect_read(root: Path | None) -> sqlite3.Connection | None:
    conn = sqlite_store.connect(root=root, create=False)
    if conn is None:
        return None
    if INBOX_TABLE not in sqlite_store.table_names(conn):
        sqlite_store.close(conn)
        return None
    return conn


# --------------------------------------------------------------------------- #
# 写
# --------------------------------------------------------------------------- #

#: `dedupe_key` 命中时更新那条；**不动 `read_at`**（保留用户的已读状态）
_UPSERT = (
    f"INSERT INTO {INBOX_TABLE}"
    " (id, job, job_label, kind, title, lines_json, link, dedupe_key, read_at, created_at)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)"
    " ON CONFLICT(dedupe_key) WHERE dedupe_key IS NOT NULL DO UPDATE SET"
    " job = excluded.job,"
    " job_label = excluded.job_label,"
    " kind = excluded.kind,"
    " title = excluded.title,"
    " lines_json = excluded.lines_json,"
    " link = excluded.link,"
    " created_at = excluded.created_at"
    " RETURNING id"
)


def add_item(
    job: str,
    job_label: str,
    title: str,
    lines: Sequence[str] | None,
    link: str | None = None,
    dedupe_key: str | None = None,
    kind: str = "digest",
    *,
    root: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """写入 / 刷新一条提醒。

    `dedupe_key` 命中时更新原行（标题 / 正文 / 时间刷新），**`read_at` 保留**；
    同一交易日重跑只留一条，不产生重复。
    """
    job_id = str(job or "").strip()
    if not job_id:
        raise _usage("提醒必须带任务 id")
    heading = str(title or "").strip()
    if not heading:
        raise _usage("提醒标题不能为空")
    body = _clean_lines(lines)
    stamp = (now or datetime.now()).astimezone().isoformat(timespec="seconds")

    conn = sqlite_store.connect(root=root, create=True)
    assert conn is not None  # create=True 时不会返回 None
    try:
        row = conn.execute(
            _UPSERT,
            (
                uuid.uuid4().hex,
                job_id,
                str(job_label or "").strip(),
                str(kind or "digest").strip() or "digest",
                heading[:MAX_LINE_CHARS],
                json.dumps(body, ensure_ascii=False),
                _clean_optional(link, "link"),
                _clean_optional(dedupe_key, "dedupe_key"),
                stamp,
            ),
        ).fetchone()
        if row is None:  # pragma: no cover - RETURNING 拿不到属于磁盘异常
            raise _usage("写入提醒失败：插入后读不到记录")
        stored = _select(conn, str(row[0]))
    finally:
        sqlite_store.close(conn)
    if stored is None:  # pragma: no cover - 同上
        raise _usage("写入提醒失败：插入后读不到记录")
    return _item_from_row(stored)


# --------------------------------------------------------------------------- #
# 读 / 已读
# --------------------------------------------------------------------------- #

def list_items(limit: int = DEFAULT_LIMIT, *, root: Path | None = None) -> list[dict[str, Any]]:
    """按 `created_at DESC` 读出提醒；库不存在 / 没这张表时返回空列表（不建库）。"""
    size = int(limit)
    if size < 1 or size > MAX_LIMIT:
        raise _usage(f"limit 要在 1–{MAX_LIMIT} 之间，收到 {limit!r}")
    conn = _connect_read(root)
    if conn is None:
        return []
    try:
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM {INBOX_TABLE}"
            " ORDER BY created_at DESC, id DESC LIMIT ?",
            (size,),
        ).fetchall()
        return [_item_from_row(row) for row in rows]
    finally:
        sqlite_store.close(conn)


def unread_count(*, root: Path | None = None) -> int:
    """未读数：`read_at IS NULL` 才算未读（已读时间为空才是未读）。"""
    conn = _connect_read(root)
    if conn is None:
        return 0
    try:
        row = conn.execute(
            f"SELECT COUNT(*) FROM {INBOX_TABLE} WHERE read_at IS NULL"
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        sqlite_store.close(conn)


def mark_read(
    item_id: str, *, root: Path | None = None, now: datetime | None = None
) -> dict[str, Any]:
    """把一条标成已读；已经读过的不改写原时间（幂等）。不存在就是 6（无结果）。"""
    text = str(item_id or "").strip()
    if not text:
        raise _usage("提醒 id 不能为空")
    stamp = (now or datetime.now()).astimezone().isoformat(timespec="seconds")
    conn = _connect_read(root)
    if conn is None:
        raise _not_found(text)
    try:
        row = _select(conn, text)
        if row is None:
            raise _not_found(text)
        if row["read_at"] is None:
            conn.execute(
                f"UPDATE {INBOX_TABLE} SET read_at = ? WHERE id = ? AND read_at IS NULL",
                (stamp, text),
            )
            row = _select(conn, text)
    finally:
        sqlite_store.close(conn)
    if row is None:  # pragma: no cover - 上面刚读到过
        raise _not_found(text)
    return _item_from_row(row)


def mark_all_read(*, root: Path | None = None, now: datetime | None = None) -> dict[str, Any]:
    """把所有未读标成已读，返回本次实际改写条数。"""
    stamp = (now or datetime.now()).astimezone().isoformat(timespec="seconds")
    conn = _connect_read(root)
    if conn is None:
        return {"updated": 0, "read_at": stamp}
    try:
        cursor = conn.execute(
            f"UPDATE {INBOX_TABLE} SET read_at = ? WHERE read_at IS NULL", (stamp,)
        )
        return {"updated": int(cursor.rowcount or 0), "read_at": stamp}
    finally:
        sqlite_store.close(conn)


__all__ = [
    "DEFAULT_LIMIT",
    "INBOX_TABLE",
    "MAX_LIMIT",
    "MAX_LINES",
    "MAX_LINE_CHARS",
    "add_item",
    "list_items",
    "mark_all_read",
    "mark_read",
    "unread_count",
]
