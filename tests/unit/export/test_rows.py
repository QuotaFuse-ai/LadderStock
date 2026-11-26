"""行集导出器单测（C 包第二半）：扁平化 / 表头稳定 / 白名单 / 文件名消毒。"""

from __future__ import annotations

import pytest

from ladderstock.errors import EXIT_CONFIG
from ladderstock.export import rows


def test_flatten_nested_and_lists() -> None:
    flat = rows.flatten_rows(
        [
            {
                "code": "600000",
                "quote": {"price": 10.5, "change": None},
                "tags": ["a", "b"],
                "on": True,
            }
        ]
    )
    assert flat[0]["code"] == "600000"
    assert flat[0]["quote.price"] == "10.5"
    assert flat[0]["quote.change"] == ""  # None → 空串，不写 "None"
    assert flat[0]["tags"] == "a|b"
    assert flat[0]["on"] == "true"


def test_headers_stable_first_row_order_then_new_keys() -> None:
    flat = [
        {"b": "1", "a": "1"},
        {"a": "2", "b": "2", "c": "3"},
    ]
    assert rows.headers_of(flat) == ["b", "a", "c"]


def test_only_whitelist_drops_internal_fields() -> None:
    out = rows.flatten_rows(
        [{"code": "600000", "internal_note": "x", "name": "浦发"}],
        only=["code", "name"],
    )
    assert set(out[0]) == {"code", "name"}


def test_rows_to_csv_shape() -> None:
    text = rows.rows_to_csv(
        [
            {"code": "600000", "name": "浦发银行,总行", "price": 10.5},
            {"code": "600519", "name": "贵州茅台", "price": None},
        ]
    )
    lines = text.splitlines()
    assert lines[0] == "code,name,price"
    # 含逗号的值按 CSV 规则带引号
    assert "浦发银行,总行" in lines[1]
    # None → 空列（行尾逗号后无字符）
    assert lines[2].endswith(",")


def test_rows_to_xlsx_roundtrip() -> None:
    pytest.importorskip("openpyxl")
    from io import BytesIO

    from openpyxl import load_workbook

    blob = rows.rows_to_xlsx([{"code": "600000", "name": "浦发"}], sheet="自选")
    sheet = load_workbook(BytesIO(blob)).active
    assert sheet is not None and sheet.title == "自选"
    assert list(sheet.iter_rows(values_only=True)) == [
        ("code", "name"),
        ("600000", "浦发"),
    ]


def test_sanitize_name_blocks_paths_and_injection() -> None:
    assert rows.sanitize_name("watch list") == "watchlist"
    assert rows.sanitize_name("../../etc/passwd") == "etcpasswd"
    # 注入字符（引号/分号/空格）直接剥掉，不留任何 shell 可用的形式
    assert '"' not in rows.sanitize_name('name"; rm -rf /')
    assert " " not in rows.sanitize_name('name"; rm -rf /')
    # 非 ASCII（中文文件名）→ export：HTTP 头是 latin-1，非 ASCII 会在响应层炸
    assert rows.sanitize_name("持仓流水") == "export"
    assert rows.sanitize_name("") == "export"
    assert len(rows.sanitize_name("a" * 200)) <= 48


def test_openpyxl_missing_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins

    real_import = builtins.__import__

    def _no_openpyxl(name: str, *args: object, **kwargs: object) -> object:
        if name == "openpyxl":
            raise ImportError("No module named 'openpyxl'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_openpyxl)
    with pytest.raises(rows.ExportUnavailable) as err:
        rows.rows_to_xlsx([{"a": 1}])
    assert err.value.code == EXIT_CONFIG
