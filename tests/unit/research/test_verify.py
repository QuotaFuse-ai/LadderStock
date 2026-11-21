"""F10 查证（`research/verify.py`）：抽取、结构化、对账三段各自的硬口径。

1. 抽取：PDF 有文本层 → ok；文本层为空（扫描版）→ needs_ocr；图无 vision → ocr_unavailable；
2. 结构化：千分位、万元/亿单位换算、vision label 关键词映射、同字段只取第一处；
3. 对账：本地有值才比，缺域记 `unverifiable`；`accuracy = matched / comparable`。
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from ladderstock.research import verify


def test_capabilities_reports_no_vision_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (verify.VISION_BASE_ENV, verify.VISION_MODEL_ENV, verify.VISION_KEY_ENV):
        monkeypatch.delenv(name, raising=False)
    caps = verify.capabilities()
    assert caps["pdf_text"] is True
    assert caps["image_vision"] is False
    assert caps["local_ocr"] is False
    assert verify.VISION_MODEL_ENV in caps["image_reason"]
    assert caps["supported_image_mime"]


def test_capabilities_detects_vision_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(verify.VISION_BASE_ENV, "http://127.0.0.1:8317/v1")
    monkeypatch.setenv(verify.VISION_MODEL_ENV, "vision-model")
    caps = verify.capabilities()
    assert caps["image_vision"] is True
    assert caps["image_reason"] == ""


def test_parse_figures_handles_thousands_and_units() -> None:
    text = (
        "贵州茅台 600519 收盘价 1,457.70 元 涨跌幅 +0.26% "
        "市盈率 22.1 倍 市净率 7.85 营业收入 1,332.84 亿元 净利润 644.67 亿元 "
        "每股收益 51.53 元 净资产收益率 24.47%"
    )
    figures = {figure.field_name: figure for figure in verify.parse_figures(text)}
    assert figures["code"].value == "600519"
    assert figures["price"].value == 1457.70
    assert figures["pct"].value == 0.26
    assert figures["pe"].value == 22.1
    assert figures["pb"].value == 7.85
    assert figures["revenue"].value == pytest.approx(1332.84)
    assert figures["net_profit"].value == pytest.approx(644.67)
    assert figures["eps"].value == 51.53
    assert figures["roe"].value == 24.47
    # 原文片段必须保留，便于人工复核
    assert figures["price"].raw.startswith("收盘价")


def test_parse_figures_converts_wan_to_yi() -> None:
    figures = verify.parse_figures("营业收入 35000 万元 净利润 1200 万元")
    by_field = {figure.field_name: figure for figure in figures}
    assert by_field["revenue"].value == pytest.approx(3.5)
    assert by_field["net_profit"].value == pytest.approx(0.12)
    assert by_field["revenue"].unit == "亿元"


def test_parse_figures_merges_vision_labels_and_dedupes() -> None:
    text = "收盘价 1457.70 元"
    vision = [
        {"label": "收盘价", "value": 1457.7, "unit": "元"},
        {"label": "市盈率(TTM)", "value": 22.1, "unit": "倍"},
        {"label": "营业收入", "value": 133284000000, "unit": "元"},
        {"label": "换手率", "value": 0.9, "unit": "%"},  # 不在字段清单，忽略
    ]
    figures = verify.parse_figures(text, vision_figures=vision)
    by_field = {figure.field_name: figure for figure in figures}
    # 正则已经抽到收盘价，vision 里的同字段不重复
    assert len([item for item in figures if item.field_name == "price"]) == 1
    assert by_field["pe"].value == 22.1
    assert by_field["revenue"].value == pytest.approx(133284000000.0)
    assert "turnover" not in by_field


def test_extract_blank_pdf_needs_ocr() -> None:
    """空页 PDF 没有文本层 → needs_ocr（不能当成功）。"""
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=595, height=842)
    buffer = io.BytesIO()
    writer.write(buffer)
    result = verify.extract_pdf(buffer.getvalue())
    assert result["status"] == verify.STATUS_NEEDS_OCR
    assert result["pages"] == 1
    assert result["chars"] == 0
    assert "OCR" in result["reason"]


def test_extract_image_without_vision_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(verify.VISION_BASE_ENV, raising=False)
    monkeypatch.delenv(verify.VISION_MODEL_ENV, raising=False)
    result = verify.extract_image(b"fake-png-bytes", "image/png")
    assert result["status"] == verify.STATUS_OCR_UNAVAILABLE
    assert result["figures"] == []
    assert verify.VISION_BASE_ENV in result["reason"]


def test_extract_image_rejects_unknown_mime() -> None:
    result = verify.extract_image(b"x", "image/gif")
    assert result["status"] == verify.STATUS_UNSUPPORTED


def test_reconcile_matches_mismatches_and_unverifiable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = {
        "daily_k": [
            {"code": "600519", "trade_date": "2026-11-16", "close": 1450.0},
            {"code": "600519", "trade_date": "2026-11-17", "close": 1457.7},
        ],
        "valuation_history": [],  # 本地没有该标的估值域 → unverifiable
        "finance_indicators": [
            {
                "code": "600519",
                "report_date": "2026-09-30",
                "revenue": 133283650000.0,
                "net_profit": 64467400000.0,
                "eps": 51.53,
                "roe": 24.47,
            }
        ],
    }
    monkeypatch.setattr(verify, "_local_rows", lambda dataset, code, root=None: rows.get(dataset, []))

    figures = verify.parse_figures(
        "600519 收盘价 1457.70 元 涨跌幅 +0.53% 市盈率 22.1 营业收入 1332.84 亿元 "
        "净利润 600.00 亿元 每股收益 51.53 元 净资产收益率 24.47%"
    )
    result = verify.reconcile(figures)
    by_field = {row["field"]: row for row in result["rows"]}
    assert by_field["code"]["verdict"] == "match"
    assert by_field["price"]["verdict"] == "match"
    assert by_field["revenue"]["verdict"] == "match"
    assert by_field["net_profit"]["verdict"] == "mismatch"  # 600 vs 644.67
    assert by_field["pe"]["verdict"] == "unverifiable"
    assert by_field["pe"]["note"]
    assert result["comparable"] == 6  # price / pct / revenue / net_profit / eps / roe
    assert result["matched"] == 5
    assert result["accuracy"] == pytest.approx(5 / 6, rel=1e-3)


def test_reconcile_without_code_is_unverifiable() -> None:
    figures = verify.parse_figures("收盘价 1457.70 元 市盈率 22.1")
    result = verify.reconcile(figures)
    assert result["code"] is None
    assert result["comparable"] == 0
    assert result["accuracy"] is None
    assert all(row["verdict"] == "unverifiable" for row in result["rows"])


def test_verify_end_to_end_persists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """一步到位：抽取 → 结构 → 对账 → 落盘；本地无数据就照实 unverifiable。"""
    monkeypatch.setattr(
        verify,
        "extract_pdf",
        lambda data, max_pages=20, password=None: {
            "status": verify.STATUS_OK,
            "text": "600519 收盘价 1457.70 元",
            "pages": 1,
            "chars": 24,
            "reason": "",
        },
    )
    monkeypatch.setattr(verify, "_local_rows", lambda dataset, code, root=None: [])
    payload = verify.verify(b"%PDF-1.4 fake", filename="a.pdf", mime="application/pdf", root=tmp_path)
    assert payload["ok"] is True
    assert payload["figures"]
    assert payload["reconciled"]["rows"]
    assert payload["saved"]
    saved = Path(payload["saved"])
    assert saved.is_file()
    # 落盘位置是 <root>/verify/<day>/<hash>.json
    assert (tmp_path / "verify") in saved.parents
    import json

    doc = json.loads(saved.read_text(encoding="utf-8"))
    assert doc["sha1"] == payload["sha1"]


def test_verify_unsupported_type_does_not_persist_ok(tmp_path: Path) -> None:
    payload = verify.verify(b"hello", filename="a.txt", mime="text/plain", root=tmp_path)
    assert payload["ok"] is False
    assert payload["extract"]["status"] == verify.STATUS_UNSUPPORTED
    assert payload["figures"] == []


def test_history_reads_store(tmp_path: Path) -> None:
    assert verify.history(tmp_path) == []
    verify.verify(
        b"%PDF-1.4 fake",
        filename="b.pdf",
        mime="application/pdf",
        root=tmp_path,
    )
    items = verify.history(tmp_path)
    assert len(items) == 1
    assert items[0]["filename"] == "b.pdf"
