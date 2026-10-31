"""`ldst schedule list|run`：定时任务表（APScheduler）与单轮 tick。

调度表落 `config/schedule.yaml`（JSON Schema 校验，`ldst config validate` 一并检查），
默认 **09:05 盘前提醒**（notify）、**盘中 6 时点快照**（snapshot，QUOT-286）与
**15:05 收盘对账**（reconcile）。

边界（DEC-006 / DEC-004）：

- 任务只跑本机动作（发本机通知、对账本地已落盘数据、按治理链路取 peer 读数）；
- 单个任务失败只记 `degraded` / `skipped`，不拖垮整轮、不中断调度器；
- `schedule run --once` 跑一轮就退出（冒烟、手动补跑都靠它）；不带 `--once` 是长驻
  调度器，按 `scheduler.timezone` 的本地时间触发，Ctrl+C 优雅退出。
- snapshot 的时点窗口在 `core.intraday` 判定：窗口外只记 `skipped`，**不补跑**。
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer

from ladderstock.cli.output import OutputOption, emit, error
from ladderstock.cli.paths import config_dir, project_root
from ladderstock.errors import EXIT_CONFIG, EXIT_OK, EXIT_USAGE, LadderStockError
from ladderstock.watch import notifier

DEFAULT_TIMEZONE = "Asia/Shanghai"
DEFAULT_MISFIRE_GRACE_SECONDS = 300

# 内置默认表：与 config/schedule.yaml 同口径。配置缺失时按此运行并给 warning，
# 这样「第一次装上就能跑」和「配置坏了不静默」两件事都不牺牲——坏了的那份会
# 解析失败并按退出码 3 报出来。
DEFAULT_JOBS: tuple[dict[str, Any], ...] = (
    {
        "id": "premarket_reminder",
        "name": "09:05 盘前提醒",
        "kind": "notify",
        "trigger": "09:05",
        "enabled": True,
        "channel": notifier.CHANNEL_CONSOLE,
        "message": "盘前提醒：核对今日盯盘阈值、昨日晋级率与自选池，09:25 前把下单计划定下来",
    },
    {
        "id": "snapshot_0925",
        "name": "09:25 竞价快照",
        "kind": "snapshot",
        "trigger": "09:25",
        "enabled": True,
    },
    {
        "id": "snapshot_0935",
        "name": "09:35 开盘快照",
        "kind": "snapshot",
        "trigger": "09:35",
        "enabled": True,
    },
    {
        "id": "snapshot_1000",
        "name": "10:00 盘中快照",
        "kind": "snapshot",
        "trigger": "10:00",
        "enabled": True,
    },
    {
        "id": "snapshot_1130",
        "name": "11:30 午盘快照",
        "kind": "snapshot",
        "trigger": "11:30",
        "enabled": True,
    },
    {
        "id": "snapshot_1400",
        "name": "14:00 午后快照",
        "kind": "snapshot",
        "trigger": "14:00",
        "enabled": True,
    },
    {
        "id": "snapshot_1500",
        "name": "15:00 收盘快照",
        "kind": "snapshot",
        "trigger": "15:00",
        "enabled": True,
    },
    {
        "id": "close_reconcile",
        "name": "15:05 收盘对账",
        "kind": "reconcile",
        "trigger": "15:05",
        "enabled": True,
    },
)


# ---------------------------------------------------------------- 配置装载


def schedule_config_path() -> Path:
    return config_dir() / "schedule.yaml"


def _schema_path() -> Path:
    """调度表的 schema：配置目录优先，否则退回仓库自带的 `config/schema/`。

    自定义 `LDST_CONFIG_DIR` 往往只有一份 schedule.yaml；此时若找不到 schema 就
    退化成"什么都能过"，等于没有校验。所以宁可读仓库里那份，也别静默放宽。
    """
    local = config_dir() / "schema" / "schedule.schema.json"
    if local.is_file():
        return local
    return project_root() / "config" / "schema" / "schedule.schema.json"


def _structural_problems(data: Mapping[str, Any]) -> list[str]:
    """schema 缺失时的兜底自检：只覆盖会让调度器起不来的硬伤。"""
    problems: list[str] = []
    scheduler = data.get("scheduler")
    if not isinstance(scheduler, Mapping):
        problems.append("/scheduler: 缺失或不是对象")
    jobs = data.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        problems.append("/jobs: 至少要有一条任务")
        return problems
    for index, job in enumerate(jobs):
        where = f"/jobs/{index}"
        if not isinstance(job, Mapping):
            problems.append(f"{where}: 不是对象")
            continue
        for field in ("id", "name", "kind", "trigger", "enabled"):
            if field not in job:
                problems.append(f"{where}/{field}: 缺少必填字段")
        trigger = job.get("trigger")
        if isinstance(trigger, str) and len(trigger) == 5 and trigger[2] == ":":
            hour, minute = trigger[:2], trigger[3:]
            if not (hour.isdigit() and minute.isdigit()):
                problems.append(f"{where}/trigger: 需要 HH:MM")
        elif "trigger" in job:
            problems.append(f"{where}/trigger: 需要 HH:MM")
        if job.get("kind") not in ("notify", "reconcile", "snapshot"):
            problems.append(f"{where}/kind: 只支持 notify / reconcile / snapshot")
    return problems


def _default_table() -> dict[str, Any]:
    return {
        "scheduler": {
            "timezone": DEFAULT_TIMEZONE,
            "misfire_grace_seconds": DEFAULT_MISFIRE_GRACE_SECONDS,
        },
        "jobs": [copy.deepcopy(job) for job in DEFAULT_JOBS],
    }


def validate_table(data: Mapping[str, Any]) -> None:
    """按 schema 校验；schema 缺失时退到结构自检（宁可严一点，也别静默放过）。"""
    schema_file = _schema_path()
    problems: list[str] = []
    if schema_file.is_file():
        from jsonschema import Draft202012Validator

        schema = json.loads(schema_file.read_text(encoding="utf-8"))
        problems = [
            f"/{'/'.join(map(str, err.path))}: {err.message}"
            for err in sorted(
                Draft202012Validator(schema).iter_errors(dict(data)),
                key=lambda err: list(err.path),
            )
        ]
    else:
        problems = _structural_problems(data)
    if not problems:
        jobs = data.get("jobs")
        if not isinstance(jobs, list) or not jobs:
            problems.append("/jobs: 至少要有一条任务")
    if problems:
        raise LadderStockError(
            "schedule.yaml: " + "；".join(problems), EXIT_CONFIG
        )


def load_schedule(
    path: Path | None = None,
) -> tuple[dict[str, Any], list[str], str]:
    """读调度表：返回 (表, 警告, 来源说明)。解析/校验失败抛 `LadderStockError(3)`。"""
    target = path or schedule_config_path()
    if not target.is_file():
        return (
            _default_table(),
            [f"配置缺失：{target}（使用内置默认表：09:05 盘前提醒、15:05 收盘对账）"],
            "builtin",
        )
    import yaml

    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 - 原样把原因交给用户
        raise LadderStockError(f"schedule.yaml 解析失败：{exc}", EXIT_CONFIG) from exc
    if not isinstance(raw, dict):
        raise LadderStockError("schedule.yaml 顶层必须是对象", EXIT_CONFIG)
    validate_table(raw)

    scheduler = raw.get("scheduler") or {}
    jobs = [job for job in raw.get("jobs") or [] if isinstance(job, dict)]
    table: dict[str, Any] = {
        "scheduler": {
            "timezone": str(scheduler.get("timezone") or DEFAULT_TIMEZONE),
            "misfire_grace_seconds": int(
                scheduler.get("misfire_grace_seconds", DEFAULT_MISFIRE_GRACE_SECONDS)
            ),
        },
        "jobs": jobs,
    }
    return table, [], str(target)


def jobs_view(table: Mapping[str, Any]) -> list[dict[str, Any]]:
    jobs = [job for job in table.get("jobs") or [] if isinstance(job, Mapping)]
    ordered = sorted(jobs, key=lambda job: str(job.get("trigger") or "99:99"))
    return [
        {
            "id": job.get("id"),
            "name": job.get("name"),
            "kind": job.get("kind"),
            "trigger": job.get("trigger"),
            "enabled": bool(job.get("enabled")),
            "channel": job.get("channel") or "-",
        }
        for job in ordered
    ]


def _fail(message: str, code: int) -> NoReturn:
    error(message)
    raise typer.Exit(code)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------- 任务执行


def _run_reconcile(day: date | None = None) -> dict[str, Any]:
    """收盘对账：只读本地已落盘的验证条件与次日数据，缺就先跳过（不伪造结论）。"""
    from ladderstock.core import validation as validation_module

    target = day or date.today()
    try:
        outcome = validation_module.reconcile(target)
    except FileNotFoundError as exc:
        return {"status": "skipped", "detail": str(exc), "date": target.isoformat()}
    status = str(outcome.get("status") or "unknown")
    return {
        "status": "ok" if status == "ok" else "partial",
        "detail": str(outcome.get("reason") or f"对账状态 {status}"),
        "date": target.isoformat(),
    }


def run_job(
    job: Mapping[str, Any],
    *,
    now: datetime | None = None,
    timezone: str | None = None,
    root: Path | None = None,
    registry: Any = None,
) -> dict[str, Any]:
    """执行一条任务；任何失败都折成结果字段，不向上抛（调度器要活着）。

    `now` / `timezone` 只给 snapshot 用：时点窗口按调度表的本地时间判定，
    测试可注入固定时刻（不靠系统时钟碰运气）。
    """
    started = datetime.now()
    result: dict[str, Any] = {
        "id": job.get("id"),
        "name": job.get("name"),
        "kind": job.get("kind"),
        "trigger": job.get("trigger"),
    }
    try:
        kind = str(job.get("kind") or "")
        if kind == "notify":
            delivery = notifier.send(
                job.get("channel"),
                str(job.get("name") or "LadderStock 提醒"),
                str(job.get("message") or ""),
            )
            result.update(
                status="ok" if delivery.ok else "degraded",
                detail=delivery.detail,
            )
        elif kind == "reconcile":
            result.update(_run_reconcile())
        elif kind == "snapshot":
            from ladderstock.core import intraday

            result.update(
                intraday.run_slot(
                    str(job.get("trigger") or ""),
                    now=now,
                    timezone=timezone,
                    root=root,
                    registry=registry,
                )
            )
        else:
            result.update(status="failed", detail=f"未知任务类型 {kind!r}")
    except Exception as exc:  # noqa: BLE001 - 单条任务失败不拖垮整轮
        result.update(status="failed", detail=f"任务异常：{exc}")
    result["finished_at"] = _now_iso()
    result["elapsed_ms"] = int((datetime.now() - started).total_seconds() * 1000)
    return result


def _run_job_logged(job: Mapping[str, Any], *, timezone: str | None = None) -> None:
    outcome = run_job(job, timezone=timezone)
    error(
        f"定时任务 {outcome.get('id')}（{outcome.get('trigger')}）"
        f"-> {outcome.get('status')}：{outcome.get('detail')}"
    )


def _trigger_time(trigger: str) -> tuple[int, int]:
    hour_text, minute_text = trigger.split(":", 1)
    return int(hour_text), int(minute_text)


def start_daemon(table: Mapping[str, Any], jobs: Sequence[Mapping[str, Any]]) -> None:
    """按任务表启动 APScheduler 长驻调度（阻塞到 Ctrl+C）。"""
    try:
        from apscheduler.schedulers.blocking import (  # type: ignore[import-untyped]
            BlockingScheduler,
        )
    except ImportError as exc:  # pragma: no cover - 环境缺依赖
        _fail("定时调度需要 APScheduler：pip install apscheduler", EXIT_CONFIG)
        raise AssertionError from exc  # pragma: no cover - _fail 已退出

    scheduler_conf = table.get("scheduler") or {}
    timezone = str(scheduler_conf.get("timezone") or DEFAULT_TIMEZONE)
    grace = int(
        scheduler_conf.get("misfire_grace_seconds", DEFAULT_MISFIRE_GRACE_SECONDS)
    )
    scheduler = BlockingScheduler(
        timezone=timezone,
        job_defaults={"misfire_grace_time": grace, "coalesce": True},
    )
    for job in jobs:
        hour, minute = _trigger_time(str(job.get("trigger")))
        scheduler.add_job(
            _run_job_logged,
            "cron",
            hour=hour,
            minute=minute,
            id=str(job.get("id")),
            name=str(job.get("name")),
            args=[job],
            kwargs={"timezone": timezone},
            replace_existing=True,
        )
    error(
        f"定时任务已启动（{timezone}）："
        + "、".join(f"{job.get('trigger')} {job.get('name')}" for job in jobs)
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):  # pragma: no cover - 交互退出
        error("定时任务已停止")
        try:
            scheduler.shutdown(wait=False)
        except Exception:  # noqa: BLE001 - 已经停了
            pass


# ---------------------------------------------------------------- 命令实现


def schedule_list(output: OutputOption = None) -> None:
    """列出调度表：时间、任务、类型、渠道、启用状态。"""
    try:
        table, warnings, source = load_schedule()
    except LadderStockError as exc:
        _fail(exc.message, exc.code)

    scheduler_conf = table["scheduler"]
    payload: dict[str, Any] = {
        "ok": True,
        "config": source,
        "timezone": scheduler_conf["timezone"],
        "misfire_grace_seconds": scheduler_conf["misfire_grace_seconds"],
        "job_count": len(table["jobs"]),
        "jobs": jobs_view(table),
    }
    if warnings:
        payload["warnings"] = warnings
        error(warnings[0])
    emit(payload, output)
    raise typer.Exit(EXIT_OK)


def schedule_run(
    once: Annotated[
        bool, typer.Option("--once", help="只跑一轮启用的任务后退出")
    ] = False,
    job: Annotated[
        str | None, typer.Option("--job", help="只跑指定任务 id")
    ] = None,
    output: OutputOption = None,
) -> None:
    """跑一轮启用的任务（`--once`），或按任务表长驻调度。"""
    try:
        table, warnings, source = load_schedule()
    except LadderStockError as exc:
        _fail(exc.message, exc.code)

    jobs: Sequence[Mapping[str, Any]] = [
        row for row in table["jobs"] if isinstance(row, Mapping)
    ]
    if job is not None:
        matched = [row for row in jobs if row.get("id") == job]
        if not matched:
            known = "、".join(str(row.get("id")) for row in jobs) or "（空）"
            _fail(f"没有 id 为 {job!r} 的任务；已有：{known}", EXIT_USAGE)
        jobs = matched

    enabled = [row for row in jobs if row.get("enabled")]
    if not enabled:
        emit(
            {
                "ok": True,
                "config": source,
                "mode": "once" if once else "daemon",
                "detail": "没有启用的任务（检查 config/schedule.yaml 的 enabled）",
                "results": [],
            },
            output,
        )
        raise typer.Exit(EXIT_OK)

    if not once:
        start_daemon(table, enabled)
        return

    results = [run_job(row, timezone=table["scheduler"]["timezone"]) for row in enabled]
    payload: dict[str, Any] = {
        "ok": True,
        "config": source,
        "mode": "once",
        "ran_at": _now_iso(),
        "job_count": len(results),
        "results": results,
    }
    if warnings:
        payload["warnings"] = warnings
        error(warnings[0])
    emit(payload, output)
    raise typer.Exit(EXIT_OK)


def register(schedule_app: typer.Typer) -> None:
    """把 `schedule` 子命令挂到 main.py 已建好的 Typer 组上。"""
    schedule_app.command("list", help="查看调度表（时间 / 任务 / 类型 / 渠道 / 启用）")(
        schedule_list
    )
    schedule_app.command("run", help="跑一轮启用的任务（--once）或长驻调度")(
        schedule_run
    )


__all__ = [
    "DEFAULT_JOBS",
    "DEFAULT_MISFIRE_GRACE_SECONDS",
    "DEFAULT_TIMEZONE",
    "jobs_view",
    "load_schedule",
    "register",
    "run_job",
    "schedule_config_path",
    "schedule_list",
    "schedule_run",
    "start_daemon",
    "validate_table",
]
