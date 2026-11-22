"""`POST /api/table-export`：把**页面已读到的行集**转成 CSV / XLSX 下载件。

为什么是 POST 而不是 GET：行集在请求体里（可能上百行），GET 查询串放不下也
放不安全；而页面自己已经从各自的只读 API 拿到了数据，这里只是「格式转换器」。

安全口径（沿 DEC-090 家族闸门 + 新增两条本面特有的）：

1. **回环 + Host 白名单**（`privacy_read_guard` 同源判据）：导出件进的是浏览器
   下载目录，内容是页面自己渲染过的行——与页面同级，不升权；
2. **同源来源证据**：写面语义（POST）要求 `X-LDST-Client: 1` 或同源 `Origin`——
   这是「从本机页面发起」的证据，外部脚本无法把任意 JSON 变成下载件；
3. **行数与字节上限**（ROWS_LIMIT / MAX_BODY_BYTES）：防把超大请求当 DoS 面；
4. **不做任何取数**：本面不 import 数据层——数据由各页面自己的 API 提供，
   这里拿到什么导什么（没有第二真相源）。

响应：`text/csv`（BOM）或 XLSX 工作簿，`attachment` 文件名 ASCII 化。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import Response

from ladderstock.export import rows as export_rows
from ladderstock.web.radar import privacy_read_guard, privacy_write_guard

TABLE_EXPORT_PREFIX = "/api/table-export"

DENIED = "表格导出只对本机回环开放（127.0.0.1 / localhost / ::1）"
WRITE_DENIED = "表格导出只接受本机页面发起的请求（Origin 或 X-LDST-Client: 1）"

#: 单次导出的行数上限（页面表格的现实上限远低于此；防滥用）
ROWS_LIMIT = 2000
#: 请求体上限（字节）：行集 + 元数据留 10 倍余量
MAX_BODY_BYTES = 4 * 1024 * 1024

_CSV_MEDIA = "text/csv; charset=utf-8"
_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

_NO_STORE = {"Cache-Control": "no-store"}


async def _export_read_guard(request: Request) -> None:
    await privacy_read_guard(request, denied=DENIED, subject="表格导出")


async def _export_write_guard(request: Request) -> None:
    await privacy_write_guard(request, denied=DENIED, subject="表格导出", write_denied=WRITE_DENIED)


def build_router() -> APIRouter:
    router = APIRouter(prefix=TABLE_EXPORT_PREFIX)

    @router.post("", dependencies=[Depends(_export_read_guard), Depends(_export_write_guard)])
    def export_table(
        body: Annotated[
            dict[str, Any],
            Body(
                examples=[
                    {
                        "name": "持仓流水",
                        "format": "csv",
                        "rows": [{"code": "600000", "shares": 1000, "price": 10.0}],
                    }
                ]
            ),
        ],
    ) -> Response:
        fmt = str(body.get("format") or "csv").strip().lower()
        if fmt not in ("csv", "xlsx"):
            raise HTTPException(status_code=400, detail="format 只支持 csv / xlsx")
        raw_rows = body.get("rows")
        if not isinstance(raw_rows, list):
            raise HTTPException(status_code=400, detail="rows 需要是数组（页面表格的行）")
        if len(raw_rows) > ROWS_LIMIT:
            raise HTTPException(
                status_code=413,
                detail=f"行数超上限（{len(raw_rows)} > {ROWS_LIMIT}）：分批导出",
            )
        if not raw_rows:
            raise HTTPException(status_code=400, detail="rows 为空：没有可导出的行")
        rows = [row for row in raw_rows if isinstance(row, dict)]
        if not rows:
            raise HTTPException(status_code=400, detail="rows 里没有对象行")
        only = body.get("only")
        if only is not None and not (
            isinstance(only, list) and all(isinstance(k, str) for k in only)
        ):
            raise HTTPException(status_code=400, detail="only 需要是字符串数组（列白名单）")
        name = export_rows.sanitize_name(str(body.get("name") or "export"))

        try:
            if fmt == "xlsx":
                content = export_rows.rows_to_xlsx(rows, sheet=name, only=only)
                media = _XLSX_MEDIA
                filename = f"{name}.xlsx"
            else:
                text = export_rows.rows_to_csv(rows, only=only)
                content = ("\ufeff" + text).encode("utf-8")
                media = _CSV_MEDIA
                filename = f"{name}.csv"
        except export_rows.ExportUnavailable as exc:
            raise HTTPException(status_code=503, detail=exc.message) from exc
        return Response(
            content=content,
            media_type=media,
            headers={
                **_NO_STORE,
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )

    return router


__all__ = ["TABLE_EXPORT_PREFIX", "build_router"]
