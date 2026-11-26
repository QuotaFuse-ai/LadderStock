"""`/api/schedule`：把「定时研究 / 盯盘提醒」搬进页面（第④步的前端入口）。

在此之前，这两类任务只活在 `config/schedule.yaml` 里——跑得再好，用户也得打开
配置文件才知道它在。四条接口都只碰本机这一份调度表，不新增第二种鉴权：

- `GET  /api/schedule/jobs`   任务清单（含今日有没有出过研究卡）；
- `POST /api/schedule/run`    立即跑一次（后台线程，立刻返回，产物进收件箱）；
- `POST /api/schedule/toggle` 开 / 关一个任务（只改目标 job 的 `enabled:` 那一行）；
- `POST /api/schedule/plan`   读一个任务「这一轮会做什么」的人读说明。

写面两条纪律：改 yaml 前先备份原文、改完**必须**回读校验（`validate_table`），
校验不过就整份写回原文——宁可这次没改成功，也不留一个半坏的表让调度器起不来。
"""

from __future__ import annotations

import re
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ladderstock.web.radar import privacy_read_guard, privacy_write_guard

SCHEDULE_PREFIX = "/api/schedule"

SCHEDULE_DENIED = "定时任务面只对本机回环开放（127.0.0.1 / localhost / ::1）"
SCHEDULE_WRITE_DENIED = "定时任务写面只接受同源来源（Origin 或 X-LDST-Client: 1）"

_NO_STORE = {"Cache-Control": "no-store"}

#: 页面上可操作的 kind（用户会手动点「执行」的那四类）
UI_KINDS: tuple[str, ...] = ("agent_research", "watch_alert", "watch_live", "plan_tick")

#: 后台高频任务：页面上**只读展示**（触发时间、最近产物、开没开），不提供开关。
#: 它们由 `web/schedule_runner` 按表跑，和 `ldst schedule sync` 抢同一批上游；
#: 摆在页面上是为了让用户知道「这些活儿有人干」，而不是让用户去点火。
BACKGROUND_KINDS: tuple[str, ...] = (
    "snapshot",
    "snapshot_fast",
    "sync_batch",
    "sync_close",
    "digest",
    "wire_news",
    "knowledge_refresh",
    "reconcile",
    "notify",
    "news_collect",
    "sentiment_live",
    "recall",
)

_KIND_LABELS = {
    "agent_research": "定时研究",
    "watch_alert": "盯盘提醒（每日快照）",
    "watch_live": "盘中实时盯盘（30 秒）",
    "plan_tick": "跨轮任务推进",
    "snapshot": "盘中快照",
    "snapshot_fast": "快照（30 秒）",
    "sync_batch": "行情同步（60 秒）",
    "sync_close": "收盘同步",
    "digest": "摘要",
    "wire_news": "快讯抓取",
    "knowledge_refresh": "知识库刷新",
    "reconcile": "对账",
    "news_collect": "资讯采集（公告/研报/快讯）",
    "notify": "通知",
    "sentiment_live": "盘中实时情绪快照",
    "recall": "观点回访判定（到期对错）",
}

#: 只在交易时段跑的后台任务——非交易时段一次上游都不打（与 `cli/schedule.py` 同口径）
_SESSION_BOUND_KINDS: tuple[str, ...] = ("snapshot", "snapshot_fast", "sync_batch", "sync_close")


async def _read_guard(request: Request) -> None:
    await privacy_read_guard(request, denied=SCHEDULE_DENIED, subject="定时任务")


async def _write_guard(request: Request) -> None:
    await privacy_write_guard(
        request,
        denied=SCHEDULE_DENIED,
        subject="定时任务",
        write_denied=SCHEDULE_WRITE_DENIED,
    )


def _data_root(data_root: Path | None) -> Path:
    if data_root is not None:
        return data_root
    from ladderstock.cli.paths import data_dir

    return data_dir()


def _run_state_path(root: Path, job_id: str) -> Path:
    """一次手动执行的结局落在这里：界面能看见「上次执行成功/失败」。"""
    return root / "schedule_runs" / f"{job_id}.json"


def _read_run_state(root: Path, job_id: str) -> dict[str, Any] | None:
    import json

    path = _run_state_path(root, job_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_run_state(root: Path, job_id: str, payload: dict[str, Any]) -> None:
    import json

    path = _run_state_path(root, job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _latest_digest(data_root: Path, job_id: str) -> dict[str, Any] | None:
    """该任务最近一次留下的研究卡（只认已落盘的产物，不编「刚刚跑过」）。"""
    base = data_root / "research_digest"
    if not base.is_dir():
        return None
    cards = sorted(base.glob(f"*/{job_id}.json"), key=lambda p: p.parent.name, reverse=True)
    if not cards:
        return None
    card = cards[0]
    try:
        stamp = datetime.fromtimestamp(card.stat().st_mtime).isoformat(timespec="minutes")
    except OSError:
        stamp = None
    return {"date": card.parent.name, "path": str(card), "written_at": stamp}


def _plans_view(root: Path) -> dict[str, Any]:
    """用户亲口交待过的长期目标（`ai/plans.py`）——没有页面入口，用户就不知道它在跟。

    列 active **和 paused**：熔断停下的任务必须留在版面上，否则「它不跑了」这件事
    用户永远看不到。每一项带 `review`（逐轮复盘），用户能判断还值不值得继续跟。
    """
    from ladderstock.ai import plans

    try:
        listed = plans.load(root)
    except Exception as exc:  # noqa: BLE001 - 状态文件坏了也要把页面撑起来
        return {"ok": False, "detail": f"任务状态读不出来：{exc}", "items": []}
    visible = [plan for plan in listed if plan.status in (plans.STATUS_ACTIVE, plans.STATUS_PAUSED)]
    visible.sort(
        key=lambda plan: (plan.status != plans.STATUS_ACTIVE, plan.created_at), reverse=False
    )
    return {
        "ok": True,
        "max_failures": plans.MAX_FAILURES,
        "items": [
            {
                "id": plan.id,
                "goal": plan.goal,
                "symbols": list(plan.symbols),
                "label": plan.symbol_label(),
                "frequency": plan.frequency,
                "expires_at": plan.expires_at,
                "status": plan.status,
                "runs": plan.runs,
                "failures": plan.failures,
                "last_error": plan.last_error,
                "last_run_at": plan.last_run_at,
                "last_summary": plan.last_summary,
                "review": plans.review(plan),
            }
            for plan in visible
        ],
    }


def jobs_payload(*, data_root: Path | None = None) -> dict[str, Any]:
    """任务清单：UI_KINDS 可操作，BACKGROUND_KINDS 只读展示。

    把后台高频任务也摆出来，是为了让用户看见「快照 / 同步这些活儿有人在干」；
    它们不带开关按钮，也没有手动执行入口——见 `run_once` 的时段门控。
    """
    from ladderstock.cli.schedule import jobs_view, load_schedule

    root = _data_root(data_root)
    try:
        table, warnings, source = load_schedule()
    except Exception as exc:  # noqa: BLE001 - 配置坏了也要把页面撑起来
        return {
            "ok": False,
            "detail": f"调度表读不出来：{exc}",
            "jobs": [],
            "background": [],
            "warnings": [],
            "source": None,
        }
    by_id = {str(job.get("id")): job for job in (table.get("jobs") or []) if isinstance(job, dict)}
    items: list[dict[str, Any]] = []
    background: list[dict[str, Any]] = []
    for row in jobs_view(table):
        kind = str(row.get("kind") or "")
        job_id = str(row.get("id"))
        job = by_id.get(job_id) or {}
        base: dict[str, Any] = {
            **row,
            "kind_label": _KIND_LABELS.get(kind, kind),
            "latest_digest": _latest_digest(root, job_id),
            "last_run": _read_run_state(root, job_id),
        }
        if kind in UI_KINDS:
            items.append(
                {
                    **base,
                    "question": job.get("question"),
                    "symbols": job.get("symbols") or [],
                    "alert_pct": job.get("alert_pct"),
                    "move_pct": job.get("move_pct"),
                    "min_gap_min": job.get("min_gap_min"),
                    # 「推进时能不能新建任务」的显式开关：只有 plan_tick 有意义
                    "allow_plan_create": (
                        bool(job.get("allow_plan_create")) if kind == "plan_tick" else None
                    ),
                }
            )
        elif kind in BACKGROUND_KINDS:
            background.append(
                {
                    **base,
                    # 后台任务没有这个开关，字段仍保留以保证前端一套解析口径
                    "allow_plan_create": None,
                    "session_bound": kind in _SESSION_BOUND_KINDS,
                }
            )
    out: dict[str, Any] = {
        "ok": True,
        "jobs": items,
        "background": background,
        "plans": _plans_view(root),
        "warnings": list(warnings),
        "source": source,
        "today": date.today().isoformat(),
    }
    # 后台调度到底在不在跑：页面据此区分「任务配好了但没人执行」和「正在按表执行」
    from ladderstock.web.schedule_runner import get_runner

    runner = get_runner()
    out["scheduler"] = runner.status() if runner is not None else None
    return out


def _patch_job_field(
    job_id: str,
    field: str,
    value: str,
    *,
    path: Path | None = None,
    create_after: str = "enabled",
) -> dict[str, Any]:
    """只改一个任务里的一个布尔字段。

    yaml 用文本改而不是 `yaml.safe_dump`：调度表里有大段中文注释，重序列化会把它们
    连同顺序一起丢掉。所以按块定位、单行替换；字段原先没有时，插在 `enabled:`
    那一行后面（缩进沿用该行），改完再让 `validate_table` 复核。
    """
    from ladderstock.cli.schedule import load_schedule, schedule_config_path, validate_table
    from ladderstock.errors import EXIT_CONFIG, LadderStockError

    target = path or schedule_config_path()
    if not target.is_file():
        raise LadderStockError(f"配置缺失：{target}", EXIT_CONFIG)
    original = target.read_text(encoding="utf-8")
    # 按行定位一个 job 块：从 `- id: <id>` 到下一个 `- id:` 之前。用正则的非贪婪
    # `(?:.*\n)*?` 只匹配到 0 行——那是踩过的坑，所以这里按行扫，边界不靠正则自觉。
    lines = original.splitlines(keepends=True)
    start: int | None = None
    for index, line in enumerate(lines):
        if re.match(rf"\s*- id:\s+{re.escape(job_id)}\s*$", line.rstrip("\n")):
            start = index
            break
    if start is None:
        raise LadderStockError(f"调度表里没有任务 {job_id}", EXIT_CONFIG)
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if re.match(r"\s*- id:", lines[index]):
            end = index
            break
    block = "".join(lines[start:end])
    updated_block, count = re.subn(
        rf"(\n\s+{re.escape(field)}:\s*)(true|false)",
        rf"\g<1>{value}",
        block,
        count=1,
    )
    if count == 0:
        # 字段没写过：插在锚点行后面，缩进照抄锚点行，保证仍是同一个 job 的字段。
        # 只匹配到行末（`[ \t]*` + 前瞻换行），否则会把 `enabled: true` 之后的换行
        # 一起吃掉，插进去就变成 `trueallow_plan_create: false`。
        match = re.search(
            rf"(\n)([ \t]+){re.escape(create_after)}:[ \t]*(?:true|false)[ \t]*(?=\n)",
            block,
        )
        if match is None:
            raise LadderStockError(
                f"任务 {job_id} 既没有 {field}，也没有可插入的 {create_after} 行", EXIT_CONFIG
            )
        insert_at = match.end()
        updated_block = (
            block[:insert_at] + f"\n{match.group(2)}{field}: {value}" + block[insert_at:]
        )
    candidate = (
        original[: sum(len(line) for line in lines[:start])] + updated_block + "".join(lines[end:])
    )
    import yaml

    try:
        validate_table(yaml.safe_load(candidate) or {})
    except Exception as exc:  # noqa: BLE001 - 校验不过就整份撤回
        raise LadderStockError(f"改完校验不过，已放弃本次修改：{exc}", EXIT_CONFIG) from exc
    target.write_text(candidate, encoding="utf-8")
    table, warnings, source = load_schedule()
    return {"id": job_id, "path": str(target), "warnings": list(warnings), "source": source}


def set_enabled(job_id: str, enabled: bool, *, path: Path | None = None) -> dict[str, Any]:
    """只改一个任务的 `enabled:` 行（字段缺失时按块结构补一行）。"""
    out = _patch_job_field(job_id, "enabled", "true" if enabled else "false", path=path)
    return {**out, "enabled": enabled}


def set_allow_plan_create(job_id: str, allow: bool, *, path: Path | None = None) -> dict[str, Any]:
    """只改一个 `plan_tick` 任务的 `allow_plan_create:` 行。

    这是「推进时不建新任务」的显式开关：默认关（推进提问本身常带「持续跟踪…」，
    开着会每推进一步就繁殖一条同标任务）。用户要它可改，所以给一条独立写接口。
    """
    from ladderstock.cli.schedule import schedule_config_path
    from ladderstock.errors import EXIT_CONFIG, LadderStockError

    target = path or schedule_config_path()
    if not target.is_file():
        raise LadderStockError(f"配置缺失：{target}", EXIT_CONFIG)
    import yaml

    try:
        table = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise LadderStockError(f"{target} 无法解析：{exc}", EXIT_CONFIG) from exc
    job = next(
        (
            item
            for item in (table.get("jobs") or [])
            if isinstance(item, dict) and str(item.get("id")) == job_id
        ),
        None,
    )
    if job is None:
        raise LadderStockError(f"调度表里没有任务 {job_id}", EXIT_CONFIG)
    if str(job.get("kind")) != "plan_tick":
        raise LadderStockError(
            f"只有跨轮任务推进（plan_tick）才有这个开关，{job_id} 是 {job.get('kind')}",
            EXIT_CONFIG,
        )
    out = _patch_job_field(job_id, "allow_plan_create", "true" if allow else "false", path=path)
    return {**out, "allow_plan_create": allow}


def run_once(job_id: str, *, data_root: Path | None = None) -> dict[str, Any]:
    """立即跑一次：后台线程执行，立刻返回（前端轮询收件箱看结果）。

    同步等会让 HTTP 请求挂到模型返回（实测一轮 agent 16 秒），页面会以为卡死。
    """
    from ladderstock.cli.schedule import load_schedule

    root = _data_root(data_root)
    try:
        table, _warnings, _source = load_schedule()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"调度表读不出来：{exc}") from exc
    jobs = [job for job in (table.get("jobs") or []) if isinstance(job, dict)]
    job = next((item for item in jobs if str(item.get("id")) == job_id), None)
    if job is None:
        raise HTTPException(status_code=404, detail=f"没有任务 {job_id}")
    kind = str(job.get("kind"))
    if kind not in UI_KINDS and kind not in BACKGROUND_KINDS:
        raise HTTPException(status_code=400, detail=f"{job_id} 不是可在页面上手动触发的类型")
    # 快照 / 同步这类后台任务会现取上游：非交易时段一律拒绝手动点火，
    # 免得页面点一下就和 `ldst schedule sync` 抢表。
    if kind in _SESSION_BOUND_KINDS:
        from ladderstock.core import intraday

        if not intraday.in_trading_session(datetime.now()):
            raise HTTPException(
                status_code=400,
                detail=f"{job_id} 只在交易时段（09:30–11:30 / 13:00–15:00）可以手动跑，现在不点火",
            )

    # 同一条任务串行：上一轮还在跑就拒绝重复点击，别让两次研究叠在一起抢上游
    last = _read_run_state(root, job_id) or {}
    if last.get("status") == "running":
        started = last.get("started_at")
        try:
            age = (datetime.now() - datetime.fromisoformat(str(started))).total_seconds()
        except (TypeError, ValueError):
            age = 0.0
        if 0 <= age < 600:
            return {
                "id": job_id,
                "started": False,
                "status": "running",
                "detail": f"上一次还在跑（{started} 开始），等它结束再点",
                **last,
            }

    captured: dict[str, Any] = {
        "status": "running",
        "started_at": datetime.now().isoformat(timespec="seconds"),
    }

    def _worker() -> None:
        from ladderstock.cli.schedule import run_job

        captured["status"] = "running"
        try:
            outcome = run_job(job, root=root)
            captured.update(
                {
                    "status": str(outcome.get("status") or "ok"),
                    "detail": outcome.get("detail"),
                    "inbox_id": outcome.get("inbox_id"),
                    "path": outcome.get("path"),
                    "finished_at": outcome.get("finished_at"),
                }
            )
            # 明细落盘（QUOT-XXX）：wire 只报「28 个源不可用」时，名字留在这里，
            # 页面才能回答「是哪几个」。只有明确白名单字段才带，避免塞进大对象。
            for extra in ("sources_unavailable",):
                value = outcome.get(extra)
                if value:
                    captured[extra] = value
        except Exception as exc:  # noqa: BLE001 - 线程里的异常只能记下来
            captured.update({"status": "failed", "detail": f"{type(exc).__name__}: {exc}"})
        captured.setdefault("finished_at", datetime.now().isoformat(timespec="seconds"))
        # 结局落盘：页面刷新后仍能看到「上次执行」成没成，不用盯着面板猜
        try:
            _write_run_state(root, job_id, {**captured, "job_id": job_id})
        except OSError:
            pass

    thread = threading.Thread(target=_worker, name=f"ldst-schedule-{job_id}", daemon=True)
    thread.start()
    return {"id": job_id, "started": True, **captured}


def build_router(*, data_root: Path | None = None) -> APIRouter:
    """四条路由；数据根可注入，单测写临时目录即可跑。"""
    router = APIRouter(prefix=SCHEDULE_PREFIX)

    @router.get("/jobs", dependencies=[Depends(_read_guard)])
    def schedule_jobs() -> JSONResponse:
        return JSONResponse(jobs_payload(data_root=data_root), headers=_NO_STORE)

    @router.get("/plan/{job_id}", dependencies=[Depends(_read_guard)])
    def schedule_plan(job_id: str) -> JSONResponse:
        payload = jobs_payload(data_root=data_root)
        for job in payload.get("jobs") or []:
            if str(job.get("id")) == job_id:
                return JSONResponse({"ok": True, "job": job}, headers=_NO_STORE)
        raise HTTPException(status_code=404, detail=f"没有任务 {job_id}")

    @router.post("/run", dependencies=[Depends(_write_guard)])
    def schedule_run(body: Annotated[dict[str, Any], Body(...)]) -> JSONResponse:
        job_id = str(body.get("id") or "").strip()
        if not job_id:
            raise HTTPException(status_code=400, detail="缺少 id")
        return JSONResponse(run_once(job_id, data_root=data_root), headers=_NO_STORE)

    @router.post("/toggle", dependencies=[Depends(_write_guard)])
    def schedule_toggle(body: Annotated[dict[str, Any], Body(...)]) -> JSONResponse:
        job_id = str(body.get("id") or "").strip()
        if not job_id:
            raise HTTPException(status_code=400, detail="缺少 id")
        if "enabled" not in body:
            raise HTTPException(status_code=400, detail="缺少 enabled")
        from ladderstock.errors import LadderStockError

        try:
            out = set_enabled(job_id, bool(body.get("enabled")))
        except LadderStockError as exc:
            raise HTTPException(status_code=400, detail=exc.message) from None
        # 开关立刻生效：web 进程内的调度器按新表重建，不用重启服务
        from ladderstock.web.schedule_runner import reload_runner

        reloaded = reload_runner()
        if reloaded is not None:
            out["scheduler"] = reloaded
        return JSONResponse(out, headers=_NO_STORE)

    @router.post("/plan/status", dependencies=[Depends(_write_guard)])
    def plan_status(body: Annotated[dict[str, Any], Body(...)]) -> JSONResponse:
        """改一条跨轮任务的状态：`active`（恢复，失败计数归零）/ `paused` / `done`。

        熔断停下的任务必须能被用户手动放行，否则「它自己停了」就变成死路——
        页面上那个「恢复」按钮走的就是这条。
        """
        from ladderstock.ai import plans

        plan_id = str(body.get("id") or "").strip()
        if not plan_id:
            raise HTTPException(status_code=400, detail="缺少 id")
        status = str(body.get("status") or "").strip()
        if status not in (plans.STATUS_ACTIVE, plans.STATUS_PAUSED, plans.STATUS_DONE):
            raise HTTPException(status_code=400, detail=f"未知任务状态：{status!r}")
        updated = plans.set_status(plan_id, status, data_root=data_root)
        if updated is None:
            raise HTTPException(status_code=404, detail=f"没有任务 {plan_id}")
        return JSONResponse(
            {"ok": True, "plan": updated.to_dict(), "plans": _plans_view(_data_root(data_root))},
            headers=_NO_STORE,
        )

    @router.post("/plan/create-flag", dependencies=[Depends(_write_guard)])
    def plan_create_flag(body: Annotated[dict[str, Any], Body(...)]) -> JSONResponse:
        """改跨轮任务推进的「允不允许新建任务」开关。

        默认关：推进提问本身常带「持续跟踪 600000…」，开着会每推进一步繁殖一条
        同标任务。用户要能自己开——所以给它一条独立的写接口与页面勾选。
        """
        from ladderstock.errors import LadderStockError

        job_id = str(body.get("id") or "").strip()
        if not job_id:
            raise HTTPException(status_code=400, detail="缺少 id")
        if "allow" not in body:
            raise HTTPException(status_code=400, detail="缺少 allow")
        try:
            out = set_allow_plan_create(job_id, bool(body.get("allow")))
        except LadderStockError as exc:
            raise HTTPException(status_code=400, detail=exc.message) from None
        from ladderstock.web.schedule_runner import reload_runner

        reloaded = reload_runner()
        if reloaded is not None:
            out["scheduler"] = reloaded
        return JSONResponse(out, headers=_NO_STORE)

    return router


__all__ = [
    "BACKGROUND_KINDS",
    "SCHEDULE_DENIED",
    "SCHEDULE_PREFIX",
    "SCHEDULE_WRITE_DENIED",
    "UI_KINDS",
    "build_router",
    "jobs_payload",
    "run_once",
    "set_allow_plan_create",
    "set_enabled",
]
