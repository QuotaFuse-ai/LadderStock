"""调度器单例锁：同一个数据目录同一时刻只允许一个调度器执行任务表。

调度表可能同时被三处拉起：`ldst run` 进程内的 `ScheduleRunner`、页面自启拉起的
`ldst schedule run` 子进程、以及用户手动开的 `ldst schedule run`。它们读同一张表、
写同一个 `data/`，同时执行就是每个定时任务跑两三遍——定时研究 / 跨轮任务推进
各调一次模型、各发一次通知。

锁沿用 `db_lock` 的内核 advisory lock（进程退出即释放，崩溃不留死锁）。拿不到锁的
一方**待命**而不是退出：页面自启的子进程若直接退出，自愈会把它当成「死了」反复重拉。
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from pathlib import Path

from ladderstock.data.db_lock import _read_holder, _try_lock, _unlock, _write_holder

LOCK_RELPATH = Path("state") / "scheduler.lock"
#: 待命方重试拿锁的间隔（秒）
STANDBY_POLL_SEC = 10.0


def lock_path(root: Path) -> Path:
    return Path(root) / LOCK_RELPATH


class SchedulerLease:
    """持有中的调度锁；`release()` 或进程退出时释放。"""

    def __init__(self, path: Path, fd: int) -> None:
        self.path = path
        self._fd: int | None = fd
        self._guard = threading.Lock()

    @property
    def held(self) -> bool:
        return self._fd is not None

    def release(self) -> None:
        with self._guard:
            fd, self._fd = self._fd, None
        if fd is None:
            return
        _unlock(fd)
        os.close(fd)


def try_acquire(root: Path, *, holder: str) -> SchedulerLease | None:
    """不等待：拿到返回租约，被别的调度器占着返回 None。"""
    path = lock_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    if not _try_lock(fd):
        os.close(fd)
        return None
    _write_holder(fd, f"{holder} pid={os.getpid()}")
    return SchedulerLease(path, fd)


def current_holder(root: Path) -> str:
    """锁文件里登记的持有者（只用于提示，不参与判定）。"""
    path = lock_path(root)
    if not path.is_file():
        return "未知进程"
    fd = os.open(str(path), os.O_RDONLY)
    try:
        return _read_holder(fd)
    finally:
        os.close(fd)


def wait_acquire(
    root: Path,
    *,
    holder: str,
    should_stop: Callable[[], bool] = lambda: False,
    poll_sec: float = STANDBY_POLL_SEC,
    on_standby: Callable[[str], None] | None = None,
) -> SchedulerLease | None:
    """阻塞到拿到锁；`should_stop()` 为真时放弃并返回 None。

    `on_standby` 只在第一次没拿到锁时回调一次（带当前持有者），用于记一行日志。
    """
    notified = False
    while True:
        lease = try_acquire(root, holder=holder)
        if lease is not None:
            return lease
        if not notified and on_standby is not None:
            on_standby(current_holder(root))
            notified = True
        deadline = time.monotonic() + poll_sec
        while time.monotonic() < deadline:
            if should_stop():
                return None
            time.sleep(min(1.0, poll_sec))


__all__ = [
    "LOCK_RELPATH",
    "STANDBY_POLL_SEC",
    "SchedulerLease",
    "current_holder",
    "lock_path",
    "try_acquire",
    "wait_acquire",
]
