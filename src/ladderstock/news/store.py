"""资讯与事件的本机存储（SQLite，`data/news.sqlite`）。

为什么单独建库、不进 `journal.sqlite`：隐私库只有交易记录 / 持仓 / 自选，
资讯是**公开数据**，混在一库里会让 `check_privacy` 的隐私面更难说清，
也让「资讯流不含隐私字段」这条验收只能靠约定。分库之后口径是一句话：
**news.sqlite 里不可能有隐私数据，因为它根本不与隐私库共用连接。**

幂等口径（DEC-007 §2.3 的资讯版）：

- `id` 由去重键（规范化 url，其次归一化标题）单向哈希得出 → 同一篇稿子重跑必然同 id；
- 写入一律 `INSERT ... ON CONFLICT(id) DO UPDATE`，`first_seen_at` 保留首次入库时间；
- 因此「同一 fixture 重跑」行数不变、id 集合不变（测试 `test_idempotent_rerun` 钉住）。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ladderstock.cli.paths import data_dir
from ladderstock.news.events import NewsEvent
from ladderstock.news.models import NewsItem

DB_FILENAME = "news.sqlite"

SCHEMA_VERSION = 1
SCHEMA_META_TABLE = "schema_meta"

TABLES: tuple[str, ...] = ("news_items", "news_events", "news_runs")

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
;
CREATE TABLE IF NOT EXISTS news_items (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    published_at TEXT,
    published_day TEXT,
    related_codes TEXT NOT NULL DEFAULT '[]',
    summary TEXT NOT NULL DEFAULT '',
    content_hash TEXT NOT NULL DEFAULT '',
    dedup_key TEXT NOT NULL DEFAULT '',
    title_key TEXT NOT NULL DEFAULT '',
    origins TEXT NOT NULL DEFAULT '[]',
    provenance TEXT NOT NULL DEFAULT '{}',
    first_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
;
CREATE INDEX IF NOT EXISTS idx_news_items_day ON news_items(published_day)
;
CREATE INDEX IF NOT EXISTS idx_news_items_title_key ON news_items(title_key)
;
CREATE INDEX IF NOT EXISTS idx_news_items_hash ON news_items(content_hash)
;
CREATE TABLE IF NOT EXISTS news_events (
    id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL DEFAULT '',
    type TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    rule_id TEXT NOT NULL DEFAULT '',
    event_date TEXT,
    title TEXT NOT NULL,
    codes TEXT NOT NULL DEFAULT '[]',
    evidence TEXT NOT NULL DEFAULT '{}',
    date_basis TEXT NOT NULL DEFAULT 'published_at',
    extractor TEXT NOT NULL DEFAULT 'rule',
    ai_note TEXT,
    first_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
;
CREATE INDEX IF NOT EXISTS idx_news_events_date ON news_events(event_date)
;
CREATE INDEX IF NOT EXISTS idx_news_events_type ON news_events(type)
;
CREATE TABLE IF NOT EXISTS news_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    window_start TEXT,
    window_end TEXT,
    datasets TEXT NOT NULL DEFAULT '[]',
    codes TEXT NOT NULL DEFAULT '[]',
    fetched INTEGER NOT NULL DEFAULT 0,
    stored INTEGER NOT NULL DEFAULT 0,
    deduped INTEGER NOT NULL DEFAULT 0,
    events INTEGER NOT NULL DEFAULT 0,
    detail TEXT NOT NULL DEFAULT '{}'
)
"""


def db_path(root: Path | None = None) -> Path:
    base = Path(root) if root is not None else data_dir()
    return base / DB_FILENAME


def connect(root: Path | None = None, *, create: bool = True) -> sqlite3.Connection | None:
    """打开资讯库。`create=False` 且文件不存在时返回 `None`（只读查询不建空库）。"""
    target = db_path(root)
    if not target.is_file() and not create:
        return None
    if create:
        target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    _migrate(conn)
    return conn


def close(conn: sqlite3.Connection | None) -> None:
    if conn is None:
        return
    try:
        conn.close()
    except Exception:  # noqa: BLE001 - 关闭失败不影响主流程
        pass


def _migrate(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    version = int(row[0]) if row and row[0] else 0
    if version > SCHEMA_VERSION:
        raise RuntimeError(
            f"本机资讯库版本 {version} 高于当前程序支持的 {SCHEMA_VERSION}；请先升级程序"
        )
    for statement in _SCHEMA_V1.strip().split("\n;\n"):
        sql = statement.strip().strip(";").strip()
        if sql:
            conn.execute(sql)
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {SCHEMA_META_TABLE}"
        " (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    conn.execute(
        f"INSERT INTO {SCHEMA_META_TABLE}(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return SCHEMA_VERSION


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# 写
# --------------------------------------------------------------------------- #

def upsert_items(conn: sqlite3.Connection, items: list[NewsItem]) -> int:
    """幂等写入资讯条目：同 id 覆盖、保留首次入库时间。"""
    if not items:
        return 0
    timestamp = _now()
    rows = []
    for item in items:
        rows.append(
            (
                item.id,
                item.source,
                item.title,
                item.url,
                item.published_at,
                item.day.isoformat() if item.day else None,
                json.dumps(list(item.related_codes), ensure_ascii=False),
                item.summary,
                item.content_hash,
                item.dedup_key,
                _title_key(item.title),
                json.dumps([dict(o) for o in item.origins], ensure_ascii=False),
                json.dumps(item.provenance, ensure_ascii=False),
                timestamp,
                timestamp,
            )
        )
    conn.executemany(
        """
        INSERT INTO news_items (
            id, source, title, url, published_at, published_day, related_codes, summary,
            content_hash, dedup_key, title_key, origins, provenance, first_seen_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            source=excluded.source, title=excluded.title, url=excluded.url,
            published_at=excluded.published_at, published_day=excluded.published_day,
            related_codes=excluded.related_codes, summary=excluded.summary,
            content_hash=excluded.content_hash, dedup_key=excluded.dedup_key,
            title_key=excluded.title_key, origins=excluded.origins,
            provenance=excluded.provenance, updated_at=excluded.updated_at
        """,
        rows,
    )
    return len(rows)


def upsert_events(conn: sqlite3.Connection, events: list[NewsEvent]) -> int:
    if not events:
        return 0
    timestamp = _now()
    rows = [
        (
            event.id,
            event.item_id,
            event.type,
            event.label,
            event.rule_id,
            event.event_date,
            event.title,
            json.dumps(list(event.codes), ensure_ascii=False),
            json.dumps(event.evidence, ensure_ascii=False),
            event.date_basis,
            event.extractor,
            event.ai_note,
            timestamp,
            timestamp,
        )
        for event in events
    ]
    conn.executemany(
        """
        INSERT INTO news_events (
            id, item_id, type, label, rule_id, event_date, title, codes, evidence,
            date_basis, extractor, ai_note, first_seen_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            item_id=excluded.item_id, type=excluded.type, label=excluded.label,
            rule_id=excluded.rule_id, event_date=excluded.event_date, title=excluded.title,
            codes=excluded.codes, evidence=excluded.evidence, date_basis=excluded.date_basis,
            extractor=excluded.extractor, ai_note=excluded.ai_note, updated_at=excluded.updated_at
        """,
        rows,
    )
    return len(rows)


def record_run(
    conn: sqlite3.Connection,
    *,
    window: tuple[date, date],
    datasets: list[str],
    codes: list[str],
    fetched: int,
    stored: int,
    deduped: int,
    events: int,
    detail: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        "INSERT INTO news_runs (started_at, window_start, window_end, datasets, codes,"
        " fetched, stored, deduped, events, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            _now(),
            window[0].isoformat(),
            window[1].isoformat(),
            json.dumps(datasets, ensure_ascii=False),
            json.dumps(codes, ensure_ascii=False),
            fetched,
            stored,
            deduped,
            events,
            json.dumps(detail or {}, ensure_ascii=False),
        ),
    )


def _title_key(title: str) -> str:
    from ladderstock.news.models import normalize_title

    return normalize_title(title)


# --------------------------------------------------------------------------- #
# 读
# --------------------------------------------------------------------------- #

def load_items(
    conn: sqlite3.Connection,
    *,
    start: date | None = None,
    end: date | None = None,
    sources: list[str] | None = None,
    code: str | None = None,
    limit: int | None = None,
) -> list[NewsItem]:
    """按时间窗读回条目（用于跨轮次合并去重：新一批要和库里已有的比，不是各比各的）。

    **没有发布时间的条目总是随窗口一起返回**：它们的日期无从比较，但标题可以和
    新一批对上，漏进候选集就会让同一篇稿子在不同轮次各留一条。窗口过滤发生在
    `stream.collect_stream`（计进 `dropped_undated`），不在这里——这里的语义是
    「候选集」，不是「展示视图」。
    """
    sql = ["SELECT * FROM news_items WHERE 1 = 1"]
    args: list[Any] = []
    if start is not None:
        sql.append("AND (published_day IS NULL OR published_day >= ?)")
        args.append(start.isoformat())
    if end is not None:
        sql.append("AND (published_day IS NULL OR published_day <= ?)")
        args.append(end.isoformat())
    if sources:
        sql.append(f"AND source IN ({', '.join('?' * len(sources))})")
        args.extend(sources)
    sql.append("ORDER BY COALESCE(published_at, '') DESC, id ASC")
    if limit is not None:
        sql.append("LIMIT ?")
        args.append(int(limit))
    rows = conn.execute(" ".join(sql), args).fetchall()
    items = [_row_to_item(row) for row in rows]
    if code:
        items = [item for item in items if code in item.related_codes]
    return items


def _row_to_item(row: sqlite3.Row) -> NewsItem:
    return NewsItem(
        id=str(row["id"]),
        source=str(row["source"]),
        title=str(row["title"]),
        url=str(row["url"]),
        published_at=row["published_at"],
        related_codes=tuple(json.loads(row["related_codes"] or "[]")),
        summary=str(row["summary"]),
        content_hash=str(row["content_hash"]),
        dedup_key=str(row["dedup_key"]),
        origins=tuple(json.loads(row["origins"] or "[]")),
        provenance=dict(json.loads(row["provenance"] or "{}")),
    )


def source_counts(
    conn: sqlite3.Connection, *, start: date, end: date
) -> dict[str, int]:
    """窗口内各栏目的条目数（`GET /api/news` 的栏目账本用，QUOT-279）。

    只算 `published_day` 落窗的行：没有发布时间的条目在这里不计——它们是合并候选，
    不是这一天的展示视图（`load_items` 的口径反过来）。
    """
    rows = conn.execute(
        "SELECT source, COUNT(1) AS n FROM news_items"
        " WHERE published_day BETWEEN ? AND ? GROUP BY source",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    return {str(row["source"]): int(row["n"]) for row in rows}


def covering_run(
    conn: sqlite3.Connection, *, start: date, end: date
) -> dict[str, Any] | None:
    """最近一次**覆盖** `[start, end]` 的取数账本；没有则 `None`。

    `news_runs.detail.sources_status` 是当时每一栏的结论（含 `failed` / `skipped` 的原因），
    只读接口用它把「某一路挂了」如实交给页面，而不必重新打一轮 peer。
    窗口是 ISO 日期串，字典序即时间序；`id` 兜底同一秒内的多次运行。
    """
    row = conn.execute(
        "SELECT * FROM news_runs WHERE window_start <= ? AND window_end >= ?"
        " ORDER BY started_at DESC, id DESC LIMIT 1",
        (start.isoformat(), end.isoformat()),
    ).fetchone()
    if row is None:
        return None
    document = dict(row)
    document["detail"] = json.loads(document.get("detail") or "{}")
    return document


def list_events(
    conn: sqlite3.Connection,
    *,
    month: str | None = None,
    event_type: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    sql = ["SELECT * FROM news_events WHERE 1 = 1"]
    args: list[Any] = []
    if month:
        sql.append("AND event_date LIKE ?")
        args.append(f"{month}-%")
    if event_type:
        sql.append("AND type = ?")
        args.append(event_type)
    sql.append("ORDER BY COALESCE(event_date, '') ASC, id ASC")
    if limit is not None:
        sql.append("LIMIT ?")
        args.append(int(limit))
    rows = conn.execute(" ".join(sql), args).fetchall()
    return [_row_to_event(row) for row in rows]


def _row_to_event(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "item_id": str(row["item_id"]),
        "type": str(row["type"]),
        "label": str(row["label"]),
        "rule_id": str(row["rule_id"]),
        "event_date": row["event_date"],
        "title": str(row["title"]),
        "codes": list(json.loads(row["codes"] or "[]")),
        "evidence": dict(json.loads(row["evidence"] or "{}")),
        "date_basis": str(row["date_basis"]),
        "extractor": str(row["extractor"]),
        "ai_note": row["ai_note"],
    }


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    out: dict[str, int] = {}
    for table in ("news_items", "news_events", "news_runs"):
        row = conn.execute(
            f"SELECT COUNT(1) AS n FROM {table}",
        ).fetchone()  # noqa: S608 - 白名单表名
        out[table] = int(row["n"]) if row else 0
    return out
