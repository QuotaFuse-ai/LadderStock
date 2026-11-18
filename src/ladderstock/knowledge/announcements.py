"""公告 → 知识库投影（形态 2）。

FinanceCoffer 只留 ODS/DWD/DWS 结构化事实：公告正文存在 DWD 里，语义检索不再由
FC 自己的 ``vector.announcement_chunk``（pgvector + bge-m3 1024 维）承担，而是投影
到 LS 的 qdrant 知识库 ``ladderstock_knowledge``（fastembed 512 维），供 agent 直接
调用。用户 2026-11-18 拍板：数据源由 LS 直读 FC 的 PostgreSQL。

口径与 :mod:`ladderstock.knowledge.vectors` 保持一致：

* **向量层是加速器**：qdrant 不通 / 模型缺失 / 维度不符，一律抛
  ``AnnouncementsUnavailable``，调用方退回 FC 的 MCP 工具
  （``search_announcements_semantically``）或按 symbol 精确读 DWD。
* **重跑覆盖**：point id 复用 ``point_id(market, symbol, ann_date, section, i)``，
  同一条公告重投影写同一批 id；整批投影前先按 ``source=announcement`` + symbol
  删旧点，避免正文改版后新旧切片并存。
* **切块按窗口**：公告正文没有 ``##`` 章节结构，按字符窗口切（默认 500 字、
  50 字重叠），每块带标题与公告日期，检索结果能回溯到具体公告。
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

SOURCE = "announcement"
MARKET = "CN"
DEFAULT_CHUNK_CHARS = 500
OVERLAP_CHARS = 50
DEFAULT_LIMIT = 200
DEFAULT_MIN_SCORE = 0.30
MAX_LIMIT = 1000


class AnnouncementsUnavailable(RuntimeError):
    """公告源（FC 的 PG）或知识库不可用：调用方退回 FC MCP。"""


# --------------------------------------------------------------------------- #
# 数据源
# --------------------------------------------------------------------------- #


def pg_dsn() -> str:
    """FC 的 PostgreSQL 连接串；端口跟 services 的口径走（正式 5432 / 测试 15432）。"""

    explicit = (os.environ.get("LDST_FC_PG_DSN") or "").strip()
    if explicit:
        return explicit
    port = 5432
    try:
        from ladderstock.modules.financecoffer import services

        port = int(services.pg_port())
    except Exception as exc:  # noqa: BLE001 - 取不到就按正式服口径
        logger.warning("announcement_pg_port_probe_failed detail=%s", exc)
    user = (os.environ.get("LDST_FC_PG_USER") or "fc").strip() or "fc"
    name = (os.environ.get("LDST_FC_PG_DB") or "financecoffer").strip() or "financecoffer"
    return f"postgresql://{user}@127.0.0.1:{port}/{name}"


def _connect() -> Any:
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - 缺依赖时退回 FC MCP
        raise AnnouncementsUnavailable(f"psycopg 未安装，无法直读 FC 的结构化库：{exc}") from exc
    try:
        return psycopg.connect(pg_dsn(), connect_timeout=5)
    except Exception as exc:  # noqa: BLE001 - 网络/信任配置失败都按不可用
        raise AnnouncementsUnavailable(f"连不上 FC 的 PostgreSQL（{pg_dsn()}）：{exc}") from exc


@dataclass(frozen=True)
class AnnouncementRow:
    id: int
    symbol: str
    ann_date: str
    title: str
    body: str
    event_id: str


def fetch_announcements(
    symbol: str | None = None, *, limit: int = DEFAULT_LIMIT
) -> list[AnnouncementRow]:
    """从 ``dwd.stock_announcement`` 读公告；只要正文非空的。"""

    bounded = max(1, min(int(limit), MAX_LIMIT))
    clauses = ["clean_content IS NOT NULL", "btrim(clean_content) <> ''"]
    params: list[Any] = []
    if symbol:
        clauses.append("symbol = %s")
        params.append(str(symbol).strip())
    sql = (
        "SELECT id, symbol, COALESCE(ann_date::text, ''), COALESCE(title, ''),"
        " clean_content, COALESCE(event_id, '')"
        " FROM dwd.stock_announcement"
        f" WHERE {' AND '.join(clauses)}"
        " ORDER BY ann_date DESC NULLS LAST, id DESC LIMIT %s"
    )
    params.append(bounded)
    with _connect() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
        except Exception as exc:  # noqa: BLE001 - 表缺失/权限问题
            raise AnnouncementsUnavailable(f"读 dwd.stock_announcement 失败：{exc}") from exc
    return [
        AnnouncementRow(
            id=int(row[0]),
            symbol=str(row[1]),
            ann_date=str(row[2] or ""),
            title=str(row[3] or ""),
            body=str(row[4] or ""),
            event_id=str(row[5] or ""),
        )
        for row in rows
    ]


# --------------------------------------------------------------------------- #
# 投影指纹 / 标的清单（给「要不要重投影」用）
# --------------------------------------------------------------------------- #


def fingerprint(symbol: str | None = None) -> dict[str, Any]:
    """投影指纹：行数 / 最大 id / 正文字符总和。

    判「要不要重投影」只看这三个数：新公告入库会让行数与最大 id 变；正文补充是
    原地 UPDATE（id 不变），但会把字符总和推大。三者任一变化才重投，稳定态零开销
    ——不必每轮都跑一次 embedding 和白写一遍 qdrant。
    """

    clauses = ["clean_content IS NOT NULL", "btrim(clean_content) <> ''"]
    params: list[Any] = []
    if symbol:
        clauses.append("symbol = %s")
        params.append(str(symbol).strip())
    sql = (
        "SELECT count(*), COALESCE(max(id), 0), COALESCE(sum(length(clean_content)), 0)"
        " FROM dwd.stock_announcement"
        f" WHERE {' AND '.join(clauses)}"
    )
    with _connect() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
        except Exception as exc:  # noqa: BLE001 - 表缺失/权限问题
            raise AnnouncementsUnavailable(f"读 dwd.stock_announcement 失败：{exc}") from exc
    return {
        "symbol": symbol or "",
        "rows": int(row[0] or 0),
        "max_id": int(row[1] or 0),
        "chars": int(row[2] or 0),
    }


def symbols() -> list[str]:
    """dwd 里已有正文的标的清单（投影时按标的逐个同步）。"""

    sql = (
        "SELECT DISTINCT symbol FROM dwd.stock_announcement"
        " WHERE clean_content IS NOT NULL AND btrim(clean_content) <> ''"
        " ORDER BY symbol"
    )
    with _connect() as conn:
        try:
            with conn.cursor() as cur:
                cur.execute(sql)
                rows = cur.fetchall()
        except Exception as exc:  # noqa: BLE001
            raise AnnouncementsUnavailable(f"读 dwd.stock_announcement 失败：{exc}") from exc
    return [str(row[0]) for row in rows if row and row[0]]


# --------------------------------------------------------------------------- #
# 切块
# --------------------------------------------------------------------------- #


def chunk_body(
    text: str, *, size: int = DEFAULT_CHUNK_CHARS, overlap: int = OVERLAP_CHARS
) -> list[str]:
    """按字符窗口切块，尽量在句号/分号/换行处收口，保留原始换行结构。"""

    body = (text or "").replace("\r", "")
    body = re.sub(r"[ \t]+", " ", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    if not body:
        return []
    window = max(120, int(size))
    step_back = max(0, min(int(overlap), window // 2))

    chunks: list[str] = []
    start = 0
    while start < len(body):
        end = min(len(body), start + window)
        if end < len(body):
            floor = start + window // 2
            for mark in ("。", "；", "\n", " "):
                cut = body.rfind(mark, floor, end)
                if cut > floor:
                    end = cut + 1
                    break
        piece = body[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(body):
            break
        start = max(start + 1, end - step_back)
    return chunks


# --------------------------------------------------------------------------- #
# 投影
# --------------------------------------------------------------------------- #


@dataclass
class SyncInfo:
    symbol: str
    announcements: int
    chunks: int
    deleted: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "announcements": self.announcements,
            "chunks": self.chunks,
            "deleted": self.deleted,
        }


def _delete_symbol(symbol: str) -> int:
    from ladderstock.knowledge.vectors import _request, collection_name

    body = {
        "filter": {
            "must": [
                {"key": "source", "match": {"value": SOURCE}},
                {"key": "symbol", "match": {"value": symbol}},
            ]
        }
    }
    result = _request("POST", f"/collections/{collection_name()}/points/delete", json_body=body)
    return int(((result or {}).get("result") or {}).get("deleted") or 0)


def sync_symbol(symbol: str, *, limit: int = DEFAULT_LIMIT, purge: bool = True) -> SyncInfo:
    """把一只票的公告投影到 qdrant；源或知识库不可用抛 AnnouncementsUnavailable。"""

    from ladderstock.knowledge.vectors import (
        VectorsUnavailable,
        _request,
        collection_name,
        embed,
        ensure_collection,
        point_id,
    )

    rows = fetch_announcements(symbol, limit=limit)
    if not rows:
        return SyncInfo(symbol=symbol, announcements=0, chunks=0, deleted=0)

    pieces: list[tuple[AnnouncementRow, int, str]] = []
    for row in rows:
        for index, chunk in enumerate(chunk_body(row.body)):
            pieces.append((row, index, chunk))
    if not pieces:
        return SyncInfo(symbol=symbol, announcements=len(rows), chunks=0, deleted=0)

    try:
        vectors = embed([chunk for _, _, chunk in pieces])
        named = ensure_collection(len(vectors[0]))
        deleted = _delete_symbol(symbol) if purge else 0
        points = []
        for (row, index, chunk), vector in zip(pieces, vectors, strict=False):
            section = f"公告:{row.event_id or row.id}"
            points.append(
                {
                    "id": point_id(MARKET, row.symbol, row.ann_date or "unknown", section, index),
                    "vector": {named: vector} if named else vector,
                    "payload": {
                        "source": SOURCE,
                        "market": MARKET,
                        "symbol": row.symbol,
                        "as_of": row.ann_date,
                        "status": "fresh",
                        "section": section,
                        "title": row.title,
                        "ann_date": row.ann_date,
                        "event_id": row.event_id,
                        "dwd_id": row.id,
                        "chunk_index": index,
                        "text": chunk,
                    },
                }
            )
        _request(
            "PUT",
            f"/collections/{collection_name()}/points",
            json_body={"points": points, "wait": True},
        )
    except VectorsUnavailable as exc:
        raise AnnouncementsUnavailable(str(exc)) from exc

    logger.info(
        "announcements_projected symbol=%s announcements=%s chunks=%s deleted=%s",
        symbol,
        len(rows),
        len(points),
        deleted,
    )
    return SyncInfo(symbol=symbol, announcements=len(rows), chunks=len(points), deleted=deleted)


# --------------------------------------------------------------------------- #
# 检索
# --------------------------------------------------------------------------- #


@dataclass
class AnnouncementHit:
    score: float
    symbol: str
    ann_date: str
    title: str
    section: str
    text: str
    event_id: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "symbol": self.symbol,
            "ann_date": self.ann_date,
            "title": self.title,
            "section": self.section,
            "event_id": self.event_id,
            "text": self.text,
        }


def search(
    query: str,
    *,
    symbol: str | None = None,
    top_k: int = 5,
    min_score: float = DEFAULT_MIN_SCORE,
) -> list[AnnouncementHit]:
    """语义检索公告切片；向量层不可用抛 AnnouncementsUnavailable。"""

    from ladderstock.knowledge.vectors import (
        VectorsUnavailable,
        _request,
        collection_name,
        embed_one,
        ensure_collection,
    )

    text = (query or "").strip()
    if not text:
        return []
    try:
        vector = embed_one(text)
        named = ensure_collection(len(vector))
        body: dict[str, Any] = {
            "vector": {"name": named, "vector": vector} if named else vector,
            "limit": max(1, int(top_k)),
            "with_payload": True,
            "score_threshold": float(min_score),
            "filter": {
                "must": [
                    {"key": "source", "match": {"value": SOURCE}},
                    *(
                        [{"key": "symbol", "match": {"value": str(symbol).strip()}}]
                        if symbol
                        else []
                    ),
                ]
            },
        }
        result = _request("POST", f"/collections/{collection_name()}/points/search", json_body=body)
    except VectorsUnavailable as exc:
        raise AnnouncementsUnavailable(str(exc)) from exc

    hits: list[AnnouncementHit] = []
    for item in (result or {}).get("result") or []:
        payload = item.get("payload") or {}
        hits.append(
            AnnouncementHit(
                score=float(item.get("score") or 0.0),
                symbol=str(payload.get("symbol") or ""),
                ann_date=str(payload.get("ann_date") or payload.get("as_of") or ""),
                title=str(payload.get("title") or ""),
                section=str(payload.get("section") or ""),
                text=str(payload.get("text") or ""),
                event_id=str(payload.get("event_id") or ""),
            )
        )
    return hits


def available() -> dict[str, Any]:
    """给设置页/接口的健康摘要：qdrant 是否在、公告点有多少。"""

    info: dict[str, Any] = {"ok": False, "dsn": pg_dsn(), "points": None, "error": None}
    try:
        from ladderstock.knowledge.vectors import _request, collection_name

        result = _request(
            "POST",
            f"/collections/{collection_name()}/points/count",
            json_body={
                "filter": {"must": [{"key": "source", "match": {"value": SOURCE}}]},
                "exact": True,
            },
        )
        info["points"] = int(((result or {}).get("result") or {}).get("count") or 0)
        info["ok"] = True
    except Exception as exc:  # noqa: BLE001
        info["error"] = str(exc)
        return info
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM dwd.stock_announcement"
                " WHERE clean_content IS NOT NULL AND btrim(clean_content) <> ''"
            )
            info["dwd_rows"] = int(cur.fetchone()[0])
    except AnnouncementsUnavailable as exc:
        info["source_error"] = str(exc)
    return info


__all__ = [
    "AnnouncementHit",
    "AnnouncementRow",
    "AnnouncementsUnavailable",
    "SyncInfo",
    "available",
    "chunk_body",
    "fetch_announcements",
    "fingerprint",
    "pg_dsn",
    "search",
    "symbols",
    "sync_symbol",
]
