"""调度器单例与自退（QUOT-522）。

同一数据目录只允许一个调度器执行任务表：`ldst run` 内置调度、页面自启的
`ldst schedule run`、手动开的调度进程同时在时，其余待命，否则每个定时任务
（定时研究 / 跨轮任务推进）都会各调一遍模型。

页面自启的 `schedule run` 看退出标记、以及拉起它的看板是否还活着自退：
Windows 子进程不随父进程退出，不自查就会留成孤儿，下次启动再叠一个调度器。
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from ladderstock.cli import schedule as schedule_cli
from ladderstock.data import scheduler_lock
from ladderstock.supervisor.page_boot import (
    CHILD_STOP_ARG_ENV,
    OWNER_PID_ENV,
    owner_gone,
    owner_token,
)
from ladderstock.web.schedule_runner import ScheduleRunner

# --------------------------------------------------------------------------- #
# 锁本身
# --------------------------------------------------------------------------- #


def test_second_acquire_is_refused_until_release(tmp_path: Path) -> None:
    first = scheduler_lock.try_acquire(tmp_path, holder="first")
    assert first is not None
    try:
        assert scheduler_lock.try_acquire(tmp_path, holder="second") is None
        assert "first" in scheduler_lock.current_holder(tmp_path)
    finally:
        first.release()
    again = scheduler_lock.try_acquire(tmp_path, holder="again")
    assert again is not None
    again.release()


def test_lock_is_released_when_holder_process_dies(tmp_path: Path) -> None:
    """持有者被杀（不走 release）：内核回收锁，下一位能接手。"""
    code = (
        "import sys, time\n"
        "from pathlib import Path\n"
        "from ladderstock.data import scheduler_lock\n"
        "lease = scheduler_lock.try_acquire(Path(sys.argv[1]), holder='child')\n"
        "print('held' if lease else 'busy', flush=True)\n"
        "time.sleep(60)\n"
    )
    src = Path(__file__).resolve().parents[3] / "src"
    proc = subprocess.Popen(  # noqa: S603 - 本测试自己给定的 argv
        [sys.executable, "-c", code, str(tmp_path)],
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": str(src)},
    )
    try:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "held"
        assert scheduler_lock.try_acquire(tmp_path, holder="me") is None
    finally:
        proc.kill()
        proc.wait(10)
    lease = scheduler_lock.try_acquire(tmp_path, holder="me")
    assert lease is not None
    lease.release()


def test_wait_acquire_gives_up_on_stop(tmp_path: Path) -> None:
    held = scheduler_lock.try_acquire(tmp_path, holder="other")
    assert held is not None
    seen: list[str] = []
    try:
        lease = scheduler_lock.wait_acquire(
            tmp_path,
            holder="me",
            should_stop=lambda: bool(seen),
            poll_sec=0.05,
            on_standby=seen.append,
        )
    finally:
        held.release()
    assert lease is None
    assert len(seen) == 1 and "other" in seen[0]


# --------------------------------------------------------------------------- #
# 看板内置调度：锁被占就待命，对方退出后接管
# --------------------------------------------------------------------------- #


def test_web_runner_stands_by_then_takes_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scheduler_lock, "STANDBY_POLL_SEC", 0.05)
    started = threading.Event()

    def fake_start(self: ScheduleRunner) -> dict[str, Any]:
        self._scheduler = object()
        started.set()
        return self.status()

    monkeypatch.setattr(ScheduleRunner, "_start_scheduler", fake_start)
    other = scheduler_lock.try_acquire(tmp_path, holder="ldst schedule run")
    assert other is not None

    runner = ScheduleRunner(data_root=tmp_path, kinds=())
    try:
        state = runner.start()
        assert state["running"] is False
        assert state["standby"] is True
        assert "ldst schedule run" in str(state["standby_holder"])
        assert not started.is_set()

        other.release()
        assert started.wait(5), "持锁方退出后内置调度没有接管"
        assert runner.status()["standby"] is False
        assert scheduler_lock.try_acquire(tmp_path, holder="third") is None
    finally:
        runner._scheduler = None
        runner.shutdown()
        other.release()


# --------------------------------------------------------------------------- #
# `schedule run` 长驻：锁被占待命；退出标记 / 看板消失时自退
# --------------------------------------------------------------------------- #


class _FakeBlockingScheduler:
    instances: list[_FakeBlockingScheduler] = []

    def __init__(self, **_: Any) -> None:
        self.jobs: dict[str, Any] = {}
        self.stopped = threading.Event()
        _FakeBlockingScheduler.instances.append(self)

    def add_job(self, func: Any, *_: Any, id: str, **__: Any) -> None:
        self.jobs[id] = func

    def start(self) -> None:
        watch = self.jobs["ldst-exit-watch"]
        while not self.stopped.wait(0.02):
            watch()

    def shutdown(self, wait: bool = True) -> None:
        self.stopped.set()


@pytest.fixture()
def daemon_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import apscheduler.schedulers.blocking as blocking

    _FakeBlockingScheduler.instances.clear()
    monkeypatch.setattr(blocking, "BlockingScheduler", _FakeBlockingScheduler)
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(scheduler_lock, "STANDBY_POLL_SEC", 0.05)
    marker = tmp_path / "scheduler.stop"
    monkeypatch.setenv(CHILD_STOP_ARG_ENV, str(marker))
    monkeypatch.delenv(OWNER_PID_ENV, raising=False)
    return marker


def _run_daemon_in_thread() -> threading.Thread:
    table = {"scheduler": {"timezone": "Asia/Shanghai"}}
    thread = threading.Thread(target=schedule_cli.start_daemon, args=(table, []), daemon=True)
    thread.start()
    return thread


def test_daemon_exits_on_stop_marker_and_releases_lock(daemon_env: Path, tmp_path: Path) -> None:
    thread = _run_daemon_in_thread()
    try:
        for _ in range(250):
            if _FakeBlockingScheduler.instances:
                break
            threading.Event().wait(0.02)
        assert _FakeBlockingScheduler.instances, "调度器没启动"
        assert scheduler_lock.try_acquire(tmp_path, holder="other") is None
        assert thread.is_alive(), "标记出现前不该退出"

        daemon_env.write_text("stop", encoding="utf-8")
        thread.join(10)
        assert not thread.is_alive(), "退出标记没让 schedule run 自行退出"
    finally:
        daemon_env.write_text("stop", encoding="utf-8")
    lease = scheduler_lock.try_acquire(tmp_path, holder="other")
    assert lease is not None, "退出后锁没释放"
    lease.release()


def test_daemon_stands_by_while_another_scheduler_runs(daemon_env: Path, tmp_path: Path) -> None:
    other = scheduler_lock.try_acquire(tmp_path, holder="ldst run（看板内置调度）")
    assert other is not None
    thread = _run_daemon_in_thread()
    try:
        threading.Event().wait(0.3)
        assert thread.is_alive(), "待命方不该退出（否则自愈会反复重拉）"
        assert not _FakeBlockingScheduler.instances, "锁被占时不该挂任务表"
    finally:
        daemon_env.write_text("stop", encoding="utf-8")
        thread.join(10)
        other.release()
    assert not thread.is_alive()
    assert not _FakeBlockingScheduler.instances


def test_daemon_exits_when_owner_is_gone(daemon_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    proc = subprocess.Popen(  # noqa: S603 - 本测试自己给定的 argv
        [sys.executable, "-c", "import time; time.sleep(60)"]
    )
    monkeypatch.setenv(OWNER_PID_ENV, owner_token(proc.pid))
    thread = _run_daemon_in_thread()
    try:
        threading.Event().wait(0.3)
        assert thread.is_alive()
        proc.kill()
        proc.wait(10)
        thread.join(10)
        assert not thread.is_alive(), "看板消失后 schedule run 没有自退"
    finally:
        proc.kill()
        daemon_env.write_text("stop", encoding="utf-8")


def test_owner_gone_detects_pid_reuse() -> None:
    pytest.importorskip("psutil")
    token = owner_token()
    assert owner_gone(token) is False
    assert owner_gone(None) is False
    pid = token.partition("@")[0]
    assert owner_gone(f"{pid}@1.0") is True
