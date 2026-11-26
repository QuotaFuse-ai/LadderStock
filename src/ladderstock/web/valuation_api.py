"""`/api/valuation/estimate`：估值换算器的 Web 面（E 包 / DEC-091 §4）。

与 `ldst valuation`（CLI 面）共用 `valuation/calculator.py` 的同一批纯函数：

- **不取数、不写库**：输入全部由页面表单显式给出；结果声明为「非研究产物」，
  研究管线的 valuation 段照旧以 facts 为唯一数字来源（DEC-025 红线不动）；
- 闸门与 `POST /api/table-export` 同款（POST 写面语义）：回环 + Host +
  同源来源证据（`X-LDST-Client: 1` 或同源 Origin）——换算本身无副作用，
  收紧到写面只是把「脚本能白嫖的计算面」与「本机页面」分开；
- 参数缺 / 非法 → 400 + 人话 `detail`（与 CLI 的 exit 2 同源，同一句文案）；
  不静默补 0、不猜；
- 响应恒带 `disclaimer`：「这是换算器，不是估值判断」。
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ladderstock.errors import LadderStockError
from ladderstock.valuation import calculator
from ladderstock.web.radar import privacy_read_guard, privacy_write_guard

VALUATION_PREFIX = "/api/valuation"

DENIED = "估值换算器只对本机回环开放（127.0.0.1 / localhost / ::1）"
WRITE_DENIED = "估值换算器只接受本机页面发起的请求（Origin 或 X-LDST-Client: 1）"

_NO_STORE = {"Cache-Control": "no-store"}

#: 表单字段 → 换算函数所需的参数名（`kind` 单独收）
KINDS = ("pe", "pb", "ps", "dividend", "earnings-yield", "dcf")

#: 每种换算需要哪些数字字段（缺一不可，与 `ldst valuation --help` 同一口径）
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "pe": ("price", "eps"),
    "pb": ("price", "bps"),
    "ps": ("price", "sps"),
    "dividend": ("price", "dps"),
    "earnings-yield": ("pe",),
    "dcf": ("fcf", "growth", "terminal_growth", "discount", "periods"),
}


async def _read_guard(request: Request) -> None:
    await privacy_read_guard(request, denied=DENIED, subject="估值换算器")


async def _write_guard(request: Request) -> None:
    await privacy_write_guard(
        request, denied=DENIED, subject="估值换算器", write_denied=WRITE_DENIED
    )


def _number(body: dict[str, Any], name: str) -> float:
    """表单数字收取：缺 / 非数 / 布尔一律当参数错（人话，与 CLI 同源）。"""
    raw = body.get(name)
    if isinstance(raw, bool) or raw is None:
        raise LadderStockError(f"缺少参数 {name}", 2)
    if isinstance(raw, int):
        return float(raw)
    if isinstance(raw, float):
        return raw
    raise LadderStockError(f"参数 {name} 必须是数字（收到 {raw!r}）", 2)


def _periods(body: dict[str, Any]) -> int:
    raw = body.get("periods")
    if isinstance(raw, bool) or raw is None:
        raise LadderStockError("缺少参数 periods", 2)
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    if isinstance(raw, float) and float(raw).is_integer():
        return int(raw)
    raise LadderStockError(f"参数 periods 必须是整数年数（收到 {raw!r}）", 2)


def estimate(body: dict[str, Any]) -> dict[str, Any]:
    """换算入口：kind → calculator 纯函数；参数错统一抛 LadderStockError（2）。"""
    kind = str(body.get("kind") or "").strip().lower()
    if kind not in KINDS:
        raise LadderStockError(f"未知换算类型 {kind!r}；可用：{' / '.join(KINDS)}", 2)
    missing = [name for name in REQUIRED_FIELDS[kind] if body.get(name) is None]
    if missing:
        raise LadderStockError(f"{kind} 需要参数：{'、'.join(missing)}", 2)
    if kind == "pe":
        return calculator.pe(_number(body, "price"), _number(body, "eps"))
    if kind == "pb":
        return calculator.pb(_number(body, "price"), _number(body, "bps"))
    if kind == "ps":
        return calculator.ps(_number(body, "price"), _number(body, "sps"))
    if kind == "dividend":
        return calculator.dividend_yield(_number(body, "price"), _number(body, "dps"))
    if kind == "earnings-yield":
        return calculator.earnings_yield(_number(body, "pe"))
    return calculator.dcf_simple(
        _number(body, "fcf"),
        _number(body, "growth"),
        _number(body, "terminal_growth"),
        _number(body, "discount"),
        _periods(body),
    )


def build_router() -> APIRouter:
    router = APIRouter(prefix=VALUATION_PREFIX)

    @router.post("/estimate", dependencies=[Depends(_read_guard), Depends(_write_guard)])
    def estimate_endpoint(
        body: Annotated[
            dict[str, Any],
            Body(
                examples=[
                    {
                        "kind": "pe",
                        "price": 10.5,
                        "eps": 2.1,
                    }
                ]
            ),
        ],
    ) -> JSONResponse:
        try:
            payload = estimate(body)
        except LadderStockError as exc:
            raise HTTPException(status_code=400, detail=exc.message) from exc
        return JSONResponse(payload, headers=_NO_STORE)

    return router


__all__ = ["KINDS", "VALUATION_PREFIX", "build_router", "estimate"]
