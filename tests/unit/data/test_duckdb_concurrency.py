"""跨进程并发写同一 `market.duckdb`（QUOT-234）。

验收两条：

1. 多进程并发写 → **0 个未捕获异常**：要么退避重试后写进去，要么明确
   `LadderStockError` + 退出码 10（`EXIT_DB_BUSY`），不许出现 traceback + exit 1；
2. `ldst sync` 入口的进程级文件锁：同库串行，等待有上限，超时明确报错。

真跨进程用 `subprocess` 起子进程（同一个解释器、`PYTHONPATH=src`、
`LDST_DATA_DIR` 钉在 tmp），锁和库冲突都走操作系统那一份，不做桩。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import duckdb
import pytest

from ladderstock.data import db_lock, duckdb_store
from ladderstock.errors import EXIT_CONFIG, EXIT_DB_BUSY, LadderStockError

ROOT = Path(__file__).resolve().parents[3]
DAY = "2026-10-24"

#: QUOT-164 里实际抛出来那句的两个平台形态（判据要同时认）
WIN_LOCK_MESSAGE = (
    'IO Error: Cannot open file "/tmp/market.duckdb": The process cannot access '
    "the file because it is being used by another process.\n"
    "File is already open in /usr/bin/python (pid 4242)"
)
POSIX_LOCK_MESSAGE = (
    'IO Error: Cannot open file "/tmp/market.duckdb": Could not set a lock on file: '
    "Conflicting lock is held in /usr/bin/python3 (pid 4242)"
)


# --------------------------------------------------------------------------- #
# 子进程脚手架
# --------------------------------------------------------------------------- #

@pytest.fixture(autouse=True)
def _data_home(tmp_path, monkeypatch):
    """把**父进程**的数据目录也钉在 tmp：否则 `connect()` 会去开仓库里的 `data/`，
    测的就不是「两个进程抢同一个库」而是各开各的库。"""
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    return tmp_path


def _env(root: Path, **extra: str) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    env["LDST_DATA_DIR"] = str(root)
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(extra)
    return env


def _run(code: str, root: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_env(root, **extra),
        cwd=str(ROOT),
        timeout=180,
    )


def _popen(code: str, root: Path) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_env(root),
        cwd=str(ROOT),
    )


def _wait_ready(proc: subprocess.Popen[str], marker: str) -> None:
    """等子进程确认它已经占到资源，再让主进程去撞——不然测的是时序运气。"""
    assert proc.stdout is not None
    line = proc.stdout.readline().strip()
    assert marker in line, (line, proc.stderr.read() if proc.stderr else "")


def _write_child(dataset: str, prefix: str, rows: int) -> str:
    return f'''
import sys
from ladderstock.data import duckdb_store
from ladderstock.errors import LadderStockError

records = [
    {{"code": "{prefix}-{{i}}", "trade_date": "{DAY}", "close": 10.0 + i}}
    for i in range({rows})
]
try:
    result = duckdb_store.write_dataset("{dataset}", records, source="fake")
except LadderStockError as exc:
    sys.stderr.write(str(exc))
    sys.exit(exc.code)
except BaseException as exc:  # noqa: BLE001 - 未捕获异常就是本单要修的洞
    sys.stderr.write("UNCAUGHT {{type(exc).__name__}}: {{exc}}")
    sys.exit(1)
sys.stdout.write(str(result.rows))
'''


# --------------------------------------------------------------------------- #
# 验收①：并发写不出未捕获异常
# --------------------------------------------------------------------------- #

def test_three_processes_write_same_db_without_uncaught_errors(tmp_path) -> None:
    procs = [_popen(_write_child("daily_k", f"p{index}", 60), tmp_path) for index in range(3)]
    outs = [(p.wait(), p.stdout.read(), p.stderr.read()) for p in procs]

    succeeded = [int(out) for rc, out, _ in outs if rc == 0]
    busy = [err for rc, _, err in outs if rc == EXIT_DB_BUSY]

    # 只允许两种结局：写进去，或被明确判成「库被占用」
    assert {rc for rc, _, _ in outs} <= {0, EXIT_DB_BUSY}, outs
    for _, _, err in outs:
        assert "UNCAUGHT" not in err and "Traceback" not in err, err
    assert succeeded, "三个进程全挂着：重试预算不够，不是可交付行为"
    for err in busy:
        assert "占用" in err

    # 成功的那些逐行落库，幂等键没被并发打乱
    assert duckdb_store.dataset_rows("daily_k", root=tmp_path) == sum(succeeded)


def test_held_db_reports_busy_instead_of_raw_ioerror(tmp_path) -> None:
    """另一个进程**按住**库不放：重试耗尽后必须是 LadderStockError(10)，不是裸异常。"""
    holder = _popen(
        "import time\n"
        "from ladderstock.data import duckdb_store\n"
        "c = duckdb_store.connect()\n"
        "print('READY', flush=True)\n"
        "time.sleep(20)\n"
        "duckdb_store.close(c)\n",
        tmp_path,
    )
    try:
        _wait_ready(holder, "READY")
        started = time.monotonic()
        with pytest.raises(LadderStockError) as excinfo:
            duckdb_store.connect()
    finally:
        holder.kill()
        holder.wait()

    assert excinfo.value.code == EXIT_DB_BUSY
    assert not isinstance(excinfo.value, duckdb.Error)
    assert "占用" in str(excinfo.value) and "重试" in str(excinfo.value)
    # 有界：不会为了撞开的库无限等下去
    assert time.monotonic() - started < 10


# --------------------------------------------------------------------------- #
# 有界重试的判据本身
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("message", [WIN_LOCK_MESSAGE, POSIX_LOCK_MESSAGE])
def test_lock_conflict_retries_with_capped_exponential_backoff(
    monkeypatch, message: str
) -> None:
    calls: list[int] = []
    slept: list[float] = []

    def boom(_path: str, **_kwargs: object) -> duckdb.DuckDBPyConnection:
        calls.append(1)
        raise duckdb.IOException(message)

    monkeypatch.setattr(duckdb, "connect", boom)
    monkeypatch.setattr(duckdb_store.time, "sleep", lambda seconds: slept.append(seconds))

    with pytest.raises(LadderStockError) as excinfo:
        duckdb_store.connect(Path("market.duckdb"))

    assert len(calls) == duckdb_store.CONNECT_ATTEMPTS
    assert len(slept) == duckdb_store.CONNECT_ATTEMPTS - 1
    assert slept[0] == duckdb_store.CONNECT_BACKOFF_BASE
    assert slept[1] == duckdb_store.CONNECT_BACKOFF_BASE * 2
    assert max(slept) <= duckdb_store.CONNECT_BACKOFF_CAP
    assert sum(slept) < 5
    assert excinfo.value.code == EXIT_DB_BUSY


def test_non_conflict_failure_is_not_retried(monkeypatch) -> None:
    """打不开 ≠ 被占用：损坏 / 无权限要立刻按配置错误报出去，不空转重试。"""
    calls: list[int] = []

    def boom(_path: str, **_kwargs: object) -> duckdb.DuckDBPyConnection:
        calls.append(1)
        raise duckdb.IOException('IO Error: Cannot open file "x": Permission denied')

    monkeypatch.setattr(duckdb, "connect", boom)

    with pytest.raises(LadderStockError) as excinfo:
        duckdb_store.connect(Path("market.duckdb"))

    assert calls == [1]
    assert excinfo.value.code == EXIT_CONFIG
    assert "Permission denied" in str(excinfo.value)


def test_read_only_open_wraps_conflict_too(monkeypatch) -> None:
    """归档读（`ldst review` / `archive.snapshot`）也走 `connect()`，同样不漏裸异常。"""

    def boom(_path: str, **_kwargs: object) -> duckdb.DuckDBPyConnection:
        raise duckdb.IOException(WIN_LOCK_MESSAGE)

    monkeypatch.setattr(duckdb, "connect", boom)

    with pytest.raises(LadderStockError) as excinfo:
        duckdb_store.connect(Path("market.duckdb"), read_only=True)

    assert excinfo.value.code == EXIT_DB_BUSY


# --------------------------------------------------------------------------- #
# 验收②：sync 入口的进程级文件锁
# --------------------------------------------------------------------------- #

def test_writer_lock_blocks_other_process_and_names_holder(tmp_path) -> None:
    with db_lock.writer_lock(root=tmp_path, holder="ldst sync"):
        child = _run(
            "import sys\n"
            "from ladderstock.data import db_lock\n"
            "from ladderstock.errors import LadderStockError\n"
            "try:\n"
            "    with db_lock.writer_lock(holder='other'):\n"
            "        print('ACQUIRED')\n"
            "except LadderStockError as exc:\n"
            "    sys.stderr.write(str(exc))\n"
            "    sys.exit(exc.code)\n",
            tmp_path,
            LDST_DB_LOCK_TIMEOUT="0.4",
        )

    assert child.returncode == EXIT_DB_BUSY, (child.stdout, child.stderr)
    assert "正被另一个进程同步" in child.stderr
    assert f"pid={os.getpid()}" in child.stderr  # 报错要点名持有者

    # 释放后同一路径立刻可拿：advisory lock 随进程退出/关闭释放，崩溃不留锁
    after = _run(
        "from ladderstock.data import db_lock\n"
        "with db_lock.writer_lock(holder='second'):\n"
        "    print('ACQUIRED')\n",
        tmp_path,
        LDST_DB_LOCK_TIMEOUT="2",
    )
    assert "ACQUIRED" in after.stdout, (after.stdout, after.stderr)


def test_writer_lock_waits_for_the_holder_instead_of_failing(tmp_path) -> None:
    holder = _popen(
        "import time\n"
        "from ladderstock.data import db_lock\n"
        "with db_lock.writer_lock(holder='child'):\n"
        "    print('HELD', flush=True)\n"
        "    time.sleep(0.5)\n",
        tmp_path,
    )
    _wait_ready(holder, "HELD")

    started = time.monotonic()
    with db_lock.writer_lock(root=tmp_path, holder="parent", timeout=15):
        waited = time.monotonic() - started
    holder.wait()

    assert 0 <= waited < 15
    assert waited >= db_lock.POLL_INTERVAL_SEC  # 真的是在等，不是立刻抢到的


def test_writer_lock_is_reentrant_in_one_process(tmp_path) -> None:
    """同进程重入不能向内核要第二次锁——那会被自己挡住，等于自锁死。"""
    with db_lock.writer_lock(root=tmp_path, holder="outer"):
        with db_lock.writer_lock(root=tmp_path, holder="inner"):
            pass
        # 内层退出不释放外层：别的进程此刻仍进不来
        child = _run(
            "from ladderstock.data import db_lock\n"
            "try:\n"
            "    with db_lock.writer_lock(holder='other'):\n"
            "        print('ACQUIRED')\n"
            "except Exception:\n"
            "    print('BUSY')\n",
            tmp_path,
            LDST_DB_LOCK_TIMEOUT="0.3",
        )
        assert "BUSY" in child.stdout, (child.stdout, child.stderr)

    assert db_lock._held == {}  # 计数没有泄漏


def test_sync_entry_is_serialized_by_the_lock(tmp_path) -> None:
    """`ldst sync` 一进来先拿锁：锁在别人手里就 exit 10，一次取数都不发。"""
    with db_lock.writer_lock(root=tmp_path, holder="ldst sync"):
        child = subprocess.run(
            [sys.executable, "-m", "ladderstock", "sync"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=_env(tmp_path, LDST_DB_LOCK_TIMEOUT="0.4"),
            cwd=str(ROOT),
            timeout=180,
        )

    assert child.returncode == EXIT_DB_BUSY, (child.stdout, child.stderr)
    assert "正被另一个进程同步" in child.stderr
    assert "Traceback" not in child.stderr
