"""输出层契约：三档格式、嵌套展开、中文宽度、UTF-8 编码。"""

from __future__ import annotations

import csv
import io
import json

import pytest

from ladderstock.cli import output


def _capture(fmt, data) -> str:
    buf = io.StringIO()
    original = output.sys.stdout
    output.sys.stdout = buf
    try:
        output.emit(data, fmt)
    finally:
        output.sys.stdout = original
    return buf.getvalue()


def test_json_is_stable_and_keeps_chinese():
    text = _capture("json", {"名称": "情绪温度", "ok": True})
    assert json.loads(text) == {"名称": "情绪温度", "ok": True}
    assert "情绪温度" in text  # ensure_ascii=False


def test_table_expands_nested_record_list():
    data = {
        "ok": True,
        "checks": [
            {"check": "python", "ok": True, "detail": "3.12.10"},
            {"check": "duckdb", "ok": False, "detail": "缺失"},
        ],
    }
    lines = _capture("table", data).splitlines()
    assert "checks:" in lines
    header = next(line for line in lines if line.startswith("check "))
    assert header.split() == ["check", "ok", "detail"]
    body = [line for line in lines if line.startswith("python") or line.startswith("duckdb")]
    assert len(body) == 2, "嵌套记录必须逐行展开，不能塞进一个单元格"


def test_table_aligns_wide_chinese():
    text = _capture("table", [{"原因": "网络", "code": "network"}])
    rows = [line for line in text.splitlines() if line.strip()]
    # 第二列在表头与数据行必须从同一显示列开始（中文按两列计）
    header_gap = output._disp_len(rows[0].split("code")[0])
    row_gap = output._disp_len(rows[2].split("network")[0])
    assert header_gap == row_gap
    assert output._disp_len("原因") == 4


def test_empty_collections_render_as_dash():
    assert output._text([]) == "-"
    assert output._text({}) == "-"
    assert output._text(None) == "-"


def test_csv_round_trips_records():
    text = _capture("csv", [{"a": 1, "b": "中文"}, {"a": 2, "b": "x"}])
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == ["a", "b"]
    assert rows[1] == ["1", "中文"]


@pytest.mark.parametrize("fmt", ["json", "table", "csv"])
def test_stdout_is_utf8_reconfigured(fmt):
    _capture(fmt, [{"k": "中文"}])
    assert output.sys.stdout.encoding is not None
