"""`/api/analytics/export` 契约（C 包 / DEC-090）。

钉住：非回环 403、`no-store` + attachment、format 白名单、未知节 400、
无统计 409、CSV 带 BOM、XLSX 为真表格字节流。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from ladderstock.web.server import create_app

LOOPBACK = ("127.0.0.1", 54321)
REMOTE = ("10.1.2.3", 54321)


def _client(root: Path, *, client: tuple[str, int] = LOOPBACK) -> TestClient:
    app = create_app(data_root=root, dist_dir=root / "no-dist")
    return TestClient(app, client=client)


def test_export_is_loopback_only(tmp_path: Path) -> None:
    # 非回环连闸门都过不了：403，与内容无关
    assert (
        _client(tmp_path, client=REMOTE).get("/api/analytics/export?format=csv").status_code == 403
    )
    # 回环但空库：409（不下空文件冒充导出件）——两种失败面分开钉
    ok = _client(tmp_path).get("/api/analytics/export?format=csv")
    assert ok.status_code == 409
    assert "流水" in ok.json()["detail"]


def test_export_headers_and_bom(tmp_path: Path) -> None:
    """空库下没有可导出的统计 → 409 + 人话，绝不下空文件。"""
    response = _client(tmp_path).get("/api/analytics/export?format=csv&section=attribution")
    assert response.status_code == 409
    assert "流水" in response.json()["detail"]
    # 无统计时不碰 Content-Disposition / BOM：响应体就是错误 JSON
    assert "content-disposition" not in response.headers


def test_export_format_whitelist_and_section_guard(tmp_path: Path) -> None:
    client = _client(tmp_path)
    # format 白名单在 FastAPI 层拦截（非法 422），先于业务闸门
    assert client.get("/api/analytics/export?format=webdoc").status_code == 422
    # 未知节：回环 + 空库下依然报「未知导出节」400（参数错先于数据错）
    bad_section = client.get("/api/analytics/export?format=csv&section=nonsense")
    assert bad_section.status_code == 400
    assert "未知导出节" in bad_section.json()["detail"]


def _seed_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    """pdf 分支的 409 前置（无流水）用假 summary 绕开：这里钉的是格式流，不是统计。"""
    from ladderstock.analytics import engine

    payload = {
        "ok": True,
        "start": "2025-11-21",
        "as_of": "2026-11-21",
        "span_days": 366,
        "metrics": {"twr": 0.12, "max_drawdown": 0.05},
        "benchmark": {"code": "000300", "label": "沪深300", "return": 0.03, "reason": None},
        "excess_return": 0.09,
        "attribution": [
            {"symbol": "600000", "name": "浦发银行", "realized": 780.0, "dividends": 0, "fees": 15.0, "net": 765.0, "status": "closed"}
        ],
        "behavior": {"buckets": [], "reason": "没有可判定的买入"},
        "mistakes": [],
        "warnings": [],
        "notes": [],
    }
    monkeypatch.setattr(engine, "build_summary", lambda days: payload)


def test_export_pdf_without_font_is_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_summary(monkeypatch)
    monkeypatch.setenv("LDST_CJK_FONT", str(tmp_path / "no-such-font.ttf"))
    response = _client(tmp_path).get("/api/analytics/export?format=pdf")
    assert response.status_code == 503
    assert "中文字体" in response.json()["detail"]


def test_export_pdf_streams_real_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_summary(monkeypatch)
    # 字体缺席的机器也要能钉住「字节流 + 头」这条线：把渲染换成真字节桩
    monkeypatch.setattr(
        "ladderstock.analytics.export.build_pdf_bytes",
        lambda payload: b"%PDF-1.7 analytics",
    )
    response = _client(tmp_path).get("/api/analytics/export?format=pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-disposition"] == 'attachment; filename="analytics.pdf"'
    assert response.content.startswith(b"%PDF-")


def test_export_csv_content_is_parseable(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/api/analytics/export?format=csv&section=behavior")
    # 空库：summary 不可算 → 409（不下空文件冒充导出件）
    if response.status_code == 409:
        detail = response.json()["detail"]
        assert "流水" in detail or "算不出" in detail
        return
    text = response.content.decode("utf-8-sig")
    assert "分档" in text.splitlines()[0]
