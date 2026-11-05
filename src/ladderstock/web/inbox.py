"""`/api/inbox`：本机提醒收件箱（DEC-058，页面由 QUOT-435 实现）。

三件事，都是**本机隐私数据**：

- `GET /api/inbox?limit=50`：平面 JSON + `Cache-Control: no-store`，回环独占；
- `POST /api/inbox/{id}/read`：标一条已读；
- `POST /api/inbox/read-all`：全部标已读。

闸门**复用 `web/radar.py` 的同一份实现**（`privacy_read_guard` / `privacy_write_guard`），
不新增第二种鉴权：读面 = 回环 + `Host` 白名单；写面再加「同源 `Origin` 或
`X-LDST-Client: 1`」。`inbox` 不在 `core/privacy.py` 的 `PRIVATE_KEYS` 里，但仍按本机
隐私数据处理——它回答的是「这台机器在盯什么、记了什么」。

响应顶层键刻意只出现 `local_only` / `store` / `items` / `unread` / `jobs`：
`notifications` 之外的隐私词不进顶层，`items[]` 里也不带任何自由文本备注（收件箱正文
本来就是生成的摘要行，没有用户手写内容）。

`jobs[]` 只列「会产生提醒」的任务（`kind: digest` 与 `kind: notify`），不是整张调度表——
把快照 / 同步任务混进提醒页会让用户以为那些也会推消息。没有持久化的运行状态库，
所以 `last_status` 这一版不提供（不拿「从未运行」冒充一个状态）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from ladderstock.errors import EXIT_NOT_FOUND, LadderStockError

# 显式子模块名 `ladderstock.journal.inbox` 给隐私白名单精确匹配（不写包级 import）
from ladderstock.journal.inbox import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    list_items,
    mark_all_read,
    mark_read,
    unread_count,
)
from ladderstock.web.radar import privacy_read_guard, privacy_write_guard

INBOX_PREFIX = "/api/inbox"

INBOX_DENIED = "提醒收件箱只对本机回环开放（127.0.0.1 / localhost / ::1）"
INBOX_WRITE_DENIED = "提醒写面只接受同源来源（Origin 或 X-LDST-Client: 1）"

#: 会产生本机提醒的任务类型（`digest` 是三类摘要，`notify` 是用户自配的静态口播）
NOTIFY_KINDS: tuple[str, ...] = ("digest", "notify")

_NO_STORE = {"Cache-Control": "no-store"}


async def _inbox_guard(request: Request) -> None:
    await privacy_read_guard(request, denied=INBOX_DENIED, subject="提醒")


async def _inbox_write_guard(request: Request) -> None:
    await privacy_write_guard(
        request,
        denied=INBOX_DENIED,
        subject="提醒",
        write_denied=INBOX_WRITE_DENIED,
    )


def _error(exc: LadderStockError) -> HTTPException:
    """业务异常 → HTTP：找不到 404，其余按用法错误 400（与其它隐私面同口径）。"""
    status = 404 if exc.code == EXIT_NOT_FOUND else 400
    return HTTPException(status_code=status, detail=exc.message)


def reminder_jobs() -> list[dict[str, Any]]:
    """调度表里会产出提醒的任务；配置读不到时返回空列表（页面只说「没有配置」）。"""
    from ladderstock.cli.schedule import load_schedule

    try:
        table, _warnings, _source = load_schedule()
    except LadderStockError:
        return []
    out: list[dict[str, Any]] = []
    for job in table.get("jobs") or []:
        if not isinstance(job, dict) or job.get("kind") not in NOTIFY_KINDS:
            continue
        out.append(
            {
                "id": job.get("id"),
                "label": job.get("name"),
                "trigger": job.get("trigger"),
                "enabled": bool(job.get("enabled")),
            }
        )
    return out


def inbox_view(
    *, data_root: Path | None = None, limit: int = DEFAULT_LIMIT
) -> dict[str, Any]:
    """收件箱读面的载荷（平面 JSON，无信封）。"""
    items = list_items(limit, root=data_root)
    return {
        "local_only": True,
        "store": "local_sqlite",
        "items": items,
        "unread": unread_count(root=data_root),
        "jobs": reminder_jobs(),
    }


def build_router(*, data_root: Path | None = None) -> APIRouter:
    """收件箱三条路由；数据根可注入，单测写临时库即可跑。"""
    router = APIRouter(prefix=INBOX_PREFIX)

    @router.get("", dependencies=[Depends(_inbox_guard)])
    def inbox_list(
        limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = (
            DEFAULT_LIMIT
        ),
    ) -> JSONResponse:
        """提醒列表：平面 JSON、`no-store`、只 GET。"""
        return JSONResponse(inbox_view(data_root=data_root, limit=limit), headers=_NO_STORE)

    @router.post("/read-all", dependencies=[Depends(_inbox_write_guard)])
    def inbox_read_all() -> JSONResponse:
        """全部标已读：返回本次实际改写条数（幂等）。"""
        payload = mark_all_read(root=data_root)
        return JSONResponse(
            {**payload, "unread": unread_count(root=data_root)},
            headers=_NO_STORE,
        )

    @router.post("/{item_id}/read", dependencies=[Depends(_inbox_write_guard)])
    def inbox_mark_read(item_id: str) -> JSONResponse:
        """标一条已读；不存在返回 404，不静默成功。"""
        try:
            item = mark_read(item_id, root=data_root)
        except LadderStockError as exc:
            raise _error(exc) from None
        return JSONResponse(
            {"item": item, "unread": unread_count(root=data_root)},
            headers=_NO_STORE,
        )

    return router


__all__ = [
    "INBOX_DENIED",
    "INBOX_PREFIX",
    "INBOX_WRITE_DENIED",
    "NOTIFY_KINDS",
    "build_router",
    "inbox_view",
    "reminder_jobs",
]
