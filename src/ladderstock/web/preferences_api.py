"""`/api/preferences`：把「我记得你的偏好」变成用户能改的东西。

偏好原本只活在 `data/preferences.json` 里——助手会按它回答，但用户只能去翻文件，
想删一句「别给我推ST股」都得手改 JSON。四条接口让它进页面：

- `GET  /api/preferences`        我记住的全部（含类别中文名、被提到几次）；
- `POST /api/preferences/add`    自己写一条（不经过抽取规则，写什么就记什么）；
- `POST /api/preferences/delete` 删一条（按文本匹配，文本就是用户的主键）；
- `POST /api/preferences/clear`  一次清空（换人用、或想重来）。

写面纪律同 `/api/schedule`：只认本机回环 + 同源来源，不做第二种鉴权；
删空之后不写空文件（`clear` 直接删文件，与 `preferences.clear` 一致）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ladderstock.ai import preferences
from ladderstock.web.radar import privacy_read_guard, privacy_write_guard

PREFIX = "/api/preferences"

DENIED = "偏好面只对本机回环开放（127.0.0.1 / localhost / ::1）"
WRITE_DENIED = "偏好写面只接受同源来源（Origin 或 X-LDST-Client: 1）"

_NO_STORE = {"Cache-Control": "no-store"}


async def _read_guard(request: Request) -> None:
    await privacy_read_guard(request, denied=DENIED, subject="偏好")


async def _write_guard(request: Request) -> None:
    await privacy_write_guard(request, denied=DENIED, subject="偏好", write_denied=WRITE_DENIED)


def payload(*, data_root: Path | None = None) -> dict[str, Any]:
    """当前偏好 + 各类别的中文名（前端直接渲染，不自己维护一份表）。"""
    body = preferences.as_dict(preferences.load(data_root))
    body["ok"] = True
    body["labels"] = dict(preferences.KIND_LABELS)
    body["path"] = str(preferences.path_for(data_root))
    body["max_items"] = preferences.MAX_ITEMS
    return body


def build_router(*, data_root: Path | None = None) -> APIRouter:
    """四条路由；数据根可注入，单测写临时目录即可跑。"""
    router = APIRouter(prefix=PREFIX)

    @router.get("", dependencies=[Depends(_read_guard)])
    def list_preferences() -> JSONResponse:
        return JSONResponse(payload(data_root=data_root), headers=_NO_STORE)

    @router.post("/add", dependencies=[Depends(_write_guard)])
    def add_preference(body: Annotated[dict[str, Any], Body(...)]) -> JSONResponse:
        text = str(body.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="缺少 text")
        kind = str(body.get("kind") or "").strip() or None
        item = preferences.add_item(text, kind=kind, data_root=data_root)
        if item is None:
            raise HTTPException(status_code=400, detail="这一条没写进偏好（文本为空或过长）")
        return JSONResponse(
            {"ok": True, "added": item.to_dict(), **payload(data_root=data_root)},
            headers=_NO_STORE,
        )

    @router.post("/delete", dependencies=[Depends(_write_guard)])
    def delete_preference(body: Annotated[dict[str, Any], Body(...)]) -> JSONResponse:
        text = str(body.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="缺少 text")
        removed = preferences.remove(text, data_root=data_root)
        if not removed:
            raise HTTPException(status_code=404, detail=f"没有这条偏好：{text}")
        return JSONResponse({"ok": True, "removed": text, **payload(data_root=data_root)}, headers=_NO_STORE)

    @router.post("/clear", dependencies=[Depends(_write_guard)])
    def clear_preferences() -> JSONResponse:
        removed = preferences.clear(data_root)
        return JSONResponse(
            {"ok": True, "cleared": bool(removed), **payload(data_root=data_root)},
            headers=_NO_STORE,
        )

    return router


__all__ = ["DENIED", "PREFIX", "WRITE_DENIED", "build_router", "payload"]
