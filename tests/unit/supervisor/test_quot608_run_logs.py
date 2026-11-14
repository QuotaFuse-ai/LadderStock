"""QUOT-608：per-run 子进程日志 + 主日志轮转不再互斥。

根因：`WindowsHostPivot.spawn` 用 `open(log_path, "a")` 打开主日志交给子进程
终身持有，Windows 默认共享模式不含 `FILE_SHARE_DELETE`，`append_capped` 的
`os.replace` 稳定抛 WinError 5。

方案 B：`spawn` 改写 `<module>-run-<n>.log`，主日志只由 `append_capped` 独占。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ladderstock.supervisor import module_adapters
from ladderstock.supervisor.logroll import append_capped, utc_line


def _spawn_ping(pivot: object, log_path: Path, tmp_path: Path) -> int:
    argv = [sys.executable, "-c", "print('child-line')"]
    return int(
        pivot.spawn(
            argv,
            cwd=tmp_path,
            env={k: v for k, v in os.environ.items()},
            log_path=log_path,
        )
    )


@pytest.mark.skipif(os.name != "nt", reason="WinError 5 复现依赖 Windows 句柄共享语义")
def test_rotation_not_blocked_while_child_holds_log(tmp_path: Path) -> None:
    """子进程仍持有 per-run 日志句柄时，主日志的 os.replace 必须成功。"""
    pivot = module_adapters.WindowsHostPivot()
    main_log = tmp_path / "scheduler.log"
    pid = _spawn_ping(pivot, main_log, tmp_path)
    try:
        append_capped(main_log, utc_line("rotate-me"))
        assert main_log.exists()
        assert not main_log.with_name(main_log.name + ".tmp").exists()
        assert "rotate-me" in main_log.read_text(encoding="utf-8")
        per_run = tmp_path / f"scheduler-run-1{main_log.suffix}"
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and "child-line" not in (
            per_run.read_text(encoding="utf-8") if per_run.exists() else ""
        ):
            time.sleep(0.05)
        assert per_run.exists(), "子进程日志必须落在 per-run 文件"
        assert "child-line" in per_run.read_text(encoding="utf-8")
    finally:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)


def test_run_log_path_increments(tmp_path: Path) -> None:
    main = tmp_path / "scheduler.log"
    assert module_adapters._run_log_path(main).name == "scheduler-run-1.log"
    (tmp_path / "scheduler-run-3.log").touch()
    assert module_adapters._run_log_path(main).name == "scheduler-run-4.log"


def test_prune_run_logs_keeps_recent(tmp_path: Path) -> None:
    old_limit = module_adapters.RUN_LOG_KEEP
    module_adapters.RUN_LOG_KEEP = 3
    try:
        for i in range(1, 6):
            (tmp_path / f"scheduler-run-{i}.log").write_text(f"run-{i}", encoding="utf-8")
        module_adapters._prune_run_logs(tmp_path / "scheduler-run-5.log")
        names = sorted(p.name for p in tmp_path.glob("scheduler-run-*.log"))
        assert names == ["scheduler-run-3.log", "scheduler-run-4.log", "scheduler-run-5.log"]
    finally:
        module_adapters.RUN_LOG_KEEP = old_limit
