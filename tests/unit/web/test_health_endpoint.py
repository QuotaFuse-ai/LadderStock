"""`/api/health` 的健康字段（QUOT-443 / DEC-063）。

`status` / `uptime_sec` / `db_ok` 是本次新增；原有字段一个不删（外部探测依赖它们）。
`db_ok` 只读探库：库文件是目录 / 不可读时回 `false` 而不是 500。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from ladderstock.web.server import create_app


def _client(root: Path) -> TestClient:
    return TestClient(create_app(data_root=root, dist_dir=root / "no-dist"))


def test_health_keeps_existing_fields_and_adds_new(tmp_path: Path) -> None:
    payload = _client(tmp_path).get("/api/health").json()

    for key in ("ok", "name", "version", "time", "data_dir", "read_only"):
        assert key in payload, f"旧字段 {key} 被删了"
    assert payload["status"] == "ok"
    assert payload["db_ok"] is True
    assert isinstance(payload["uptime_sec"], int)
    assert payload["uptime_sec"] >= 0


def test_health_db_ok_true_for_real_library(tmp_path: Path) -> None:
    conn = sqlite3.connect(tmp_path / "journal.sqlite")
    try:
        conn.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY)")
        conn.execute("PRAGMA user_version = 3")
    finally:
        conn.close()

    payload = _client(tmp_path).get("/api/health").json()
    assert payload["db_ok"] is True
    assert payload["status"] == "ok"


def test_health_degrades_when_db_path_is_a_directory(tmp_path: Path) -> None:
    (tmp_path / "journal.sqlite").mkdir()

    response = _client(tmp_path).get("/api/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["db_ok"] is False
    assert payload["status"] == "degraded"
    assert payload["ok"] is True  # 进程活着；降级不等于 500


def test_health_uptime_is_monotonic_seconds(tmp_path: Path) -> None:
    client = _client(tmp_path)
    first = client.get("/api/health").json()["uptime_sec"]
    second = client.get("/api/health").json()["uptime_sec"]
    assert second >= first
