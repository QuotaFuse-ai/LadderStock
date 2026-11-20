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
    """FC 的 PostgreSQL 连接串。

    端口**不能**直接信 ``services.pg_port()``：那条路径的口径是「基础端口 +
    ``LDST_PORT_OFFSET``」，在没有 offset 的进程（5950 正式服、CLI、脚本）里
    会算成 5432，而 FC 实际监听 15432——实测就是这个回落把公告链路连到了空端口。
    所以这里交给 ``collector.fc_pg_port()``：它按 [推测值, 15432, 5432] 依次探监听，
    探不到才退回。
    """

    explicit = (os.environ.get("LDST_FC_PG_DSN") or "").strip()
    if explicit:
        return explicit
    port = 5432
    try:
        from ladderstock.data import collector

        port = int(collector.fc_pg_port())
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


# 公告分类：值域固定、可枚举，才能当 qdrant 的 keyword 过滤条件用。
# 顺序即优先级——一条标题可能同时像两类（"关于年度报告的事前认可意见"），
# 取最先命中的那一类，保证同一标题每次分类结果一致。
DOC_KINDS: dict[str, tuple[str, ...]] = {
    "financial_report": (
        "年度报告",
        "半年度报告",
        "季度报告",
        "中期报告",
        "中期业绩",
        "审阅报告",
    ),
    "earnings_preview": ("业绩预告", "业绩快报"),
    "dividend": ("权益分派", "利润分配", "分红", "派息"),
    "buyback": ("回购",),
    # 风险类必须排在再融资之前：审核问询函的回复标题里同时有「向特定对象发行」和「问询」，
    # 用户查「有没有被问询/被立案」时要求命中的是后者，不能被再融资吃掉。
    "risk_event": ("诉讼", "仲裁", "处罚", "问询", "监管措施", "立案", "风险提示"),
    # 并购重组排在再融资之前：重组预案常带「发行股份购买资产」，主体是重组不是融资。
    "merger": ("重大资产重组", "购买资产", "资产置换", "吸收合并", "资产出售"),
    # 再融资/发行：募集说明书、预案、招股书这一类占了 fallback 的大头，
    # 原文标题的固定叫法很稳定，不需要语义猜测。
    "refinancing": (
        "募集说明书",
        "招股说明书",
        "招股意向书",
        "上市公告书",
        "发行保荐书",
        "发行公告",
        "向特定对象发行",
        "向不特定对象发行",
        "发行股份",
        "发行预案",
        "配股",
        "可转债",
        "转债",
        "定增",
        "发行结果",
        "付息",
    ),
    "stock_incentive": ("股权激励", "员工持股", "股票期权", "限制性股票"),
    "esg_report": ("可持续发展", "社会责任报告", "环境、社会", "ESG"),
    "investor_relations": (
        "投资者关系活动",
        "投资者关系管理信息",
        "业绩说明会",
        "调研纪要",
        "路演",
    ),
    "guarantee": ("对外担保", "提供担保", "担保额度", "担保事项"),
    # 中介机构出具的核查/督导文件，既不是公司自己的决议，也不是法律意见书。
    "agency_report": ("核查意见", "专项核查", "持续督导", "专项报告"),
    "legal": ("法律意见书", "律师事务所"),
    "related_party": ("关联交易", "关联方"),
    "equity_change": ("减持", "增持", "质押", "解押", "股份变动", "限售", "权益变动"),
    "personnel": ("聘任", "辞职", "离职", "离任", "选举", "薪酬", "候选人", "高级管理人员变更"),
    "governance": (
        "章程",
        "议事规则",
        "审计",
        "内部控制",
        "董事会决议",
        "监事会决议",
        "独立董事",
        # 「会议决议公告」只匹配董事会/监事会的届次会议（"股东大会决议公告" 不含这个词）
        "会议决议公告",
        "管理制度",
        "管理办法",
        "信息披露",
        "董事会秘书",
        "续聘会计师事务所",
    ),
    # 「股东会」是最泛的词（大量公告标题都带它，包括法律意见书、会议资料），
    # 所以压到最后一档——先让更具体的类型吃掉自己的标题。
    "shareholder_meeting": ("股东会", "股东大会"),
}
DOC_KIND_FALLBACK = "notice"

_CN_QUARTER = {
    "一": "Q1",
    "二": "Q2",
    "三": "Q3",
    "四": "Q4",
    "1": "Q1",
    "2": "Q2",
    "3": "Q3",
    "4": "Q4",
}
_PERIOD_RE = re.compile(r"(20\d{2})\s*年?\s*第?\s*([一二三四1-4])\s*季度")
_HALF_RE = re.compile(r"(20\d{2})\s*年?\s*(?:半年度|中期)")
_ANNUAL_RE = re.compile(r"(19|20\d{2})\s*年?\s*年度报告")


def classify(title: str) -> tuple[str, str]:
    """标题 → ``(doc_kind, period)``。

    只按**法规固定叫法**判断，不做语义猜测：宁可落到 ``notice`` 也不误标成财报。
    ``period`` 只在财报类里抽（``2026Q3`` / ``2026H1`` / ``2025A``），其余为空——
    "2025年度股东会决议"里的年份是会议年度，不是报表期，标错比空着更糟。
    """

    text = title or ""
    kind = DOC_KIND_FALLBACK
    for name, markers in DOC_KINDS.items():
        if any(marker in text for marker in markers):
            kind = name
            break
    period = ""
    if kind == "financial_report":
        match = _PERIOD_RE.search(text)
        if match:
            period = f"{match.group(1)}{_CN_QUARTER.get(match.group(2), '')}"
        else:
            match = _HALF_RE.search(text)
            if match:
                period = f"{match.group(1)}H1"
            else:
                match = _ANNUAL_RE.search(text)
                if match:
                    period = f"{match.group(1)}A"
    return kind, period


@dataclass(frozen=True)
class AnnouncementRow:
    id: int
    symbol: str
    ann_date: str
    title: str
    body: str
    event_id: str
    ann_type: str = ""
    content_chars: int = 0
    provider: str = ""

    @property
    def doc_kind(self) -> str:
        """公告的分类标签（见 :data:`DOC_KINDS`）。"""

        return classify(self.title)[0]

    @property
    def period(self) -> str:
        """财报期（``2026Q3``/``2026H1``/``2025A``），非财报类为空。"""

        return classify(self.title)[1]


def _apply_local_fulltext(rows: list[AnnouncementRow], symbol: str | None) -> list[AnnouncementRow]:
    """用本地已抽出来的 PDF 全文覆盖"只有标题"的行。

    DWD 里的 ``clean_content`` 对绝大多数公告等于标题（TickHub 只给 PDF 链接），
    直接投影进去的就是标题碎片。正文由 :mod:`ladderstock.knowledge.fulltext`
    下载 PDF 抽出来后落 ``var/knowledge/fulltext``，这里只做覆盖，不重新抓。
    """

    if not symbol:
        return rows
    try:
        from dataclasses import replace

        from ladderstock.knowledge import fulltext

        local = fulltext.load(str(symbol).strip())
    except Exception as exc:  # noqa: BLE001 - 缓存缺失不影响主链路
        logger.info("announcement_fulltext_cache_miss symbol=%s detail=%s", symbol, exc)
        return rows
    if not local:
        return rows
    threshold = fulltext.MIN_BODY_CHARS
    patched: list[AnnouncementRow] = []
    for row in rows:
        key = f"{row.ann_date}|{row.title}"
        text = local.get(key)
        if text and int(row.content_chars or 0) < threshold:
            patched.append(replace(row, body=text, content_chars=len(text)))
        else:
            patched.append(row)
    return patched


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
        " clean_content, COALESCE(event_id, ''), COALESCE(ann_type, ''),"
        " COALESCE(length(clean_content), 0), COALESCE(provider, '')"
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
    return _apply_local_fulltext(
        [
            AnnouncementRow(
                id=int(row[0]),
                symbol=str(row[1]),
                ann_date=str(row[2] or ""),
                title=str(row[3] or ""),
                body=str(row[4] or ""),
                event_id=str(row[5] or ""),
                ann_type=str(row[6] or ""),
                content_chars=int(row[7] or 0),
                provider=str(row[8] or ""),
            )
            for row in rows
        ],
        symbol,
    )


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
    """删掉这只票的旧切片，返回删前的切片数。

    qdrant 的 ``points/delete`` 响应里只有 ``operation_id`` 和 ``status``，没有
    ``deleted`` 计数——照它取永远是 0，看起来像"没删掉"，其实是删了。所以先
    count 一次再删，用删前的数字当已删数。
    """

    from ladderstock.knowledge.vectors import _request, collection_name

    only_symbol = {
        "must": [
            {"key": "source", "match": {"value": SOURCE}},
            {"key": "symbol", "match": {"value": symbol}},
        ]
    }
    counted = _request(
        "POST",
        f"/collections/{collection_name()}/points/count",
        json_body={"filter": only_symbol, "exact": True},
    )
    before = int(((counted or {}).get("result") or {}).get("count") or 0)
    if before == 0:
        return 0
    _request(
        "POST",
        f"/collections/{collection_name()}/points/delete",
        json_body={"filter": only_symbol},
    )
    return before


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
    chunks_of: dict[int, int] = {}
    for row in rows:
        for index, chunk in enumerate(chunk_body(row.body)):
            pieces.append((row, index, chunk))
            chunks_of[row.id] = index + 1
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
                        # 分类属性：检索时能按「只看财报类公告」这类条件先过滤再算余弦
                        "ann_type": row.ann_type,
                        "doc_kind": row.doc_kind,
                        "period": row.period,
                        "provider": row.provider,
                        "content_chars": row.content_chars,
                        "chunk_total": chunks_of.get(row.id, 0),
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


def sync_many(
    symbols: list[str],
    *,
    limit: int = DEFAULT_LIMIT,
    workers: int = 3,
    purge: bool = True,
    progress: Any = None,
) -> dict[str, Any]:
    """批量投影：把 DWD 里那批票的公告逐只写进 qdrant。

    单只失败不拖垮整批——记进 ``failed`` 原样返回，让上层如实说"这几十只没进去"，
    而不是整批报成功、库里却缺一块。

    并发默认 3：embedding 服务是本地进程，并发再高会把它排成长队，
    反而比逐只跑更慢（实测单只 30 条公告切出 ~600 片）。
    """

    from concurrent.futures import ThreadPoolExecutor

    wanted = []
    seen: set[str] = set()
    for item in symbols or []:
        code = str(item or "").strip()
        if code and code not in seen:
            seen.add(code)
            wanted.append(code)

    if not wanted:
        return {"ok": True, "symbols": 0, "chunks": 0, "announcements": 0, "failed": []}

    def one(code: str) -> SyncInfo | Exception:
        try:
            info = sync_symbol(code, limit=limit, purge=purge)
        except Exception as exc:  # noqa: BLE001 - 单只失败要能被记下来
            logger.warning("announcements_sync_failed symbol=%s detail=%s", code, exc)
            return exc
        if progress is not None:
            try:
                progress(info)
            except Exception:  # noqa: BLE001 - 回调出错不影响主链路
                pass
        return info

    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
        results = list(pool.map(one, wanted))

    ok_infos = [r for r in results if isinstance(r, SyncInfo)]
    failed = [
        {"symbol": code, "error": f"{type(r).__name__}: {r}"}
        for code, r in zip(wanted, results, strict=True)
        if isinstance(r, Exception)
    ]
    return {
        "ok": not failed,
        "symbols": len(wanted),
        "synced": len(ok_infos),
        "announcements": sum(i.announcements for i in ok_infos),
        "chunks": sum(i.chunks for i in ok_infos),
        "deleted": sum(i.deleted for i in ok_infos),
        "failed": failed,
    }


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
    ann_type: str = ""
    doc_kind: str = "notice"
    period: str = ""
    provider: str = ""
    chunk_index: int = 0
    chunk_total: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "symbol": self.symbol,
            "ann_date": self.ann_date,
            "title": self.title,
            "section": self.section,
            "event_id": self.event_id,
            "text": self.text,
            "ann_type": self.ann_type,
            "doc_kind": self.doc_kind,
            "period": self.period,
            "provider": self.provider,
            "chunk_index": self.chunk_index,
            "chunk_total": self.chunk_total,
        }


def search(
    query: str,
    *,
    symbol: str | None = None,
    top_k: int = 5,
    min_score: float = DEFAULT_MIN_SCORE,
    doc_kinds: list[str] | None = None,
    periods: list[str] | None = None,
    ann_from: str | None = None,
    ann_to: str | None = None,
) -> list[AnnouncementHit]:
    """语义检索公告切片；向量层不可用抛 AnnouncementsUnavailable。

    过滤条件都在算余弦之前生效，所以"只看 2026Q3 的财报类公告"这类问法不会
    被一堆无关切片抢占 top_k。不传任何条件 = 原有行为不变。
    """

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
    kinds = [str(kind).strip() for kind in (doc_kinds or []) if str(kind).strip()]
    terms = [str(item).strip() for item in (periods or []) if str(item).strip()]
    must: list[dict[str, Any]] = [{"key": "source", "match": {"value": SOURCE}}]
    if symbol:
        must.append({"key": "symbol", "match": {"value": str(symbol).strip()}})
    if kinds:
        must.append({"key": "doc_kind", "match": {"any": kinds}})
    if terms:
        must.append({"key": "period", "match": {"any": terms}})
    date_range: dict[str, Any] = {}
    if ann_from:
        date_range["gte"] = str(ann_from).strip()
    if ann_to:
        date_range["lte"] = str(ann_to).strip()
    if date_range:
        must.append({"key": "ann_date", "range": date_range})

    try:
        vector = embed_one(text)
        named = ensure_collection(len(vector))
        body: dict[str, Any] = {
            "vector": {"name": named, "vector": vector} if named else vector,
            "limit": max(1, int(top_k)),
            "with_payload": True,
            "score_threshold": float(min_score),
            "filter": {"must": must},
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
                ann_type=str(payload.get("ann_type") or ""),
                doc_kind=str(payload.get("doc_kind") or "notice"),
                period=str(payload.get("period") or ""),
                provider=str(payload.get("provider") or ""),
                chunk_index=int(payload.get("chunk_index") or 0),
                chunk_total=int(payload.get("chunk_total") or 0),
            )
        )
    return hits


# qdrant 的 facet / 精确过滤要有 payload index 才快；这几个键就是知识库的分类维度。
INDEXED_FIELDS = ("source", "symbol", "doc_kind", "period")


def _ensure_indexes() -> None:
    """给分类字段建 keyword 索引（幂等，已存在的会被 qdrant 直接认下）。"""

    from ladderstock.knowledge.vectors import _request, collection_name

    for field in INDEXED_FIELDS:
        try:
            _request(
                "PUT",
                f"/collections/{collection_name()}/index?wait=true",
                json_body={"field_name": field, "field_schema": "keyword"},
            )
        except Exception as exc:  # noqa: BLE001 - 索引只是加速，缺了照样能查
            logger.info("announcement_index_skip field=%s detail=%s", field, exc)


def catalog() -> dict[str, Any]:
    """知识库分类目录：有哪些分类维度、每类多少片、一共多少源行。

    探"知识库有没有分类"不能靠猜——这里把 qdrant 里的实际分布读出来，前端拿它
    渲染筛选器，agent 拿它决定该传哪个 ``doc_kinds``。
    """

    from ladderstock.knowledge.vectors import _request, collection_name

    _ensure_indexes()

    info: dict[str, Any] = {
        "ok": False,
        "points": 0,
        "doc_kinds": {},
        "periods": {},
        "kinds_inventory": list(DOC_KINDS) + [DOC_KIND_FALLBACK],
        "fields": [
            {"key": "source", "label": "来源", "type": "keyword", "sample": SOURCE},
            {"key": "symbol", "label": "标的代码", "type": "keyword", "sample": "600519"},
            {
                "key": "doc_kind",
                "label": "公告类型",
                "type": "keyword",
                "sample": " / ".join(list(DOC_KINDS) + [DOC_KIND_FALLBACK]),
            },
            {
                "key": "period",
                "label": "财报期",
                "type": "keyword",
                "sample": "2026Q3 / 2026H1 / 2025A",
            },
            {"key": "ann_date", "label": "公告日期", "type": "date", "sample": "2026-10-31"},
            {"key": "ann_type", "label": "源端类型", "type": "keyword", "sample": "（源库未填）"},
            {"key": "provider", "label": "采集渠道", "type": "keyword", "sample": "tickhub-cn"},
            {"key": "content_chars", "label": "正文长度", "type": "integer", "sample": "11057"},
            {"key": "chunk_index", "label": "切片序号", "type": "integer", "sample": "0"},
            {"key": "chunk_total", "label": "切片总数", "type": "integer", "sample": "23"},
            {"key": "dwd_id", "label": "DWD 主键", "type": "integer", "sample": "12345"},
            {
                "key": "title",
                "label": "公告标题",
                "type": "keyword",
                "sample": "贵州茅台2026年第三季度报告",
            },
            {"key": "text", "label": "切片正文", "type": "text", "sample": "…"},
        ],
        "error": None,
    }
    only_source = {"must": [{"key": "source", "match": {"value": SOURCE}}]}
    try:
        count = _request(
            "POST",
            f"/collections/{collection_name()}/points/count",
            json_body={"filter": only_source, "exact": True},
        )
        info["points"] = int(((count or {}).get("result") or {}).get("count") or 0)
        for key, bucket in (("doc_kind", "doc_kinds"), ("period", "periods")):
            facets = _request(
                "POST",
                f"/collections/{collection_name()}/facet",
                json_body={"key": key, "limit": 200, "filter": only_source, "exact": True},
            )
            hits = ((facets or {}).get("result") or {}).get("hits") or []
            info[bucket] = {str(h.get("value") or ""): int(h.get("count") or 0) for h in hits}
        info["ok"] = True
    except Exception as exc:  # noqa: BLE001 - 知识库不可用时如实报错
        info["error"] = str(exc)
    try:
        info["source_symbols"] = len(symbols())
    except AnnouncementsUnavailable:
        pass
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM dwd.stock_announcement")
            row = cur.fetchone()
            info["dwd_rows"] = int(row[0]) if row else 0
    except Exception as exc:  # noqa: BLE001 - 源库读不到时留空，不掩盖 qdrant 侧的结论
        logger.info("catalog_dwd_rows_skip detail=%s", exc)
    return info


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
    "DOC_KIND_FALLBACK",
    "DOC_KINDS",
    "SyncInfo",
    "available",
    "catalog",
    "chunk_body",
    "classify",
    "fetch_announcements",
    "fingerprint",
    "pg_dsn",
    "search",
    "symbols",
    "sync_many",
    "sync_symbol",
]
