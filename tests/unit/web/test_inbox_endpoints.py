"""`/api/inbox` 三条路由的契约（QUOT-432 / DEC-058）。

钉住：

| 场景 | 期望 |
|---|---|
| 非回环来源（即使开了远程 Token / 控制面） | **403**，且不泄露任何一条提醒 |
| `Host` 不在白名单 | **403**，`detail` 带上收到的 Host |
| 本机没有提醒 | **200** + 空清单；不建库 |
| 任何响应 | `Cache-Control: no-store` + **平面 JSON**（无 `payload` / `domain` / `fetched_at`） |
| 写接口缺来源证据 | **403**；带 `X-LDST-Client: 1` 才放行 |
| 标已读 | 幂等；未知 id → **404** |
| `jobs[]` | 只列会产生提醒的任务（`digest` / `notify`），不伪造 `last_status` |

全程离线：这条面不取任何上游数据。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ladderstock.data import sqlite_store
from ladderstock.journal import inbox as inbox_store
from ladderstock.web.inbox import INBOX_DENIED, INBOX_WRITE_DENIED
from ladderstock.web.server import create_app

LOOPBACK = ("127.0.0.1", 54321)
REMOTE = ("10.1.2.3", 54321)

WRITE_HEADERS = {"X-LDST-Client": "1"}
ENVELOPE_KEYS = ("payload", "domain", "fetched_at")
PRIVACY_WORDS = ("watchlist", "holdings", "positions", "trades", "journal", "自选", "持仓")


def _client(root: Path, *, client: tuple[str, int] = LOOPBACK) -> TestClient:
    app = create_app(data_root=root, dist_dir=root / "no-dist")
    return TestClient(app, client=client)


def _seed(root: Path, count: int = 1) -> list[dict]:
    items = []
    for index in range(count):
        items.append(
            inbox_store.add_item(
                "premarket_digest",
                "09:05 盘前摘要",
                f"盘前摘要 2026-11-0{index + 1}",
                ["持仓 1 只 · 自选 1 只", "600000 现价 10.50 元"],
                "/positions",
                f"premarket_digest:2026-11-0{index + 1}",
                "digest",
                root=root,
            )
        )
    return items


# --------------------------------------------------------------------- 闸门


def test_read_face_is_loopback_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path)
    # Token 管「谁连得进来」，不管「隐私数据该不该出去」：开了远程也不能读提醒
    monkeypatch.setenv("LDST_WEB_ALLOW_REMOTE", "1")
    monkeypatch.setenv("LDST_WEB_TOKEN", "0123456789abcdef0123")

    denied = _client(tmp_path, client=REMOTE).get("/api/inbox")
    assert denied.status_code == 403
    assert denied.json()["detail"] == INBOX_DENIED
    assert "盘前摘要" not in denied.text

    assert _client(tmp_path).get("/api/inbox").status_code == 200


def test_bad_host_header_is_403(tmp_path: Path) -> None:
    _seed(tmp_path)
    response = _client(tmp_path).get("/api/inbox", headers={"host": "evil.example.com"})

    assert response.status_code == 403
    assert "Host" in response.json()["detail"]
    assert "evil.example.com" in response.json()["detail"]


def test_control_switch_does_not_close_the_read_face(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`LDST_CONTROL_ENABLED=0` 关的是控制面，不是本机数据面。"""
    _seed(tmp_path)
    monkeypatch.setenv("LDST_CONTROL_ENABLED", "0")

    assert _client(tmp_path).get("/api/inbox").status_code == 200


def test_write_face_needs_source_evidence(tmp_path: Path) -> None:
    items = _seed(tmp_path)
    client = _client(tmp_path)

    denied = client.post(f"/api/inbox/{items[0]['id']}/read")
    assert denied.status_code == 403
    assert denied.json()["detail"] == INBOX_WRITE_DENIED

    denied_all = client.post("/api/inbox/read-all")
    assert denied_all.status_code == 403
    assert denied_all.json()["detail"] == INBOX_WRITE_DENIED

    # 被拒的写请求不能产生任何副作用
    assert inbox_store.unread_count(root=tmp_path) == 1


def test_write_surface_is_only_post(tmp_path: Path) -> None:
    spec = _client(tmp_path).app.openapi()  # type: ignore[attr-defined]
    for path, operations in spec["paths"].items():
        if not path.startswith("/api/inbox"):
            continue
        assert set(operations) <= {"get", "post"}, (path, set(operations))
    assert set(spec["paths"]["/api/inbox"]) == {"get"}
    assert set(spec["paths"]["/api/inbox/read-all"]) == {"post"}
    assert set(spec["paths"]["/api/inbox/{item_id}/read"]) == {"post"}


# ------------------------------------------------------------------ 平面 JSON


def test_read_is_flat_json_no_store_and_carries_no_privacy_keys(
    tmp_path: Path, repo: Path
) -> None:
    _seed(tmp_path)
    response = _client(tmp_path).get("/api/inbox")

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"

    body = response.json()
    for envelope_key in ENVELOPE_KEYS:
        assert envelope_key not in body
    assert set(body) == {"local_only", "store", "items", "unread", "jobs"}
    assert body["local_only"] is True and body["store"] == "local_sqlite"
    assert body["unread"] == 1
    for word in PRIVACY_WORDS:
        assert word not in body

    item = body["items"][0]
    assert set(item) == {
        "id",
        "job",
        "job_label",
        "kind",
        "title",
        "lines",
        "link",
        "is_read",
        "read_at",
        "created_at",
    }
    assert item["is_read"] is False and item["read_at"] is None
    assert item["lines"] == ["持仓 1 只 · 自选 1 只", "600000 现价 10.50 元"]

    from ladderstock.core.privacy import private_keys_present

    assert private_keys_present(body) == []


def test_jobs_lists_only_reminder_jobs_without_fake_last_status(
    tmp_path: Path, repo: Path
) -> None:
    body = _client(tmp_path).get("/api/inbox").json()

    ids = {job["id"] for job in body["jobs"]}
    assert ids == {"premarket_digest", "close_digest", "watch_changes"}
    for job in body["jobs"]:
        assert set(job) == {"id", "label", "trigger", "enabled"}
        assert "last_status" not in job


# ---------------------------------------------------------------------- 空态


def test_empty_inbox_is_200_and_does_not_create_the_database(
    tmp_path: Path, repo: Path
) -> None:
    response = _client(tmp_path).get("/api/inbox")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["items"] == []
    assert body["unread"] == 0
    assert (tmp_path / sqlite_store.DB_FILENAME).exists() is False


# ---------------------------------------------------------------------- 读写


def test_mark_read_and_read_all_are_idempotent(tmp_path: Path) -> None:
    first, _second = _seed(tmp_path, count=2)
    client = _client(tmp_path)

    read = client.post(f"/api/inbox/{first['id']}/read", headers=WRITE_HEADERS)
    assert read.status_code == 200
    assert read.headers["cache-control"] == "no-store"
    assert read.json()["item"]["is_read"] is True
    assert read.json()["unread"] == 1

    again = client.post(f"/api/inbox/{first['id']}/read", headers=WRITE_HEADERS)
    assert again.status_code == 200
    assert again.json()["unread"] == 1

    all_read = client.post("/api/inbox/read-all", headers=WRITE_HEADERS)
    assert all_read.status_code == 200
    assert all_read.json()["updated"] == 1
    assert all_read.json()["unread"] == 0

    repeated = client.post("/api/inbox/read-all", headers=WRITE_HEADERS)
    assert repeated.json()["updated"] == 0


def test_unknown_item_id_is_404(tmp_path: Path) -> None:
    _seed(tmp_path)

    response = _client(tmp_path).post("/api/inbox/nope/read", headers=WRITE_HEADERS)

    assert response.status_code == 404
    assert "nope" in response.json()["detail"]


def test_limit_is_bounded_and_applied(tmp_path: Path) -> None:
    _seed(tmp_path, count=3)
    client = _client(tmp_path)

    assert len(client.get("/api/inbox?limit=2").json()["items"]) == 2
    assert client.get("/api/inbox?limit=0").status_code == 422
    assert client.get(f"/api/inbox?limit={inbox_store.MAX_LIMIT + 1}").status_code == 422
