"""`POST /api/table-export` 契约（C 包第二半 / DEC-090）。

钉住：非回环 403、缺来源证据 403（POST 写面语义）、BOM + attachment、
format/rows 校验链、行数上限、列白名单、XLSX 真工作簿。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from ladderstock.web.server import create_app

LOOPBACK = ("127.0.0.1", 54321)
REMOTE = ("10.1.2.3", 54321)
WRITE_HEADERS = {"X-LDST-Client": "1"}


def _client(root: Path, *, client: tuple[str, int] = LOOPBACK) -> TestClient:
    app = create_app(data_root=root, dist_dir=root / "no-dist")
    return TestClient(app, client=client)


def _body(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "portfolio-trades",
        "format": "csv",
        "rows": [
            {"code": "600000", "name": "浦发银行", "shares": 1000, "price": 10.0},
            {"code": "600519", "name": "贵州茅台", "shares": 100, "price": 1500.0},
        ],
    }
    payload.update(overrides)
    return payload


# --------------------------------------------------------------------- 闸门


def test_export_is_loopback_only(tmp_path: Path) -> None:
    response = _client(tmp_path, client=REMOTE).post(
        "/api/table-export", json=_body(), headers=WRITE_HEADERS
    )
    assert response.status_code == 403


def test_export_needs_source_evidence(tmp_path: Path) -> None:
    # 回环但无 Origin / X-LDST-Client：写面语义，403 且无副作用（没有下载件产生）
    response = _client(tmp_path).post("/api/table-export", json=_body())
    assert response.status_code == 403


# ------------------------------------------------------------------ 基本面


def test_csv_download_headers_and_bom(tmp_path: Path) -> None:
    response = _client(tmp_path).post("/api/table-export", json=_body(), headers=WRITE_HEADERS)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["content-disposition"] == 'attachment; filename="portfolio-trades.csv"'
    assert response.content.startswith(b"\xef\xbb\xbf")
    text = response.content.decode("utf-8-sig")
    assert text.splitlines()[0] == "code,name,shares,price"


def test_xlsx_is_a_real_workbook(tmp_path: Path) -> None:
    from io import BytesIO

    from openpyxl import load_workbook

    response = _client(tmp_path).post(
        "/api/table-export", json=_body(format="xlsx"), headers=WRITE_HEADERS
    )
    assert response.status_code == 200
    assert response.headers["content-type"].endswith("spreadsheetml.sheet")
    sheet = load_workbook(BytesIO(response.content)).active
    assert sheet is not None
    assert list(sheet.iter_rows(values_only=True))[1][0] == "600000"


# ------------------------------------------------------------------ 校验链


def test_format_whitelist_and_empty_rows(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert (
        client.post(
            "/api/table-export", json=_body(format="pdf"), headers=WRITE_HEADERS
        ).status_code
        == 400
    )
    assert (
        client.post("/api/table-export", json=_body(rows=[]), headers=WRITE_HEADERS).status_code
        == 400
    )
    assert (
        client.post("/api/table-export", json=_body(rows=[1, 2]), headers=WRITE_HEADERS).status_code
        == 400
    )
    assert (
        client.post(
            "/api/table-export",
            json=_body(only="code"),
            headers=WRITE_HEADERS,
        ).status_code
        == 400
    )


def test_row_limit_is_enforced(tmp_path: Path) -> None:
    rows = [{"code": str(index)} for index in range(2001)]
    response = _client(tmp_path).post(
        "/api/table-export", json=_body(rows=rows), headers=WRITE_HEADERS
    )
    assert response.status_code == 413
    assert "2000" in response.json()["detail"]


def test_only_whitelist_applies(tmp_path: Path) -> None:
    payload = _body(
        only=["code", "name"],
        rows=[{"code": "600000", "name": "浦发", "secret_field": "x"}],
    )
    response = _client(tmp_path).post("/api/table-export", json=payload, headers=WRITE_HEADERS)
    assert response.status_code == 200
    text = response.content.decode("utf-8-sig")
    assert "secret_field" not in text
    assert text.splitlines()[0] == "code,name"


def test_name_is_sanitized_in_disposition(tmp_path: Path) -> None:
    payload = _body(name='evil"; rm -rf /')
    response = _client(tmp_path).post("/api/table-export", json=payload, headers=WRITE_HEADERS)
    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    # 文件名只剩白名单字符（alnum/-/_）——注入字符全部剥掉，头里无可执行形式
    filename = disposition.split('filename="')[1].rstrip('"')
    assert filename
    assert all(ch.isalnum() or ch in "-._" for ch in filename)
    for bad in ('"', ";", " ", "/"):
        assert bad not in filename
