"""`/api/verify`：F10 截图 / PDF 查证面。

三个接口：

- `GET  /api/verify/capabilities`：本机能做什么（PDF 文本层 / vision 后端 / 本地 OCR）；
- `POST /api/verify/file`：上传一份 PDF 或截图（multipart `file`，可选 `code`），
  抽取 → 关键数字 → 与本地数据对账，结论落 `data/verify/<day>/`；
- `GET  /api/verify/history?limit=`：最近查过什么。

口径：

- 读面（capabilities / history）= 回环 + `Host` 白名单；
- 写面（上传）= 同源 `Origin` 或 `X-LDST-Client: 1`，闸门复用 `web/radar.py`；
- **不伪造识别**：OCR 不可用时返回 `200` 但 `ok=false` + `extract.status=ocr_unavailable`，
  让前端把原因显示出来（而不是 500 或者一份空结果冒充成功）；
- 上传大小有上限（默认 8MB，可用 `LDST_VERIFY_MAX_BYTES` 调），超了 413。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse

from ladderstock.research import verify as verify_module
from ladderstock.web.radar import privacy_read_guard, privacy_write_guard

VERIFY_PREFIX = "/api/verify"

VERIFY_DENIED = "查证面只对本机回环开放（127.0.0.1 / localhost / ::1）"
VERIFY_WRITE_DENIED = "查证上传只接受同源来源（Origin 或 X-LDST-Client: 1）"

MAX_BYTES_ENV = "LDST_VERIFY_MAX_BYTES"
DEFAULT_MAX_BYTES = 8 * 1024 * 1024

_MIME_BY_SUFFIX = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}

_NO_STORE = {"Cache-Control": "no-store"}


def _max_bytes() -> int:
    try:
        return int(os.environ.get(MAX_BYTES_ENV) or DEFAULT_MAX_BYTES)
    except ValueError:
        return DEFAULT_MAX_BYTES


async def _verify_guard(request: Request) -> None:
    await privacy_read_guard(request, denied=VERIFY_DENIED, subject="查证")


async def _verify_write_guard(request: Request) -> None:
    await privacy_write_guard(
        request,
        denied=VERIFY_DENIED,
        subject="查证",
        write_denied=VERIFY_WRITE_DENIED,
    )


def build_router(*, data_root: Path | None = None) -> APIRouter:
    router = APIRouter(prefix=VERIFY_PREFIX)

    @router.get("/capabilities", dependencies=[Depends(_verify_guard)])
    def verify_capabilities() -> JSONResponse:
        return JSONResponse(
            {"local_only": True, **verify_module.capabilities(), "max_bytes": _max_bytes()},
            headers=_NO_STORE,
        )

    @router.get("/history", dependencies=[Depends(_verify_guard)])
    def verify_history(
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> JSONResponse:
        return JSONResponse(
            {"local_only": True, "items": verify_module.history(data_root, limit=limit)},
            headers=_NO_STORE,
        )

    @router.post("/file", dependencies=[Depends(_verify_write_guard)])
    async def verify_file(
        file: Annotated[UploadFile, File(...)],
        code: Annotated[str | None, Form()] = None,
    ) -> JSONResponse:
        """上传一份 PDF / 截图并立即对账。识别不了就如实说，不编数字。"""
        filename = str(file.filename or "").strip() or "upload"
        suffix = Path(filename).suffix.lower()
        mime = _MIME_BY_SUFFIX.get(suffix) or str(file.content_type or "").strip()
        if suffix and not _MIME_BY_SUFFIX.get(suffix):
            raise HTTPException(
                status_code=415,
                detail=f"不支持的文件类型 {suffix}；只收 pdf / png / jpg / jpeg / webp / bmp",
            )
        data = await file.read()
        limit = _max_bytes()
        if len(data) > limit:
            raise HTTPException(status_code=413, detail=f"文件 {len(data)} 字节超过上限 {limit}")
        if not data:
            raise HTTPException(status_code=400, detail="上传内容为空")
        payload: dict[str, Any] = verify_module.verify(
            data,
            filename=filename,
            mime=mime or "application/octet-stream",
            root=data_root,
            code=(code or "").strip() or None,
        )
        return JSONResponse(payload, headers=_NO_STORE)

    return router


__all__ = [
    "DEFAULT_MAX_BYTES",
    "MAX_BYTES_ENV",
    "VERIFY_DENIED",
    "VERIFY_PREFIX",
    "VERIFY_WRITE_DENIED",
    "build_router",
]
