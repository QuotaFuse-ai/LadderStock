"""`/api/coverage`：F12 数据集覆盖看板 + 受控补采。

四个接口：

- `GET  /api/coverage/board?dataset=`：62 个数据集逐个的 state / 行数 / 最新 / 池内覆盖 / 原因；
- `GET  /api/coverage/gaps`：只挑需要处理的（按严重度排序），给页面「先修哪个」；
- `GET  /api/coverage/plan?dataset=`：这批补采会请求什么（不发请求）；
- `POST /api/coverage/backfill?dataset=&execute=`：默认返回计划；`execute=true` 才真跑一次。

`board` 是**现场算**的（单连接扫 62 张表的行数 / 最新日期），实测约 0.3 秒，不缓存：
缓存会让「刚刚补采成功」这张卡还显示旧的缺口。

读面 = 回环 + Host 白名单；写面再加同源 Origin 或 `X-LDST-Client: 1`。补采会真发请求、
写库，所以它落在写面，且 `execute` 必须显式给，缺省永远是 dry-run。
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from ladderstock.data import coverage_board as cb
from ladderstock.web.radar import privacy_read_guard, privacy_write_guard

COVERAGE_PREFIX = "/api/coverage"

COVERAGE_DENIED = "覆盖看板只对本机回环开放（127.0.0.1 / localhost / ::1）"
COVERAGE_WRITE_DENIED = "补采只接受同源来源（Origin 或 X-LDST-Client: 1）"

_NO_STORE = {"Cache-Control": "no-store"}


async def _coverage_guard(request: Request) -> None:
    await privacy_read_guard(request, denied=COVERAGE_DENIED, subject="覆盖看板")


async def _coverage_write_guard(request: Request) -> None:
    await privacy_write_guard(
        request,
        denied=COVERAGE_DENIED,
        subject="覆盖看板",
        write_denied=COVERAGE_WRITE_DENIED,
    )


def _require_dataset(dataset: str) -> str:
    name = str(dataset or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="缺少 dataset 参数")
    if name not in cb.catalog_datasets():
        raise HTTPException(
            status_code=404,
            detail=f"没有这个数据集 {name!r}；它必须在 DATASET_CATALOG 里注册",
        )
    return name


def board_payload(
    *,
    data_root: Path | None = None,
    datasets: list[str] | None = None,
) -> dict[str, Any]:
    """看板载荷；加一份中文状态标签表，前端不用自己维护映射。"""
    payload = cb.board(root=data_root, datasets=datasets)
    payload["labels"] = dict(cb.STATE_LABELS)
    payload["gaps"] = cb.gaps(payload)
    return payload


def build_router(*, data_root: Path | None = None) -> APIRouter:
    """四条路由；数据根可注入，单测写临时目录即可跑。"""
    router = APIRouter(prefix=COVERAGE_PREFIX)

    @router.get("/board", dependencies=[Depends(_coverage_guard)])
    def coverage_board(
        dataset: Annotated[list[str] | None, Query()] = None,
    ) -> JSONResponse:
        """62 个数据集的落盘情况（现场扫库）。"""
        return JSONResponse(
            {"local_only": True, **board_payload(data_root=data_root, datasets=dataset)},
            headers=_NO_STORE,
        )

    @router.get("/gaps", dependencies=[Depends(_coverage_guard)])
    def coverage_gaps() -> JSONResponse:
        """只列需要处理的（严重度排序）。"""
        payload = board_payload(data_root=data_root)
        return JSONResponse(
            {
                "local_only": True,
                "generated_at": payload["generated_at"],
                "summary": payload["summary"],
                "gaps": payload["gaps"],
                "labels": payload["labels"],
            },
            headers=_NO_STORE,
        )

    @router.get("/plan", dependencies=[Depends(_coverage_guard)])
    def coverage_plan(
        dataset: Annotated[str, Query()],
        codes: Annotated[str | None, Query()] = None,
        max_codes: Annotated[int, Query(ge=1, le=cb.MAX_BATCH_CODES)] = cb.MAX_BATCH_CODES,
    ) -> JSONResponse:
        """这批补采会请求什么；不发请求。"""
        name = _require_dataset(dataset)
        code_list = [c.strip() for c in codes.split(",") if c.strip()] if codes else None
        try:
            prepared = cb.plan(name, codes=code_list, max_codes=max_codes)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return JSONResponse(prepared, headers=_NO_STORE)

    @router.post("/backfill", dependencies=[Depends(_coverage_write_guard)])
    def coverage_backfill(
        dataset: Annotated[str, Query()],
        execute: Annotated[bool, Query()] = False,
        codes: Annotated[str | None, Query()] = None,
        max_codes: Annotated[int, Query(ge=1, le=cb.MAX_BATCH_CODES)] = cb.MAX_BATCH_CODES,
    ) -> JSONResponse:
        """受控补采：`execute` 缺省 false（只回计划），真跑必须显式传 true。"""
        name = _require_dataset(dataset)
        code_list = [c.strip() for c in codes.split(",") if c.strip()] if codes else None
        try:
            result = cb.backfill(
                name,
                codes=code_list,
                execute=bool(execute),
                root=data_root,
                max_codes=max_codes,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return JSONResponse(result, headers=_NO_STORE)

    return router


__all__ = [
    "COVERAGE_DENIED",
    "COVERAGE_PREFIX",
    "COVERAGE_WRITE_DENIED",
    "board_payload",
    "build_router",
]
