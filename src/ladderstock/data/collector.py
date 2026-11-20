"""补数执行器（F2 的落地部分）。

Agent 发现本地缺数时，真正"去取"的动作落在这里，而不是散在工具实现里。分三步，
每步都可单独验证：

1. :func:`collect` —— 调 TickHub-CN 的 CLI 采集一类数据；
2. :func:`wait_rows` —— 轮询本地库，等 ETL 把事件落到 DWD；
3. :func:`build_wide` —— 调 FinanceCoffer 的 CLI 重建这只票的宽表。

三条都必须带超时。这里不做重试：上游限流时重试只会更糟，把失败原因交回给
Agent，由它决定要不要换个口径或让用户稍后再试。

进程启动口径说明：

* TickHub 的采集入口只开在它自己的 CLI 上（``th-cn crawler run``）。它的 admin
  8787 那套是**另一份部署**（``D:\\workplace\\TickHub-CN``），管不到测试服用的
  这份库，所以不能走 HTTP。
* FinanceCoffer 的 CLI 依赖装在 ``runtime/api-deps``，与 API 进程同一套，
  这里复用 ``modules.financecoffer.services`` 给出的路径，避免写死。
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: 本地种类 → TickHub 任务名。
TICKHUB_TASKS: dict[str, str] = {
    "financial": "financials",
    "announcement": "announcements",
    "kline": "kline",
    "quote": "quote",
}

DEFAULT_COLLECT_TIMEOUT = 90.0
DEFAULT_BUILD_TIMEOUT = 60.0
DEFAULT_WAIT_SECONDS = 20.0
POLL_INTERVAL = 1.5


class CollectError(RuntimeError):
    """采集或建表失败；带上可读原因，让上层能如实说缺口。"""


def _repo_root() -> Path:
    """仓库根（``src/ladderstock/data/collector.py`` 往上四级）。"""

    return Path(__file__).resolve().parents[3]


def _create_no_window() -> int:
    return 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW


def tickhub_home() -> Path:
    explicit = (os.environ.get("LDST_TICKHUB_CN_HOME") or "").strip()
    if explicit:
        return Path(explicit)
    return _repo_root() / "file" / "modules" / "tickhub-cn"


def tickhub_exe() -> Path | None:
    candidate = tickhub_home() / ".venv" / "Scripts" / "th-cn.exe"
    return candidate if candidate.exists() else None


def _port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _first_open(candidates: list[int]) -> int | None:
    for port in candidates:
        if port and _port_open(port):
            return int(port)
    return None


def fc_redis_url() -> str:
    """FC 事件流使用的 Redis 地址。

    不能直接用 ``services.redis_port()``：那个值等于「基础端口 + 端口偏移」，
    而 FC 的 Redis 永远跑在固定的 16379（测试服 18790 那套与它无关）。在没设
    ``LDST_PORT_OFFSET`` 的进程里（例如正式服 5950 的启动口径）它会算出 6379，
    事件就会发给一个没人消费的 Redis——实测 TickHub 会报
    ``event delivery failed, parked in outbox``，数据静默丢失。
    """

    explicit = (os.environ.get("LDST_FC_EVENT_REDIS_URL") or "").strip()
    if explicit:
        return explicit

    try:
        from ladderstock.modules.financecoffer import services

        guessed = int(services.redis_port())
    except Exception as exc:  # noqa: BLE001 - 探不到就用探测结果
        logger.warning("collector_redis_port_probe_failed detail=%s", exc)
        guessed = 6379

    found = _first_open([guessed, 16379, 6379])
    port = found or guessed
    return f"redis://127.0.0.1:{port}/1"


def fc_pg_port() -> int:
    """FC 的 PostgreSQL 端口，同样按实际监听探测，不信任偏移推算。"""

    try:
        from ladderstock.modules.financecoffer import services

        guessed = int(services.pg_port())
    except Exception as exc:  # noqa: BLE001
        logger.warning("collector_pg_port_probe_failed detail=%s", exc)
        guessed = 5432
    return _first_open([guessed, 15432, 5432]) or guessed


def _tickhub_env() -> dict[str, str]:
    """采集子进程的环境。

    必须显式打开 ``EVENT_PUBLISH_ENABLE`` 并把 Redis 指到 FC 用的那个库：
    默认值分别是 ``false`` 和 ``6379/1``，照默认跑只会把数据落进 TickHub 自己的
    SQLite，FC 一个事件都收不到（这是 11-20 实测踩到的坑）。
    """

    env = dict(os.environ)
    env["EVENT_PUBLISH_ENABLE"] = "true"
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env["FC_EVENT_REDIS_URL"] = fc_redis_url()
    return env


def _fc_env() -> dict[str, str]:
    """FinanceCoffer CLI 子进程的环境（依赖目录与 DSN）。"""

    from ladderstock.modules.financecoffer import services

    env = dict(os.environ)
    deps = str(services.api_deps_dir())
    src = str(services.api_src_dir())
    existing = env.get("PYTHONPATH") or ""
    env["PYTHONPATH"] = ";".join([p for p in (deps, src, existing) if p])
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env["FC_DB_URL"] = _fc_dsn()
    return env


def _fc_dsn() -> str:
    """把取数侧用的 DSN 转成 CLI 认的驱动前缀。"""

    explicit = (os.environ.get("LDST_FC_PG_DSN") or "").strip()
    if explicit.startswith("postgresql+psycopg://"):
        return explicit
    if explicit.startswith("postgresql://"):
        return "postgresql+psycopg://" + explicit[len("postgresql://"):]

    from ladderstock.knowledge.announcements import pg_dsn

    dsn = pg_dsn()
    # pg_dsn() 的端口同样来自偏移推算；这里按实际监听纠正一次
    port = fc_pg_port()
    if f":{port}/" in dsn:
        pass
    else:
        import re as _re

        dsn = _re.sub(r":\d+/", f":{port}/", dsn, count=1)
    if dsn.startswith("postgresql+psycopg://"):
        return dsn
    if dsn.startswith("postgresql://"):
        return "postgresql+psycopg://" + dsn[len("postgresql://"):]
    return dsn


def _run(cmd: list[str], *, env: dict[str, str], timeout: float) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            creationflags=_create_no_window(),
        )
    except subprocess.TimeoutExpired as exc:
        raise CollectError(f"子进程超时（{timeout:.0f}s）：{' '.join(cmd[:3])}") from exc
    except OSError as exc:
        raise CollectError(f"子进程启动失败：{exc}") from exc


def collect(
    kind: str,
    symbols: list[str],
    *,
    days: int = 30,
    timeout: float = DEFAULT_COLLECT_TIMEOUT,
) -> dict[str, Any]:
    """采集一类数据，返回 TickHub 的原始结果摘要。

    :raises CollectError: 可执行不存在、超时、或 TickHub 报部分失败
    """

    task = TICKHUB_TASKS.get(str(kind).strip().lower())
    if not task:
        raise CollectError(f"TickHub 不支持的数据类：{kind}")

    codes = [str(s).strip() for s in symbols if str(s).strip()]
    if not codes:
        raise CollectError("symbols 为空")

    exe = tickhub_exe()
    if exe is None:
        raise CollectError(f"找不到 th-cn.exe（{tickhub_home()}）")

    cmd = [
        str(exe),
        "crawler",
        "run",
        "--task",
        task,
        "--codes",
        ",".join(codes),
        "--limit",
        str(max(1, len(codes))),
        "--days",
        str(max(1, int(days))),
    ]
    proc = _run(cmd, env=_tickhub_env(), timeout=timeout)
    output = (proc.stdout or "") + (proc.stderr or "")

    # TickHub 成功/部分失败都会把 envelope 打到 stdout；退出码非 0 说明至少一类失败
    if proc.returncode != 0:
        tail = output.strip().splitlines()[-4:]
        raise CollectError(
            f"采集失败（{task} exit={proc.returncode}）：{' / '.join(tail)[:300]}"
        )

    fetched = _grep_int(output, '"fetched":')
    inserted = _grep_int(output, '"inserted":')
    return {
        "kind": kind,
        "task": task,
        "symbols": codes,
        "fetched": fetched,
        "inserted": inserted,
        "stdout_tail": output.strip().splitlines()[-6:],
    }


def _grep_int(text: str, marker: str) -> int | None:
    idx = text.rfind(marker)
    if idx < 0:
        return None
    rest = text[idx + len(marker): idx + len(marker) + 12]
    digits = "".join(ch for ch in rest if ch.isdigit())
    return int(digits) if digits else None


def build_wide(
    symbol: str,
    *,
    as_of: str | None = None,
    years: int = 5,
    timeout: float = DEFAULT_BUILD_TIMEOUT,
) -> dict[str, Any]:
    """重建单票宽表。实测 0.88 秒，超时给 60 秒足够。"""

    code = str(symbol).strip()
    if not code:
        raise CollectError("symbol 为空")

    python = (os.environ.get("LDST_FC_PYTHON") or "").strip() or sys.executable
    cmd = [python, "-m", "financecoffer", "dws", "build", "--symbol", code]
    if as_of:
        cmd += ["--as-of", str(as_of)]
    if years and int(years) != 5:
        cmd += ["--years", str(int(years))]

    proc = _run(cmd, env=_fc_env(), timeout=timeout)
    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        tail = output.strip().splitlines()[-4:]
        raise CollectError(
            f"建宽表失败（{code} exit={proc.returncode}）：{' / '.join(tail)[:300]}"
        )
    return {"symbol": code, "ok": True, "stdout_tail": output.strip().splitlines()[-4:]}


def wait_rows(
    symbol: str,
    kind: str,
    *,
    seconds: float = DEFAULT_WAIT_SECONDS,
    poll: float = POLL_INTERVAL,
) -> dict[str, Any]:
    """轮询覆盖判定，直到该 kind 不再是 missing，或超时。"""

    from ladderstock.data import coverage

    deadline = time.monotonic() + max(1.0, float(seconds))
    last: dict[str, Any] | None = None
    while True:
        try:
            payload = coverage.check_coverage(symbol, kinds=[kind])
            last = payload["kinds"].get(kind)
        except coverage.CoverageUnavailable as exc:
            # 库短暂不可用不算数据没到，继续等；超时由上层报
            logger.warning("wait_rows_probe_failed symbol=%s detail=%s", symbol, exc)
            last = None
        if last and last.get("state") == coverage.STATE_READY:
            return {"symbol": symbol, "kind": kind, "state": "ready", "info": last}
        if time.monotonic() >= deadline:
            return {
                "symbol": symbol,
                "kind": kind,
                "state": (last or {}).get("state") or "unknown",
                "info": last,
                "timed_out": True,
            }
        time.sleep(poll)
