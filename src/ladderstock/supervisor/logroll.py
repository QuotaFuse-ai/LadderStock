"""有上限的日志追加（QUOT-491 / 482-C）。

D5 定稿要求：自愈日志必须有上限（轮转或截断），禁止无界追加——磁盘写满正是
这类日志的典型事故。这里给一个极简但可靠的实现：每次追加后，文件**只保留
最近 `max_lines` 行**，用「读尾 + 临时文件 + 原子替换」落盘，读写全程持锁。

验收口径（任务书证据 5）：人为刷 500 行，文件仍只保留上限条数。
"""

from __future__ import annotations

import os
import threading
from collections import deque
from datetime import UTC, datetime
from pathlib import Path

_LOG_LOCK = threading.Lock()


def utc_line(message: str) -> str:
    """给一行日志打 UTC 时间戳（自愈日志的统一行格式）。"""
    stamp = datetime.now(UTC).isoformat(timespec="milliseconds")
    return f"{stamp} {message}\n"


def append_capped(
    path: str | os.PathLike[str],
    line: str,
    *,
    max_lines: int = 200,
    lock: threading.Lock | None = None,
) -> int:
    """追加一行并保证文件不超过 `max_lines` 行，返回写后的行数。

    - 文件不存在则创建（含父目录）；
    - 超限即截断，只保留**最后** `max_lines` 行（最旧的先丢）；
    - 原子替换：先写 `.tmp` 再 `os.replace`，读侧永远看到完整文件。
    """
    if max_lines <= 0:
        raise ValueError("max_lines 必须为正")
    target = Path(path)
    guard = lock or _LOG_LOCK
    with guard:
        kept: deque[str] = deque(maxlen=max_lines)
        if target.exists():
            with target.open("r", encoding="utf-8", errors="replace") as existing:
                for row in existing:
                    kept.append(row.rstrip("\n"))
        kept.append(line.rstrip("\n"))
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text("\n".join(kept) + "\n", encoding="utf-8")
        os.replace(tmp, target)
        return len(kept)


def read_tail(path: str | os.PathLike[str], lines: int) -> list[str]:
    """读文件最后 `lines` 行（供接口/验收取证用，不抛缺文件）。"""
    target = Path(path)
    if not target.exists():
        return []
    with target.open("r", encoding="utf-8", errors="replace") as handle:
        return list(deque(handle, maxlen=lines))


__all__ = ["append_capped", "read_tail", "utc_line"]
