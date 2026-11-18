"""web 进程内的定时调度：只开一个服务，整张调度表就自己跑。

`ldst schedule serve` 一直存在，但它是一个**单独的常驻进程**——用户得知道去起第二个
黑窗口，否则 `config/schedule.yaml` 里的任务一条都不会执行（实测：只起 5951 时
`watch_live_state.json` 停在最后一次手动触发的时刻）。对「装了就能用」的诉求，这是必须
补的一环。

**默认整表都挂**（`JOB_KINDS` 全枚举），不只是研究/盯盘那三类：08:45 盯盘提醒、16:40
定时研究、30s 盘中盯盘、09:25–15:00 的六个快照、30s/60s 的盘中快刷、15:05 对账、
15:35 收盘同步、15:40 收盘摘要、16:30 知识库刷新、07:30 题材资讯——一条不落。

盘外不会打上游：`snapshot_fast` / `sync_batch` / `watch_live` 三个高频任务自己带交易时段
门控（非 09:30–11:30 / 13:00–15:00 直接 skip），所以页面开着过夜也不会白烧额度。

两个环境开关：

- `LDST_SCHEDULE_IN_WEB=0` 整个后台调度关掉（测试、排障、或你要改用 `schedule serve`）；
- `LDST_SCHEDULE_KINDS=agent_research,watch_live` 只挂指定几类（想减负时用）。

**别同时开 `ldst schedule serve`**：两边都会按表执行，任务会跑两遍（提醒重复、上游双份）。
"""

from __future__ import annotations

import os
import threading
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

RUNNER_ENV = "LDST_SCHEDULE_IN_WEB"
KINDS_ENV = "LDST_SCHEDULE_KINDS"

_RUNNER: ScheduleRunner | None = None
_RUNNER_LOCK = threading.Lock()


def _all_kinds() -> tuple[str, ...]:
    """调度表支持的全部任务类型（单一来源：cli/schedule.py 的 JOB_KINDS）。"""
    from ladderstock.cli.schedule import JOB_KINDS

    return tuple(JOB_KINDS)


#: 默认挂全表；`LDST_SCHEDULE_KINDS` 可把它收窄成任意子集。
DEFAULT_KINDS: tuple[str, ...] = _all_kinds()


_RUNNER: ScheduleRunner | None = None
_RUNNER_LOCK = threading.Lock()


class ScheduleRunner:
    """把调度表里「可手动触发的那几类」挂到 APScheduler 的后台线程上。"""

    def __init__(self, *, data_root: Path, kinds: Sequence[str] | None = None) -> None:
        self._root = Path(data_root)
        self._kinds = tuple(kinds) if kinds is not None else self._kinds_from_env()
        self._lock = threading.Lock()
        self._scheduler: Any | None = None
        self._timezone = "Asia/Shanghai"
        self._loaded: list[str] = []
        self._error: str | None = None

    # ------------------------------------------------------------ 生命周期

    @staticmethod
    def _kinds_from_env() -> tuple[str, ...]:
        """`LDST_SCHEDULE_KINDS` 逗号分隔时只挂这几类，否则整表全挂。"""
        raw = os.environ.get(KINDS_ENV, "").strip()
        if not raw:
            return _all_kinds()
        wanted = {part.strip() for part in raw.split(",") if part.strip()}
        return tuple(kind for kind in _all_kinds() if kind in wanted)

    def enabled_by_env(self) -> bool:
        return os.environ.get(RUNNER_ENV, "1").strip().lower() not in ("0", "false", "no", "off")

    def start(self) -> dict[str, Any]:
        """装载并按表启动；已经在跑就只做一次 reload。"""
        if self._scheduler is not None:
            self.reload()
            return self.status()
        try:
            from apscheduler.schedulers.background import (  # type: ignore[import-untyped]
                BackgroundScheduler,
            )
        except ImportError as exc:  # pragma: no cover - 环境缺依赖
            self._error = f"APScheduler 缺失：{exc}"
            return self.status()

        from ladderstock.cli.schedule import load_schedule

        table, _warnings, _source = load_schedule()
        conf = table.get("scheduler") or {}
        self._timezone = str(conf.get("timezone") or "Asia/Shanghai")
        grace = int(conf.get("misfire_grace_seconds", 300))
        scheduler = BackgroundScheduler(
            timezone=self._timezone,
            job_defaults={
                "misfire_grace_time": grace,
                "coalesce": True,
                "max_instances": 1,
            },
        )
        scheduler.start()
        self._scheduler = scheduler
        self.reload()
        return self.status()

    def reload(self) -> dict[str, Any]:
        """按当前调度表重建 job 列表（页面开关一按就生效，不用重启服务）。"""
        with self._lock:
            scheduler = self._scheduler
            if scheduler is None:
                return self.status()
            for job in list(scheduler.get_jobs()):
                try:
                    scheduler.remove_job(job.id)
                except Exception:  # noqa: BLE001 - 已经被别处移走
                    pass
            self._loaded = []
            self._error = None
            try:
                table, _warnings, _source = self._load()
            except Exception as exc:  # noqa: BLE001 - 配置坏了就把原因留在 status 里
                self._error = f"调度表读不出来：{exc}"
                return self.status()

            conf = table.get("scheduler") or {}
            grace = int(conf.get("misfire_grace_seconds", 300))
            for job in table.get("jobs") or []:
                if not isinstance(job, dict):
                    continue
                if job.get("kind") not in self._kinds or not job.get("enabled"):
                    continue
                self._add(scheduler, job, grace=grace)
            return self.status()

    def shutdown(self) -> None:
        scheduler = self._scheduler
        self._scheduler = None
        if scheduler is not None:
            try:
                scheduler.shutdown(wait=False)
            except Exception:  # noqa: BLE001 - 已经停了
                pass

    def status(self) -> dict[str, Any]:
        return {
            "running": self._scheduler is not None,
            "timezone": self._timezone,
            "kinds": list(self._kinds),
            "jobs": list(self._loaded),
            "error": self._error,
        }

    # ------------------------------------------------------------ 内部

    def _load(self) -> tuple[dict[str, Any], list[str], str]:
        from ladderstock.cli.schedule import load_schedule

        return load_schedule()

    def _add(self, scheduler: Any, job: dict[str, Any], *, grace: int) -> None:
        from ladderstock.cli.schedule import trigger_interval_seconds

        job_id = str(job.get("id"))
        trigger = job.get("trigger")
        interval = trigger_interval_seconds(trigger)
        common: dict[str, Any] = {
            "id": job_id,
            "name": str(job.get("name") or job_id),
            "replace_existing": True,
            "args": [job],
        }
        try:
            if interval is not None:
                scheduler.add_job(
                    self._execute,
                    "interval",
                    seconds=interval,
                    misfire_grace_time=min(grace, max(1, interval)),
                    **common,
                )
            else:
                hour_text, minute_text = str(trigger).split(":", 1)
                scheduler.add_job(
                    self._execute,
                    "cron",
                    hour=int(hour_text),
                    minute=int(minute_text),
                    **common,
                )
        except Exception as exc:  # noqa: BLE001 - 单条装不上不影响其它任务
            self._error = f"{job_id} 装载失败：{exc}"
            return
        self._loaded.append(job_id)

    def _execute(self, job: dict[str, Any]) -> None:
        """真正执行一轮：结局落盘，页面「上次执行」直接读它。"""
        from ladderstock.cli.schedule import run_job
        from ladderstock.web.schedule_api import _write_run_state

        job_id = str(job.get("id"))
        started_at = datetime.now().isoformat(timespec="seconds")
        payload: dict[str, Any] = {"status": "running", "started_at": started_at, "job_id": job_id}
        try:
            outcome = run_job(job, root=self._root, timezone=self._timezone)
            payload.update(
                {
                    "status": str(outcome.get("status") or "ok"),
                    "detail": outcome.get("detail"),
                    "inbox_id": outcome.get("inbox_id"),
                    "path": outcome.get("path"),
                }
            )
        except Exception as exc:  # noqa: BLE001 - 后台线程里只能记下来
            payload.update({"status": "failed", "detail": f"{type(exc).__name__}: {exc}"})
        payload["finished_at"] = datetime.now().isoformat(timespec="seconds")
        try:
            _write_run_state(self._root, job_id, payload)
        except OSError:
            pass


# ---------------------------------------------------------------- 进程级单例


def get_runner() -> ScheduleRunner | None:
    return _RUNNER


def start_runner(*, data_root: Path | None = None) -> ScheduleRunner | None:
    """进程内启动（幂等）；`LDST_SCHEDULE_IN_WEB=0` 时返回 None。"""
    global _RUNNER
    with _RUNNER_LOCK:
        if _RUNNER is not None:
            return _RUNNER
        root = Path(data_root) if data_root is not None else None
        if root is None:
            from ladderstock.cli.paths import data_dir

            root = Path(data_dir())
        runner = ScheduleRunner(data_root=root)
        if not runner.enabled_by_env():
            return None
        runner.start()
        _RUNNER = runner
        return runner


def reload_runner() -> dict[str, Any] | None:
    """页面开关后调用；没有 runner（单测 / 命令行）时安静返回 None。"""
    runner = get_runner()
    if runner is None:
        return None
    return runner.reload()


__all__ = [
    "DEFAULT_KINDS",
    "RUNNER_ENV",
    "ScheduleRunner",
    "get_runner",
    "reload_runner",
    "start_runner",
]
