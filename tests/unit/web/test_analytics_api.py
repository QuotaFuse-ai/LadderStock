"""`/api/analytics` 契约（B 包 / DEC-089）。

钉住：非回环 403、`no-store`、空库给 `ok=false`（不补 0、不抛 500）、
参数边界（days 30–3650）。
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


def test_analytics_is_loopback_only(tmp_path: Path) -> None:
    assert _client(tmp_path, client=REMOTE).get("/api/analytics/summary").status_code == 403

    ok = _client(tmp_path).get("/api/analytics/summary")
    assert ok.status_code == 200
    assert ok.headers["cache-control"] == "no-store"


def test_analytics_empty_state_is_honest(tmp_path: Path) -> None:
    body = _client(tmp_path).get("/api/analytics/summary").json()
    assert body["ok"] is False
    assert body["metrics"] is None
    assert body["curve"] is None
    assert body["mistakes"] == []
    assert "还没有交易流水" in body["reason"]


def test_analytics_days_bounds(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.get("/api/analytics/summary?days=29").status_code == 422
    assert client.get("/api/analytics/summary?days=3651").status_code == 422
    assert client.get("/api/analytics/summary?days=90").status_code == 200


def test_analytics_openapi_shape(tmp_path: Path) -> None:
    spec = _client(tmp_path).app.openapi()  # type: ignore[attr-defined]
    assert "/api/analytics/summary" in spec["paths"]
