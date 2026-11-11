"""`/api/supervisor/{start,restart,stop}`（QUOT-523 单入口）的对外契约。

钉住：

| 场景 | 期望 |
|---|---|
| 写面来源证据 | 无 `X-LDST-Client` → **403**；远程来源 → **403** |
| 全停 | 必须 `confirm: true`，否则 **422**；确认后反向全停 |
| 单模块停 | `modules: [名]` 只停那些，失败进 `skipped` 而不是 500 |
| 全起 / 单模块起 / 重启 | 汇总每模块 `{ok, error}`，路由自身不抛 |
| 生命周期 | 默认附加模式：退出只停自愈巡检，**不碰子进程**；单入口模式才全起全停 |

引擎全部是假件：不起真进程、不读真实账本。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from ladderstock.supervisor.engine import SupervisorError
from ladderstock.supervisor.modules import MODULE_PORTS
from ladderstock.web.server import create_app
from ladderstock.web.supervisor_api import (
    OWN_PROCESSES_ENV,
    SUPERVISOR_DENIED,
    SUPERVISOR_WRITE_DENIED,
    own_processes,
)

WRITE_HEADERS = {"X-LDST-Client": "1"}
ALLOWED_ORIGIN = {"Origin": "http://127.0.0.1:5950"}
LOOPBACK = ("127.0.0.1", 54321)
REMOTE = ("10.1.2.3", 54321)


class FakeProcesses:
    """顺序启停引擎假件：记录每个动作，可指定某些模块失败。"""

    def __init__(self, *, failing: set[str] | None = None) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.failing = failing or set()

    def _view(self, name: str) -> dict[str, Any]:
        if name in self.failing:
            raise SupervisorError(f"{name} 的端口已被其它进程占用")
        return {"module": name, "ok": True}

    def start_all(self) -> dict[str, Any]:
        self.calls.append(("start_all", None))
        return {"ok": True, "modules": {name: {"ok": True, "error": None} for name in MODULE_PORTS}}

    def start_module(self, name: str) -> dict[str, Any]:
        self.calls.append(("start", name))
        return self._view(name)

    def stop_all(self) -> dict[str, Any]:
        self.calls.append(("stop_all", None))
        modules = {
            name: (
                {"ok": False, "error": "failed"}
                if name in self.failing
                else {"ok": True, "error": None}
            )
            for name in MODULE_PORTS
        }
        return {"ok": not self.failing, "modules": modules}

    def stop_owned(self) -> dict[str, Any]:
        """QUOT-527：应用退出只停自有子进程，adopted 保留。"""
        self.calls.append(("stop_owned", None))
        modules = {
            name: (
                {"ok": False, "error": "failed"}
                if name in self.failing
                else {"ok": True, "error": None}
            )
            for name in MODULE_PORTS
        }
        return {"ok": not self.failing, "modules": modules, "kept_adopted": []}

    def stop_module(self, name: str) -> dict[str, Any]:
        self.calls.append(("stop", name))
        return self._view(name)

    def restart_module(self, name: str) -> dict[str, Any]:
        self.calls.append(("restart", name))
        return self._view(name)


class FakeHeal:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    def snapshot(self) -> dict[str, Any]:
        return {"ok": True, "engine": None, "modules": []}

    def start(self) -> None:
        self.started = True

    def stop(self, timeout: float | None = None) -> None:
        self.stopped = True

    def reset(self, module: str) -> dict[str, Any] | None:
        return None


def _client(
    processes: FakeProcesses | None,
    *,
    heal: FakeHeal | None = None,
    own: bool = False,
    peer: tuple[str, int] = LOOPBACK,
) -> tuple[TestClient, FakeHeal]:
    heal = heal or FakeHeal()
    app = create_app(
        data_root=Path("."),
        dist_dir=Path("missing"),
        supervisor_engine=heal,
        supervisor_processes=processes,
        own_processes_on_lifecycle=own,
    )
    return TestClient(app, base_url="http://127.0.0.1:5950", client=peer), heal


# -------------------------------------------------------------------- 闸门


def test_stop_requires_write_source_evidence() -> None:
    client, _ = _client(FakeProcesses())

    denied = client.post("/api/supervisor/stop", json={"confirm": True})
    assert denied.status_code == 403
    assert denied.json()["detail"] == SUPERVISOR_WRITE_DENIED

    remote, _ = _client(FakeProcesses(), peer=REMOTE)
    remote_denied = remote.post(
        "/api/supervisor/stop", json={"confirm": True}, headers=WRITE_HEADERS
    )
    assert remote_denied.status_code == 403
    assert remote_denied.json()["detail"] == SUPERVISOR_DENIED


def test_stop_all_needs_explicit_confirmation() -> None:
    processes = FakeProcesses()
    client, _ = _client(processes)

    response = client.post("/api/supervisor/stop", json={}, headers=ALLOWED_ORIGIN)

    assert response.status_code == 422
    assert "confirm" in response.json()["detail"]
    assert processes.calls == []


# -------------------------------------------------------------------- 停止


def test_confirmed_stop_all_stops_every_module() -> None:
    processes = FakeProcesses()
    client, _ = _client(processes)

    response = client.post("/api/supervisor/stop", json={"confirm": True}, headers=ALLOWED_ORIGIN)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert set(body["stopped"]) == set(MODULE_PORTS)
    assert body["skipped"] == []
    assert processes.calls == [("stop_all", None)]


def test_stop_reports_failed_modules_as_skipped() -> None:
    processes = FakeProcesses(failing={"senate"})
    client, _ = _client(processes)

    body = client.post("/api/supervisor/stop", json={"confirm": True}, headers=WRITE_HEADERS).json()

    assert body["ok"] is False
    assert body["skipped"] == ["senate"]
    assert "senate" not in body["stopped"]


def test_stop_named_modules_does_not_need_confirmation() -> None:
    processes = FakeProcesses()
    client, _ = _client(processes)

    body = client.post(
        "/api/supervisor/stop", json={"modules": ["watch", "wire"]}, headers=WRITE_HEADERS
    ).json()

    assert body == {"ok": True, "stopped": ["watch", "wire"], "skipped": []}
    assert processes.calls == [("stop", "watch"), ("stop", "wire")]


# -------------------------------------------------------------------- 启动


def test_start_all_hits_every_module_in_order_by_delegation() -> None:
    processes = FakeProcesses()
    client, _ = _client(processes)

    response = client.post("/api/supervisor/start", json={}, headers=WRITE_HEADERS)

    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    assert set(response.json()["modules"]) == set(MODULE_PORTS)
    assert processes.calls == [("start_all", None)]


def test_start_single_module_keeps_going_after_a_failure() -> None:
    processes = FakeProcesses(failing={"financecoffer"})
    client, _ = _client(processes)

    body = client.post(
        "/api/supervisor/start", json={"modules": ["watch", "financecoffer"]}, headers=WRITE_HEADERS
    ).json()

    assert body["ok"] is False
    assert body["modules"]["watch"]["ok"] is True
    assert body["modules"]["financecoffer"]["ok"] is False
    assert "占用" in body["modules"]["financecoffer"]["error"]


def test_restart_requires_modules() -> None:
    processes = FakeProcesses()
    client, _ = _client(processes)

    response = client.post("/api/supervisor/restart", json={}, headers=WRITE_HEADERS)

    assert response.status_code == 422
    assert processes.calls == []


def test_restart_only_touches_the_named_module() -> None:
    processes = FakeProcesses()
    client, _ = _client(processes)

    body = client.post(
        "/api/supervisor/restart", json={"modules": ["watch"]}, headers=WRITE_HEADERS
    ).json()

    assert body == {"ok": True, "modules": {"watch": {"ok": True, "error": None}}}
    assert processes.calls == [("restart", "watch")]


# -------------------------------------------------------------------- 生命周期


def test_attached_mode_never_starts_or_stops_child_processes() -> None:
    processes = FakeProcesses()
    client, heal = _client(processes, own=False)

    with client:
        assert heal.started is True
        assert processes.calls == []

    assert heal.stopped is True
    assert processes.calls == []


def test_own_mode_starts_on_startup_and_stops_owned_on_shutdown() -> None:
    """QUOT-527：退出走 stop_owned（adopted peer 不碰），不是 stop_all。"""
    processes = FakeProcesses()
    client, heal = _client(processes, own=True)

    with client:
        assert heal.started is True
        assert processes.calls == [("start_all", None)]

    assert heal.stopped is True
    assert processes.calls == [("start_all", None), ("stop_owned", None)]


def test_no_process_engine_means_no_start_stop_routes() -> None:
    client, heal = _client(None)

    with client:
        assert heal.started is True

    response = client.post("/api/supervisor/stop", json={"confirm": True}, headers=WRITE_HEADERS)
    assert response.status_code == 405


def test_own_processes_flag_reads_the_environment(monkeypatch: Any) -> None:
    monkeypatch.delenv(OWN_PROCESSES_ENV, raising=False)
    assert own_processes() is False
    monkeypatch.setenv(OWN_PROCESSES_ENV, "0")
    assert own_processes() is False
    monkeypatch.setenv(OWN_PROCESSES_ENV, "1")
    assert own_processes() is True
    assert own_processes({}) is False
