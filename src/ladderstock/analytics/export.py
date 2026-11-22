"""组合统计的导出（C 包）：把 B 包结果变成可交付的 CSV / XLSX / PDF。

三个消费方共用这里：
- Web `GET /api/analytics/export`（浏览器下载，format=csv|xlsx|pdf）；
- CLI `ldst analytics --xlsx PATH / --pdf PATH`（落文件）。

口径：**只导 B 包已经算出的聚合结果**（归因 / 行为 / 错题本 / 指标 + 净值），
不在这里重算、不引用隐私库——数据来自 `engine.build_summary()` 的返回值。

- CSV：每节一个文件（表头 + 数据行），UTF-8 with BOM（Excel 直接打开不乱码）；
- XLSX：一个工作簿四张表（指标 / 归因 / 行为 / 错题本）；openpyxl 缺席时报
  `ExportUnavailable`，调用方降级 CSV 并提示装 `ladderstock[export]`；
- PDF：A4 竖版文档（指标表 + 三张明细表），复用 `review/export.py` 的 CJK
  字体探测管线（DEC-090 §4「复用 ldst export 的 reportlab 管线」）；reportlab
  或中文字体缺席时报 `ExportUnavailable`，**不静默换格式**（用户点了 PDF 就要 PDF）。
"""

from __future__ import annotations

import csv
import io
from typing import Any

from ladderstock.errors import LadderStockError

CSV_SEPARATOR = ","

#: 每节的列定义（列名, 字段键）；数字直接给原值，展示层自己格式化
SECTION_COLUMNS: dict[str, tuple[tuple[str, str], ...]] = {
    "attribution": (
        ("代码", "symbol"),
        ("名称", "name"),
        ("已实现", "realized"),
        ("分红", "dividends"),
        ("费用", "fees"),
        ("净贡献", "net"),
        ("状态", "status"),
    ),
    "behavior": (
        ("分档", "bucket"),
        ("说明", "label"),
        ("笔数", "count"),
        ("前向均值", "avg_forward_return"),
        ("胜率", "win_rate"),
        ("窗口(日)", "window_days"),
    ),
    "mistakes": (
        ("日期", "date"),
        ("代码", "symbol"),
        ("股数", "shares"),
        ("卖出额", "proceeds"),
        ("成本", "cost"),
        ("盈亏", "pnl"),
        ("盈亏%", "pnl_pct"),
        ("持有天数", "hold_days"),
    ),
}

SECTION_LABELS: dict[str, str] = {
    "attribution": "交易归因",
    "behavior": "行为统计",
    "mistakes": "错题本",
}


class ExportUnavailable(LadderStockError):
    """XLSX 依赖（openpyxl）缺席；调用方降级 CSV。"""


def section_rows(payload: dict[str, Any], section: str) -> list[dict[str, Any]]:
    """从 build_summary 的返回里取一节的行；未知节抛用法错误。"""
    if section not in SECTION_COLUMNS:
        raise LadderStockError(f"未知导出节 {section!r}；可用：{' / '.join(SECTION_COLUMNS)}", 2)
    source = payload.get(section)
    if not isinstance(source, list):
        return []
    rows = [row for row in source if isinstance(row, dict)]
    return rows


def to_csv(payload: dict[str, Any], section: str) -> str:
    """一节 → CSV 文本（UTF-8 BOM 由调用方写入响应/文件）。"""
    rows = section_rows(payload, section)  # 先校验节名（未知 → exit 2）
    columns = SECTION_COLUMNS[section]
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow([name for name, _key in columns])
    for row in rows:
        writer.writerow([row.get(key, "") for _name, key in columns])
    return buf.getvalue()


def build_workbook(payload: dict[str, Any]) -> bytes:
    """四张表的 XLSX 字节流；openpyxl 缺席抛 `ExportUnavailable`。"""
    try:
        from openpyxl import Workbook
    except ImportError as exc:  # pragma: no cover - 环境缺包
        raise ExportUnavailable(
            "XLSX 导出需要 openpyxl：pip install 'ladderstock[export]'（或单独安装 openpyxl）",
            3,
        ) from exc

    workbook = Workbook()
    # 第一张：指标 + 基准 + 超额（key/value）
    summary = workbook.active
    assert summary is not None
    summary.title = "指标"
    metrics = payload.get("metrics") or {}
    if isinstance(metrics, dict):
        for key, value in metrics.items():
            if isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    summary.append([f"{key}.{sub_key}", sub_value])
            else:
                summary.append([key, value])
    benchmark = payload.get("benchmark") or {}
    if isinstance(benchmark, dict):
        summary.append(["基准代码", benchmark.get("code")])
        summary.append(["基准区间收益", benchmark.get("return")])
        summary.append(["基准年化", benchmark.get("annualized")])
        summary.append(["超额收益", payload.get("excess_return")])
        if benchmark.get("reason"):
            summary.append(["基准原因", benchmark.get("reason")])

    for section in ("attribution", "behavior", "mistakes"):
        columns = SECTION_COLUMNS[section]
        sheet = workbook.create_sheet(SECTION_LABELS.get(section, section))
        sheet.append([name for name, _key in columns])
        for row in section_rows(payload, section):
            sheet.append([row.get(key, "") for _name, key in columns])

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def build_pdf_bytes(payload: dict[str, Any]) -> bytes:
    """组合统计 PDF（A4 竖版）：指标表 + 归因 / 行为 / 错题本三张明细表。

    复用 `review/export.py` 的 CJK 字体探测（同一套候选与降级原因）。reportlab
    或中文字体缺席时抛 `ExportUnavailable`（exit 3），由调用方给 503 / 人话，
    绝不静默换成 CSV——「下载到的东西和按钮写的不一样」是 DEC-090 明令禁止的。
    """
    try:
        from reportlab.lib import colors  # type: ignore[import-untyped]
        from reportlab.lib.pagesizes import A4  # type: ignore[import-untyped]
        from reportlab.lib.styles import ParagraphStyle  # type: ignore[import-untyped]
        from reportlab.lib.units import mm  # type: ignore[import-untyped]
        from reportlab.platypus import (  # type: ignore[import-untyped]
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )
    except ImportError as exc:  # pragma: no cover - 环境缺包
        raise ExportUnavailable(
            f"PDF 导出需要 reportlab：pip install reportlab（{exc}）", 3
        ) from exc

    from ladderstock.review.export import register_cjk_font

    font, reason = register_cjk_font()
    if font is None:
        raise ExportUnavailable(f"PDF 导出需要可用的中文字体：{reason}", 3)

    bold_name = "LadderCJK-Bold" if font.bold_path else "LadderCJK"

    def _escape(text: str) -> str:
        return (
            text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        )

    def _scalar(value: Any) -> str:
        if value is None:
            return "-"
        if isinstance(value, bool):
            return "是" if value else "否"
        return str(value)

    h1 = ParagraphStyle("AnaH1", fontName=bold_name, fontSize=18, leading=26, spaceAfter=6)
    meta = ParagraphStyle(
        "AnaMeta", fontName="LadderCJK", fontSize=9, leading=14, spaceAfter=8
    )
    h2 = ParagraphStyle(
        "AnaH2", fontName=bold_name, fontSize=13, leading=18, spaceBefore=10, spaceAfter=4
    )

    def _table(headers: tuple[str, ...], rows: list[list[str]]) -> Table:
        data = [list(headers)] + rows
        table = Table(data, hAlign="LEFT", repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), "LadderCJK"),
                    ("FONTNAME", (0, 0), (-1, 0), bold_name),
                    ("FONTSIZE", (0, 0), (-1, -1), 9),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#999999")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ]
            )
        )
        return table

    story: list[Any] = []
    start = payload.get("start")
    as_of = payload.get("as_of")
    span = payload.get("span_days")
    title = "组合统计"
    if start and as_of:
        title = f"组合统计（{start} → {as_of}，{_scalar(span)} 天）"
    story.append(Paragraph(_escape(title), h1))
    story.append(
        Paragraph(_escape(f"导出格式：PDF（中文字体：{font.path.name}）"), meta)
    )
    story.append(
        Paragraph(
            _escape("只含本机已算出的聚合结果，不含原始交易流水与持仓明细。"),
            meta,
        )
    )
    story.append(Spacer(1, 3 * mm))

    # 指标 + 基准 + 超额（与 XLSX 第一张表同序）
    metric_rows: list[list[str]] = []
    metrics = payload.get("metrics") or {}
    if isinstance(metrics, dict):
        for key, value in metrics.items():
            if isinstance(value, dict):
                for sub_key, sub_value in value.items():
                    metric_rows.append([_escape(f"{key}.{sub_key}"), _escape(_scalar(sub_value))])
            else:
                metric_rows.append([_escape(str(key)), _escape(_scalar(value))])
    benchmark = payload.get("benchmark") or {}
    if isinstance(benchmark, dict):
        metric_rows.append([_escape("基准代码"), _escape(_scalar(benchmark.get("code")))])
        metric_rows.append([_escape("基准区间收益"), _escape(_scalar(benchmark.get("return")))])
        metric_rows.append([_escape("基准年化"), _escape(_scalar(benchmark.get("annualized")))])
        metric_rows.append([_escape("超额收益"), _escape(_scalar(payload.get("excess_return")))])
        if benchmark.get("reason"):
            metric_rows.append([_escape("基准原因"), _escape(str(benchmark.get("reason")))])
    story.append(Paragraph("指标", h2))
    story.append(_table(("指标", "数值"), metric_rows))
    story.append(Spacer(1, 4 * mm))

    for section in ("attribution", "behavior", "mistakes"):
        columns = SECTION_COLUMNS[section]
        rows = [
            [_escape(_scalar(row.get(key, ""))) for _name, key in columns]
            for row in section_rows(payload, section)
        ]
        story.append(Paragraph(SECTION_LABELS.get(section, section), h2))
        story.append(_table(tuple(name for name, _key in columns), rows))
        story.append(Spacer(1, 4 * mm))

    for warning in payload.get("warnings") or []:
        story.append(Paragraph(_escape(f"注意：{warning}"), meta))

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title="LadderStock 组合统计",
        author="LadderStock",
    )
    doc.build(story)
    return buffer.getvalue()


__all__ = [
    "SECTION_COLUMNS",
    "ExportUnavailable",
    "build_pdf_bytes",
    "build_workbook",
    "section_rows",
    "to_csv",
]
