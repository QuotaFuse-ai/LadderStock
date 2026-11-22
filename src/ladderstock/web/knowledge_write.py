"""`/api/knowledge` 的写入与检索面（E 包 / DEC-091）。

既有两条只读路由（`GET /api/knowledge`、`GET /api/knowledge/{market}/{symbol}`）
不动；本模块新增三个面：

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/knowledge/notes?target=&tag=&limit=` | 笔记列表（按标的 / 标签过滤） |
| POST | `/api/knowledge/notes` | 新增笔记 |
| PUT | `/api/knowledge/notes/{id}` | 改笔记（只动传入字段） |
| DELETE | `/api/knowledge/notes/{id}` | 删除笔记 |
| GET | `/api/knowledge/notes/tags` | 标签清单 + 计数 |
| GET | `/api/knowledge/search?q=&limit=` | 跨票检索（向量优先 → 本地 LIKE 降级） |

闸门：读 = 回环 + Host；写 = 再加同源来源证据（`X-LDST-Client: 1` 或同源
Origin）——与 `/api/alert` 同一套，不新增鉴权体系。`no-store`。
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from ladderstock.errors import EXIT_NOT_FOUND, LadderStockError
from ladderstock.knowledge import notes as notes_store
from ladderstock.knowledge import vectors
from ladderstock.web.radar import privacy_read_guard, privacy_write_guard

NOTES_PREFIX = "/api/knowledge"

DENIED = "知识库只对本机回环开放（127.0.0.1 / localhost / ::1）"
WRITE_DENIED = "知识库写面只接受本机页面发起的请求（Origin 或 X-LDST-Client: 1）"

_NO_STORE = {"Cache-Control": "no-store"}

#: 检索词长度上限（防把检索面当文件读取器）
MAX_QUERY_CHARS = 200
#: 检索命中上限
MAX_HITS = 30


async def _read_guard(request: Request) -> None:
    await privacy_read_guard(request, denied=DENIED, subject="知识库")


async def _write_guard(request: Request) -> None:
    await privacy_write_guard(request, denied=DENIED, subject="知识库", write_denied=WRITE_DENIED)


def _error(exc: LadderStockError) -> HTTPException:
    status = 404 if exc.code == EXIT_NOT_FOUND else 400
    return HTTPException(status_code=status, detail=exc.message)


def _search_local(query: str, limit: int, data_root: Path | None) -> list[dict[str, Any]]:
    """降级层：本地 LIKE 扫档案正文（qdrant 不可用时的字面命中）。

    目录枚举走 `knowledge/store.knowledge_root`（数据根之下的既定布局），
    每个目录读 profile.md 的正文（标题行 + 命中行上下文各 60 字），
    不解 frontmatter——这是检索面不是档案读取面。
    """
    root = notes_store.db_path(data_root).parent / "knowledge"
    if not root.is_dir():
        return []
    needle = query.lower()
    hits: list[dict[str, Any]] = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        profile = entry / "profile.md"
        if not profile.is_file():
            continue
        try:
            text = profile.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lowered = text.lower()
        position = lowered.find(needle)
        if position < 0:
            continue
        start = max(0, position - 60)
        snippet = text[start : position + len(query) + 60].replace("\n", " ")
        market, _, symbol = entry.name.partition("_")
        hits.append(
            {
                "market": market,
                "symbol": symbol,
                "as_of": None,
                "score": None,
                "snippet": snippet,
                "source": "local",
            }
        )
        if len(hits) >= limit:
            break
    return hits


def build_router(plane: Any) -> APIRouter:
    """笔记 + 检索面；`plane` 是既有 KnowledgePlane（只为复用其 data_root）。"""
    router = APIRouter(prefix=NOTES_PREFIX)
    data_root = getattr(plane, "data_root", None)

    @router.get("/notes", dependencies=[Depends(_read_guard)])
    def notes_list(
        target: Annotated[str | None, Query()] = None,
        tag: Annotated[str | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
    ) -> JSONResponse:
        items = notes_store.list_notes(root=data_root, target=target, tag=tag, limit=limit)
        return JSONResponse({"ok": True, "total": len(items), "items": items}, headers=_NO_STORE)

    @router.get("/notes/tags", dependencies=[Depends(_read_guard)])
    def tags_list() -> JSONResponse:
        return JSONResponse(
            {"ok": True, "tags": notes_store.list_tags(root=data_root)},
            headers=_NO_STORE,
        )

    @router.post("/notes", dependencies=[Depends(_read_guard), Depends(_write_guard)])
    def notes_add(
        body: Annotated[
            dict[str, Any],
            Body(
                examples=[
                    {"body": "等三季报再决定加仓", "target": "SH:600000", "tags": ["银行", "观察"]}
                ]
            ),
        ],
    ) -> JSONResponse:
        try:
            note = notes_store.add_note(
                str(body.get("body") or ""),
                target=str(body.get("target") or "") or None,
                title=str(body.get("title") or ""),
                tags=[str(item) for item in body.get("tags") or []],
                root=data_root,
            )
        except LadderStockError as exc:
            raise _error(exc) from exc
        return JSONResponse({"ok": True, "note": note})

    @router.put("/notes/{note_id}", dependencies=[Depends(_read_guard), Depends(_write_guard)])
    def notes_update(
        note_id: int,
        body: Annotated[dict[str, Any], Body()],
    ) -> JSONResponse:
        raw_body = body.get("body")
        raw_title = body.get("title")
        raw_target = body.get("target")
        raw_tags = body.get("tags")
        try:
            note = notes_store.update_note(
                note_id,
                root=data_root,
                body=raw_body if isinstance(raw_body, str) else None,
                title=raw_title if isinstance(raw_title, str) else None,
                target=raw_target if isinstance(raw_target, str) else None,
                tags=(
                    [str(item) for item in raw_tags]
                    if isinstance(raw_tags, list)
                    else None
                ),
            )
        except LadderStockError as exc:
            raise _error(exc) from exc
        return JSONResponse({"ok": True, "note": note})

    @router.delete("/notes/{note_id}", dependencies=[Depends(_read_guard), Depends(_write_guard)])
    def notes_remove(note_id: int) -> JSONResponse:
        try:
            payload = notes_store.remove_note(note_id, root=data_root)
        except LadderStockError as exc:
            raise _error(exc) from exc
        return JSONResponse({"ok": True, **payload})

    @router.get("/search", dependencies=[Depends(_read_guard)])
    def search(
        q: Annotated[str, Query(min_length=1, max_length=MAX_QUERY_CHARS)],
        limit: Annotated[int, Query(ge=1, le=MAX_HITS)] = 8,
    ) -> JSONResponse:
        """跨票检索：向量层优先；不可用降级本地 LIKE——来源分开报。"""
        query = q.strip()
        if not query:
            raise HTTPException(status_code=400, detail="检索词不能为空")
        hits: list[dict[str, Any]] = []
        engine = "vector"
        vector_error: str | None = None
        try:
            for hit in vectors.search(query, top_k=limit):
                hits.append(
                    {
                        "market": hit.market,
                        "symbol": hit.symbol,
                        "as_of": hit.as_of,
                        "status": hit.status,
                        "section": hit.section,
                        "score": round(hit.score, 4),
                        "snippet": hit.text[:240],
                        "source": "vector",
                    }
                )
        except Exception as exc:  # noqa: BLE001 - 向量层挂了走降级，检索不因此不可用
            vector_error = f"{type(exc).__name__}: {exc}"
        if not hits:
            engine = "local"
            hits = _search_local(query, limit, data_root)
        return JSONResponse(
            {
                "ok": True,
                "q": query,
                "engine": engine,
                "vector_error": vector_error,
                "total": len(hits),
                "hits": hits,
            },
            headers=_NO_STORE,
        )

    return router


__all__ = ["NOTES_PREFIX", "build_router"]
