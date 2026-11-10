"""看板拉起的 `schedule run` 看退出标记自退（QUOT-522）。

Windows 上无控制台的 python 收不到优雅终止，所以关闭看板时由看板写标记，
`schedule run` 在调度器启动时挂一个 2 秒检查，标记出现就 shutdown 自行退出。
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from pathlib import Path
from time import sleep as _real_sleep
from typing import Any

import pytest
from apscheduler.schedulers.base import BaseScheduler

from ladderstock.cli.main import _stop_when_marked
from ladderstock.supervisor.page_boot import CHILD_STOP_ARG_ENV


class _FakeScheduler:
    """只模拟 `cli/schedule.start_daemon` 用到的 APScheduler 子集。"""

    def __init__(self, marker: Path) -> None:
        self.marker = marker
        self.stopping = False
        self.jobs: list[tuple[str, float]] = []
        self.started = threading.Event()
        self.stopped = threading.Event()

    def add_job(self, func: Any, trigger: str, *, seconds: float, id: str, **_: Any) -> None:
        assert trigger == "interval"
        self.jobs.append((id, seconds))

        def tick() -> None:
            # 模拟每 2 秒一次的检查：直到标记出现才「到点」
            while not self.marker.exists() and not self.stopping:
                _real_sleep(0.02)
            func()

        threading.Thread(target=tick, daemon=True).start()

    def start(self) -> None:
        self.started.set()
        self.stopped.wait(5)

    def shutdown(self, wait: bool = True) -> None:
        self.stopping = True
        self.stopped.set()


def _fake_base_start(scheduler: _FakeScheduler) -> Any:
    def start(_self: Any) -> None:
        scheduler.start()

    return start


@pytest.fixture()
def marker(tmp_path: Path) -> Iterator[tuple[Path, _FakeScheduler]]:
    path = tmp_path / "scheduler.stop"
    scheduler = _FakeScheduler(path)
    original_start = BaseScheduler.start
    os.environ[CHILD_STOP_ARG_ENV] = str(path)
    try:
        yield path, scheduler
    finally:
        os.environ.pop(CHILD_STOP_ARG_ENV, None)
        BaseScheduler.start = original_start  # type: ignore[method-assign]


def test_without_marker_env_command_runs_untouched() -> None:
    os.environ.pop(CHILD_STOP_ARG_ENV, None)
    seen: list[str] = []

    @_stop_when_marked
    def command() -> None:
        seen.append("ran")

    command()

    assert seen == ["ran"]


def test_stop_marker_makes_blocking_scheduler_exit(
    marker: tuple[Path, _FakeScheduler],
) -> None:
    path, scheduler = marker
    original_start = BaseScheduler.start
    BaseScheduler.start = _fake_base_start(scheduler)  # type: ignore[method-assign]

    @_stop_when_marked
    def run_daemon() -> None:
        # 与 start_daemon 同形：经由被替换的基类 start 阻塞
        BaseScheduler.start(scheduler)

    thread = threading.Thread(target=run_daemon, daemon=True)
    thread.start()
    try:
        assert scheduler.started.wait(5), "调度器没启动"
        assert ("page-boot-stop", 2) in scheduler.jobs

        assert thread.is_alive(), "标记出现前不该退出"
        path.write_text("stop", encoding="utf-8")
        thread.join(10)

        assert not thread.is_alive(), "退出标记没让 schedule run 自行退出"
        assert BaseScheduler.start is not original_start, "钩子没恢复基类 start"
    finally:
        BaseScheduler.start = original_start  # type: ignore[method-assign]
