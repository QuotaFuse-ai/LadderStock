"""`/api/supervisor/events` 与确认后一键重启的对外契约（QUOT-556）。

钉住：

| 场景 | 期望 |
|---|---|
| GET /events | 回环只读 200；`since`/`module`/`limit` 过滤；远程 **403** |
| 未接事件总线 | `configured: false` 降级，不 500 |
| /restart 认领态 peer | 无 `confirm: true` → **422**，引擎一次都不被调 |
| /restart 确认后 | 200 + 自愈失败窗口清零（526 重新看护新进程） |
| /restart 自有模块 | 不需要 confirm（526 语义不变） |
| 旧账本 adopted 无标记 | 失败关闭仍要 confirm（DEC-079 口径） |
| /state | 顶层 `events_latest` + 每模块 `adopted_peer` |

进程引擎是假件（带真账本），不起真进程；事件总线 / 自愈引擎用真实现。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from ladderstock.supervisor.engine import SupervisorError
from ladderstock.supervisor.health_events import (
    EVENT_CRASH,
    EVENT_RECOVERED,
    HealthEventBus,
)
from ladderstock.supervisor.ledger import HEALTH_NEEDS_HUMAN, Ledger, ModuleRecord
from ladderstock.supervisor.selfheal import RestartOutcome, SelfHealEngine
from ladderstock.web.server import create_app
from ladderstock.web.supervisor_api import SUPERVISOR_DENIED

WRITE_HEADERS = {"X-LDST-Client": "1"}
LOOPBACK = ("127.0.0.1", 54321)
REMOTE = ("10.1.2.3", 54321)

EVENT_KEYS = {
    "seq",
    "ts",
    "module",
    "event",
    "health",
    "state",
    "reason",
    "pid",
    "ports",
    "origin",
    "adopted_peer",
}


class FakeHeal:
    """自愈引擎假件：可选挂真事件总线（/events 只读 bus）。"""

    def __init__(self, bus: HealthEventBus | None = None) -> None:
        if bus is not None:
            self.event_bus = bus

    def snapshot(self) -> dict[str, Any]:
        return {"ok": True, "engine": None, "modules": []}

    def start(self) -> None:
        pass

    def stop(self, timeout: float | None = None) -> None:
        pass

    def reset(self, module: str) -> dict[str, Any] | None:
        return None


class FakeProcesses:
    """顺序启停假件：带真账本（confirm 闸门与 reset_heal 都读它）。"""

    def __init__(self, ledger: Ledger | None, *, failing: set[str] | None = None) -> None:
        self.ledger = ledger
        self.failing = failing or set()
        self.calls: list[tuple[str, str]] = []

    def restart_module(self, name: str) -> dict[str, Any]:
        self.calls.append(("restart", name))
        if name in self.failing:
            raise SupervisorError(f"{name} 的端口已被其它进程占用")
        return {"module": name, "ok": True, "record": {"module": name}}

    def state(self) -> dict[str, Any]:
        """合并 /state 视图（482-A）会读归属面；假件给空视图即可。"""
        return {"supervisor_pid": None, "modules": {}}

    def start_all(self) -> dict[str, Any]:
        return {"ok": True, "modules": {}}

    def stop_all(self) -> dict[str, Any]:
        return {"ok": True, "modules": {}}

    def stop_owned(self) -> dict[str, Any]:
        return {"ok": True, "modules": {}}


def _client(
    root: Path,
    *,
    heal: Any,
    processes: Any = None,
    peer: tuple[str, int] = LOOPBACK,
) -> TestClient:
    app = create_app(
        data_root=root,
        dist_dir=root / "no-dist",
        supervisor_engine=heal,
        supervisor_processes=processes,
    )
    return TestClient(app, base_url="http://127.0.0.1:5950", client=peer)


def _adopted_record(module: str = "wire", **overrides: Any) -> ModuleRecord:
    fields: dict[str, Any] = {
        "ports": [8790, 8791],
        "pid": 101,
        "origin": "adopted",
        "adopted_peer": True,
        "state": "running",
    }
    fields.update(overrides)
    return ModuleRecord(module=module, **fields)


# -------------------------------------------------------------------- /events


def test_events_without_bus_degrades_to_unconfigured(tmp_path: Path) -> None:
    response = _client(tmp_path, heal=FakeHeal()).get("/api/supervisor/events")

    assert response.status_code == 200, response.text
    assert response.json() == {"ok": True, "configured": False, "latest": 0, "events": []}


def test_events_since_module_limit_filters(tmp_path: Path) -> None:
    bus = HealthEventBus()
    bus.publish(module="wire", event=EVENT_CRASH, health="ok", state="down", reason="r1")
    bus.publish(module="senate", event=EVENT_CRASH, health="ok", state="down", reason="r2")
    bus.publish(
        module="wire", event=EVENT_RECOVERED, health="ok", state="running", reason="r3", pid=102
    )
    client = _client(tmp_path, heal=FakeHeal(bus))

    body = client.get("/api/supervisor/events").json()
    assert body["ok"] is True and body["configured"] is True
    assert body["latest"] == 3
    assert [item["seq"] for item in body["events"]] == [1, 2, 3]
    assert set(body["events"][0]) == EVENT_KEYS
    assert body["events"][0]["reason"] == "r1"

    since_body = client.get("/api/supervisor/events?since=1").json()
    assert [item["seq"] for item in since_body["events"]] == [2, 3]
    module_body = client.get("/api/supervisor/events?module=wire").json()
    assert [item["seq"] for item in module_body["events"]] == [1, 3]
    limit_body = client.get("/api/supervisor/events?limit=1").json()
    assert [item["seq"] for item in limit_body["events"]] == [1]
    assert client.get("/api/supervisor/events?since=3").json()["events"] == []
    negative_body = client.get("/api/supervisor/events?since=-5").json()
    assert [item["seq"] for item in negative_body["events"]] == [1, 2, 3]


def test_events_is_loopback_readonly(tmp_path: Path) -> None:
    bus = HealthEventBus()
    client = _client(tmp_path, heal=FakeHeal(bus))
    assert client.get("/api/supervisor/events").status_code == 200  # 读面不需写证据

    remote = _client(tmp_path, heal=FakeHeal(bus), peer=REMOTE)
    denied = remote.get("/api/supervisor/events")
    assert denied.status_code == 403
    assert denied.json()["detail"] == SUPERVISOR_DENIED


# -------------------------------------------------------------------- /restart


def test_restart_adopted_peer_requires_explicit_confirm(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "supervisor" / "ledger.json")
    ledger.update(_adopted_record())
    processes = FakeProcesses(ledger)
    client = _client(tmp_path, heal=FakeHeal(), processes=processes)

    denied = client.post(
        "/api/supervisor/restart", json={"modules": ["wire"]}, headers=WRITE_HEADERS
    )
    assert denied.status_code == 422
    assert "confirm" in denied.json()["detail"]
    assert "wire" in denied.json()["detail"]
    assert processes.calls == [], "未确认就调了引擎重启"

    weak = client.post(
        "/api/supervisor/restart",
        json={"modules": ["wire"], "confirm": "true"},
        headers=WRITE_HEADERS,
    )
    assert weak.status_code == 422, "confirm 必须是布尔 true，字符串不算确认"
    assert processes.calls == []


def test_confirmed_restart_clears_selfheal_window(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "supervisor" / "ledger.json")
    now = time.time()
    ledger.update(
        _adopted_record(
            health=HEALTH_NEEDS_HUMAN,
            restart_count=3,
            restart_window=[now - 30, now - 20, now - 10],
        )
    )
    processes = FakeProcesses(ledger)
    client = _client(tmp_path, heal=FakeHeal(), processes=processes)

    response = client.post(
        "/api/supervisor/restart",
        json={"modules": ["wire"], "confirm": True},
        headers=WRITE_HEADERS,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["modules"]["wire"] == {"ok": True, "error": None}
    assert processes.calls == [("restart", "wire")]
    record = ledger.record("wire")
    assert record.restart_window == [], "确认重启后自愈失败窗口没有清零"
    assert record.health == "ok"


def test_restart_owned_module_needs_no_confirm(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "supervisor" / "ledger.json")
    ledger.update(_adopted_record(origin="spawned", adopted_peer=False))
    processes = FakeProcesses(ledger)
    client = _client(tmp_path, heal=FakeHeal(), processes=processes)

    response = client.post(
        "/api/supervisor/restart", json={"modules": ["wire"]}, headers=WRITE_HEADERS
    )

    assert response.status_code == 200, response.text
    assert processes.calls == [("restart", "wire")]


def test_restart_legacy_adopted_without_flag_still_gated(tmp_path: Path) -> None:
    """DEC-079 失败关闭：旧账本 origin=adopted 即使标记滞后也按外部 peer 要确认。"""
    ledger = Ledger(tmp_path / "supervisor" / "ledger.json")
    ledger.update(_adopted_record(adopted_peer=False))
    processes = FakeProcesses(ledger)
    client = _client(tmp_path, heal=FakeHeal(), processes=processes)

    denied = client.post(
        "/api/supervisor/restart", json={"modules": ["wire"]}, headers=WRITE_HEADERS
    )

    assert denied.status_code == 422
    assert processes.calls == []


def test_restart_mixed_modules_gate_names_only_adopted(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "supervisor" / "ledger.json")
    ledger.update(_adopted_record())
    ledger.update(
        _adopted_record(module="senate", ports=[8765], origin="spawned", adopted_peer=False)
    )
    processes = FakeProcesses(ledger)
    client = _client(tmp_path, heal=FakeHeal(), processes=processes)

    denied = client.post(
        "/api/supervisor/restart",
        json={"modules": ["wire", "senate"]},
        headers=WRITE_HEADERS,
    )
    assert denied.status_code == 422
    assert "wire" in denied.json()["detail"]
    assert "senate" not in denied.json()["detail"]
    assert processes.calls == []

    confirmed = client.post(
        "/api/supervisor/restart",
        json={"modules": ["wire", "senate"], "confirm": True},
        headers=WRITE_HEADERS,
    )
    assert confirmed.status_code == 200, confirmed.text
    assert processes.calls == [("restart", "wire"), ("restart", "senate")]


def test_restart_engine_failure_reported_per_module_and_window_kept(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "supervisor" / "ledger.json")
    now = time.time()
    ledger.update(_adopted_record(restart_window=[now - 10]))
    processes = FakeProcesses(ledger, failing={"wire"})
    client = _client(tmp_path, heal=FakeHeal(), processes=processes)

    response = client.post(
        "/api/supervisor/restart",
        json={"modules": ["wire"], "confirm": True},
        headers=WRITE_HEADERS,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is False
    assert body["modules"]["wire"]["ok"] is False
    assert "端口" in body["modules"]["wire"]["error"]
    assert ledger.record("wire").restart_window, "重启失败却清了自愈窗口"


def test_restart_fake_without_ledger_keeps_legacy_behavior(tmp_path: Path) -> None:
    """读不到账本的假件 / 未接线装配不加闸：引擎层停止边界仍兜底。"""
    processes = FakeProcesses(None)
    client = _client(tmp_path, heal=FakeHeal(), processes=processes)

    response = client.post(
        "/api/supervisor/restart", json={"modules": ["wire"]}, headers=WRITE_HEADERS
    )

    assert response.status_code == 200, response.text
    assert processes.calls == [("restart", "wire")]


# -------------------------------------------------------------------- /state


class _DeadRestarter:
    def is_alive(self, entry: ModuleRecord) -> bool:
        return False

    def restart(self, module: str) -> RestartOutcome:
        return RestartOutcome(ok=False, detail="测试假件")


def test_state_exposes_events_latest_and_adopted_peer(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "supervisor" / "ledger.json")
    ledger.update(_adopted_record())
    bus = HealthEventBus()
    heal = SelfHealEngine(ledger, _DeadRestarter(), event_bus=bus)
    client = _client(tmp_path, heal=heal, processes=FakeProcesses(ledger))

    body = client.get("/api/supervisor/state").json()
    assert body["events_latest"] == 0
    by_module = {item["module"]: item for item in body["modules"]}
    assert by_module["wire"]["adopted_peer"] is True

    bus.publish(
        module="wire", event=EVENT_CRASH, health="ok", state="down", reason="r", adopted_peer=True
    )
    body = client.get("/api/supervisor/state").json()
    assert body["events_latest"] == 1
    events = client.get("/api/supervisor/events?since=0").json()
    assert events["latest"] == 1
    assert events["events"][0]["module"] == "wire"
    assert events["events"][0]["adopted_peer"] is True