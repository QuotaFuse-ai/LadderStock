"""QUOT-482-A × 482-C：`GET /api/supervisor/state` 合并视图端点行为。

482-A 的归属视角（ownership / evidence / listening）叠加在 482-C 自愈状态面
（health / state / self_heal，前端 484 契约）之上；引擎未接线（482-B 未合并）
时 `configured: False` 降级，482-C 视角原样保留。路由注册顺序：482-A 的
GET /state 先注册（合并版响应），482-C 的 POST /state/{module}/reset 不受影响。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from ladderstock.supervisor import MODULE_ORDER
from ladderstock.web.control import ControlPlane
from ladderstock.web.server import create_app

LOOPBACK = ("127.0.0.1", 54321)
REMOTE = ("10.1.2.3", 54321)


class FakeSelfHeal:
    """482-C 引擎假件：snapshot() 返回任务书契约的 health 视角。"""

    def __init__(self, modules: list[dict[str, Any]] | None = None) -> None:
        self._modules = modules if modules is not None else [
            {
                "module": "wire",
                "ports": [8790, 8791],
                "pid": 111,
                "health": "ok",
                "state": "running",
                "restarts_5min": 0,
                "restart_count": 0,
                "stopping_by_user": False,
                "origin": None,
                "cmdline_hash": None,
                "last_exit_reason": None,
                "self_heal": {"attempts": 0, "max": 3, "status": "ok"},
            }
        ]

    def snapshot(self) -> dict[str, Any]:
        return {
            "ok": True,
            "engine": {"running": False, "window_seconds": 300.0},
            "modules": [dict(item) for item in self._modules],
        }

    def start(self) -> None:  # pragma: no cover - startup 钩子不参与断言
        pass

    def stop(self, timeout: float | None = None) -> None:  # pragma: no cover
        pass

    def reset(self, module: str) -> dict[str, Any]:
        return {"module": module, "health": "ok"}


class FakeSupervisor:
    """482-A 引擎假件：state() 返回归属视角。"""

    def state(self) -> dict[str, Any]:
        return {
            "supervisor_pid": 4242,
            "order": list(MODULE_ORDER),
            "modules": {
                "wire": {"ownership": "managed", "evidence": "ledger", "listening": True}
            },
        }


def _client(
    supervisor: object | None, heal: object | None = None, peer: tuple[str, int] = LOOPBACK
) -> TestClient:
    plane = ControlPlane(data_root=Path("."), supervisor=supervisor)
    app = create_app(
        data_root=Path("."),
        dist_dir=Path("missing"),
        control_plane=plane,
        supervisor_engine=heal if heal is not None else FakeSelfHeal(),
    )
    return TestClient(app, base_url="http://127.0.0.1:5950", client=peer)


def test_state_without_engine_reports_unconfigured_with_heal_view() -> None:
    """482-B 未接线：configured=False 降级，482-C 健康视角原样保留。"""
    client = _client(None)
    res = client.get("/api/supervisor/state")
    assert res.status_code == 200
    payload = res.json()
    assert payload["configured"] is False
    assert payload["order"] == list(MODULE_ORDER)
    wire = next(item for item in payload["modules"] if item["module"] == "wire")
    assert wire["health"] == "ok"
    assert "ownership" not in wire


def test_state_merges_ownership_into_heal_modules() -> None:
    """482-A 引擎接线：归属字段叠加进同一份 modules 条目，health 不丢。"""
    client = _client(FakeSupervisor())
    res = client.get("/api/supervisor/state")
    assert res.status_code == 200
    payload = res.json()
    assert payload["configured"] is True
    assert payload["supervisor_pid"] == 4242
    assert payload["order"] == list(MODULE_ORDER)
    wire = next(item for item in payload["modules"] if item["module"] == "wire")
    # 482-C 契约字段仍在
    assert wire["health"] == "ok"
    assert wire["self_heal"] == {"attempts": 0, "max": 3, "status": "ok"}
    # 482-A 归属字段叠加
    assert wire["ownership"] == "managed"
    assert wire["evidence"] == "ledger"
    assert wire["listening"] is True


def test_state_rejects_remote_source() -> None:
    client = _client(None, peer=REMOTE)
    assert client.get("/api/supervisor/state").status_code == 403


def test_state_is_read_only() -> None:
    client = _client(None)
    assert client.post("/api/supervisor/state").status_code == 405


def test_selfheal_reset_route_survives_registration_order() -> None:
    """482-C 的 POST /state/{module}/reset 不被先注册的 GET 合并版挤掉。"""
    client = _client(None)
    res = client.post(
        "/api/supervisor/state/wire/reset",
        headers={"Origin": "http://127.0.0.1:5950"},
    )
    assert res.status_code == 200
    assert res.json()["module"] == "wire"
