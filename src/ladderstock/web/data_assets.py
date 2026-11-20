"""`/api/data/*`：数据资产视图、补数任务句柄、权限档读写。

为什么单独一组接口：数据资产是"平台上看得见 140 只票的财报/公告"的唯一入口，
补数任务和权限档又是它的两个动作面（补哪只、要不要先问），三件事共用一套
本地库判定，拆开写会让前端每个页面各查一遍 PG。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/data/assets` | 全部标的的覆盖摘要（票数 / 财报期数 / 公告条数 / 最新报告日） |
| GET | `/api/data/assets/{symbol}` | 单票明细：覆盖判定 + 最近财报 + 最近公告 |
| GET | `/api/data/task/{task_id}` | 补数任务状态 |
| GET | `/api/data/policy` | 权限档与三档说明 |
| POST | `/api/data/policy` | 切换权限档（`always_ask` / `ask_on_risk` / `allow_all`） |

全部走控制面闸门（回环 + Host + 来源证据），因为它们读的是真实库、写的是真实配置。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException

from ladderstock.core import policy
from ladderstock.web.control import _guard

DATA_PREFIX = "/api/data"


def _connect():
    from ladderstock.data import coverage

    return coverage._connect()


def _asset_rows() -> list[dict[str, Any]]:
    """一次查三张表拼出每只票的资产摘要，不做 140 次逐票判定。"""

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT symbol, max(as_of), count(*) FROM dws.stock_agent_analysis_wide"
                " GROUP BY symbol"
            )
            wide = {
                str(r[0]): {"wide_as_of": str(r[1])[:10], "wide_rows": int(r[2])}
                for r in (cur.fetchall() or [])
            }

            cur.execute(
                "SELECT symbol, count(*), max(report_date) FROM dwd.stock_financial_snapshot"
                " GROUP BY symbol"
            )
            fin = {
                str(r[0]): {"financial_rows": int(r[1]), "latest_report": str(r[2])[:10]}
                for r in (cur.fetchall() or [])
            }

            cur.execute(
                "SELECT symbol, count(*), max(ann_date),"
                " count(*) FILTER (WHERE clean_content IS NOT NULL AND length(clean_content) > 0)"
                " FROM dwd.stock_announcement GROUP BY symbol"
            )
            ann = {
                str(r[0]): {
                    "announcement_rows": int(r[1]),
                    "latest_ann_date": str(r[2])[:10] if r[2] else None,
                    "announcement_with_text": int(r[3] or 0),
                }
                for r in (cur.fetchall() or [])
            }

    symbols = sorted(set(wide) | set(fin) | set(ann))
    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        row = {"symbol": symbol}
        row.update(wide.get(symbol, {"wide_as_of": None, "wide_rows": 0}))
        row.update(fin.get(symbol, {"financial_rows": 0, "latest_report": None}))
        row.update(
            ann.get(
                symbol,
                {"announcement_rows": 0, "latest_ann_date": None, "announcement_with_text": 0},
            )
        )
        row["has_financial"] = row["financial_rows"] > 0
        row["has_wide"] = row["wide_rows"] > 0
        row["has_announcement"] = row["announcement_with_text"] > 0
        rows.append(row)
    return rows


def build_data_router(root: Path | None = None) -> APIRouter:
    router = APIRouter(prefix=DATA_PREFIX, dependencies=[Depends(_guard)])

    @router.get("/assets")
    def assets(limit: int = 200) -> dict[str, Any]:
        try:
            rows = _asset_rows()
        except Exception as exc:  # noqa: BLE001 - 库不可用要如实报，不是 500
            raise HTTPException(status_code=503, detail=f"本地库不可用：{exc}") from exc

        rows.sort(key=lambda r: (not r["has_wide"], not r["has_financial"], r["symbol"]))
        total = len(rows)
        return {
            "ok": True,
            "total": total,
            "with_financial": sum(1 for r in rows if r["has_financial"]),
            "with_wide": sum(1 for r in rows if r["has_wide"]),
            "with_announcement": sum(1 for r in rows if r["has_announcement"]),
            "financial_rows": sum(r["financial_rows"] for r in rows),
            "announcement_rows": sum(r["announcement_rows"] for r in rows),
            "items": rows[: max(1, int(limit))],
        }

    @router.get("/assets/{symbol}")
    def asset_detail(symbol: str) -> dict[str, Any]:
        from ladderstock.data import coverage

        code = coverage.normalize_symbol(symbol)
        if not code:
            raise HTTPException(status_code=400, detail="需要 6 位 A 股代码")

        try:
            verdict = coverage.check_coverage(
                code, kinds=["financial", "quote", "announcement", "kline"]
            )
        except coverage.CoverageUnavailable as exc:
            raise HTTPException(status_code=503, detail=f"本地库不可用：{exc}") from exc

        reports: list[dict[str, Any]] = []
        announcements: list[dict[str, Any]] = []
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT report_date, report_type, amount_revenue, amount_net_profit,"
                        " quality_status FROM dwd.stock_financial_snapshot"
                        " WHERE symbol = %s ORDER BY report_date DESC LIMIT 12",
                        (code,),
                    )
                    reports = [
                        {
                            "report_date": str(r[0])[:10],
                            "report_type": r[1],
                            "revenue": float(r[2]) if r[2] is not None else None,
                            "net_profit": float(r[3]) if r[3] is not None else None,
                            "quality_status": r[4],
                        }
                        for r in (cur.fetchall() or [])
                    ]
                    cur.execute(
                        "SELECT ann_date, ann_type, title,"
                        " length(coalesce(clean_content, ''))"
                        " FROM dwd.stock_announcement WHERE symbol = %s"
                        " ORDER BY ann_date DESC LIMIT 20",
                        (code,),
                    )
                    announcements = [
                        {
                            "ann_date": str(r[0])[:10] if r[0] else None,
                            "ann_type": r[1],
                            "title": r[2],
                            "content_chars": int(r[3] or 0),
                        }
                        for r in (cur.fetchall() or [])
                    ]
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail=f"本地库不可用：{exc}") from exc

        return {
            "ok": True,
            "symbol": code,
            "coverage": verdict,
            "reports": reports,
            "announcements": announcements,
        }

    @router.get("/task/{task_id}")
    def task_status(task_id: str) -> dict[str, Any]:
        from ladderstock.data import tasks

        record = tasks.get(task_id)
        if record is None:
            raise HTTPException(status_code=404, detail="任务不存在或已过期（保留 30 分钟）")
        return {"ok": True, "task": record}

    @router.get("/tasks")
    def task_list() -> dict[str, Any]:
        from ladderstock.data import tasks

        return {"ok": True, "items": tasks.get_manager().list_active()}

    @router.get("/policy")
    def get_policy() -> dict[str, Any]:
        current = policy.load_policy(root)
        return {"ok": True, **current, "describe": policy.describe(current["confirm_policy"])}

    @router.post("/policy")
    def set_policy(
        confirm_policy: str = Body(..., embed=True),
        scope: str = Body("session", embed=True),
    ) -> dict[str, Any]:
        try:
            saved = policy.save_policy(confirm_policy=confirm_policy, scope=scope, root=root)
        except policy.PolicyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, **saved, "describe": policy.describe(saved["confirm_policy"])}

    @router.get("/knowledge")
    def knowledge_catalog() -> dict[str, Any]:
        """知识库目录：分类分布 + 字段清单 + 点数。知识库不在线也返回 ok=False 而不是 500。"""

        from ladderstock.knowledge import announcements as kb

        try:
            return kb.catalog()
        except Exception as exc:  # noqa: BLE001 - 缺依赖/网络都按"不可用"报
            return {"ok": False, "error": str(exc), "doc_kinds": {}, "periods": {}, "fields": []}

    @router.get("/knowledge/search")
    def knowledge_search(
        q: str,
        symbol: str | None = None,
        doc_kind: str | None = None,
        period: str | None = None,
        top_k: int = 5,
    ) -> dict[str, Any]:
        from ladderstock.knowledge import announcements as kb

        if not (q or "").strip():
            raise HTTPException(status_code=400, detail="需要检索词 q")
        try:
            hits = kb.search(
                q,
                symbol=symbol,
                top_k=top_k,
                doc_kinds=[doc_kind] if doc_kind else None,
                periods=[period] if period else None,
            )
        except kb.AnnouncementsUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"ok": True, "query": q, "top_k": top_k, "hits": [h.as_dict() for h in hits]}

    @router.get("/knowledge/symbol/{symbol}")
    def knowledge_symbol(symbol: str) -> dict[str, Any]:
        """单票知识库口径：DWD 有几条、几条真有正文、知识库里投影了多少片。"""

        from ladderstock.knowledge import announcements as kb
        from ladderstock.knowledge import fulltext as ft

        code = str(symbol or "").strip()
        if not code:
            raise HTTPException(status_code=400, detail="需要股票代码")
        rows = []
        try:
            rows = kb.fetch_announcements(code, limit=kb.DEFAULT_LIMIT)
        except kb.AnnouncementsUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        kinds: dict[str, int] = {}
        for row in rows:
            kinds[row.doc_kind] = kinds.get(row.doc_kind, 0) + 1
        cached = 0
        try:
            cached = len(ft.load(code))
        except Exception:  # noqa: BLE001
            cached = 0
        return {
            "ok": True,
            "symbol": code,
            "dwd_rows": len(rows),
            "with_body": sum(1 for row in rows if int(row.content_chars or 0) >= ft.MIN_BODY_CHARS),
            "fulltext_cached": cached,
            "doc_kinds": kinds,
            "items": [
                {
                    "dwd_id": row.id,
                    "ann_date": row.ann_date,
                    "title": row.title,
                    "doc_kind": row.doc_kind,
                    "period": row.period,
                    "content_chars": row.content_chars,
                }
                for row in rows[:50]
            ],
        }

    return router
