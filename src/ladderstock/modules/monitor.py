"""模块监控：把增益包的自愈挂进 LS 主进程，跨端纯 Python。

为什么不做成 Windows 服务 / 计划任务 / `.vbs`：便携包要在 win/mac/linux 上同一套行为。
这里就是一条 daemon 线程，每 `interval_sec` 让 `financecoffer.services.ensure_running()`
补一轮挂掉的服务；主进程退出线程随之结束，不需要任何系统级注册，也不需要管理员权限。

三条纪律：

1. **只补不抢**：`ensure_running` 只拉起 probe 判为「没在跑」的服务，已经在跑的一律
   不动，因此不会和 `ldst supervisor` 抢进程（那条链路的归属判定不受影响）。
2. **绝不打断主进程**：每一轮都包 try/except，监控自身的失败只记进 `status()`，
   不冒泡到 web 层。
3. **可关**：`LDST_MODULES_MONITOR=0/off` 时 `start()` 直接返回 not_started；
   `LDST_MODULES_INTERVAL` 可改间隔（秒）。

形态 2 下 embedding 不再被拉起（见 `services.embedding_enabled()`）：公告语义检索走
LS 自己的 qdrant，FC 只留 ODS/DWD/DWS 结构化。
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SEC = 60.0

#: 每多少轮检查一次公告投影（默认 5 轮 ≈ 5 分钟）。投影要跑 embedding，
#: 不能每轮都做；靠指纹判断，没变化就一秒跳过。
PROJECTION_EVERY = 5

_STATE: dict[str, Any] = {
    "running": False,
    "interval_sec": 0.0,
    "rounds": 0,
    "restarts": 0,
    "last_at": None,
    "last_actions": [],
    "last_error": None,
    "projection": {"runs": 0, "chunks": 0, "last_at": None, "last_error": None, "skipped": 0},
}
#: 上一次投影用的指纹（symbol → "rows:max_id:chars"）。进程重启会清空，
#: 首轮因此会重投一遍——这正好能修「qdrant 被清空/重装」那种情况。
_FINGERPRINTS: dict[str, str] = {}
_LOCK = threading.Lock()
_STOP = threading.Event()
_THREAD: threading.Thread | None = None


def enabled() -> bool:
    """监控默认开；`LDST_MODULES_MONITOR=0/off/false/no` 关闭。"""

    raw = (os.environ.get("LDST_MODULES_MONITOR") or "1").strip().lower()
    return raw not in ("0", "off", "false", "no")


def interval_sec() -> float:
    raw = (os.environ.get("LDST_MODULES_INTERVAL") or "").strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_SEC
    return max(10.0, value)


def _one_round(root: Any) -> None:
    """跑一轮自愈，把结果记进状态；任何异常都收成 last_error。"""

    from ladderstock.modules.financecoffer import services

    try:
        result = services.ensure_running(root)
    except Exception as exc:  # noqa: BLE001 - 监控自身失败不该影响主进程
        with _LOCK:
            _STATE["last_error"] = f"{type(exc).__name__}: {exc}"
            _STATE["last_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        return
    actions = result.get("actions") or []
    with _LOCK:
        _STATE["rounds"] = int(_STATE["rounds"]) + 1
        _STATE["restarts"] = int(_STATE["restarts"]) + len(actions)
        _STATE["last_actions"] = actions
        _STATE["last_error"] = None
        _STATE["last_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")


def _record_projection(**fields: Any) -> None:
    with _LOCK:
        bucket = _STATE.setdefault("projection", {})
        bucket.setdefault("runs", 0)
        bucket.setdefault("chunks", 0)
        bucket.setdefault("skipped", 0)
        bucket.update(fields)


def _project_announcements() -> None:
    """把 dwd 里新增/变长的公告投影进 qdrant；指纹没变就跳过。

    这是形态 2 的自动接线：公告落库后由监控接手，不再需要人手动跑脚本。
    任何一步失败都只记在 `status()["projection"]` 里，不影响服务自愈那几轮。
    """

    from ladderstock.knowledge import announcements as ann

    try:
        targets = ann.symbols()
    except Exception as exc:  # noqa: BLE001 - PG 不在/表缺失都属于可选依赖缺席
        _record_projection(last_error=f"读标的清单失败：{type(exc).__name__}: {exc}")
        return

    projected: list[dict[str, Any]] = []
    skipped = 0
    for symbol in targets:
        try:
            fp = ann.fingerprint(symbol)
        except Exception as exc:  # noqa: BLE001
            _record_projection(last_error=f"{symbol} 指纹失败：{type(exc).__name__}: {exc}")
            continue
        key = f"{fp['rows']}:{fp['max_id']}:{fp['chars']}"
        if _FINGERPRINTS.get(symbol) == key:
            skipped += 1
            continue
        try:
            info = ann.sync_symbol(symbol)
        except Exception as exc:  # noqa: BLE001 - qdrant 挂了就是挂了，记下来
            _record_projection(last_error=f"{symbol} 投影失败：{type(exc).__name__}: {exc}")
            continue
        _FINGERPRINTS[symbol] = key
        projected.append({"symbol": symbol, **info.as_dict()})
        # 走 stderr 直写：主进程的 logging 只放行 WARNING 以上，INFO 会被滤掉，
        # 投影这种「该发生但没日志」的动作必须能被看见，否则排障只能靠猜。
        print(
            f"[monitor] announcements_projected symbol={symbol} "
            f"announcements={info.announcements} chunks={info.chunks}",
            file=sys.stderr,
            flush=True,
        )

    if not projected:
        _record_projection(skipped=skipped, last_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
        return
    with _LOCK:
        bucket = _STATE.setdefault("projection", {})
        bucket["runs"] = int(bucket.get("runs") or 0) + 1
        bucket["chunks"] = int(bucket.get("chunks") or 0) + sum(
            int(item.get("chunks") or 0) for item in projected
        )
        bucket["skipped"] = skipped
        bucket["last_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        bucket["last_error"] = None
        bucket["last_projected"] = projected


def _loop(root: Any, wait: float) -> None:
    ticks = 0
    while not _STOP.is_set():
        _one_round(root)
        ticks += 1
        # 首轮就投一次：进程刚起、或 qdrant 被重装过，靠这一轮立刻补齐；
        # 之后每 PROJECTION_EVERY 轮按指纹判一次，稳定态一秒跳过。
        if ticks == 1 or ticks % PROJECTION_EVERY == 0:
            _project_announcements()
        # 用 Event.wait 而不是 sleep：stop() 能立刻叫停，不用等满一个间隔。
        if _STOP.wait(wait):
            break
    with _LOCK:
        _STATE["running"] = False


def start(root: Any = None, *, initial_delay: float = 5.0) -> dict[str, Any]:
    """拉起监控线程（幂等：重复调用只会有一个线程）。返回当前状态。

    `initial_delay` 目前只做说明用：首轮由线程自己跑，调用方不被阻塞——
    web 启动不该为了等 PG 就绪而卡住端口。
    """

    global _THREAD
    if not enabled():
        with _LOCK:
            _STATE.update({"running": False, "interval_sec": 0.0})
        return {"started": False, "reason": "disabled_by_env", **status()}

    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return {"started": False, "reason": "already_running", **status()}
        wait = interval_sec()
        _STATE.update({"running": True, "interval_sec": wait})
        _STOP.clear()
        _THREAD = threading.Thread(
            target=_loop,
            args=(root, wait),
            name="modules-monitor",
            daemon=True,
        )
        _THREAD.start()
    return {"started": True, "reason": "launched", **status()}


def stop(timeout: float = 3.0) -> dict[str, Any]:
    """叫停监控线程（不等人，最多等 `timeout` 秒收尾）。"""

    global _THREAD
    _STOP.set()
    thread = _THREAD
    if thread is not None and thread.is_alive():
        thread.join(timeout=timeout)
    with _LOCK:
        _STATE["running"] = False
    _THREAD = None
    return status()


def status() -> dict[str, Any]:
    with _LOCK:
        payload = dict(_STATE)
    payload["thread_alive"] = bool(_THREAD is not None and _THREAD.is_alive())
    payload["enabled"] = enabled()
    return payload


__all__ = [
    "DEFAULT_INTERVAL_SEC",
    "PROJECTION_EVERY",
    "enabled",
    "interval_sec",
    "start",
    "status",
    "stop",
]
