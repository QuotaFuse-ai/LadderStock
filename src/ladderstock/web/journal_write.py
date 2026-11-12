"""`POST /api/journal/trades`：交易日志的本机写入口（QUOT-561 / DEC-083）。

为什么需要这条写面：日志页此前只有 CLI 一条写入路径（`ldst journal add`），网页
「记录一笔」是一颗永远 disabled 的按钮。QUOT-561 要求产物面在 UI 里能真正落地，
所以补**最小**写面，而不是把日志改造成新的数据面：

- **只写本机**：落库复用 `journal.log.add_trade()`（与 CLI 同一个函数、同一张
  `journal.sqlite`），HTTP 层不重算、不派生任何指标；
- **闸门复用既有实现**：回环 + `Host` 白名单 + 写来源证据（同源 `Origin` 或
  `X-LDST-Client: 1`），用 `web/radar.py` 的 `privacy_write_guard`，不新增鉴权；
- **白名单硬拒绝**：请求体多一个键就 400（券商账号 / 资金密码这类字段没有入口）；
- **响应扁平、`no-store`、只回这一笔**：字段严格等于 `journal.log.TRADE_FIELDS`，
  不携带其它成交、不做聚合（聚合仍只走 `GET /api/journal/stats`）。

``note`` 是用户自己写的自由文本，只进本机 SQLite：写响应会回显这一笔（用户刚输入
的内容），但任何聚合 / 列表 / 信封出口都不会带它（DEC-015 的出口口径不变）。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ladderstock.data import sqlite_store
from ladderstock.errors import EXIT_USAGE, LadderStockError
from ladderstock.journal import log as journal_log
from ladderstock.web.radar import privacy_read_guard, privacy_write_guard

JOURNAL_WRITE_PREFIX = "/api/journal"

JOURNAL_DENIED = "交易日志只对本机回环开放（127.0.0.1 / localhost / ::1）"
JOURNAL_WRITE_DENIED = "交易日志写面只接受同源来源（Origin 或 X-LDST-Client: 1）"

#: 请求体白名单：多一个键就拒绝（与 `journal.log.add_trade` 的参数一一对应）
TRADE_WRITE_FIELDS: frozenset[str] = frozenset(
    {"code", "side", "price", "qty", "note", "trade_date", "plan_price"}
)
TRADE_REQUIRED_FIELDS: tuple[str, ...] = ("code", "side", "price", "qty")

#: 单笔备注的上限：日志页只记一句话，过长多半是粘错了东西
NOTE_MAX_LEN = 200

_NO_STORE = {"Cache-Control": "no-store"}
#: 写响应只回这一笔的固定字段（顺序即 `journal.log.TRADE_FIELDS`）
_TRADE_FIELDS = journal_log.TRADE_FIELDS


def _bad_body(detail: str) -> HTTPException:
    return HTTPException(status_code=400, detail=detail)


def _number(raw: Any, key: str) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise _bad_body(f"{key} 必须是数值，收到 {raw!r}")
    return float(raw)


def trade_write_fields(raw: Any) -> dict[str, Any]:
    """把请求体收成 `journal.log.add_trade` 的关键字参数（白名单 + 类型 + 日期形状）。

    数值的正负 / 侧别校验仍留给 `add_trade()`：那是 CLI 与 HTTP 共用的唯一口径。
    """
    if not isinstance(raw, dict):
        raise _bad_body("请求体必须是 JSON 对象")
    extra = sorted(set(raw) - TRADE_WRITE_FIELDS)
    if extra:
        raise _bad_body(
            "不接受的字段："
            + ", ".join(extra)
            + f"（只认 {' / '.join(sorted(TRADE_WRITE_FIELDS))}）"
        )
    missing = [key for key in TRADE_REQUIRED_FIELDS if key not in raw]
    if missing:
        raise _bad_body(f"缺少必填字段：{', '.join(missing)}")
    code = raw["code"]
    if not isinstance(code, str) or not code.strip():
        raise _bad_body("code 必须是非空字符串")
    side = raw["side"]
    if not isinstance(side, str):
        raise _bad_body("side 必须是字符串（buy / sell）")
    note = raw.get("note") or ""
    if not isinstance(note, str):
        raise _bad_body("note 必须是字符串")
    if len(note.strip()) > NOTE_MAX_LEN:
        raise _bad_body(f"note 最长 {NOTE_MAX_LEN} 个字符，收到 {len(note.strip())} 个")
    trade_date = raw.get("trade_date")
    if trade_date is not None:
        if not isinstance(trade_date, str):
            raise _bad_body("trade_date 必须是 YYYY-MM-DD 字符串")
        try:
            date.fromisoformat(trade_date.strip())
        except ValueError:
            raise _bad_body(f"非法 trade_date：{trade_date!r}（需要 YYYY-MM-DD）") from None
        trade_date = trade_date.strip()
    plan_price = raw.get("plan_price")
    return {
        "code": code,
        "side": side,
        "price": _number(raw["price"], "price"),
        "qty": _number(raw["qty"], "qty"),
        "note": note,
        "trade_date": trade_date,
        "plan_price": None if plan_price is None else _number(plan_price, "plan_price"),
    }


def trade_payload(row: dict[str, Any]) -> dict[str, Any]:
    """写响应：扁平 + 固定字段（不带 `local_only` 之外的任何聚合 / 其它成交）。"""
    payload: dict[str, Any] = {
        "ok": True,
        "local_only": True,
        "store": "local_sqlite",
    }
    payload.update({key: row.get(key) for key in _TRADE_FIELDS})
    return payload


async def _journal_read_guard(request: Request) -> None:
    await privacy_read_guard(request, denied=JOURNAL_DENIED, subject="交易日志")


async def _journal_write_guard(request: Request) -> None:
    await privacy_write_guard(
        request,
        denied=JOURNAL_DENIED,
        subject="交易日志",
        write_denied=JOURNAL_WRITE_DENIED,
    )


def build_router(data_root: Path | None) -> APIRouter:
    router = APIRouter(prefix=JOURNAL_WRITE_PREFIX)

    @router.get("/write-info", dependencies=[Depends(_journal_read_guard)])
    def journal_write_info() -> JSONResponse:
        """日志页「记录一笔」的可写性探测：只回开关状态，不触发写入。"""
        return JSONResponse(
            {
                "ok": True,
                "local_only": True,
                "store": "local_sqlite",
                "writable": True,
                "write_path": str(sqlite_store.db_path(data_root)),
                "sides": list(journal_log.SIDES),
                "note_max_len": NOTE_MAX_LEN,
            },
            headers=_NO_STORE,
        )

    @router.post("/trades", dependencies=[Depends(_journal_write_guard)])
    def journal_add_trade(body: Any = Body(...)) -> JSONResponse:
        """追加一笔成交（与本机 CLI 同一个写入函数）；校验失败 400，不静默成功。"""
        fields = trade_write_fields(body)
        try:
            row = journal_log.add_trade(root=data_root, **fields)
        except LadderStockError as exc:
            status = 400 if exc.code == EXIT_USAGE else 500
            raise HTTPException(status_code=status, detail=exc.message) from None
        return JSONResponse(trade_payload(row), headers=_NO_STORE)

    return router


__all__ = [
    "JOURNAL_DENIED",
    "JOURNAL_WRITE_DENIED",
    "JOURNAL_WRITE_PREFIX",
    "NOTE_MAX_LEN",
    "TRADE_REQUIRED_FIELDS",
    "TRADE_WRITE_FIELDS",
    "build_router",
    "trade_payload",
    "trade_write_fields",
]
