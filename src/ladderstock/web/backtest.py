"""`/api/backtest/*`：回测运行卡 HTTP 端点（QUOT-500）。

**页面只读运行卡产物，不在前端重算任何指标**（QUOT-500 红线）：这里所有 GET 都
只读 ``<data_dir>/backtests/<run_id>/`` 下已落盘的 JSON / Markdown / HTML，不调
撮合、不计分、不换算单位。前端拿到 ``/card`` 的 JSON 后原样渲染。

端点（全部 GET；写面只有本机 CLI ``ldst backtest run``）：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/backtest/runs` | 已落盘运行卡摘要列表（新的在前，``?limit=`` 钳 1–200） |
| GET | `/api/backtest/latest` | 最新一张运行卡（无产物 → 404） |
| GET | `/api/backtest/runs/{run_id}` | 单张运行卡 JSON（即运行卡契约本体） |
| GET | `/api/backtest/runs/{run_id}/export.json` | 下载运行卡 JSON（``attachment``） |
| GET | `/api/backtest/runs/{run_id}/export.md` | 下载 Markdown 版运行卡 |
| GET | `/api/backtest/runs/{run_id}/export.html` | 下载自包含 HTML（未通过校验门时页头/横幅标红） |

状态码：``200`` 成功；``400`` run_id 形态不合法；``404`` 没有产物；``403`` 非回环来源
或 ``Host`` 不在白名单（与 ``/api/calc/*`` 同一闸门）；``500`` 产物损坏（JSON 解不开）。

闸门同研究面 / 计算面：回环独占 + ``Host`` 白名单；纯只读数据面，不受
``LDST_CONTROL_ENABLED`` 影响；注册在 SPA 兜底之前。
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response

from ladderstock.backtest.runner import BacktestError, artifacts_root, list_runs, load_run_card
from ladderstock.web import control as control_plane

BACKTEST_PREFIX: Final = "/api/backtest"
_RUN_ID_CHARSET: Final = frozenset("0123456789abcdefghijklmnopqrstuvwxyz_")


def _json(payload: Any, *, status: int = 200) -> JSONResponse:
    # 严格 JSON：NaN / Infinity 一律不许出网（运行卡的数值指标必然有限）
    body = json.loads(json.dumps(payload, ensure_ascii=False, allow_nan=False))
    return JSONResponse(body, status_code=status)


def _bad_run_id(run_id: str) -> bool:
    return not run_id or any(ch not in _RUN_ID_CHARSET for ch in run_id)


def _artifact(run_id: str, suffix: str) -> Any:
    """读产物文件；不存在 → 404（带 run_id），路径非法 → 400。"""
    if _bad_run_id(run_id):
        raise HTTPException(status_code=400, detail=f"run_id 不合法：{run_id!r}")
    path = artifacts_root() / run_id / f"{run_id}{suffix}"
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"运行卡不存在：{run_id}")
    return path


async def _guard(request: Request) -> None:
    """回环来源 + `Host` 白名单（与 `/api/calc/*` 同一形状）。"""
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


def _runs(limit: int) -> dict[str, Any]:
    runs = list_runs(limit=limit)
    latest = runs[0]["run_id"] if runs else None
    return {"root": str(artifacts_root()), "count": len(runs), "latest": latest, "runs": runs}


def _latest_card() -> dict[str, Any]:
    runs = list_runs(limit=1)
    if not runs:
        raise HTTPException(
            status_code=404, detail="本机还没有回测运行卡（先跑 `ldst backtest run`）"
        )
    try:
        return load_run_card(str(runs[0]["run_id"]))
    except (FileNotFoundError, BacktestError) as exc:  # pragma: no cover - 目录刚被清掉
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def build_router() -> APIRouter:
    """构造只读的运行卡端点组。"""
    router = APIRouter(prefix=BACKTEST_PREFIX, dependencies=[Depends(_guard)])

    @router.get("/runs")
    def runs(
        limit: Annotated[int, Query(description="返回条数，默认 20，钳 1–200")] = 20,
    ) -> Any:
        return _json(_runs(max(1, min(200, int(limit)))))

    @router.get("/latest")
    def latest() -> Any:
        return _json(_latest_card())

    @router.get("/runs/{run_id}")
    def card(run_id: str) -> Any:
        try:
            return _json(load_run_card(run_id))
        except BacktestError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=f"运行卡不存在：{run_id}") from exc
        except json.JSONDecodeError as exc:  # pragma: no cover - 文件被人改坏
            raise HTTPException(status_code=500, detail=f"运行卡 JSON 损坏：{exc}") from exc

    @router.get("/runs/{run_id}/export.json")
    def export_json(run_id: str) -> Any:
        path = _artifact(run_id, ".json")
        return Response(
            content=path.read_bytes(),
            media_type="application/json; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{run_id}.json"'},
        )

    @router.get("/runs/{run_id}/export.md")
    def export_markdown(run_id: str) -> Any:
        path = _artifact(run_id, ".md")
        return PlainTextResponse(
            content=path.read_text(encoding="utf-8"),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{run_id}.md"'},
        )

    @router.get("/runs/{run_id}/export.html")
    def export_html(run_id: str) -> Any:
        path = _artifact(run_id, ".html")
        return Response(
            content=path.read_bytes(),
            media_type="text/html; charset=utf-8",
        )

    return router


__all__ = ["BACKTEST_PREFIX", "build_router"]
