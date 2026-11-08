"""`/api/supervisor/state` 的对外契约（QUOT-491 / 482-C）。

钉住：

| 场景 | 期望 |
|---|---|
| GET 状态面 | **200** 平面 JSON：每模块 health 三值 + restarts_5min + self_heal |
| 注册表里的模块 | 全部出现在 `modules[]`，快照是只读静态字段（无进程也不 500） |
| `POST .../reset` | 需写面来源证据（`X-LDST-Client: 1`），否则 **403** |
| reset 未知模块 | **404** |
| reset 成功 | `needs_human` 解除：窗口清零、health 回 `ok` |

引擎注入假件（`supervisor_engine=`），不碰真实进程与真实命令表。
"""

from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from ladderstock.supervisor.ledger import (
    HEALTH_NEEDS_HUMAN,
    HEALTH_RESTARTING,
    Ledger,
)
from ladderstock.supervisor.modules import MODULE_PORTS
from ladderstock.supervisor.selfheal import SelfHealEngine
from ladderstock.web.server import create_app
from ladderstock.web.supervisor_api import SUPERVISOR_DENIED, SUPERVISOR_WRITE_DENIED

WRITE_HEADERS = {"X-LDST-Client": "1"}
LOOPBACK = ("127.0.0.1", 54321)

MODULE_SNAPSHOT_KEYS = {
    "module",
    "ports",
    "pid",
    "health",
    "state",
    "restarts_5min",
    "restart_count",
    "stopping_by_user",
    "origin",
    "cmdline_hash",
    "last_exit_reason",
    "self_heal",
}


def _engine(root: Path) -> SelfHealEngine:
    ledger = Ledger(root / "supervisor" / "ledger.json", window_seconds=300.0)
    return SelfHealEngine(ledger, _IdleRestarter())


class _IdleRestarter:
    """假重拉通道：探测一律「活着」，重拉一律「无通道失败」。"""

    def is_alive(self, entry: object) -> bool:  # noqa: ARG002 — 假件不需要 entry
        return True

    def restart(self, module: str) -> object:
        from ladderstock.supervisor.selfheal import RestartOutcome

        return RestartOutcome(ok=False, detail="无可用重拉通道（重启命令表未配置该模块）")


def _client(root: Path) -> TestClient:
    app = create_app(data_root=root, dist_dir=root / "no-dist", supervisor_engine=_engine(root))
    return TestClient(app, client=LOOPBACK)


# -------------------------------------------------------------------- 读面


def test_state_is_flat_json_with_health_and_restarts_5min(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/api/supervisor/state")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["engine"]["max_restarts"] == 3
    assert body["engine"]["backoff"] == [5.0, 15.0, 45.0]

    by_module = {module["module"]: module for module in body["modules"]}
    assert set(MODULE_PORTS) <= set(by_module)
    for module in body["modules"]:
        assert set(module) == MODULE_SNAPSHOT_KEYS
        assert module["health"] in {"ok", "restarting", "needs_human"}
        assert module["restarts_5min"] == 0
        assert module["self_heal"] == {"attempts": 0, "max": 3, "status": "ok"}


def test_state_reflects_restarting_and_needs_human(tmp_path: Path) -> None:
    root = tmp_path
    now = time.time()  # 引擎默认 clock=time.time()，窗口时刻必须落在真时钟 5 分钟内
    ledger = Ledger(root / "supervisor" / "ledger.json", window_seconds=300.0)
    ledger.ensure("tickhub", ports=(8000, 8787))
    ledger.update("tickhub", pid=101, health=HEALTH_RESTARTING)
    ledger.get("tickhub").restart_window = [now - 30.0]
    ledger.ensure("senate", ports=(8765,))
    ledger.update("senate", pid=202, health=HEALTH_NEEDS_HUMAN, restart_count=3)
    ledger.get("senate").restart_window = [now - 30.0, now - 20.0, now - 10.0]
    ledger.save()

    body = _client(root).get("/api/supervisor/state").json()

    by_module = {module["module"]: module for module in body["modules"]}
    assert by_module["tickhub"]["health"] == "restarting"
    assert by_module["tickhub"]["self_heal"] == {"attempts": 1, "max": 3, "status": "healing"}
    assert by_module["tickhub"]["restarts_5min"] == 1
    assert by_module["senate"]["health"] == "needs_human"
    assert by_module["senate"]["self_heal"] == {"attempts": 3, "max": 3, "status": "gave_up"}


# -------------------------------------------------------------------- 写面


def test_reset_requires_source_evidence(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "supervisor" / "ledger.json", window_seconds=300.0)
    ledger.ensure("senate", ports=(8765,))
    ledger.update("senate", pid=202, health=HEALTH_NEEDS_HUMAN)

    client = _client(tmp_path)
    denied = client.post("/api/supervisor/state/senate/reset")
    assert denied.status_code == 403
    assert denied.json()["detail"] == SUPERVISOR_WRITE_DENIED

    remote = create_app(
        data_root=tmp_path, dist_dir=tmp_path / "no-dist", supervisor_engine=_engine(tmp_path)
    )
    remote_denied = TestClient(remote, client=("10.1.2.3", 54321)).post(
        "/api/supervisor/state/senate/reset", headers=WRITE_HEADERS
    )
    assert remote_denied.status_code == 403
    assert remote_denied.json()["detail"] == SUPERVISOR_DENIED


def test_reset_unknown_module_is_404(tmp_path: Path) -> None:
    client = _client(tmp_path)

    response = client.post("/api/supervisor/state/ghost/reset", headers=WRITE_HEADERS)

    assert response.status_code == 404
    assert "ghost" in response.json()["detail"]


def test_reset_clears_needs_human(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "supervisor" / "ledger.json", window_seconds=300.0)
    ledger.ensure("senate", ports=(8765,))
    ledger.update("senate", pid=202, health=HEALTH_NEEDS_HUMAN, restart_count=3)
    ledger.get("senate").restart_window = [1000.0, 1010.0, 1020.0]

    response = _client(tmp_path).post(
        "/api/supervisor/state/senate/reset", headers=WRITE_HEADERS
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["health"] == "ok"
    assert body["restarts_5min"] == 0

    ledger.load()  # 测试实例与引擎内账本是两个内存对象，落盘后重读才看到 reset 结果
    assert ledger.get("senate").health == "ok"
    assert ledger.get("senate").restart_window == []
