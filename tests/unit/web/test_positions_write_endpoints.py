"""QUOT-285：持仓写接口契约（`POST /api/positions` upsert + `POST /api/positions/{code}/delete`）。

写面照 vibe-astock 的口径：**所有写操作一律 `POST`**，删除也不换动词（换 `DELETE`
就绕开了「写方法必须有来源证据」这条防线）。本文件钉住四组事情：

1. **校验**：六字段白名单（多一个就 400）、三个必填（缺一个就 400）、
   `cost`/`qty` 非数值或为负 400、`entry_date` 非 `YYYY-MM-DD` 400；
2. **闸门**：非回环 / 坏 `Host` / 缺来源证据 / 跨站 `Sec-Fetch-Site` 一律 403，
   同源 `Origin` 与 `X-LDST-Client: 1` 二选一放行；
3. **语义**：按 `code` upsert（缺省不改建仓日）、硬删除（不留标记、不加审计表）、
   删不存在的 code 404；
4. **出口**：写响应是平面 JSON，不进 `LadderEnvelope`；GET 立刻能读到写结果。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.journal import position as position_module
from ladderstock.web import radar
from ladderstock.web.radar import (
    POSITIONS_DENIED,
    POSITIONS_WRITE_DENIED,
    POSITIONS_WRITE_FIELDS,
    RadarPlane,
)
from ladderstock.web.server import create_app

LOOPBACK = ("127.0.0.1", 54321)
REMOTE = ("10.1.2.3", 54321)
SAME_ORIGIN = "http://127.0.0.1:5950"
CODE = "600519"

#: 同源浏览器请求 / 非浏览器客户端（curl、脚本）各自的来源证据
BROWSER_HEADERS = {"origin": SAME_ORIGIN}
SCRIPT_HEADERS = {"x-ldst-client": "1"}

#: 信封顶层键：持仓写响应一个都不许有
ENVELOPE_KEYS = {"domain", "topic", "payload", "source", "fetched_at", "freshness"}


def _client(
    root: Path,
    *,
    client: tuple[str, int] = LOOPBACK,
) -> TestClient:
    plane = RadarPlane(data_root=root)
    app = create_app(data_root=root, dist_dir=root / "no-dist", radar_plane=plane)
    return TestClient(app, client=client)


def _post(client: TestClient, body: Any, headers: dict[str, str] | None = None):
    return client.post("/api/positions", json=body, headers=headers or BROWSER_HEADERS)


def _delete(client: TestClient, code: str, headers: dict[str, str] | None = None):
    return client.post(f"/api/positions/{code}/delete", headers=headers or BROWSER_HEADERS)


VALID = {
    "code": CODE,
    "cost": 12.34,
    "qty": 1000,
    "entry_date": "2026-09-01",
    "industry": "白酒",
    "note": "底仓",
}


# --------------------------------------------------------------------------- #
# 校验：白名单 / 必填 / 类型
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("body", "needle"),
    [
        ({**VALID, "broker_account": "888888"}, "broker_account"),
        ({**VALID, "fund_password": "secret"}, "fund_password"),
        ({**VALID, "id": 1}, "id"),
        ({k: v for k, v in VALID.items() if k != "code"}, "code"),
        ({k: v for k, v in VALID.items() if k != "cost"}, "cost"),
        ({k: v for k, v in VALID.items() if k != "qty"}, "qty"),
        ({**VALID, "cost": "12.34"}, "cost"),
        ({**VALID, "qty": "1000"}, "qty"),
        ({**VALID, "cost": None}, "cost"),
        ({**VALID, "cost": True}, "cost"),
        ({**VALID, "cost": -1}, "cost"),
        ({**VALID, "qty": -100}, "qty"),
        ({**VALID, "entry_date": "2026/09/01"}, "entry_date"),
        ({**VALID, "entry_date": "20260901"}, "entry_date"),
        ({**VALID, "entry_date": "2026-13-01"}, "entry_date"),
        ({**VALID, "entry_date": 20260901}, "entry_date"),
        ({**VALID, "industry": 1}, "industry"),
        ({**VALID, "note": ["x"]}, "note"),
        ({**VALID, "code": "   "}, "code"),
    ],
)
def test_invalid_bodies_are_400(tmp_path: Path, body: Any, needle: str) -> None:
    response = _post(_client(tmp_path), body)
    assert response.status_code == 400, response.text
    assert needle in response.json()["detail"]
    assert position_module.list_positions(root=tmp_path) == [], "校验失败不能落库"


def test_body_must_be_a_json_object(tmp_path: Path) -> None:
    response = _post(_client(tmp_path), [VALID])
    assert response.status_code == 400
    assert "JSON 对象" in response.json()["detail"]


def test_whitelist_is_the_rejection_list(tmp_path: Path) -> None:
    """白名单之外的字段一个都不接受——券商账号 / 资金密码没有入口。"""
    client = _client(tmp_path)
    for field in ("broker_account", "fund_password", "money_password", "account"):
        response = _post(client, {**VALID, field: "x"})
        assert response.status_code == 400, (field, response.text)
        assert field in response.json()["detail"]


def test_zero_cost_or_qty_is_rejected_like_the_cli(tmp_path: Path) -> None:
    """0 不是负数，但 `add_position` 与 CLI 同口径要求大于 0，仍按 400 拒绝。"""
    client = _client(tmp_path)
    for body in ({**VALID, "cost": 0}, {**VALID, "qty": 0}):
        response = _post(client, body)
        assert response.status_code == 400, response.text


# --------------------------------------------------------------------------- #
# 闸门：回环 + Host + 写方法来源证据
# --------------------------------------------------------------------------- #


def test_non_loopback_write_is_403(tmp_path: Path) -> None:
    response = _post(_client(tmp_path, client=REMOTE), VALID)
    assert response.status_code == 403
    assert response.json()["detail"] == POSITIONS_DENIED
    assert position_module.list_positions(root=tmp_path) == []


def test_rebinding_host_is_403(tmp_path: Path) -> None:
    response = _client(tmp_path).post(
        "/api/positions", json=VALID, headers={"host": "evil.example.com", **BROWSER_HEADERS}
    )
    assert response.status_code == 403
    assert "Host" in response.json()["detail"]


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"origin": "https://evil.example.com"},
        {"origin": "null"},
        {"origin": "http://127.0.0.1:5950", "sec-fetch-site": "cross-site"},
    ],
)
def test_missing_or_cross_site_write_evidence_is_403(
    tmp_path: Path, headers: dict[str, str]
) -> None:
    client = _client(tmp_path)
    response = client.post("/api/positions", json=VALID, headers=headers)
    assert response.status_code == 403, response.text
    detail = response.json()["detail"]
    assert "X-LDST-Client" in detail or "Sec-Fetch-Site" in detail
    assert position_module.list_positions(root=tmp_path) == []


def test_cross_site_origin_is_not_rescued_by_the_client_header(tmp_path: Path) -> None:
    """带了跨站 `Origin` 就只认 `Origin`，自定义头救不回来（浏览器强制写 Origin）。"""
    response = _post(
        _client(tmp_path),
        VALID,
        headers={"origin": "https://evil.example.com", "x-ldst-client": "1"},
    )
    assert response.status_code == 403


@pytest.mark.parametrize("headers", [BROWSER_HEADERS, SCRIPT_HEADERS])
def test_same_origin_or_client_header_is_accepted(tmp_path: Path, headers: dict[str, str]) -> None:
    response = _post(_client(tmp_path), VALID, headers=headers)
    assert response.status_code == 200, response.text


def test_delete_requires_the_same_write_evidence(tmp_path: Path) -> None:
    _post(_client(tmp_path), VALID)
    response = _client(tmp_path).post(f"/api/positions/{CODE}/delete")
    assert response.status_code == 403
    assert response.json()["detail"] == POSITIONS_WRITE_DENIED
    assert len(position_module.list_positions(root=tmp_path)) == 1


def test_write_face_ignores_control_switch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`LDST_CONTROL_ENABLED=0` 关的是控制面写路径，不是持仓写面（同 `_journal_guard`）。"""
    monkeypatch.setenv("LDST_CONTROL_ENABLED", "0")
    response = _post(_client(tmp_path), VALID)
    assert response.status_code == 200, response.text


# --------------------------------------------------------------------------- #
# upsert：写后 GET 立刻可见，平面 JSON 不进信封
# --------------------------------------------------------------------------- #


def test_upsert_is_flat_json_and_readable_by_get(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = _post(client, VALID)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"

    body = response.json()
    assert ENVELOPE_KEYS.isdisjoint(body)
    assert body["ok"] is True and body["local_only"] is True
    assert body["store"] == "local_sqlite"
    assert body["position"]["code"] == CODE
    assert body["position"]["cost"] == 12.34
    assert body["position"]["entry_date"] == "2026-09-01"
    assert body["position"]["note"] == "底仓"

    listed = client.get("/api/positions").json()
    assert listed["position_count"] == 1
    item = listed["items"][0]
    assert (item["code"], item["cost"], item["qty"]) == (CODE, 12.34, 1000)
    assert item["note"] == "底仓"


def test_upsert_overwrites_by_code_without_touching_entry_date(tmp_path: Path) -> None:
    """缺 `entry_date` 的覆盖保留原建仓日；给了新日期才改。"""
    client = _client(tmp_path)
    _post(client, VALID)
    _post(client, {"code": CODE, "cost": 20.0, "qty": 200, "industry": "食品"})

    rows = position_module.list_positions(root=tmp_path)
    assert len(rows) == 1
    assert (rows[0]["cost"], rows[0]["qty"]) == (20.0, 200)
    assert rows[0]["entry_date"] == "2026-09-01", "没填建仓日不能把旧值改到今天"
    assert rows[0]["industry"] == "食品"
    assert rows[0]["note"] == "", "全量 upsert：没给的可选字段按空处理"

    _post(client, {**VALID, "entry_date": "2026-10-09"})
    assert position_module.list_positions(root=tmp_path)[0]["entry_date"] == "2026-10-09"


def test_upsert_without_entry_date_defaults_to_today_on_insert(tmp_path: Path) -> None:
    from datetime import date

    _post(_client(tmp_path), {"code": CODE, "cost": 1.0, "qty": 1.0})
    assert position_module.list_positions(root=tmp_path)[0]["entry_date"] == (
        date.today().isoformat()
    )


def test_write_response_does_not_leak_into_a_ladder_envelope(tmp_path: Path) -> None:
    """写接口与读接口同一条口径：平面 JSON，`payload` / `domain` / `fetched_at` 都不出现。"""
    body = _post(_client(tmp_path), VALID).json()
    assert ENVELOPE_KEYS.isdisjoint(body)
    assert body["position"]["note"] == "底仓"  # 隐私在闸门，不在投影


# --------------------------------------------------------------------------- #
# 删除：硬删除、404、不留标记
# --------------------------------------------------------------------------- #


def test_delete_removes_the_row_and_it_disappears_from_get(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _post(client, VALID)
    _post(client, {"code": "000001", "cost": 10.0, "qty": 500.0})

    response = _delete(client, CODE)
    assert response.status_code == 200, response.text
    body = response.json()
    assert ENVELOPE_KEYS.isdisjoint(body)
    assert body["ok"] is True
    assert body["removed"] == {"code": CODE, "cost": 12.34, "qty": 1000.0}

    listed = client.get("/api/positions").json()
    assert [item["code"] for item in listed["items"]] == ["000001"]
    assert position_module.list_positions(code=CODE, root=tmp_path) == []


def test_delete_missing_code_is_404(tmp_path: Path) -> None:
    response = _delete(_client(tmp_path), "999999")
    assert response.status_code == 404
    assert "999999" in response.json()["detail"]


def test_delete_is_hard_and_leaves_no_marker(tmp_path: Path) -> None:
    """删完就是没有：没有软删除列、没有审计表、没有备份目录。"""
    client = _client(tmp_path)
    _post(client, VALID)
    _delete(client, CODE)

    db = tmp_path / "journal.sqlite"
    with sqlite3.connect(db) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert tables == {"schema_meta", "trades", "positions", "watchlist"}
        columns = {row[1] for row in conn.execute("PRAGMA table_info(positions)")}
        assert columns == {
            "id",
            "code",
            "cost",
            "qty",
            "entry_date",
            "industry",
            "note",
            "updated_at",
        }
        assert conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0
    artifacts = sorted(path.name for path in tmp_path.iterdir())
    assert all(name.startswith("journal.sqlite") for name in artifacts), artifacts


# --------------------------------------------------------------------------- #
# 内部错误 → 500（不冒充 200，也不把内部异常文本原样吐回去）
# --------------------------------------------------------------------------- #


def test_write_failures_are_500(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(radar, "add_position", boom)
    response = _post(_client(tmp_path), VALID)
    assert response.status_code == 500, response.text
    assert response.json()["detail"].startswith(radar.WRITE_FAILURE)


# --------------------------------------------------------------------------- #
# 契约常量：白名单就是这六个
# --------------------------------------------------------------------------- #


def test_write_whitelist_has_exactly_six_fields() -> None:
    assert POSITIONS_WRITE_FIELDS == ("code", "cost", "qty", "entry_date", "industry", "note")
