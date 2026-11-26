"""`/api/analytics`：组合统计（B 包 / DEC-089 授权面）+ 导出（C 包 / DEC-090）。

- `GET /api/analytics/summary?days=365`：指标 / 曲线 / 基准 / 归因 / 行为 / 错题本；
- `GET /api/analytics/export?format=csv|xlsx|pdf&section=`：下载一个导出件。

数据来自本机隐私库（交易流水）与本机日线缓存，因此按隐私面处理：**只接受回环
来源 + Host 白名单**，`no-store`，不进信封 / MCP / AI。

导出的授权与条件见 `docs/decisions/DEC-090-export-surface.md`：
只导 B 包已算出的聚合结果（归因/行为/错题本），CSV 恒可用；XLSX 需要
`ladderstock[export]`（openpyxl），PDF 需要 reportlab + 中文字体（复用
`review/export.py` 的 CJK 探测管线），任一缺席时 503 + 人话，不静默换格式。
Content-Disposition 文件名只用 ASCII（中文在部分浏览器会被转义）。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from ladderstock.analytics import engine
from ladderstock.analytics import export as export_mod
from ladderstock.errors import LadderStockError
from ladderstock.web.radar import privacy_read_guard

ANALYTICS_PREFIX = "/api/analytics"

DENIED = "组合统计只对本机回环开放（127.0.0.1 / localhost / ::1）"

_NO_STORE = {"Cache-Control": "no-store"}

_CSV_MEDIA = "text/csv; charset=utf-8"
_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_PDF_MEDIA = "application/pdf"


async def _analytics_guard(request: Request) -> None:
    await privacy_read_guard(request, denied=DENIED, subject="组合统计")


def build_router() -> APIRouter:
    router = APIRouter(prefix=ANALYTICS_PREFIX)

    @router.get("/summary", dependencies=[Depends(_analytics_guard)])
    def summary(
        days: Annotated[int, Query(ge=30, le=3650)] = 365,
    ) -> JSONResponse:
        payload: dict[str, Any] = engine.build_summary(days=days)
        return JSONResponse(payload, headers=_NO_STORE)

    @router.get("/export", dependencies=[Depends(_analytics_guard)])
    def export_report(
        format: Annotated[str, Query(pattern="^(csv|xlsx|pdf)$")] = "csv",
        section: Annotated[str, Query()] = "attribution",
        days: Annotated[int, Query(ge=30, le=3650)] = 365,
    ) -> Response:
        # 参数校验先于数据计算：未知节是用户错，不该用 409（数据不可算）糊它
        if format != "pdf" and section not in export_mod.SECTION_COLUMNS:
            raise HTTPException(
                status_code=400,
                detail=f"未知导出节 {section!r}；可用：{' / '.join(export_mod.SECTION_COLUMNS)}",
            )
        payload = engine.build_summary(days=days)
        if not payload.get("ok"):
            raise HTTPException(
                status_code=409, detail=f"没有可导出的统计：{payload.get('reason')}"
            )
        try:
            if format == "xlsx":
                content = export_mod.build_workbook(payload)
                media = _XLSX_MEDIA
                filename = "analytics.xlsx"
            elif format == "pdf":
                content = export_mod.build_pdf_bytes(payload)
                media = _PDF_MEDIA
                filename = "analytics.pdf"
            else:
                # BOM 让 Excel 双击打开不乱码；CSV 是聚合数据，不是凭据
                content = ("\ufeff" + export_mod.to_csv(payload, section)).encode("utf-8")
                media = _CSV_MEDIA
                filename = f"analytics-{section}.csv"
        except LadderStockError as exc:
            status = 503 if exc.code == 3 else 400
            raise HTTPException(status_code=status, detail=exc.message) from exc
        return Response(
            content=content,
            media_type=media,
            headers={
                **_NO_STORE,
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )

    return router


__all__ = ["ANALYTICS_PREFIX", "build_router"]
