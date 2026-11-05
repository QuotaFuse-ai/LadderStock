"""QUOT-419 / DEC-055：自选写接口契约。

`POST /api/watchlist`（幂等新增）+ `POST /api/watchlist/{code}/delete`（硬删除）。
本文件钉住五组事情：

1. **语义**：新增幂等（已存在不刷新 `created_at`）、前缀归一化、删除不存在的 code 404；
2. **校验**：只认 `code` 一个键、只收 6 位数字（可带 sh/sz/bj）、多一个键 / 类型不对 400；
3. **闸门**：非回环 / 坏 `Host` / 缺来源证据 / 跨站 `Sec-Fetch-Site` 一律 403，
   同源 `Origin` 与 `X-LDST-Client: 1` 二选一放行；
4. **出口**：平面 JSON、`no-store`、不进 `LadderEnvelope`；
5. **闭环**：写完之后 `GET /api/watchlist` 立刻读得到（首页与自选页都靠这条）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.journal import watchlist as watchlist_module
from ladderstock.web.radar import (
    WATCHLIST_DENIED,
    WATCHLIST_WRITE_DENIED,
    RadarPlane,
)
from ladderstock.web.server import create_app

LOOPBACK = ("127.0.0.1", 54321)
REMOTE = ("10.1.2.3", 54321)
SAME_ORIGIN = "http://127.0.0.1:5950"
CODE = "600519"

BROWSER_HEADERS = {"origin": SAME_ORIGIN}
SCRIPT_HEADERS = {"x-ldst-client": "1"}

ENVELOPE_KEYS = {"domain", "topic", "payload", "source", "fetched_at", "freshness"}


class _NoQuotes:
    """取价替身：写面测试不碰网络，GET 只用来确认清单，不需要真实报价。"""

    prices: dict[str, Any] = {}
    status = "missing"
    as_of = None
    source_detail = "offline-test"


@pytest.fixture(autouse=True)
def _offline_prices(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ladderstock.web.radar.fetch_prices", lambda *args, **kwargs: _NoQuotes()
    )


def _client(
    root: Path,
    *,
    client: tuple[str, int] = LOOPBACK,
) -> TestClient:
    plane = RadarPlane(data_root=root)
    app = create_app(data_root=root, dist_dir=root / "no-dist", radar_plane=plane)
    return TestClient(app, client=client)


def _add(client: TestClient, body: Any, headers: dict[str, str] | None = None):
    return client.post("/api/watchlist", json=body, headers=headers or BROWSER_HEADERS)


def _delete(client: TestClient, code: str, headers: dict[str, str] | None = None):
    return client.post(f"/api/watchlist/{code}/delete", headers=headers or BROWSER_HEADERS)


# --------------------------------------------------------------------------- #
# 语义
# --------------------------------------------------------------------------- #


def test_add_then_get_sees_the_row(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.get("/api/watchlist").json()["watch_count"] == 0

    response = _add(client, {"code": CODE})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["local_only"] is True
    assert body["store"] == "local_sqlite"
    assert body["watch"]["code"] == CODE
    assert body["watch"]["created_at"]

    after = client.get("/api/watchlist").json()
    assert after["watch_count"] == 1
    assert [item["code"] for item in after["items"]] == [CODE]


def test_add_is_idempotent_and_keeps_created_at(tmp_path: Path) -> None:
    client = _client(tmp_path)
    first = _add(client, {"code": CODE}).json()["watch"]
    second = _add(client, {"code": CODE}).json()["watch"]

    assert second["created_at"] == first["created_at"]
    assert watchlist_module.list_watch(root=tmp_path) == [first]


@pytest.mark.parametrize(
    ("raw", "stored"),
    [
        ("sh600000", "600000"),
        ("SZ000001", "000001"),
        ("bj430047", "430047"),
        ("600000", "600000"),
        (600000, "600000"),
    ],
)
def test_code_prefix_is_normalised(tmp_path: Path, raw: Any, stored: str) -> None:
    response = _add(_client(tmp_path), {"code": raw})
    assert response.status_code == 200, response.text
    assert response.json()["watch"]["code"] == stored


def test_delete_removes_the_row(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _add(client, {"code": CODE})
    _add(client, {"code": "000001"})

    response = _delete(client, CODE)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["removed"] == {"code": CODE}

    remaining = client.get("/api/watchlist").json()
    assert [item["code"] for item in remaining["items"]] == ["000001"]


def test_delete_missing_code_is_404(tmp_path: Path) -> None:
    response = _delete(_client(tmp_path), "600000")
    assert response.status_code == 404
    assert "600000" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# 校验
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("body", "needle"),
    [
        ([{"code": CODE}], "JSON 对象"),
        ({}, "code"),
        ({"code": CODE, "note": "顺手加备注"}, "note"),
        ({"code": CODE, "cost": 1}, "cost"),
        ({"code": True}, "code"),
        ({"code": 600000.5}, "code"),
        ({"code": None}, "code"),
        ({"code": "60000"}, "6 位"),
        ({"code": "sh60000"}, "6 位"),
        ({"code": "   "}, "不能为空"),
    ],
)
def test_invalid_bodies_are_400(tmp_path: Path, body: Any, needle: str) -> None:
    response = _add(_client(tmp_path), body)
    assert response.status_code == 400, response.text
    assert needle in response.json()["detail"]
    assert watchlist_module.list_watch(root=tmp_path) == [], "校验失败不能落库"


# --------------------------------------------------------------------------- #
# 闸门
# --------------------------------------------------------------------------- #


def test_write_requires_loopback(tmp_path: Path) -> None:
    response = _add(_client(tmp_path, client=REMOTE), {"code": CODE})
    assert response.status_code == 403
    assert response.json()["detail"] == WATCHLIST_DENIED
    assert watchlist_module.list_watch(root=tmp_path) == []


def test_write_requires_source_evidence(tmp_path: Path) -> None:
    client = _client(tmp_path)
    missing = client.post("/api/watchlist", json={"code": CODE})
    assert missing.status_code == 403
    assert missing.json()["detail"] == WATCHLIST_WRITE_DENIED
    assert watchlist_module.list_watch(root=tmp_path) == []

    # 非浏览器客户端（curl / 脚本）用显式头，同样放行
    assert _add(client, {"code": CODE}, SCRIPT_HEADERS).status_code == 200


def test_cross_site_fetch_metadata_is_403(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = _add(
        client,
        {"code": CODE},
        {**BROWSER_HEADERS, "sec-fetch-site": "cross-site"},
    )
    assert response.status_code == 403
    assert "Sec-Fetch-Site" in response.json()["detail"]

    same = _add(
        client,
        {"code": CODE},
        {**BROWSER_HEADERS, "sec-fetch-site": "same-origin"},
    )
    assert same.status_code == 200


def test_bad_host_header_is_403(tmp_path: Path) -> None:
    response = _add(
        _client(tmp_path),
        {"code": CODE},
        {**BROWSER_HEADERS, "host": "evil.example.com"},
    )
    assert response.status_code == 403
    assert "evil.example.com" in response.json()["detail"]


def test_control_switch_does_not_close_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`LDST_CONTROL_ENABLED=0` 关的是控制面，不是本机数据面（DEC-033 §2.5 同一条）。"""
    monkeypatch.setenv("LDST_CONTROL_ENABLED", "0")
    assert _add(_client(tmp_path), {"code": CODE}).status_code == 200


# --------------------------------------------------------------------------- #
# 出口
# --------------------------------------------------------------------------- #


def test_write_response_is_flat_json_no_store(tmp_path: Path) -> None:
    response = _add(_client(tmp_path), {"code": CODE})
    assert response.headers["cache-control"] == "no-store"

    body = response.json()
    assert not (ENVELOPE_KEYS & set(body))
    # 字段名不能叫 watchlist / 自选——两个词在 PRIVATE_KEYS 里，进信封出口会被静默剥掉
    assert "watchlist" not in body and "自选" not in body
    assert "note" not in json.dumps(body, ensure_ascii=False)
