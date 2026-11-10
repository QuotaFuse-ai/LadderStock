"""页面打开即自启的自有子进程生命周期验收（QUOT-522）：真实宿主、真实进程。

与 tests/unit/supervisor/test_page_boot.py（全 fake）互补：本文件在部署宿主上
用真 `WindowsHostPivot` 跑 `PageBoot.ensure()`，断言

1. 打开页面后 watch 守护 / 取数调度器**真的在跑**（进程存活 + 账本 pid 一致），
   不依赖任何 CLI 或计划任务；
2. 二次打开幂等：pid 不变，`already_running`，不重复拉起；
3. peer **adopt-only**：已在监听的 peer PID 与页面打开前完全一致；
   关闭（`stop_children()`）只停自有子进程，peer 原样存活。

平台门：仅 Windows（CI ubuntu 整模块 skip，沿用 QUOT-514 先例）。测试用独立的
临时 `data_root` / 账本 / 日志目录，不读也不写生产 `data/`，不占业务端口。
startup 时当前没有分钟样本是正常形态（守护进入退避等待），本单只验生命周期。
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from ladderstock.supervisor.ledger import Ledger
from ladderstock.supervisor.module_adapters import build_default_adapters
from ladderstock.supervisor.page_boot import (
    PAGE_AUTOSTART_ENV,
    SCHEDULER_MODULE,
    SELF_MODULES,
    STATE_ALREADY_RUNNING,
    WATCH_GUARD_MODULE,
    PageBoot,
    WatchScanner,
)

IS_WINDOWS = os.name == "nt"
WINDOWS_ONLY = pytest.mark.skipif(
    not IS_WINDOWS,
    reason="真实宿主验收只在 Windows 部署机执行（CI ubuntu 无 WindowsHostPivot）",
)

pytestmark = WINDOWS_ONLY

POLL_TIMEOUT_SEC = 30.0
POLL_INTERVAL_SEC = 0.5


def _alive(pid: int) -> bool:
    out = subprocess.run(  # noqa: S603, S607
        ["tasklist", "/FI", f"PID eq {int(pid)}", "/NH"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    ).stdout
    return str(pid) in out


def _await_dead(pid: int, label: str) -> None:
    deadline = time.monotonic() + POLL_TIMEOUT_SEC
    while time.monotonic() < deadline:
        if not _alive(pid):
            return
        time.sleep(POLL_INTERVAL_SEC)
    pytest.fail(f"期望 {label} 已停 / 实际：PID={pid} 仍存活")


def _peer_baseline(boot: PageBoot) -> dict[str, int]:
    """页面打开前已在监听的 peer → 当前持有 PID（adopt-only 的对照基线）。"""
    baseline: dict[str, int] = {}
    for name, adapter in boot.adapters.items():
        ident = adapter.identify()
        for ev in ident.ports:
            if ev.listening and ev.matched and ev.pid is not None:
                baseline[name] = int(ev.pid)
                print(f"[baseline] {name} port={ev.port} pid={ev.pid}")
                break
    return baseline


@pytest.fixture()
def boot(tmp_path: Path) -> PageBoot:
    log_dir = tmp_path / "logs"
    return PageBoot(
        data_root=tmp_path / "data",
        ledger=Ledger(tmp_path / "supervisor" / "ledger.json"),
        adapters=dict(build_default_adapters(log_dir=log_dir)),
        scanner=WatchScanner(tmp_path / "data"),
        run_cwd=Path.cwd(),
        log_dir=log_dir,
        env={**os.environ, PAGE_AUTOSTART_ENV: "1"},
        tick_sec=60.0,
    )


def test_page_open_starts_owned_children_and_leaves_peers_untouched(boot: PageBoot) -> None:
    baseline = _peer_baseline(boot)
    try:
        first = boot.ensure()
        second = boot.ensure(trigger="page-refresh")

        guard_pid = int(first["children"][WATCH_GUARD_MODULE]["pid"])
        scheduler_pid = int(first["children"][SCHEDULER_MODULE]["pid"])
        assert _alive(guard_pid), f"watch 守护未在跑 PID={guard_pid}：{first['children']}"
        assert _alive(scheduler_pid), f"取数调度器未在跑 PID={scheduler_pid}"

        assert second["actions"][WATCH_GUARD_MODULE] == STATE_ALREADY_RUNNING
        assert second["actions"][SCHEDULER_MODULE] == STATE_ALREADY_RUNNING
        assert second["children"][WATCH_GUARD_MODULE]["pid"] == guard_pid
        assert second["children"][SCHEDULER_MODULE]["pid"] == scheduler_pid

        guard = boot.ledger.record(WATCH_GUARD_MODULE)
        assert guard is not None and guard.pid == guard_pid and guard.origin == "spawned"

        for name, pid in baseline.items():
            record = boot.ledger.record(name)
            assert record is not None and record.pid == pid, f"peer {name} PID 被改动"
            assert _alive(pid), f"peer {name} PID={pid} 不再存活"
        print(f"[page-open] own children running: guard={guard_pid} scheduler={scheduler_pid}")
    finally:
        outcomes = boot.stop_children()
        print(f"[shutdown] {outcomes}")

    for name in SELF_MODULES:
        record = boot.ledger.record(name)
        if record is None or record.origin != "spawned" or record.pid is None:
            continue
        _await_dead(int(record.pid), f"{name} 自有子进程")
    for name, pid in baseline.items():
        assert _alive(pid), f"peer {name} PID={pid} 被关闭流程误停"
