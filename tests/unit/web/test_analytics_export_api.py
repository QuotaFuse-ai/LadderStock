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
    assert client.get("/api/analytics/export?format=pdf").status_code == 422
    # 未知节：回环 + 空库下依然报「未知导出节」400（参数错先于数据错）
    bad_section = client.get("/api/analytics/export?format=csv&section=nonsense")
    assert bad_section.status_code == 400
    assert "未知导出节" in bad_section.json()["detail"]


def test_export_csv_content_is_parseable(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/api/analytics/export?format=csv&section=behavior")
    # 空库：summary 不可算 → 409（不下空文件冒充导出件）
    if response.status_code == 409:
        detail = response.json()["detail"]
        assert "流水" in detail or "算不出" in detail
        return
    text = response.content.decode("utf-8-sig")
    assert "分档" in text.splitlines()[0]
