"""StockSenate 动作面：在 LadderStock 里真发起一次合议 / 回测。

为什么单独开一个模块：`senate_reports`（`stock_senate_report_query`）只读**已经
落盘的报告**，是取数口；而 `stock_senate_analysis` / `stock_senate_backtest` 是
**动作**——跑一次完整链路（7 分析师 → 多空辩论 → 研究经理 → 交易员 → 风控辩论
→ 终审），实测分钟级，且会新写一份报告。把动作塞进 `config/sources.yaml` 的
dataset 会让每次同步都触发一次合议（且带 hitl 确认），语义就乱了；所以动作走本
模块的独立端点，按需触发，取数仍回到 `senate_reports`。

口径：
- 连接地址只从 `resolve_peer_control()["senate"].mcp_port` 取（进程内已按当前环境
  平移，test=18765 / prod=8765），不在这份代码里写死任何端口；
- 触发即起后台线程跑 `asyncio.run(...)`，接口立刻回 task_id，前端轮询进度；
  合议是分钟级任务，不能占着一个 HTTP 请求不放；
- 结果原样回传 Senate 的 payload（评级/依据/风险/质量分），不加解释、不编数字；
  Senate 没给的一律缺省为空，本模块不补默认值。
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ladderstock.core.boundary import resolve_peer_control
from ladderstock.research import debate as debate_bridge
from ladderstock.web.control import write_source_allowed

ANALYSIS_TOOL = "stock_senate_analysis"
BACKTEST_TOOL = "stock_senate_backtest"
REPORT_TOOL = "stock_senate_report_query"

#: 一次完整合议的兜底上限：600519 实测 515s，留足余量但不无限等。
ANALYSIS_TIMEOUT_SEC = 3600.0
BACKTEST_TIMEOUT_SEC = 1800.0
CAPABILITY_TIMEOUT_SEC = 20.0

#: 任务视图里保留最近多少个（进程内，重启即清）
MAX_TASKS_KEPT = 40


class SenateAnalysisRequest(BaseModel):
    code: str = Field(..., min_length=1, description="6 位 A 股代码，如 600000")
    date: str | None = Field(None, description="YYYY-MM-DD；缺省=最近交易日")
    hitl: bool = Field(False, description="是否在中途人工确认；默认关，跑通不打断")


class SenateBacktestRequest(BaseModel):
    codes: list[str] = Field(..., min_length=1, description="标的池，如 ['600000']")
    start: str = Field(..., description="回测起点 YYYY-MM-DD")
    end: str = Field(..., description="回测终点 YYYY-MM-DD")
    window: int = Field(5, ge=1, le=60, description="持有窗口（交易日）")
    slippage: float = Field(0.0, ge=0.0, description="滑点（比例）")
    fee: float = Field(0.0, ge=0.0, description="费率（比例）")


@dataclass
class _Task:
    id: str
    kind: str
    tool: str
    arguments: dict[str, Any]
    status: str = "running"  # running | done | error
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    result: Any = None
    error: str | None = None
    #: 已完成的大模型往返次数（真实计数，来自 Senate 进程日志；拿不到日志时保持 0）
    steps_done: int = 0
    #: 按 steps_done 推出来的环节名（`分析师` / `多空辩论` / …），仅用于展示
    stage_label: str = ""
    #: 环节进入时刻（相对任务开始的秒数）——页面用它画"辩论过程"的时间线
    stage_log: list[dict[str, Any]] = field(default_factory=list)


#: Senate 分析链路的环节与「大致发生在第几次模型往返之后」。这是**展示用**的映射：
#: 进度条上的次数是真实计数，环节名是按次数推断的（顺序来自 Senate 的角色编排）。
_STAGE_STEPS: tuple[tuple[int, str], ...] = (
    (0, "准备取数"),
    (1, "分析师集群"),
    (7, "多空辩论"),
    (9, "研究经理裁决"),
    (11, "交易员方案"),
    (13, "风控与终审"),
)

#: Senate 一条完整合议大约多少次模型往返（用于进度条总量的粗口径）
_SENATE_STEPS_ESTIMATE = 15


def _stage_for(steps: int) -> str:
    label = _STAGE_STEPS[0][1]
    for threshold, name in _STAGE_STEPS:
        if steps >= threshold:
            label = name
    return label


def _senate_log_candidates() -> list[Path]:
    """Senate 进程的日志落点候选（它的 stderr 每条一行 `HTTP Request: POST …`）。

    按环境变量 `LDST_SENATE_LOG` → `%TEMP%` → 历史落点 `D:\\Temp` 的顺序找；
    全都不存在就不提供进度（前端只显示已用时），不猜、不编。
    """
    out: list[Path] = []
    env = os.environ.get("LDST_SENATE_LOG")
    if env:
        out.append(Path(env))
    temp = os.environ.get("TEMP") or os.environ.get("TMP")
    if temp:
        out.append(Path(temp) / "ss-mcp.err")
    out.append(Path(r"D:\Temp\ss-mcp.err"))
    return out


def _find_senate_log() -> Path | None:
    for path in _senate_log_candidates():
        try:
            if path.is_file():
                return path
        except OSError:
            continue
    return None


def _count_llm_roundtrips(path: Path) -> int:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return -1
    return text.count("HTTP Request: POST")


def _watch_progress(task: _Task, log_path: Path, baseline: int) -> None:
    """后台数日志里的模型往返次数：这是 Senate 唯一稳定可读的真实进度信号。"""
    last_stage = ""
    while True:
        with _LOCK:
            if task.status != "running":
                return
        time.sleep(1.0)
        total = _count_llm_roundtrips(log_path)
        if total < 0:
            return
        steps = max(0, total - baseline)
        # 并发跑两个任务时日志会串台（已用 start_task 的串行守卫挡住，这里是兜底）：
        # 计数超过一条合议的往返总量就截断，别让页面出现 20/15 这种自相矛盾的数字。
        steps = min(steps, _SENATE_STEPS_ESTIMATE)
        stage = _stage_for(steps)
        with _LOCK:
            if task.status != "running":
                return
            task.steps_done = steps
            task.stage_label = stage
            if stage != last_stage:
                last_stage = stage
                task.stage_log.append(
                    {
                        "stage": stage,
                        "steps": steps,
                        "at_sec": round(time.time() - task.started_at, 1),
                    }
                )


_TASKS: dict[str, _Task] = {}
_LOCK = threading.Lock()


def senate_mcp_url() -> str:
    """按当前环境解析 Senate 的 MCP 地址（端口来自 peer 注册表，不写死）。"""
    ctl = resolve_peer_control()["senate"]
    return f"http://127.0.0.1:{int(ctl.mcp_port)}/mcp"


def _content_text(result: Any) -> str:
    parts: list[str] = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text.strip():
            parts.append(text)
    return "\n".join(parts)


def _decode(text: str) -> Any:
    """Senate 的工具回 JSON 文本；解不开就原样带回去，不丢内容、不假装成功。"""
    stripped = text.strip()
    if not stripped:
        return {}
    try:
        return json.loads(stripped)
    except ValueError:
        return {"raw": stripped}


async def _call_tool(tool: str, arguments: dict[str, Any], timeout: float) -> Any:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(senate_mcp_url()) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await asyncio.wait_for(session.call_tool(tool, arguments), timeout=timeout)
            return _decode(_content_text(result))


def _prune() -> None:
    if len(_TASKS) <= MAX_TASKS_KEPT:
        return
    finished = sorted(
        (t for t in _TASKS.values() if t.status != "running"),
        key=lambda t: t.started_at,
    )
    for task in finished[: len(_TASKS) - MAX_TASKS_KEPT]:
        _TASKS.pop(task.id, None)


def start_task(kind: str, tool: str, arguments: dict[str, Any], timeout: float) -> _Task:
    task = _Task(id=uuid.uuid4().hex[:12], kind=kind, tool=tool, arguments=dict(arguments))
    with _LOCK:
        # Senate 只有一个实例、日志也只有一份，并发跑会让「模型往返计数」互相串台，
        # 进度就失真了。所以同一时刻只放一个任务，第二个直接挡回去（页面会显示原因）。
        running = [t for t in _TASKS.values() if t.status == "running"]
        if running:
            raise RuntimeError(f"已有合议在运行（任务 {running[0].id}），等它结束再发起")
        _TASKS[task.id] = task
        _prune()

    def worker() -> None:
        try:
            payload = asyncio.run(_call_tool(tool, arguments, timeout))
        except Exception as exc:  # noqa: BLE001 - 任务失败要如实回给页面，不能让线程静默死
            with _LOCK:
                task.error = f"{type(exc).__name__}: {exc}"
                task.status = "error"
                task.finished_at = time.time()
            return
        with _LOCK:
            task.result = payload
            task.status = "done"
            task.finished_at = time.time()

    threading.Thread(target=worker, daemon=True, name=f"senate-{kind}").start()
    # 进度：Senate 每完成一次模型往返就往自己的 stderr 写一行，数这个增量就是真实进度。
    log_path = _find_senate_log()
    baseline = _count_llm_roundtrips(log_path) if log_path is not None else -1
    if log_path is not None and baseline >= 0:
        threading.Thread(
            target=_watch_progress,
            args=(task, log_path, baseline),
            daemon=True,
            name=f"senate-{kind}-progress",
        ).start()
    return task


def _view(task: _Task) -> dict[str, Any]:
    elapsed = (task.finished_at or time.time()) - task.started_at
    return {
        "task_id": task.id,
        "kind": task.kind,
        "tool": task.tool,
        "status": task.status,
        "arguments": task.arguments,
        "elapsed_sec": round(elapsed, 1),
        "steps_done": task.steps_done,
        "steps_total": _SENATE_STEPS_ESTIMATE,
        "stage_label": task.stage_label
        or ("已结束" if task.status != "running" else _STAGE_STEPS[0][1]),
        # 页面画辩论过程用的元数据：环节顺序 + 起始往返次数 + 真实进入时刻
        "stages": [{"name": name, "from_steps": threshold} for threshold, name in _STAGE_STEPS],
        "stage_log": list(task.stage_log),
        "result": task.result,
        "error": task.error,
    }


def _write_guard(request: Request) -> None:
    """写面判据与其余本地面一致：同源 Origin 或显式 `X-LDST-Client: 1`。"""
    if not write_source_allowed(request):
        raise HTTPException(
            status_code=403,
            detail="跨站来源被拒绝：发起合议会消耗模型额度，只接受本机页面调用",
        )


def build_senate_actions_router() -> APIRouter:
    router = APIRouter(prefix="/api/senate", tags=["senate"])

    @router.get("/capability")
    async def capability() -> dict[str, Any]:
        """探一次 Senate MCP：能不能连、认不认得那几个工具。页面用它决定是否放行。"""
        url = senate_mcp_url()
        try:
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client

            async with streamable_http_client(url) as (read, write):
                async with ClientSession(read, write) as session:
                    init = await asyncio.wait_for(session.initialize(), CAPABILITY_TIMEOUT_SEC)
                    tools = await asyncio.wait_for(session.list_tools(), CAPABILITY_TIMEOUT_SEC)
        except Exception as exc:  # noqa: BLE001 - 探不通就如实回不可用，不抛给前端
            return {
                "ok": False,
                "url": url,
                "error": f"{type(exc).__name__}: {exc}",
                "tools": [],
            }
        names = sorted(tool.name for tool in tools.tools)
        info = getattr(init, "server_info", None)
        return {
            "ok": ANALYSIS_TOOL in names and BACKTEST_TOOL in names,
            "url": url,
            "server": getattr(info, "name", None),
            "version": getattr(info, "version", None),
            "tools": names,
        }

    @router.post("/analysis", dependencies=[Depends(_write_guard)])
    def analysis(body: SenateAnalysisRequest) -> dict[str, Any]:
        args: dict[str, Any] = {"code": body.code.strip(), "hitl": body.hitl}
        if body.date:
            args["date"] = body.date
        try:
            task = start_task("analysis", ANALYSIS_TOOL, args, ANALYSIS_TIMEOUT_SEC)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True, **_view(task)}

    @router.get("/analysis/{task_id}")
    def analysis_status(task_id: str) -> dict[str, Any]:
        with _LOCK:
            task = _TASKS.get(task_id)
        if task is None or task.kind != "analysis":
            raise HTTPException(status_code=404, detail=f"没有这条合议任务：{task_id}")
        return {"ok": task.status != "error", **_view(task)}

    @router.post("/backtest", dependencies=[Depends(_write_guard)])
    def backtest(body: SenateBacktestRequest) -> dict[str, Any]:
        args = {
            "codes": [c.strip() for c in body.codes if c.strip()],
            "start": body.start,
            "end": body.end,
            "window": body.window,
            "slippage": body.slippage,
            "fee": body.fee,
        }
        try:
            task = start_task("backtest", BACKTEST_TOOL, args, BACKTEST_TIMEOUT_SEC)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"ok": True, **_view(task)}

    @router.get("/backtest/{task_id}")
    def backtest_status(task_id: str) -> dict[str, Any]:
        with _LOCK:
            task = _TASKS.get(task_id)
        if task is None or task.kind != "backtest":
            raise HTTPException(status_code=404, detail=f"没有这条回测任务：{task_id}")
        return {"ok": task.status != "error", **_view(task)}

    @router.get("/tasks")
    def tasks() -> dict[str, Any]:
        with _LOCK:
            items = sorted(_TASKS.values(), key=lambda t: t.started_at, reverse=True)
        return {"ok": True, "tasks": [_view(t) for t in items[:MAX_TASKS_KEPT]]}

    # ------------------------------------------------------------------ #
    # F7 多空对照：已落盘报告的「读 + 门控 + 对照表」
    #
    # 与上面的 `/analysis` 分工：那个负责"跑"（分钟级、写新报告），这里只负责
    # "读已有的报告"并回答"够不够格出对照表"。跑完的 task 结果里没有
    # `debate` 结构化对照，页面要的正是这份——所以单独开一组 GET。
    # ------------------------------------------------------------------ #

    @router.get("/debate/status")
    def debate_status() -> dict[str, Any]:
        """能不能跑、有没有报告——页面用它解释「为什么这轮没有辩论」。"""
        return {"ok": True, **debate_bridge.availability()}

    @router.get("/debate/reports")
    def debate_reports(limit: int = 20) -> dict[str, Any]:
        items = debate_bridge.list_reports(limit=max(1, min(int(limit), 200)))
        return {"ok": True, "items": items}

    @router.get("/debate/{code}")
    def debate_view(code: str, date: str | None = None) -> dict[str, Any]:
        """某标的某天（或最近）的多空对照 + 门控结论；不触发新合议。"""
        path = debate_bridge.latest_report(code, date)
        if path is None:
            raise HTTPException(
                status_code=404,
                detail=f"没有 {code} 在 {date or '任意日期'} 的合议报告（先跑 /api/senate/analysis）",
            )
        loaded = debate_bridge.load_report(path)
        if not loaded.get("ok"):
            raise HTTPException(status_code=422, detail=str(loaded.get("reason") or "报告读不出来"))
        normalized = debate_bridge.normalize(loaded)
        gate = debate_bridge.assess(normalized)
        try:
            blocks = debate_bridge.to_blocks(normalized, gate)
            blocks_error = None
        except Exception as exc:  # noqa: BLE001 - 块契约错误不该吞掉门控结果
            blocks = []
            blocks_error = f"{type(exc).__name__}: {exc}"
        return {
            "ok": True,
            "run_id": normalized.get("run_id"),
            "code": normalized.get("code"),
            "date": normalized.get("date"),
            "report_dir": str(path),
            "normalized": normalized,
            "gate": gate,
            "blocks": blocks,
            "blocks_error": blocks_error,
        }

    return router


__all__ = [
    "ANALYSIS_TOOL",
    "BACKTEST_TOOL",
    "REPORT_TOOL",
    "build_senate_actions_router",
    "senate_mcp_url",
    "start_task",
]
