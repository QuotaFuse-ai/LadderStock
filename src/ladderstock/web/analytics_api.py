"""`/api/analytics`：组合统计（B 包 / DEC-089 授权面）。

`GET /api/analytics/summary?days=365` 返回组合指标 / 净值曲线 / 基准对比 /
交易归因 / 行为统计 / 错题本。数据来自本机隐私库（交易流水）与本机日线缓存，
因此按隐私面处理：**只接受回环来源 + Host 白名单**，`no-store`，不进信封 / MCP / AI。

授权与条件见 `docs/decisions/DEC-089-analytics-surface.md`：
只读、只聚合、明细不落日志；页面就是本人的复盘面。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from ladderstock.analytics import engine
from ladderstock.web.radar import privacy_read_guard

ANALYTICS_PREFIX = "/api/analytics"

DENIED = "组合统计只对本机回环开放（127.0.0.1 / localhost / ::1）"

_NO_STORE = {"Cache-Control": "no-store"}


async def _analytics_guard(request: Request) -> None:
    await privacy_read_guard(request, denied=DENIED, subject="组合统计")


def build_router() -> APIRouter:
    router = APIRouter(prefix=ANALYTICS_PREFIX)

    @router.get("/summary", dependencies=[Depends(_analytics_guard)])
    def summary(
        days: Annotated[int, Query(ge=30, le=3650)] = 365,
    ) -> JSONResponse:
        payload: dict[str, Any] = engine.build_summary(days=days)
        return JSONResponse(payload, headers=_NO_STORE)

    return router


__all__ = ["ANALYTICS_PREFIX", "build_router"]
