"""静态看板的缓存契约：入口页必须回源校验，带哈希的 assets 才能长期缓存。

起因：换 bundle 后浏览器启发式缓存了旧 `index.html`，看板看起来还是老版导航。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from ladderstock.web.server import create_app


def _dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>LadderStock</title>", encoding="utf-8")
    (dist / "assets" / "index-abc12345.js").write_text("console.log(1)", encoding="utf-8")
    return dist


def test_index_revalidates(tmp_path: Path) -> None:
    client = TestClient(create_app(data_root=tmp_path, dist_dir=_dist(tmp_path)))
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-cache, must-revalidate"


def test_spa_fallback_revalidates(tmp_path: Path) -> None:
    client = TestClient(create_app(data_root=tmp_path, dist_dir=_dist(tmp_path)))
    resp = client.get("/review/ai")
    assert resp.status_code == 200
    assert "LadderStock" in resp.text
    assert resp.headers["cache-control"] == "no-cache, must-revalidate"


def test_hashed_asset_immutable(tmp_path: Path) -> None:
    client = TestClient(create_app(data_root=tmp_path, dist_dir=_dist(tmp_path)))
    resp = client.get("/assets/index-abc12345.js")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_non_asset_file_revalidates(tmp_path: Path) -> None:
    dist = _dist(tmp_path)
    (dist / "favicon.ico").write_bytes(b"\x00")
    client = TestClient(create_app(data_root=tmp_path, dist_dir=dist))
    resp = client.get("/favicon.ico")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-cache, must-revalidate"
