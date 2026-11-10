"""`/api/backtest/*`：回测运行卡 HTTP 面（QUOT-500 只读 + QUOT-535 触发面）。

**页面只读运行卡产物，不在前端重算任何指标**（QUOT-500 红线）：GET 类端点只读
``<data_dir>/backtests/<run_id>/`` 下已落盘的 JSON / Markdown / HTML，不调撮合、不计分。

端点：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/backtest/runs` | 已落盘运行卡摘要列表（新的在前，``?limit=`` 钳 1–200） |
| GET | `/api/backtest/latest` | 最新一张运行卡（无产物 → 404） |
| GET | `/api/backtest/runs/{run_id}` | 单张运行卡 JSON（即运行卡契约本体） |
| GET | `/api/backtest/runs/{run_id}/export.json` | 下载运行卡 JSON（``attachment``） |
| GET | `/api/backtest/runs/{run_id}/export.md` | 下载 Markdown 版运行卡 |
| GET | `/api/backtest/runs/{run_id}/export.html` | 下载自包含 HTML（未过门时标红） |
| GET | `/api/backtest/strategies` | 策略清单 + 成本默认值 + 默认标的（QUOT-535） |
| POST | `/api/backtest/run` | 提交一次回测（异步，202 + job_id） |
| GET | `/api/backtest/jobs/{job_id}` | 回测任务状态（`status/run_id/gate_passed/exit_code`） |

闸门与现有写面同口径：回环来源 + ``Host`` 白名单；``POST`` 另需正向来源证据
（本机页面 ``Origin`` 或 ``X-LDST-Client: 1``，见 ``control.write_source_allowed``）。
写面只落本机运行卡，与 CLI ``ldst backtest run`` 走同一个 ``run_backtest`` 入口，
不另写撮合逻辑；异常按 CLI 退出码映射（2/3/4，闸门不过 = 8）。

状态码：``200`` 成功；``202`` 已入队；``400`` 参数非法（结构化 ``{error, message}``）；
``403`` 非回环 / 坏 ``Host`` / 写请求缺来源证据；``404`` 产物或任务不存在；
``422`` 请求体形状不对；``500`` 产物损坏或配置读不动。
"""

from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel

from ladderstock.backtest.config import BacktestConfig, BacktestConfigError, load_config
from ladderstock.backtest.feed import FeedError
from ladderstock.backtest.runner import (
    DEFAULT_SYMBOL,
    BacktestError,
    artifacts_root,
    list_runs,
    load_run_card,
    parse_day,
    run_backtest,
)
from ladderstock.backtest.strategy import BUILTIN, StrategyError, catalog, make
from ladderstock.backtest.universe import UniverseError, classify
from ladderstock.cli.paths import config_dir
from ladderstock.errors import EXIT_CONFIG, EXIT_OK, EXIT_PARTIAL, EXIT_SOURCE, EXIT_USAGE
from ladderstock.web import control as control_plane

BACKTEST_PREFIX: Final = "/api/backtest"
_RUN_ID_CHARSET: Final = frozenset("0123456789abcdefghijklmnopqrstuvwxyz_")
_MAX_JOBS: Final = 200
_POLL_STATUSES: Final = frozenset({"queued", "running"})

#: 与 CLI 相同的异常 → 退出码映射（``ldst backtest run`` 的 2/3/4）
_EXIT_BY_ERROR: Final[tuple[tuple[type[BaseException], int], ...]] = (
    (BacktestConfigError, EXIT_CONFIG),
    (FeedError, EXIT_SOURCE),
    (UniverseError, EXIT_USAGE),
    (StrategyError, EXIT_USAGE),
    (BacktestError, EXIT_USAGE),
)


def _json(payload: Any, *, status: int = 200) -> JSONResponse:
    # 严格 JSON：NaN / Infinity 一律不许出网（运行卡的数值指标必然有限）
    body = json.loads(json.dumps(payload, ensure_ascii=False, allow_nan=False))
    return JSONResponse(body, status_code=status)


def _error(status: int, code: str, message: str) -> HTTPException:
    """结构化错误：``{"detail": {"error": code, "message": ...}}``。"""
    return HTTPException(status_code=status, detail={"error": code, "message": message})


def _bad_run_id(run_id: str) -> bool:
    return not run_id or any(ch not in _RUN_ID_CHARSET for ch in run_id)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _exit_code_for(exc: BaseException) -> int:
    for cls, code in _EXIT_BY_ERROR:
        if isinstance(exc, cls):
            return code
    return EXIT_USAGE


def _default_config_path() -> Path | None:
    candidate = config_dir() / "backtest.yaml"
    return candidate if candidate.is_file() else None


@dataclass(slots=True)
class BacktestJob:
    """一次异步回测的登记项；终态字段与 CLI 输出一一对应。"""

    id: str
    strategy: str
    strategy_params: dict[str, Any]
    symbol: str
    start: str
    end: str
    config: BacktestConfig
    status: str = "queued"
    run_id: str | None = None
    gate_passed: bool | None = None
    exit_code: int | None = None
    error: str | None = None
    created_at: str = field(default_factory=_now)
    started_at: str | None = None
    ended_at: str | None = None

    def view(self) -> dict[str, Any]:
        return {
            "job_id": self.id,
            "status": self.status,
            "run_id": self.run_id,
            "gate_passed": self.gate_passed,
            "exit_code": self.exit_code,
            "error": self.error,
            "strategy": self.strategy,
            "strategy_params": dict(self.strategy_params),
            "symbol": self.symbol,
            "start": self.start,
            "end": self.end,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
        }


class BacktestRunRequest(BaseModel):
    """`POST /api/backtest/run` 的请求体。

    金额/费率字段同时接受旧小数口径（`commission` / `stamp_duty`）与券商万分之口径
    （`commission_rate_wan` / `transfer_fee_wan` / ...）；`None` 的键不覆盖配置。
    """

    strategy: str
    strategy_params: dict[str, Any] | None = None
    symbol: str
    start: str
    end: str
    commission: float | None = None
    slippage: float | None = None
    stamp_duty: float | None = None
    min_trades: int | None = None
    in_sample_ratio: float | None = None
    cost_tiers: list[float] | None = None
    commission_rate_wan: float | None = None
    commission_floor_yuan: float | None = None
    transfer_fee_wan: float | None = None
    stamp_duty_wan: float | None = None


class BacktestPlane:
    """策略目录 + 异步回测任务；执行与 CLI 共用 ``run_backtest``。"""

    def __init__(
        self,
        *,
        data_root: Path,
        config_path: Path | None = None,
        max_workers: int = 1,
    ) -> None:
        self.data_root = Path(data_root)
        self._config_path = config_path
        self._max_workers = max_workers
        self._lock = threading.Lock()
        self._jobs: dict[str, BacktestJob] = {}
        self._executor: ThreadPoolExecutor | None = None

    # ---------------------------------------------------------------- 读取

    def config(self) -> BacktestConfig:
        path = self._config_path if self._config_path is not None else _default_config_path()
        return load_config(path)

    def strategies(self) -> dict[str, Any]:
        try:
            cfg = self.config()
        except BacktestConfigError as exc:
            raise _error(500, "config_invalid", f"回测配置不可读：{exc}") from exc
        return {
            "strategies": catalog(),
            "config_defaults": {
                "commission": cfg.commission,
                "slippage": cfg.slippage,
                "stamp_duty": cfg.stamp_duty,
                "min_trades": cfg.min_trades,
                "in_sample_ratio": cfg.in_sample_ratio,
                "cost_tiers": list(cfg.cost_tiers),
                "commission_rate_wan": cfg.commission_rate_wan,
                "commission_floor_yuan": cfg.commission_floor_yuan,
                "transfer_fee_wan": cfg.transfer_fee_wan,
                "stamp_duty_wan": cfg.stamp_duty_wan,
                "presets": [preset.as_dict() for preset in cfg.presets],
                "config_sources": cfg.provenance(),
            },
            "symbol_default": DEFAULT_SYMBOL,
        }

    # ---------------------------------------------------------------- 写面

    def start_run(self, request: BacktestRunRequest) -> dict[str, Any]:
        """同步校验 → 入队；校验失败直接 4xx，不占任务槽。"""
        if request.strategy not in BUILTIN:
            raise _error(
                400,
                "unknown_strategy",
                f"未知策略 {request.strategy!r}（内置：{', '.join(BUILTIN)}）",
            )
        params = dict(request.strategy_params or {})
        try:
            make(request.strategy, **params)
        except StrategyError as exc:
            raise _error(400, "invalid_strategy_params", str(exc)) from exc
        try:
            classify(request.symbol)
        except UniverseError as exc:
            raise _error(400, "invalid_symbol", str(exc)) from exc
        try:
            start = parse_day(request.start, field_name="start")
            end = parse_day(request.end, field_name="end")
        except BacktestError as exc:
            raise _error(400, "invalid_date", str(exc)) from exc
        if start > end:
            raise _error(400, "invalid_period", f"起始日 {start} 晚于结束日 {end}")

        overrides: dict[str, Any] = {
            key: value
            for key, value in (
                ("commission", request.commission),
                ("slippage", request.slippage),
                ("stamp_duty", request.stamp_duty),
                ("min_trades", request.min_trades),
                ("in_sample_ratio", request.in_sample_ratio),
                ("commission_rate_wan", request.commission_rate_wan),
                ("commission_floor_yuan", request.commission_floor_yuan),
                ("transfer_fee_wan", request.transfer_fee_wan),
                ("stamp_duty_wan", request.stamp_duty_wan),
            )
            if value is not None
        }
        if request.cost_tiers is not None:
            overrides["cost_tiers"] = tuple(request.cost_tiers)
        try:
            cfg = self.config()
            if overrides:
                cfg = cfg.with_overrides(**overrides)
        except BacktestConfigError as exc:
            raise _error(400, "invalid_cost", str(exc)) from exc

        job = BacktestJob(
            id=uuid.uuid4().hex,
            strategy=request.strategy,
            strategy_params=params,
            symbol=request.symbol.strip(),
            start=start.isoformat(),
            end=end.isoformat(),
            config=cfg,
        )
        # 应答状态在提交线程池**之前**取样，恒为 queued——后续 running/ok 是进度端点的事，
        # 不该让 202 的应答跟着线程调度抖动（与研究面 `start_run` 同口径）。
        ack = {"job_id": job.id, "status": job.status}
        with self._lock:
            self._jobs[job.id] = job
            while len(self._jobs) > _MAX_JOBS:
                oldest = next(iter(self._jobs))
                del self._jobs[oldest]
            self._executor = self._executor or ThreadPoolExecutor(
                max_workers=self._max_workers, thread_name_prefix="ldst-backtest"
            )
            self._executor.submit(self._execute, job)
        return ack

    def job_view(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"未知回测任务：{job_id}")
        return job.view()

    def _execute(self, job: BacktestJob) -> None:
        job.status = "running"
        job.started_at = _now()
        try:
            result = run_backtest(
                strategy=job.strategy,
                strategy_kwargs=job.strategy_params,
                symbol=job.symbol,
                start=job.start,
                end=job.end,
                config=job.config,
                data_root=self.data_root,
                artifacts_root_dir=artifacts_root(self.data_root),
            )
        except Exception as exc:  # noqa: BLE001 - 任务线程必须把失败如实落进状态
            job.status = "failed"
            job.exit_code = _exit_code_for(exc)
            job.error = str(exc) or exc.__class__.__name__
        else:
            job.run_id = result.run_id
            job.gate_passed = bool(result.card["gate"]["passed"])
            job.exit_code = EXIT_OK if job.gate_passed else EXIT_PARTIAL
            job.status = "ok"
        finally:
            job.ended_at = _now()


async def _guard(request: Request) -> None:
    """回环来源 + `Host` 白名单；写方法另需正向来源证据（与研究面同口径）。"""
    if not control_plane.is_loopback_host(control_plane.client_host(request.client)):
        raise HTTPException(status_code=403, detail="回测端点只接受回环来源")
    if not control_plane.host_header_allowed(request):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Host 不在白名单内：{request.headers.get('host') or '(空)'}"
                "（只接受 127.0.0.1 / localhost / ::1 或本机监听地址）"
            ),
        )
    if request.method.upper() in control_plane.WRITE_METHODS:
        if not control_plane.write_source_allowed(request):
            raise HTTPException(
                status_code=403,
                detail=(
                    "写请求缺少来源证据：需要本机页面 Origin，或显式头 "
                    f"{control_plane.CLIENT_HEADER}: {control_plane.CLIENT_HEADER_VALUE}"
                ),
            )
        site = request.headers.get("sec-fetch-site")
        if site is not None and site.strip().lower() not in control_plane.SAME_ORIGIN_SITES:
            raise HTTPException(
                status_code=403,
                detail=f"跨站来源被拒绝：Sec-Fetch-Site={site}（只接受 same-origin / none）",
            )


def _artifact(plane: BacktestPlane, run_id: str, suffix: str) -> Any:
    """读产物文件；不存在 → 404（带 run_id），路径非法 → 400。"""
    if _bad_run_id(run_id):
        raise HTTPException(status_code=400, detail=f"run_id 不合法：{run_id!r}")
    path = artifacts_root(plane.data_root) / run_id / f"{run_id}{suffix}"
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"运行卡不存在：{run_id}")
    return path


def _runs(plane: BacktestPlane, limit: int) -> dict[str, Any]:
    runs = list_runs(root=plane.data_root, limit=limit)
    latest = runs[0]["run_id"] if runs else None
    return {
        "root": str(artifacts_root(plane.data_root)),
        "count": len(runs),
        "latest": latest,
        "runs": runs,
    }


def _latest_card(plane: BacktestPlane) -> dict[str, Any]:
    runs = list_runs(root=plane.data_root, limit=1)
    if not runs:
        raise HTTPException(
            status_code=404, detail="本机还没有回测运行卡（先跑 `ldst backtest run`）"
        )
    try:
        return load_run_card(str(runs[0]["run_id"]), root=plane.data_root)
    except (FileNotFoundError, BacktestError) as exc:  # pragma: no cover - 目录刚被清掉
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def build_router(
    data_root: Path | None = None, plane: BacktestPlane | None = None
) -> APIRouter:
    """构造回测端点组（只读运行卡 + strategies/run/jobs 触发面）。"""
    from ladderstock.cli.paths import data_dir

    plane = plane or BacktestPlane(data_root=Path(data_root) if data_root else data_dir())
    router = APIRouter(prefix=BACKTEST_PREFIX, dependencies=[Depends(_guard)])

    @router.get("/runs")
    def runs(
        limit: Annotated[int, Query(description="返回条数，默认 20，钳 1–200")] = 20,
    ) -> Any:
        return _json(_runs(plane, max(1, min(200, int(limit)))))

    @router.get("/latest")
    def latest() -> Any:
        return _json(_latest_card(plane))

    @router.get("/strategies")
    def strategies() -> Any:
        return _json(plane.strategies())

    @router.post("/run")
    def start_run(request: BacktestRunRequest) -> Any:
        return _json(plane.start_run(request), status=202)

    @router.get("/jobs/{job_id}")
    def job(job_id: str) -> Any:
        return _json(plane.job_view(job_id))

    @router.get("/runs/{run_id}")
    def card(run_id: str) -> Any:
        try:
            return _json(load_run_card(run_id, root=plane.data_root))
        except BacktestError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"运行卡不存在：{run_id}") from exc
        except json.JSONDecodeError as exc:  # pragma: no cover - 文件被人改坏
            raise HTTPException(status_code=500, detail=f"运行卡 JSON 损坏：{exc}") from exc

    @router.get("/runs/{run_id}/export.json")
    def export_json(run_id: str) -> Any:
        path = _artifact(plane, run_id, ".json")
        return Response(
            content=path.read_bytes(),
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{run_id}.json"'},
        )

    @router.get("/runs/{run_id}/export.md")
    def export_markdown(run_id: str) -> Any:
        path = _artifact(plane, run_id, ".md")
        return PlainTextResponse(
            content=path.read_text(encoding="utf-8"),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{run_id}.md"'},
        )

    @router.get("/runs/{run_id}/export.html")
    def export_html(run_id: str) -> Any:
        path = _artifact(plane, run_id, ".html")
        return Response(
            content=path.read_bytes(),
            media_type="text/html; charset=utf-8",
        )

    return router


__all__ = ["BACKTEST_PREFIX", "BacktestJob", "BacktestPlane", "BacktestRunRequest", "build_router"]
