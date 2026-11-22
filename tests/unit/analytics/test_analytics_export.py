"""导出单测（C 包 / DEC-090）：CSV 形状 / XLSX 四表 / 缺包降级语义。"""

from __future__ import annotations

from typing import Any

import pytest

from ladderstock.analytics import export
from ladderstock.errors import EXIT_CONFIG, EXIT_USAGE, LadderStockError


def _payload() -> dict[str, Any]:
    return {
        "ok": True,
        "metrics": {
            "twr": 0.12,
            "max_drawdown": 0.05,
            "realized": {"win_rate": 0.5},
        },
        "benchmark": {"code": "000300", "return": 0.03, "annualized": None},
        "excess_return": 0.09,
        "attribution": [
            {
                "symbol": "600000",
                "name": "浦发银行",
                "realized": 780.0,
                "dividends": 0,
                "fees": 15.0,
                "net": 765.0,
                "status": "closed",
            }
        ],
        "behavior": {
            "buckets": [
                {
                    "bucket": "chase",
                    "label": "追高",
                    "count": 2,
                    "avg_forward_return": -0.03,
                    "win_rate": 0.5,
                    "window_days": 5,
                }
            ]
        },
        "mistakes": [
            {
                "symbol": "600519",
                "date": "2026-11-20",
                "shares": 100,
                "proceeds": 96000,
                "cost": 100000,
                "pnl": -4000,
                "pnl_pct": -0.04,
                "hold_days": 15,
            }
        ],
    }


def test_to_csv_has_header_and_rows() -> None:
    text = export.to_csv(_payload(), "attribution")
    lines = text.splitlines()
    assert lines[0] == "代码,名称,已实现,分红,费用,净贡献,状态"
    assert "600000" in lines[1]
    assert "765" in lines[1]


def test_unknown_section_is_usage_error() -> None:
    with pytest.raises(LadderStockError) as err:
        export.to_csv(_payload(), "nonsense")
    assert err.value.code == EXIT_USAGE


def test_workbook_has_four_sheets() -> None:
    pytest.importorskip("openpyxl")
    blob = export.build_workbook(_payload())
    from io import BytesIO

    from openpyxl import load_workbook

    workbook = load_workbook(BytesIO(blob))
    assert workbook.sheetnames == ["指标", "交易归因", "行为统计", "错题本"]
    summary = workbook["指标"]
    values = {str(row[0]): row[1] for row in summary.iter_rows(values_only=True)}
    assert values["twr"] == 0.12
    assert values["超额收益"] == 0.09
    attribution = workbook["交易归因"]
    rows = list(attribution.iter_rows(values_only=True))
    assert rows[0] == ("代码", "名称", "已实现", "分红", "费用", "净贡献", "状态")
    assert rows[1][0] == "600000"


def test_workbook_missing_openpyxl_reports_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def _no_openpyxl(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "openpyxl":
            raise ImportError("No module named 'openpyxl'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_openpyxl)
    with pytest.raises(export.ExportUnavailable) as err:
        export.build_workbook(_payload())
    assert err.value.code == EXIT_CONFIG
    assert "openpyxl" in err.value.message


def test_pdf_without_cjk_font_reports_config_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """字体探测被 LDST_CJK_FONT 钉死到不存在的文件 → ExportUnavailable（不静默换格式）。"""
    monkeypatch.setenv("LDST_CJK_FONT", str(tmp_path / "no-such-font.ttf"))
    with pytest.raises(export.ExportUnavailable) as err:
        export.build_pdf_bytes(_payload())
    assert err.value.code == EXIT_CONFIG
    assert "中文字体" in err.value.message


def test_pdf_bytes_start_with_pdf_magic(monkeypatch: pytest.MonkeyPatch) -> None:
    """reportlab + 真字体可用时产出真 PDF；字体缺席的机器上明确报配置错。"""
    from ladderstock.review.export import register_cjk_font

    monkeypatch.delenv("LDST_CJK_FONT", raising=False)
    font, _reason = register_cjk_font()
    if font is None:
        with pytest.raises(export.ExportUnavailable):
            export.build_pdf_bytes(_payload())
        return
    blob = export.build_pdf_bytes(_payload())
    assert blob[:5] == b"%PDF-"
    assert b"/Font" in blob
