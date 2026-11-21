"""F11 简报 API 契约：`/api/brief/*` 的读写分界与去重。

钉住四件事：

1. 读面（today / history / config）回环可用，`no-store`，`local_only` 标记在；
2. 写面（run / config）缺来源证据 403，带 `X-LDST-Client: 1` 才通过；
3. `run` 幂等：同一天连跑两次，第二次 `pushed == []` 且 `skipped` 数等于条目数；
4. 不认识的 kind 404，不静默返回一份空简报。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.watch import brief as brief_module
from ladderstock.web.server import create_app

LOOPBACK = ("127.0.0.1", 54321)
BASE_URL = "http://127.0.0.1:5950"
WRITE_HEADERS = {"X-LDST-Client": "1"}


class _NullControlPlane:
    peers: dict[str, Any] = {}


def _client(root: Path, headers: dict[str, str] | None = None) -> TestClient:
    app = create_app(
        data_root=root,
        dist_dir=root / "no-dist",
        control_plane=_NullControlPlane(),
    )
    return TestClient(app, base_url=BASE_URL, client=LOOPBACK, headers=headers or {})


def test_today_returns_three_briefs_with_sources(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.get("/api/brief/today")
    assert response.status_code == 200
    assert response.headers.get("cache-control") == "no-store"
    payload = response.json()
    assert payload["local_only"] is True
    assert [row["kind"] for row in payload["briefs"]] == list(brief_module.BRIEFS)
    assert payload["day"]
    for row in payload["briefs"]:
        assert row["status"] in {"ok", "skipped", "failed", "degraded"}
        assert row["detail"]
        for item in row["items"]:
            assert item["source"], f"{item['title']} 缺出处"
            assert len(item["fingerprint"]) == 16


def test_today_single_kind(tmp_path: Path) -> None:
    client = _client(tmp_path)
    payload = client.get("/api/brief/today", params={"kind": "position_alerts"}).json()
    assert [row["kind"] for row in payload["briefs"]] == ["position_alerts"]


def test_unknown_kind_is_404(tmp_path: Path) -> None:
    client = _client(tmp_path, WRITE_HEADERS)
    assert client.get("/api/brief/today", params={"kind": "nope"}).status_code == 404
    assert client.post("/api/brief/nope/run").status_code == 404


def test_run_requires_write_evidence(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.post("/api/brief/morning/run").status_code == 403
    assert client.put("/api/brief/config", json={"notify": True}).status_code == 403


def test_run_is_idempotent(tmp_path: Path) -> None:
    client = _client(tmp_path, WRITE_HEADERS)
    first = client.post("/api/brief/morning/run")
    assert first.status_code == 200
    payload = first.json()
    assert payload["ok"] is True
    pushed = {row["fingerprint"] for row in payload["pushed"]}
    assert pushed

    second = client.post("/api/brief/morning/run").json()
    assert second["pushed"] == []
    assert {row["fingerprint"] for row in second["skipped"]} == pushed

    # 状态文件就落在注入的 data_root 下，不是进程默认目录
    state = Path(second["state_path"])
    assert state.parent.parent == tmp_path
    assert state.is_file()


def test_history_and_inbox_cross_check(tmp_path: Path) -> None:
    client = _client(tmp_path, WRITE_HEADERS)
    client.post("/api/brief/morning/run")
    days = client.get("/api/brief/history", params={"limit": 3}).json()["days"]
    assert len(days) == 1
    assert days[0]["published"] > 0
    items = client.get("/api/inbox").json()["items"]
    assert [row for row in items if row["kind"] == "brief"]


def test_config_put_validates_and_persists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`PUT /api/brief/config` 写的是 `config/brief.yaml`：这里把它指到 tmp。"""
    config_root = tmp_path / "config"
    monkeypatch.setattr(brief_module, "settings_path", lambda *a, **k: config_root / "brief.yaml")

    client = _client(tmp_path, WRITE_HEADERS)
    response = client.put("/api/brief/config", json={"notify": True, "channel": "desktop"})
    assert response.status_code == 200
    body = response.json()
    assert body["settings"]["notify"] is True
    assert body["settings"]["channel"] == "desktop"
    assert (config_root / "brief.yaml").is_file()

    bad = client.put("/api/brief/config", json={"channel": "telegram"})
    assert bad.status_code == 400
    assert "telegram" in bad.json()["detail"]

    unknown = client.put("/api/brief/config", json={"threshold": 6, "extra": 1}).json()
    assert unknown["ignored"] == ["extra"]
