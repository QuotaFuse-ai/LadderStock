"""组合统计的导出（C 包）：把 B 包结果变成可交付的 CSV / XLSX。

两个消费方共用这里：
- Web `GET /api/analytics/export`（浏览器下载）；
- CLI `ldst analytics --xlsx PATH`（落文件）。

口径：**只导 B 包已经算出的聚合结果**（归因 / 行为 / 错题本 / 指标 + 净值），
不在这里重算、不引用隐私库——数据来自 `engine.build_summary()` 的返回值。

- CSV：每节一个文件（表头 + 数据行），UTF-8 with BOM（Excel 直接打开不乱码）；
- XLSX：一个工作簿四张表（指标 / 归因 / 行为 / 错题本）；openpyxl 缺席时报
  `ExportUnavailable`，调用方降级 CSV 并提示装 `ladderstock[export]`。
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


__all__ = [
    "SECTION_COLUMNS",
    "ExportUnavailable",
    "build_workbook",
    "section_rows",
    "to_csv",
]
