"""`ldst sync` 的进程级串行锁（QUOT-234）。

DuckDB 的 `market.duckdb` 文件锁是**独占**的：同机两个进程同时写，后到的那个
直接 `_duckdb.IOException`。取数链路本身又是分钟级的，靠 `duckdb_store.connect()`
的有界退避重试扛不住一轮完整同步，所以在 sync 入口先拿这把锁，把同库的写**串行化**。

为什么是 advisory lock 而不是哨兵文件：哨兵文件（`O_CREAT|O_EXCL`）在进程崩溃后
就是死锁，还得自己判陈旧；`flock` / `msvcrt.locking` 由内核在 fd 关闭、进程退出时
释放，崩溃不留锁。锁文件本身只是个 inode，不承担存储职责，只在持有期间写一行
持有者信息，供等待方在超时报错里点名。

等待有上限：默认 `DEFAULT_TIMEOUT_SEC`，`LDST_DB_LOCK_TIMEOUT`（秒）覆盖；
超时转 `LadderStockError(EXIT_DB_BUSY)`，不静默跳过、不无限等。
"""

from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ladderstock.data.duckdb_store import DB_FILENAME, db_path
from ladderstock.errors import EXIT_DB_BUSY, LadderStockError

# 按平台选锁实现：mypy 会跟着 `sys.platform` 分支裁剪，两个平台各自只看得到自己那一半
if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

LOCK_FILENAME = f"{DB_FILENAME}.lock"
DEFAULT_TIMEOUT_SEC = 30.0
POLL_INTERVAL_SEC = 0.1

#: 锁只占文件开头 1 个字节，持有者信息写在 `LABEL_OFFSET` 之后的固定宽字段里。
#: Windows 的字节范围锁会**连读取一起挡**，若把 pid 写在被锁的范围内，
#: 等待方就永远读不到「是谁占着」——这正是最需要它的报错时刻。
LOCK_REGION_LEN = 1
LABEL_OFFSET = 64
LABEL_WIDTH = 240

_guard = threading.Lock()
#: 本进程已持有的锁文件 → 引用计数。同一进程重入（`schedule run` 里再跑一轮 sync、
#: 测试里嵌套调用）不再向内核要第二次锁——同一文件的第二个 fd 上锁会被自己挡住，
#: 那就不是「等别人」而是自锁死。
_held: dict[str, int] = {}


def lock_path(root: Path | None = None) -> Path:
    """跟着实际被写的那个库走：锁的名字 = 库的路径。"""
    return db_path(root).with_name(LOCK_FILENAME)


def configured_timeout() -> float:
    raw = os.environ.get("LDST_DB_LOCK_TIMEOUT")
    if not raw:
        return DEFAULT_TIMEOUT_SEC
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_TIMEOUT_SEC
    return value if value >= 0 else DEFAULT_TIMEOUT_SEC


def _try_lock(fd: int) -> bool:
    os.lseek(fd, 0, os.SEEK_SET)
    try:
        if sys.platform == "win32":
            msvcrt.locking(fd, msvcrt.LK_NBLCK, LOCK_REGION_LEN)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(fd: int) -> None:
    try:
        if sys.platform == "win32":
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, LOCK_REGION_LEN)
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:  # 释放失败也继续往下走，进程退出时内核会兜
        pass


def _write_holder(fd: int, text: str) -> None:
    """定宽写入：不截断文件，避免覆盖式 truncate 和轮询方的读取撞车。"""
    payload = text.encode("utf-8", errors="replace")[:LABEL_WIDTH].ljust(LABEL_WIDTH)
    os.lseek(fd, LABEL_OFFSET, os.SEEK_SET)
    os.write(fd, payload)


def _read_holder(fd: int) -> str:
    try:
        os.lseek(fd, LABEL_OFFSET, os.SEEK_SET)
        raw = os.read(fd, LABEL_WIDTH)
    except OSError:
        return "未知进程"
    text = raw.decode("utf-8", errors="replace").strip()
    return text or "未知进程（持有者未写明）"


@contextmanager
def writer_lock(
    root: Path | None = None,
    *,
    holder: str = "",
    timeout: float | None = None,
) -> Iterator[None]:
    """独占持有本机的历史库写锁；拿不到等到超时才报错。

    `holder` 是写进锁文件的一行说明（命令名），只用于报错时点名，不参与判定。
    """
    path = lock_path(root)
    key = str(path)
    with _guard:
        depth = _held.get(key, 0)
        if depth:
            _held[key] = depth + 1
    if depth:
        # 同进程重入：外层已握着内核锁，这里只加引用计数
        try:
            yield
        finally:
            _drop_ref(key)
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    wait = configured_timeout() if timeout is None else float(timeout)
    deadline = time.monotonic() + max(0.0, wait)
    try:
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                raise LadderStockError(
                    f"历史库正被另一个进程同步，等待 {wait:.1f}s 超时：{path}\n"
                    f"持有者：{_read_holder(fd)}\n"
                    "提示：同一时刻只允许一个 sync 在写这个库。等前一轮结束后再跑，"
                    "或调 `LDST_DB_LOCK_TIMEOUT`（秒）放宽等待",
                    EXIT_DB_BUSY,
                )
            time.sleep(POLL_INTERVAL_SEC)
    except BaseException:
        os.close(fd)
        raise

    try:
        _write_holder(fd, f"{holder or 'ldst'} pid={os.getpid()}")
        _add_ref(key)
        yield
    finally:
        _drop_ref(key)
        _unlock(fd)
        os.close(fd)


def _add_ref(key: str) -> None:
    with _guard:
        _held[key] = _held.get(key, 0) + 1


def _drop_ref(key: str) -> None:
    with _guard:
        count = _held.get(key, 0) - 1
        if count > 0:
            _held[key] = count
        else:
            _held.pop(key, None)
