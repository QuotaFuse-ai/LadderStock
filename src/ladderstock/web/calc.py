"""`/api/calc/*`：计算层 HTTP 端点（QUOT-511）。

只做路由层：算子目录 + 参数校验 + 结果信封 + 状态码纪律，**不新增算子、
不改算子实现、不改业务模块**。算子全部来自 `ladderstock.calc`（QUOT-508/509/510），
纯函数、无 IO；序列输入由调用方传入并带数据指纹。

契约：

- `GET /api/calc/ops`：算子清单（参数名 / 类型 / 必填与默认值）、单位要求、CALC_VERSION；
- `POST /api/calc/{op}`：请求体 `{"args": {...}, "data_fingerprint": "..."}`。
  `data_fingerprint` 可选（缺省按 "none" 计指纹，与 `calc.data_fingerprint(None)` 一致）；
  请求体多余字段忽略；缺失必填字段报 400 并指名；
- 成功：`{"ok": true, "op", "calc_id", "calculation": {value, unit, display, details}}`；
- 无意义域：**HTTP 200 + ok:false + code:not_meaningful**；
- 参数非法：**HTTP 400 + code:bad_argument + field 指明参数**；
- 未知算子：**HTTP 404 + 响应里回可用算子列表**；
- 内部错误：500 + code:internal_error；
- 严格 JSON：`allow_nan=False`。

算子目录不放 `calc.__all__`（基础层基线测试钉住不含业务算子），登记在本模块。
`convert_amount` 的首参是 `CalcResult`（进程内组合用），JSON 表达不了，不进 HTTP 目录。

闸门同研究面 / 标的字典面：回环独占 + `Host` 白名单；纯计算面，
不受 `LDST_CONTROL_ENABLED` 影响。
"""

from __future__ import annotations

import inspect
import math
import re
import types
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Union, get_args, get_origin, get_type_hints

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ladderstock.calc import CALC_VERSION, earnings, growth, indicators, money, periods, valuation
from ladderstock.calc.fingerprint import calc_id
from ladderstock.calc.result import CalcResult
from ladderstock.web import control as control_plane

CALC_PREFIX = "/api/calc"

_BAD_ARGUMENT: Final = "bad_argument"
_NOT_MEANINGFUL: Final = "not_meaningful"
_INTERNAL_ERROR: Final = "internal_error"
_UNKNOWN_OP: Final = "unknown_op"


@dataclass(frozen=True, slots=True)
class _OpSpec:
    func: Any
    group: str
    unit_note: str


#: HTTP 算子目录：`op → (函数, 分组, 单位要求)`。参数名 / 类型 / 默认值从签名反射，
#: 不手抄第二份，算子签名变更时目录自动跟上。
_REGISTRY: Final[Mapping[str, _OpSpec]] = {
    "amount": _OpSpec(
        money.amount, "money", "金额必须同单位同币种；跨币种需成对给 fx_rate + fx_unit"
    ),
    "pe_ttm": _OpSpec(valuation.pe_ttm, "valuation", "市值与 TTM 净利必须同单位同币种"),
    "pe_deducted_x4": _OpSpec(
        valuation.pe_deducted_x4, "valuation", "市值与单季扣非净利必须同单位同币种"
    ),
    "pe_forward": _OpSpec(valuation.pe_forward, "valuation", "市值与预期净利必须同单位同币种"),
    "peg": _OpSpec(valuation.peg, "valuation", "ttm_yoy 传小数（0.25 = 25%）"),
    "pe_digestion_years": _OpSpec(
        valuation.pe_digestion_years, "valuation", "cagr 传小数（0.25 = 25%）"
    ),
    "forward_cagr": _OpSpec(valuation.forward_cagr, "valuation", "两期净利必须同单位同币种"),
    "yoy": _OpSpec(growth.yoy, "growth", "current 与 prior 必须同单位同币种"),
    "qoq": _OpSpec(growth.qoq, "growth", "current 与 prior 必须同单位同币种"),
    "gross_margin": _OpSpec(growth.gross_margin, "growth", "毛利与营收必须同单位同币种"),
    "expense_ratio": _OpSpec(growth.expense_ratio, "growth", "费用与营收必须同单位同币种"),
    "debt_ratio": _OpSpec(growth.debt_ratio, "growth", "负债与总资产必须同单位同币种"),
    "cumulative_to_single": _OpSpec(
        periods.cumulative_to_single, "periods", "金额单位由 unit 指定（默认元），不猜单位"
    ),
    "ttm": _OpSpec(periods.ttm, "periods", "金额单位由 unit 指定（默认元），不猜单位"),
    "ttm_yoy": _OpSpec(periods.ttm_yoy, "periods", "两期 TTM 必须同单位同币种"),
    "moving_average": _OpSpec(
        indicators.moving_average, "indicators", "价格单位由 unit 指定（默认元）"
    ),
    "rsi": _OpSpec(indicators.rsi, "indicators", "无量纲指数，0–100"),
    "macd": _OpSpec(indicators.macd, "indicators", "价格单位由 unit 指定（默认元）"),
    "atr": _OpSpec(indicators.atr, "indicators", "价格单位由 unit 指定（默认元）"),
    "bollinger": _OpSpec(indicators.bollinger, "indicators", "价格单位由 unit 指定（默认元）"),
    "chip_concentration": _OpSpec(indicators.chip_concentration, "indicators", "集中度为小数，0–1"),
    "earnings_reaction": _OpSpec(
        earnings.earnings_reaction, "earnings", "涨跌为小数；K 线价格须同单位"
    ),
    "earnings_surprise": _OpSpec(
        earnings.earnings_surprise, "earnings", "实际与预期必须同单位；unit 只留痕不换算"
    ),
}

#: 算子模块都带 `from __future__ import annotations`，签名注解是字符串；
#: 目录展示与参数校验都用 get_type_hints 解析成真类型，不解析字符串字面量。
_HINTS: Final[Mapping[str, Mapping[str, Any]]] = {
    name: get_type_hints(spec.func) for name, spec in _REGISTRY.items()
}

_ATOMIC_LABELS: Final[Mapping[Any, str]] = {
    int: "integer",
    float: "number",
    str: "string",
    bool: "boolean",
    type(None): "null",
}


def _type_label(annotation: Any) -> str:
    if annotation is inspect.Parameter.empty:
        return "any"
    if annotation in _ATOMIC_LABELS:
        return _ATOMIC_LABELS[annotation]
    origin = get_origin(annotation)
    if origin in (Union, types.UnionType):
        return " | ".join(_type_label(arg) for arg in get_args(annotation))
    if origin in (Sequence, list):
        args = get_args(annotation)
        return f"array<{_type_label(args[0])}>" if args else "array"
    if origin in (Mapping, dict):
        return "object"
    if annotation is Any:
        return "any"
    return getattr(annotation, "__name__", str(annotation))


def ops_catalog() -> dict[str, Any]:
    """`GET /api/calc/ops` 的响应体：算子、参数名与类型、单位要求、CALC_VERSION。"""
    ops = []
    for name, spec in _REGISTRY.items():
        hints = _HINTS[name]
        params = []
        for param in inspect.signature(spec.func).parameters.values():
            if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                continue
            optional = param.default is not inspect.Parameter.empty
            params.append(
                {
                    "name": param.name,
                    "type": _type_label(hints.get(param.name, param.annotation)),
                    "required": not optional,
                    "default": param.default if optional else None,
                }
            )
        ops.append(
            {
                "name": name,
                "group": spec.group,
                "params": params,
                "unit_requirement": spec.unit_note,
            }
        )
    return {"ok": True, "calc_version": CALC_VERSION, "ops": ops}


class _CalcPlane:
    """请求解析与算子调用；纯计算，无数据根、无 IO。"""

    def __init__(self, registry: Mapping[str, _OpSpec] | None = None) -> None:
        # 注册表在构造时捕获引用；测试可注入假算子验证 500 纪律
        self._registry = _REGISTRY if registry is None else registry

    def invoke(self, op: str, raw_body: Any) -> JSONResponse:
        spec = self._registry.get(op)
        if spec is None:
            return _error_response(
                404,
                op,
                None,
                _UNKNOWN_OP,
                f"未知算子 {op!r}",
                available_ops=sorted(_REGISTRY),
            )

        body = raw_body if isinstance(raw_body, dict) else {}
        raw_args = body.get("args", {})
        if not isinstance(raw_args, dict):
            return _error_response(400, op, None, _BAD_ARGUMENT, "args 必须是对象", field="args")
        raw_fp = body.get("data_fingerprint", "none")
        if not isinstance(raw_fp, str) or not raw_fp.strip():
            return _error_response(
                400, op, None, _BAD_ARGUMENT, "data_fingerprint 必须是非空字符串",
                field="data_fingerprint",
            )
        data_fp = raw_fp.strip()

        bound, error = _bind_args(op, spec, raw_args)
        if error is not None:
            field, message = error
            return _error_response(400, op, None, _BAD_ARGUMENT, message, field=field)

        # calc_id 只依赖参数与数据指纹；绑定成功即可复算，与结果成败无关
        identifier = calc_id(op, bound, data_fp=data_fp)

        try:
            result = spec.func(**bound)
        except Exception:  # noqa: BLE001 - 算子内部异常统一 500，不透栈
            return _error_response(500, op, identifier, _INTERNAL_ERROR, "计算内部错误")
        if not isinstance(result, CalcResult):
            return _error_response(500, op, identifier, _INTERNAL_ERROR, "算子返回类型异常")
        try:
            return _result_response(op, identifier, result)
        except Exception:  # noqa: BLE001 - 返回树非有限 / 不可序列化同样 500
            return _error_response(500, op, identifier, _INTERNAL_ERROR, "计算结果无法严格序列化")


def _result_response(op: str, identifier: str, result: CalcResult) -> JSONResponse:
    if result.ok:
        payload = {
            "ok": True,
            "op": op,
            "calc_id": identifier,
            "calculation": {
                "value": result.value,
                "unit": result.unit,
                "display": result.display,
                "details": dict(result.details),
            },
        }
        return JSONResponse(payload)
    details = dict(result.details)
    if result.reason == _BAD_ARGUMENT:
        field = details.get("field")
        message = str(details.get("message") or "参数非法")
        return _error_response(400, op, identifier, _BAD_ARGUMENT, message, field=field)
    message = str(details.get("message") or "结果无意义")
    return _error_response(200, op, identifier, _NOT_MEANINGFUL, message, details=details)


def _bind_args(
    op: str, spec: _OpSpec, raw_args: Mapping[str, Any]
) -> tuple[dict[str, Any], tuple[str | None, str] | None]:
    """按签名校验并转型 args；返回 (绑定参数, (field, message) 错误)。

    缺失必填字段、未知参数、类型不符都在这层拦成 400 并指名；算子自己的域校验
    （分母 <= 0、百分数误传等）仍由算子返回 CalcResult 决定状态码。
    """
    bound: dict[str, Any] = {}
    signature = inspect.signature(spec.func)
    hints = _HINTS.get(op) or get_type_hints(spec.func)
    known = {
        name
        for name, param in signature.parameters.items()
        if param.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    }
    for name in raw_args:
        if name not in known:
            return {}, (name, f"未知参数 {name}（{spec.func.__name__} 无此入参）")
    for name, param in signature.parameters.items():
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        optional = param.default is not inspect.Parameter.empty
        if name not in raw_args:
            if optional:
                continue  # 用算子签名默认值，不手抄
            return {}, (name, f"缺少必填参数 {name}")
        value, error = _coerce(raw_args[name], hints.get(name, param.annotation), name)
        if error is not None:
            return {}, (name, error)
        bound[name] = value
    return bound, None


def _coerce(value: Any, annotation: Any, field: str) -> tuple[Any, str | None]:
    if annotation is inspect.Parameter.empty or annotation is Any:
        return value, None
    origin = get_origin(annotation)
    if origin in (Union, types.UnionType):
        options = get_args(annotation)
        if value is None and type(None) in options:
            return None, None
        errors: list[str] = []
        for option in options:
            if option is type(None):
                continue
            coerced, error = _coerce(value, option, field)
            if error is None:
                return coerced, None
            errors.append(error)
        return None, errors[0] if errors else f"{field} 类型不符"
    if annotation is int:
        if isinstance(value, bool) or not isinstance(value, int):
            return None, f"{field} 必须是整数，收到 {value!r}"
        return value, None
    if annotation is float:
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None, f"{field} 必须是有限数值，收到 {value!r}"
        number = float(value)
        if not math.isfinite(number):
            return None, f"{field} 必须是有限数值，收到 {value!r}"
        return number, None
    if annotation is str:
        if not isinstance(value, str):
            return None, f"{field} 必须是字符串，收到 {value!r}"
        return value, None
    if annotation is bool:
        if not isinstance(value, bool):
            return None, f"{field} 必须是布尔值，收到 {value!r}"
        return value, None
    if origin in (Sequence, list):
        if isinstance(value, str | bytes | bytearray) or not isinstance(value, list):
            return None, f"{field} 必须是数组，收到 {value!r}"
        args = get_args(annotation)
        if not args:
            return list(value), None
        items = []
        for index, item in enumerate(value):
            coerced, error = _coerce(item, args[0], f"{field}[{index}]")
            if error is not None:
                return None, error
            items.append(coerced)
        return items, None
    if origin in (Mapping, dict):
        if not isinstance(value, dict):
            return None, f"{field} 必须是对象，收到 {value!r}"
        return dict(value), None
    return value, None


_FIELD_IN_TEXT: Final = re.compile(r"([A-Za-z_][A-Za-z0-9_]*(?:\[\d+\])?)")


def _error_response(
    status: int,
    op: str,
    identifier: str | None,
    code: str,
    message: str,
    *,
    field: str | None = None,
    details: Mapping[str, Any] | None = None,
    available_ops: Sequence[str] | None = None,
) -> JSONResponse:
    if field is None and code == _BAD_ARGUMENT:
        # 算子的 bad_argument 不一定带 field；从消息里提参数名，提不出才留 null
        match = _FIELD_IN_TEXT.search(message)
        field = match.group(1) if match else None
    error: dict[str, Any] = {"code": code, "message": message, "field": field}
    if details:
        error["details"] = dict(details)
    payload: dict[str, Any] = {"ok": False, "op": op, "calc_id": identifier, "error": error}
    if available_ops is not None:
        payload["available_ops"] = list(available_ops)
    return JSONResponse(payload, status_code=status)


async def _guard(request: Request) -> None:
    """回环来源 + `Host` 白名单（照 `/api/research/*` / `/api/symbols/*` 的形状）。"""
    if not control_plane.is_loopback_host(control_plane.client_host(request.client)):
        raise HTTPException(status_code=403, detail="计算端点只接受回环来源")
    if not control_plane.host_header_allowed(request):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Host 不在白名单内：{request.headers.get('host') or '(空)'}"
                "（只接受 127.0.0.1 / localhost / ::1 或本机监听地址）"
            ),
        )


def build_router() -> APIRouter:
    """构造 `/api/calc/ops` 与 `/api/calc/{op}` 两个路由。"""
    router = APIRouter(prefix=CALC_PREFIX, dependencies=[Depends(_guard)])
    plane = _CalcPlane()

    @router.get("/ops")
    def list_ops() -> JSONResponse:
        return JSONResponse(ops_catalog())

    @router.post("/{op}")
    async def invoke_op(op: str, request: Request) -> JSONResponse:
        try:
            raw_body = await request.json()
        except ValueError:  # json.JSONDecodeError 是 ValueError 子类
            return _error_response(
                400, op, None, _BAD_ARGUMENT, "请求体必须是合法 JSON", field="body"
            )
        return plane.invoke(op, raw_body)

    return router


__all__ = ["CALC_PREFIX", "build_router", "ops_catalog"]