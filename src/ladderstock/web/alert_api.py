"""`/api/alert`：提醒规则与出站渠道的管理面（DEC-087）。

与收件箱同一条隐私口径（`web/radar` 的 guard 复用，不另起第二种鉴权）：
**读面** = 回环 + `Host` 白名单；**写面**再加同源 `Origin` 或 `X-LDST-Client: 1`。
之所以按隐私面处理：规则与渠道描述「这台机器在盯谁、往哪发消息」，webhook URL
有它就能往群里发——所以 GET 一律打码（`public_config`），值只在 POST / PUT 进来，
且响应里也不回显。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/alert/rules` | 规则清单（含运行态） |
| POST | `/api/alert/rules` | 新增规则 |
| PUT | `/api/alert/rules/{id}` | 改规则 |
| DELETE | `/api/alert/rules/{id}` | 删规则 |
| POST | `/api/alert/rules/{id}/toggle` | 启停 |
| GET | `/api/alert/channels` | 渠道清单（URL 打码） |
| POST | `/api/alert/channels` | 新增渠道 |
| PUT | `/api/alert/channels/{id}` | 改渠道 |
| DELETE | `/api/alert/channels/{id}` | 删渠道 |
| POST | `/api/alert/channels/{id}/test` | 真发一条测试消息 |
| POST | `/api/alert/run` | 手动跑一轮（与调度同体） |
| GET | `/api/alert/history` | 最近触发历史 |
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from ladderstock.alert import run as alert_run
from ladderstock.alert import store
from ladderstock.alert.channels import deliver, public_config
from ladderstock.alert.rules import RULE_KINDS
from ladderstock.errors import EXIT_NOT_FOUND, LadderStockError
from ladderstock.web.radar import privacy_read_guard, privacy_write_guard

ALERT_PREFIX = "/api/alert"

DENIED = "提醒管理只对本机回环开放（127.0.0.1 / localhost / ::1）"
WRITE_DENIED = "提醒管理写面只接受同源来源（Origin 或 X-LDST-Client: 1）"

_NO_STORE = {"Cache-Control": "no-store"}


async def _alert_guard(request: Request) -> None:
    await privacy_read_guard(request, denied=DENIED, subject="提醒")


async def _alert_write_guard(request: Request) -> None:
    await privacy_write_guard(request, denied=DENIED, subject="提醒", write_denied=WRITE_DENIED)


def _error(exc: LadderStockError) -> HTTPException:
    status = 404 if exc.code == EXIT_NOT_FOUND else 400
    return HTTPException(status_code=status, detail=exc.message)


def build_router(*, data_root: Path | None = None) -> APIRouter:
    """提醒管理路由；数据根可注入，单测写临时库即可跑。"""
    router = APIRouter(prefix=ALERT_PREFIX)

    @router.get("/rules", dependencies=[Depends(_alert_guard)])
    def rules_list() -> JSONResponse:
        items = [
            {**rule, "state": store.get_state(rule["id"], root=data_root)}
            for rule in store.list_rules(root=data_root)
        ]
        return JSONResponse(
            {"ok": True, "total": len(items), "kinds": list(RULE_KINDS), "rules": items},
            headers=_NO_STORE,
        )

    @router.post("/rules", dependencies=[Depends(_alert_write_guard)])
    def rules_add(
        body: Annotated[
            dict[str, Any],
            Body(
                examples=[
                    {
                        "kind": "pnl_drop",
                        "symbol": "600000",
                        "threshold": 5,
                        "channels": ["dingtalk-main"],
                    },
                    {
                        "kind": "price_below",
                        "symbol": "600000",
                        "threshold": 10.2,
                        "note": "止损线",
                    },
                ]
            ),
        ],
    ) -> JSONResponse:
        raw_threshold = body.get("threshold")
        raw_channels = body.get("channels")
        raw_window = body.get("window")
        try:
            rule = store.add_rule(
                str(body.get("kind") or ""),
                str(body.get("symbol") or ""),
                float(raw_threshold) if raw_threshold is not None else 0.0,
                window=int(raw_window) if raw_window is not None else None,
                cooldown_min=int(body.get("cooldown_min") or 60),
                channels=[str(item) for item in (raw_channels or [])],
                note=str(body.get("note") or ""),
                root=data_root,
            )
        except (LadderStockError, TypeError, ValueError) as exc:
            raise _error(
                exc if isinstance(exc, LadderStockError) else LadderStockError(str(exc))
            ) from exc
        return JSONResponse({"ok": True, "rule": rule})

    @router.put("/rules/{rule_id}", dependencies=[Depends(_alert_write_guard)])
    def rules_update(rule_id: str, body: Annotated[dict[str, Any], Body()]) -> JSONResponse:
        raw_channels = body.get("channels")
        channels_arg: list[str] | None = None
        if isinstance(raw_channels, list):
            channels_arg = [str(item) for item in raw_channels]
        try:
            rule = store.update_rule(
                rule_id,
                root=data_root,
                threshold=body.get("threshold"),
                window=body.get("window"),
                cooldown_min=body.get("cooldown_min"),
                channels=channels_arg,
                note=body.get("note"),
                symbol=body.get("symbol") or None,
                kind=body.get("kind") or None,
            )
        except (LadderStockError, TypeError, ValueError) as exc:
            raise _error(
                exc if isinstance(exc, LadderStockError) else LadderStockError(str(exc))
            ) from exc
        return JSONResponse({"ok": True, "rule": rule})

    @router.delete("/rules/{rule_id}", dependencies=[Depends(_alert_write_guard)])
    def rules_remove(rule_id: str) -> JSONResponse:
        try:
            payload = store.remove_rule(rule_id, root=data_root)
        except LadderStockError as exc:
            raise _error(exc) from exc
        return JSONResponse({"ok": True, **payload})

    @router.post("/rules/{rule_id}/toggle", dependencies=[Depends(_alert_write_guard)])
    def rules_toggle(rule_id: str, body: Annotated[dict[str, Any], Body()]) -> JSONResponse:
        enabled = bool(body.get("enabled", True))
        try:
            rule = store.toggle_rule(rule_id, enabled, root=data_root)
        except LadderStockError as exc:
            raise _error(exc) from exc
        return JSONResponse({"ok": True, "rule": rule})

    @router.get("/channels", dependencies=[Depends(_alert_guard)])
    def channels_list() -> JSONResponse:
        items = [
            {**item, "config": public_config(item["kind"], item["config"])}
            for item in store.list_channels(root=data_root)
        ]
        return JSONResponse({"ok": True, "total": len(items), "channels": items}, headers=_NO_STORE)

    @router.post("/channels", dependencies=[Depends(_alert_write_guard)])
    def channels_add(body: Annotated[dict[str, Any], Body()]) -> JSONResponse:
        try:
            channel = store.add_channel(
                str(body.get("id") or ""),
                str(body.get("kind") or ""),
                name=str(body.get("name") or ""),
                config=dict(body.get("config") or {}),
                root=data_root,
            )
        except (LadderStockError, TypeError, ValueError) as exc:
            raise _error(
                exc if isinstance(exc, LadderStockError) else LadderStockError(str(exc))
            ) from exc
        return JSONResponse(
            {
                "ok": True,
                "channel": {**channel, "config": public_config(channel["kind"], channel["config"])},
            }
        )

    @router.put("/channels/{channel_id}", dependencies=[Depends(_alert_write_guard)])
    def channels_update(channel_id: str, body: Annotated[dict[str, Any], Body()]) -> JSONResponse:
        try:
            channel = store.update_channel(
                channel_id,
                root=data_root,
                name=body.get("name"),
                config=body.get("config"),
                enabled=body.get("enabled"),
            )
        except (LadderStockError, TypeError, ValueError) as exc:
            raise _error(
                exc if isinstance(exc, LadderStockError) else LadderStockError(str(exc))
            ) from exc
        return JSONResponse(
            {
                "ok": True,
                "channel": {**channel, "config": public_config(channel["kind"], channel["config"])},
            }
        )

    @router.delete("/channels/{channel_id}", dependencies=[Depends(_alert_write_guard)])
    def channels_remove(channel_id: str) -> JSONResponse:
        try:
            payload = store.remove_channel(channel_id, root=data_root)
        except LadderStockError as exc:
            raise _error(exc) from exc
        return JSONResponse({"ok": True, "removed": payload.get("removed", True)})

    @router.post("/channels/{channel_id}/test", dependencies=[Depends(_alert_write_guard)])
    def channels_test(channel_id: str) -> JSONResponse:
        try:
            channel = store.get_channel(channel_id, root=data_root)
        except LadderStockError as exc:
            raise _error(exc) from exc
        result = deliver(channel, "LadderStock 渠道测试", "收到即说明该渠道可用。")
        return JSONResponse({"ok": result.ok, "result": result.as_dict()})

    @router.post("/run", dependencies=[Depends(_alert_write_guard)])
    def run_alerts() -> JSONResponse:
        report = alert_run.run_once(root=data_root)
        return JSONResponse(report)

    @router.get("/history", dependencies=[Depends(_alert_guard)])
    def history(limit: Annotated[int, Query(ge=1, le=200)] = 50) -> JSONResponse:
        items = store.list_history(limit, root=data_root)
        return JSONResponse({"ok": True, "items": items}, headers=_NO_STORE)

    return router


__all__ = ["ALERT_PREFIX", "build_router"]
