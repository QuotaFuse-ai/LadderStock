"""QUOT-522 契约：看板打开（startup）即触发页面自启 + `/api/supervisor/page-boot`。

不碰真宿主：`create_app(page_boot=FakeBoot())` 注入假件，断言
- 应用启动钩子调 `ensure()`，关闭钩子调 `stop_children()`；
- GET 只读报告（`configured / enabled / last_report`），不触发自启；
- POST 幂等再跑一次（回环同源写入），远程来源 403；
- 未接线（`boot=None`）时 GET 降级、POST 503。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ladderstock.web.control import ControlPlane
from ladderstock.web.server import create_app
from ladderstock.web.supervisor_api import build_page_boot_router

LOOPBACK = ("127.0.0.1", 54321)
REMOTE = ("10.1.2.3", 54321)
ORIGIN = {"Origin": "http://127.0.0.1:5950"}


class FakeBoot:
    def __init__(self) -> None:
        self.ensures: list[str] = []
        self.stops = 0
        self.enabled = True
        self._last: dict[str, Any] | None = None

    def ensure(self, *, trigger: str = "page-open") -> dict[str, Any]:
        self.ensures.append(trigger)
        self._last = {
            "ok": True,
            "trigger": trigger,
            "enabled": True,
            "actions": {"watch_guard": "spawned", "scheduler": "spawned"},
            "children": {},
            "peers": {},
        }
        return self._last

    def stop_children(self) -> dict[str, str]:
        self.stops += 1
        return {"watch_guard": "stopped", "scheduler": "stopped"}

    def last_report(self) -> dict[str, Any] | None:
        return self._last


def _client(boot: FakeBoot | None, peer: tuple[str, int] = LOOPBACK) -> TestClient:
    plane = ControlPlane(data_root=Path("."), supervisor=None)
    app = create_app(
        data_root=Path("."),
        dist_dir=Path("missing"),
        control_plane=plane,
        page_boot=boot,
    )
    return TestClient(app, base_url="http://127.0.0.1:5950", client=peer)


def test_startup_opens_page_boot_and_shutdown_stops_owned_children() -> None:
    boot = FakeBoot()
    with _client(boot) as client:
        assert boot.ensures == ["page-open"]
        assert client.get("/api/health").status_code == 200
    assert boot.stops == 1


def test_get_reports_last_boot_without_triggering_again() -> None:
    boot = FakeBoot()
    with _client(boot) as client:
        payload = client.get("/api/supervisor/page-boot").json()
        assert payload["ok"] is True
        assert payload["configured"] is True
        assert payload["enabled"] is True
        assert payload["last_report"]["trigger"] == "page-open"
    assert boot.ensures == ["page-open"]


def test_post_reruns_ensure_idempotently() -> None:
    boot = FakeBoot()
    with _client(boot) as client:
        res = client.post("/api/supervisor/page-boot", headers=ORIGIN)
        assert res.status_code == 200
        assert res.json()["trigger"] == "api-request"
    assert boot.ensures == ["page-open", "api-request"]


def test_write_surface_rejects_remote_source() -> None:
    boot = FakeBoot()
    with _client(boot, peer=REMOTE) as client:
        assert client.post("/api/supervisor/page-boot", headers=ORIGIN).status_code == 403
    assert boot.ensures == ["page-open"]


def test_unconfigured_router_degrades() -> None:
    """路由层 `boot=None`（未接线）：GET 报 configured=False，POST 503，不起子进程。"""
    app = FastAPI()
    app.include_router(build_page_boot_router(None))
    client = TestClient(app, base_url="http://127.0.0.1:5950", client=LOOPBACK)

    payload = client.get("/api/supervisor/page-boot").json()
    assert payload == {
        "ok": True,
        "configured": False,
        "enabled": False,
        "last_report": None,
    }
    assert client.post("/api/supervisor/page-boot", headers=ORIGIN).status_code == 503
