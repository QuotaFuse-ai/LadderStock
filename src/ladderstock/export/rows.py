"""通用记录导出器（C 包第二半）：把「平面 JSON 行集」变成 CSV / XLSX。

与 `analytics/export.py` 的分工：
- 那边导**B 包算出的聚合**（列结构写死，随统计口径演进）；
- 这里导**任何页面已经读到的行集**（列结构由数据自己决定：首行键序 = 表头，
  嵌套结构扁平化为 `a.b` 键），供 Web `/api/table-export` 与前端共用。

设计约束（沿 DEC-090）：
- **只导调用方传来的行**，不新开取数链路——数据面仍由各页面自己的 API 出；
- 键序取**首行出现序 + 后续行新键追加**（dict 无序会跳列，这里保稳定）；
- 嵌套 dict 扁平化（`{"a": {"b": 1}}` → `a.b` 列）；list 序列化成 `|` 连接
  的紧凑文本（保留原始形状交给 JSON，CSV 保持一格一值）；
- **None → 空串**（CSV 里空列），不写 "None" 污染表格；
- bool → true/false（与 CLI `_text` 同口径）；
- 键白名单：调用方可传 `only`（页面声明允许导出的列，防把内部字段一起带走）。
"""

from __future__ import annotations

import csv
import io
from typing import Any

from ladderstock.errors import LadderStockError


class ExportUnavailable(LadderStockError):
    """XLSX 依赖（openpyxl）缺席；调用方降级 CSV。"""


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _flatten(row: dict[str, Any], prefix: str = "") -> dict[str, str]:
    """一行 → 扁平键值；嵌套 dict 递归、list 用 `|` 连接。"""
    out: dict[str, str] = {}
    for key, value in row.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            for sub_key, sub_value in _flatten(value, name).items():
                out[sub_key] = sub_value
        elif isinstance(value, (list, tuple)):
            from json import dumps

            parts = [
                dumps(item, ensure_ascii=False, default=str)
                if isinstance(item, dict)
                else _cell(item)
                for item in value
            ]
            out[name] = "|".join(parts)
        else:
            out[name] = _cell(value)
    return out


def flatten_rows(
    rows: list[dict[str, Any]], *, only: list[str] | None = None
) -> list[dict[str, str]]:
    """行集 → 扁平行集（键稳定、值全是字符串）。"""
    cleaned = [row for row in rows if isinstance(row, dict)]
    if only:
        wanted = [str(key) for key in only]
        cleaned = [{key: row.get(key) for key in wanted if key in row} for row in cleaned]
    return [_flatten(row) for row in cleaned]


def headers_of(flat_rows: list[dict[str, str]]) -> list[str]:
    """首行键序 + 后续新键追加（保证同批行表头稳定）。"""
    seen: dict[str, None] = {}
    for row in flat_rows:
        for key in row:
            seen.setdefault(key, None)
    return list(seen)


def rows_to_csv(rows: list[dict[str, Any]], *, only: list[str] | None = None) -> str:
    """行集 → CSV 文本（调用方负责 BOM / 响应头）。"""
    flat = flatten_rows(rows, only=only)
    headers = headers_of(flat)
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(headers)
    for row in flat:
        writer.writerow([row.get(header, "") for header in headers])
    return buf.getvalue()


def rows_to_xlsx(
    rows: list[dict[str, Any]], *, sheet: str = "数据", only: list[str] | None = None
) -> bytes:
    """行集 → 单表 XLSX；openpyxl 缺席抛 `ExportUnavailable`。"""
    try:
        from openpyxl import Workbook
    except ImportError as exc:  # pragma: no cover - 环境缺包
        raise ExportUnavailable(
            "XLSX 导出需要 openpyxl：pip install 'ladderstock[export]'", 3
        ) from exc
    flat = flatten_rows(rows, only=only)
    headers = headers_of(flat)
    workbook = Workbook()
    worksheet = workbook.active
    assert worksheet is not None
    worksheet.title = str(sheet)[:31] or "数据"
    worksheet.append(headers)
    for row in flat:
        worksheet.append([row.get(header, "") for header in headers])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def sanitize_name(raw: str) -> str:
    """Content-Disposition 文件名：ASCII 白名单 + 防注入。

    HTTP 头是 latin-1，非 ASCII 名会在响应层炸 UnicodeEncodeError——所以这里
    **非 ASCII 字符直接丢弃**（而不是替换为 `-`，避免中文文件名生成一堆破折号），
    全非 ASCII 时回退 `export`。调用方想要中文文件名时自己改 `rows_to_*` 的
    独立参数；本面文件名统一 ASCII。
    """
    text = str(raw or "").strip()
    cleaned = "".join(ch for ch in text if ch.isascii() and (ch.isalnum() or ch in "-_"))
    cleaned = cleaned.strip("-")
    return cleaned[:48] or "export"


__all__ = [
    "ExportUnavailable",
    "flatten_rows",
    "headers_of",
    "rows_to_csv",
    "rows_to_xlsx",
    "sanitize_name",
]
