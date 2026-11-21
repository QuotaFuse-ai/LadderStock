"""`/api/brief`：F11 简报面（晨报 / 收盘复盘 / 持仓异动）。

四个接口，全部是本机数据：

- `GET  /api/brief/today?kind=`：今日三份简报的**实时内容**（现场 build，不落状态），
  外加当天状态文件里的 `published` / `briefs`（已经推过什么、跳过了什么）；
- `GET  /api/brief/history?limit=`：最近若干天的状态摘要；
- `GET  /api/brief/config`：通知开关（`notify` / `channel` / `threshold`）；
- `PUT  /api/brief/config`：改开关；
- `POST /api/brief/{kind}/run`：生成 → 去重 → 落收件箱 →（开关打开时）投递。

读面 = 回环 + `Host` 白名单；写面再加同源 `Origin` 或 `X-LDST-Client: 1`，
闸门直接复用 `web/radar.py` 的 `privacy_read_guard` / `privacy_write_guard`，不新增鉴权。

`today` 是**现场生成**而不是读缓存：简报的价值在于「此刻能查到什么」，
读缓存会把「三天前的行情」当成今天的结论。生成成本是几次本地 SQLite / DuckDB 查询，
实测在 1 秒级；失败时 `status=failed` 并写明原因，绝不返回一份空白简报冒充成功。
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from ladderstock.watch import brief as brief_module
from ladderstock.web.radar import privacy_read_guard, privacy_write_guard

BRIEF_PREFIX = "/api/brief"

BRIEF_DENIED = "简报只对本机回环开放（127.0.0.1 / localhost / ::1）"
BRIEF_WRITE_DENIED = "简报写面只接受同源来源（Origin 或 X-LDST-Client: 1）"

_NO_STORE = {"Cache-Control": "no-store"}

#: `weekday` 决定今天该不该有晨报/收盘：周末不生成这两份，但页面要看得到原因
WEEKDAY_BRIEFS: tuple[str, ...] = (brief_module.BRIEF_MORNING, brief_module.BRIEF_CLOSE)


async def _brief_guard(request: Request) -> None:
    await privacy_read_guard(request, denied=BRIEF_DENIED, subject="简报")


async def _brief_write_guard(request: Request) -> None:
    await privacy_write_guard(
        request,
        denied=BRIEF_DENIED,
        subject="简报",
        write_denied=BRIEF_WRITE_DENIED,
    )


def _require_kind(kind: str) -> str:
    name = str(kind or "").strip().lower()
    if name not in brief_module.BRIEFS:
        raise HTTPException(
            status_code=404,
            detail=f"没有这种简报 {kind!r}；可用：{' / '.join(brief_module.BRIEFS)}",
        )
    return name


def today_payload(
    *,
    data_root: Path | None = None,
    kinds: tuple[str, ...] = brief_module.BRIEFS,
) -> dict[str, Any]:
    """现场生成指定类型的简报 + 当天状态。生成失败只影响那一份，不影响其余。"""
    settings = brief_module.load_settings()
    built: list[dict[str, Any]] = []
    for kind in kinds:
        result = brief_module.build(kind, root=data_root, threshold=settings["threshold"])
        built.append(result.as_dict())

    state = brief_module._read_state(data_root, built[0]["trade_day"] if built else "")
    day = built[0]["trade_day"] if built else ""
    return {
        "local_only": True,
        "day": day,
        "settings": {
            "notify": settings["notify"],
            "channel": settings["channel"],
            "threshold": settings["threshold"],
        },
        "briefs": built,
        "published": sorted(
            [
                {"fingerprint": key, **value}
                for key, value in (state.get("published") or {}).items()
            ],
            key=lambda row: str(row.get("at") or ""),
        ),
        "runs": state.get("briefs") or [],
    }


def build_router(*, data_root: Path | None = None) -> APIRouter:
    """六条路由；数据根可注入，单测写临时目录即可跑。"""
    router = APIRouter(prefix=BRIEF_PREFIX)

    @router.get("/today", dependencies=[Depends(_brief_guard)])
    def brief_today(
        kind: Annotated[str | None, Query()] = None,
    ) -> JSONResponse:
        """今日简报（现场生成）。`kind` 只取一类时仍返回同一份 payload 形状。"""
        kinds: tuple[str, ...] = brief_module.BRIEFS
        if kind:
            kinds = (_require_kind(kind),)
        return JSONResponse(today_payload(data_root=data_root, kinds=kinds), headers=_NO_STORE)

    @router.get("/history", dependencies=[Depends(_brief_guard)])
    def brief_history(
        limit: Annotated[int, Query(ge=1, le=60)] = 20,
    ) -> JSONResponse:
        """最近若干天的状态（只读状态文件，不再生成简报）。"""
        return JSONResponse(
            {"local_only": True, "days": brief_module.history(root=data_root, limit=limit)},
            headers=_NO_STORE,
        )

    @router.get("/config", dependencies=[Depends(_brief_guard)])
    def brief_config() -> JSONResponse:
        settings = brief_module.load_settings()
        return JSONResponse(
            {
                "settings": {
                    "notify": settings["notify"],
                    "channel": settings["channel"],
                    "threshold": settings["threshold"],
                },
                "channels": ["console", "desktop"],
                "path": str(brief_module.settings_path()),
            },
            headers=_NO_STORE,
        )

    @router.put("/config", dependencies=[Depends(_brief_write_guard)])
    def brief_config_update(payload: dict[str, Any] = Body(default={})) -> JSONResponse:
        """改开关；未知字段忽略，非法值 400（不静默改成默认）。"""
        body = payload if isinstance(payload, dict) else {}
        unknown = sorted(set(body) - {"notify", "channel", "threshold"})
        try:
            settings = brief_module.save_settings(
                notify=body.get("notify") if "notify" in body else None,
                channel=body.get("channel") if "channel" in body else None,
                threshold=body.get("threshold") if "threshold" in body else None,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return JSONResponse(
            {
                "settings": {
                    "notify": settings["notify"],
                    "channel": settings["channel"],
                    "threshold": settings["threshold"],
                },
                "ignored": unknown,
            },
            headers=_NO_STORE,
        )

    @router.post("/{kind}/run", dependencies=[Depends(_brief_write_guard)])
    def brief_run(
        kind: str,
        notify: Annotated[bool | None, Query()] = None,
    ) -> JSONResponse:
        """生成并发布：去重 → 落收件箱 →（开关或 `?notify=1`）投递。"""
        name = _require_kind(kind)
        settings = brief_module.load_settings()
        result = brief_module.build(name, root=data_root, threshold=settings["threshold"])
        send = settings["notify"] if notify is None else bool(notify)
        outcome = brief_module.publish(
            result,
            root=data_root,
            notify=send,
            channel=settings["channel"] if send else None,
        )
        return JSONResponse({**outcome, "brief": result.as_dict()}, headers=_NO_STORE)

    return router


__all__ = [
    "BRIEF_DENIED",
    "BRIEF_PREFIX",
    "BRIEF_WRITE_DENIED",
    "WEEKDAY_BRIEFS",
    "build_router",
    "today_payload",
]
