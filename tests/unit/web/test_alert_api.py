"""`/api/alert/*` 契约（DEC-087）：闸门、打码、CRUD、测试渠道与手动跑一轮。

钉住：

| 场景 | 期望 |
|---|---|
| 非回环来源 | **403** |
| `Host` 白名单外 | **403** |
| 写接口缺来源证据 | **403**、无副作用；带 `X-LDST-Client: 1` 放行 |
| 渠道读面 | webhook URL **打码**；明文 token 不出现在任何响应里 |
| 规则 CRUD | 非法参数 **400**；未知 id **404** |
| `POST /run` | 不真实取数（monkeypatch 执行体），透传执行体报告 |
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.alert import store
from ladderstock.web.server import create_app

LOOPBACK = ("127.0.0.1", 54321)
REMOTE = ("10.1.2.3", 54321)
WRITE_HEADERS = {"X-LDST-Client": "1"}

SECRET = "SEC_TOKEN_DO_NOT_LEAK"


def _client(root: Path, *, client: tuple[str, int] = LOOPBACK) -> TestClient:
    app = create_app(data_root=root, dist_dir=root / "no-dist")
    return TestClient(app, client=client)


def _seed_rule(root: Path, **overrides: Any) -> dict:
    payload: dict[str, Any] = {"kind": "price_below", "symbol": "600000", "threshold": 10.2}
    payload.update(overrides)
    return store.add_rule(
        payload["kind"],
        payload["symbol"],
        payload["threshold"],
        **{
            key: value
            for key, value in payload.items()
            if key in ("channels", "note", "window", "cooldown_min")
        },
        root=root,
    )


# ----------------------------------------------------------------------- 闸门


def test_read_face_is_loopback_only(tmp_path: Path) -> None:
    _seed_rule(tmp_path)
    assert _client(tmp_path, client=REMOTE).get("/api/alert/rules").status_code == 403
    assert _client(tmp_path, client=REMOTE).get("/api/alert/channels").status_code == 403

    ok = _client(tmp_path).get("/api/alert/rules")
    assert ok.status_code == 200
    assert len(ok.json()["rules"]) == 1


def test_bad_host_is_403(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/api/alert/rules", headers={"host": "evil.example.com"})
    assert response.status_code == 403
    assert "Host" in response.json()["detail"]


def test_write_face_needs_source_evidence(tmp_path: Path) -> None:
    client = _client(tmp_path)
    denied = client.post(
        "/api/alert/rules", json={"kind": "price_above", "symbol": "600000", "threshold": 1}
    )
    assert denied.status_code == 403
    # 被拒的写不产生副作用
    assert store.list_rules(root=tmp_path) == []


# ------------------------------------------------------------------ 规则 CRUD


def test_rules_crud_and_validation_errors(tmp_path: Path) -> None:
    client = _client(tmp_path)

    created = client.post(
        "/api/alert/rules",
        json={"kind": "pnl_drop", "symbol": "600000", "threshold": 5, "note": "止损"},
        headers=WRITE_HEADERS,
    )
    assert created.status_code == 200, created.text
    rule = created.json()["rule"]
    assert rule["symbol"] == "600000" and rule["enabled"] is True

    # 读面带运行态
    listed = client.get("/api/alert/rules").json()
    assert listed["rules"][0]["state"]["status"] == "armed"

    # 非法参数 → 400，写说明人话
    bad_kind = client.post(
        "/api/alert/rules",
        json={"kind": "nope", "symbol": "600000", "threshold": 1},
        headers=WRITE_HEADERS,
    )
    assert bad_kind.status_code == 400
    assert "未知规则类型" in bad_kind.json()["detail"]

    bad_symbol = client.post(
        "/api/alert/rules",
        json={"kind": "price_above", "symbol": "sh600000", "threshold": 1},
        headers=WRITE_HEADERS,
    )
    assert bad_symbol.status_code == 400

    # 更新 / 启停 / 删除
    updated = client.put(
        f"/api/alert/rules/{rule['id']}",
        json={"threshold": 6},
        headers=WRITE_HEADERS,
    )
    assert updated.status_code == 200
    assert updated.json()["rule"]["threshold"] == 6

    toggled = client.post(
        f"/api/alert/rules/{rule['id']}/toggle", json={"enabled": False}, headers=WRITE_HEADERS
    )
    assert toggled.json()["rule"]["enabled"] is False

    removed = client.delete(f"/api/alert/rules/{rule['id']}", headers=WRITE_HEADERS)
    assert removed.status_code == 200
    assert client.get("/api/alert/rules").json()["total"] == 0

    # 未知 id → 404
    assert client.delete("/api/alert/rules/ghost", headers=WRITE_HEADERS).status_code == 404


# ------------------------------------------------------------------ 渠道打码


def test_channels_masked_on_every_read(tmp_path: Path) -> None:
    store.add_channel(
        "ding-main",
        "dingtalk",
        name="钉钉主群",
        config={"url": f"https://oapi.dingtalk.com/robot/send?access_token={SECRET}"},
        root=tmp_path,
    )
    client = _client(tmp_path)

    listing = client.get("/api/alert/channels")
    assert listing.status_code == 200
    assert SECRET not in listing.text
    assert listing.json()["channels"][0]["config"]["url"].endswith("***")

    # 创建回包同样打码
    created = client.post(
        "/api/alert/channels",
        json={
            "id": "hook-2",
            "kind": "webhook",
            "name": "值机",
            "config": {"url": f"https://example.com/hook?token={SECRET}"},
        },
        headers=WRITE_HEADERS,
    )
    assert created.status_code == 200
    assert SECRET not in created.text

    # 更新同理
    updated = client.put(
        "/api/alert/channels/hook-2",
        json={"name": "值机改"},
        headers=WRITE_HEADERS,
    )
    assert updated.status_code == 200
    assert "值机改" in updated.text and SECRET not in updated.text

    assert client.delete("/api/alert/channels/hook-2", headers=WRITE_HEADERS).status_code == 200


def test_channel_test_endpoint_uses_real_deliver_console(tmp_path: Path) -> None:
    store.add_channel("console-main", "console", root=tmp_path)
    client = _client(tmp_path)

    result = client.post("/api/alert/channels/console-main/test", headers=WRITE_HEADERS)
    assert result.status_code == 200
    assert result.json()["ok"] is True

    assert client.post("/api/alert/channels/ghost/test", headers=WRITE_HEADERS).status_code == 404


# --------------------------------------------------------------------- 手动跑


def test_run_endpoint_delegates_to_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_rule(tmp_path, channels=["console-main"])
    monkeypatch.setattr(
        "ladderstock.alert.run.run_once",
        lambda **kwargs: {"status": "partial", "checked": 1, "fired": [], "detail": "x"},
    )
    response = _client(tmp_path).post("/api/alert/run", headers=WRITE_HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "partial" and body["checked"] == 1


def test_history_is_read_only_and_ordered(tmp_path: Path) -> None:
    _seed_rule(tmp_path)
    store.append_history("R1", "t1", "b1", [], root=tmp_path)
    client = _client(tmp_path)
    body = client.get("/api/alert/history").json()
    assert [item["title"] for item in body["items"]] == ["t1"]

    # history 只有 GET（无 POST）
    assert client.post("/api/alert/history", headers=WRITE_HEADERS).status_code == 405


def test_openapi_shape(tmp_path: Path) -> None:
    spec = _client(tmp_path).app.openapi()  # type: ignore[attr-defined]
    alert_paths = {path for path in spec["paths"] if path.startswith("/api/alert")}
    assert alert_paths == {
        "/api/alert/rules",
        "/api/alert/rules/{rule_id}",
        "/api/alert/rules/{rule_id}/toggle",
        "/api/alert/channels",
        "/api/alert/channels/{channel_id}",
        "/api/alert/channels/{channel_id}/test",
        "/api/alert/run",
        "/api/alert/history",
    }
