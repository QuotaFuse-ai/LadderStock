"""自愈冷启动生命周期验收（QUOT-526）：全停后由 bundle 命令表拉起。

被测对象是**生产装配**（`build_supervisor_engines` + `AdapterRestarter`），不是
直接调适配器：

1. 单入口模式下 `start_all()` 全起（既有匹配监听者按 adopted 认领，不 spawn）；
2. **模拟整机断电**：绕过 `stop_all`，直接整树强杀本轮 spawn 的进程（不写主动停
   意图），等端口释放、`reconcile()` 记账为 stopped；
3. `SelfHealEngine.tick()` 走 `AdapterRestarter`（bundle 自带命令表）把七个模块
   全拉回来——不依赖 `restart-commands.json`；
4. 认领的 adopted 进程 PID 全程不变（peer adopt-only / 外部进程不碰）；
5. 收尾 `stop_all()` 只停自己拉起的面。

**默认不执行**：本文件会真起真停 8000 等模块端口，只在用户批准的、已全停的
部署机上显式开启：

    LDST_SELFHEAL_COLD_START=1 python -m pytest tests/ops/test_lifecycle_selfheal_cold_start.py

平台门仅 Windows（CI 是 ubuntu）；宿主上有端口在听但不属于对应模块时整例 skip
（外部占口不碰，也不在它上面做冷启动验收）。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from ladderstock.supervisor.adapters import MODULE_ORDER, MODULE_PORTS
from ladderstock.supervisor.module_adapters import build_default_adapters, default_host_pivot
from ladderstock.supervisor.restart import AdapterRestarter
from ladderstock.supervisor.selfheal import SelfHealEngine
from ladderstock.web.supervisor_api import OWN_PROCESSES_ENV, build_supervisor_engines
from ops.test_lifecycle_cold_start import POLL_INTERVAL_SEC, POLL_TIMEOUT_SEC, WINDOWS_ONLY
from ops.test_lifecycle_single_entry import _existing_peer_dirs

pytestmark = [
    WINDOWS_ONLY,
    pytest.mark.skipif(
        (os.environ.get("LDST_SELFHEAL_COLD_START") or "").strip() != "1",
        reason="真实冷启动会起停模块端口（8000 等）；仅在批准的已全停部署机上显式开启",
    ),
]


def test_full_outage_then_selfheal_cold_start(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    with _existing_peer_dirs(repo_root):
        _run(tmp_path)


def _run(root: Path) -> None:
    env = {**os.environ, OWN_PROCESSES_ENV: "1"}
    engines = build_supervisor_engines(root, panel_url="http://127.0.0.1:5950/", env=env)
    adapters = build_default_adapters(env=env, log_dir=root / "logs", evidence_cache_sec=0.0)
    host = default_host_pivot()

    adopted: dict[str, int] = {}
    for name in MODULE_ORDER:
        for evidence in adapters[name].identify().ports:
            if evidence.listening and not evidence.matched:
                pytest.skip(
                    f"{name} 端口 {evidence.port} 在听但持有者不属于本模块"
                    f"（pid={evidence.pid}）；冷启动验收要求干净宿主"
                )
            if evidence.listening and evidence.matched and evidence.pid is not None:
                adopted.setdefault(name, int(evidence.pid))

    try:
        started = engines.processes.start_all()
        assert started["ok"] is True, f"start_all 未全起：{started}"
        spawned = {
            name: engines.ledger.record(name).pid
            for name in MODULE_ORDER
            if name not in adopted and engines.ledger.record(name) is not None
        }
        assert all(pid is not None for pid in spawned.values()), spawned

        # 整机断电：直接强杀，不写 stopping_by_user（否则自愈按 D4 不重拉）
        for pid in spawned.values():
            host.terminate_tree(int(pid), force=True)
        _wait_quiet(adapters)
        engines.processes.reconcile()
        for name in spawned:
            record = engines.ledger.record(name)
            assert record is not None and record.state == "stopped", name

        heal = SelfHealEngine(
            engines.ledger,
            AdapterRestarter(engines.processes),
            sleeper=lambda _seconds: None,
            modules=MODULE_ORDER,
            cold_start=True,
            module_ports=MODULE_PORTS,
        )
        heal.tick()
        _wait_quiet(adapters, expect_listening=True)
        for name in MODULE_ORDER:
            record = engines.ledger.record(name)
            assert record is not None and record.pid, f"{name} 冷启动后账本没有 pid"
            assert engines.processes.state()["modules"][name]["ownership"] == "managed", name

        # 认领的既有进程一个都不能被换掉（peer adopt-only / 外部不碰）
        for name, pid in adopted.items():
            for evidence in adapters[name].identify().ports:
                if evidence.listening:
                    assert evidence.pid == pid, f"adopted {name} 的 PID 被改动"
    finally:
        engines.processes.stop_all()


def _wait_quiet(adapters, *, expect_listening: bool = False) -> None:
    """轮询直到所有模块端口都空闲（或都在听），超时即 fail。"""
    deadline = time.monotonic() + POLL_TIMEOUT_SEC
    while True:
        ports = [
            evidence.listening
            for name in MODULE_ORDER
            for evidence in adapters[name].identify().ports
        ]
        if all(ports) if expect_listening else not any(ports):
            return
        if time.monotonic() >= deadline:
            pytest.fail(
                f"端口状态未收敛（expect_listening={expect_listening}，ports={ports}）"
            )
        time.sleep(POLL_INTERVAL_SEC)
