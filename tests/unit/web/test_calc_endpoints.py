"""QUOT-511：`/api/calc/*` 计算层 HTTP 端点的契约测试。

钉住的口径（每项断言 状态码 + ok + code + field 四项，不许只断言 ok）：

1. `GET /api/calc/ops`：算子清单含参数名 / 类型 / 必填 / 默认值、单位要求、CALC_VERSION；
2. `POST /api/calc/{op}` 成功：`calculation.value` 与算子直算一致，calc_id 可复算；
3. 无意义域 → HTTP 200 + ok:false + code:not_meaningful；
4. 参数非法 → HTTP 400 + code:bad_argument + field 指名（缺失必填 / 未知参数 / 类型不符）；
5. 未知算子 → HTTP 404 + available_ops 列表；
6. 内部错误 → 500 + code:internal_error；
7. 严格 JSON：响应体不含 NaN / Infinity；多余请求体字段忽略；
8. 闸门：非回环 403、Host 不在白名单 403（照 `/api/symbols/*` 的形状）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.calc import CALC_VERSION, valuation
from ladderstock.calc.fingerprint import calc_id
from ladderstock.web import calc as calc_api
from ladderstock.web.server import create_app

LOOPBACK = ("127.0.0.1", 54321)
REMOTE = ("10.1.2.3", 54321)

FP = "4:2026-11-03:2026-11-06:0123456789abcdef"


def _client(root: Path, client: tuple[str, int] = LOOPBACK) -> TestClient:
    return TestClient(create_app(data_root=root, dist_dir=root / "no-dist"), client=client)


def _post(root: Path, op: str, args: dict[str, Any], **body: Any) -> Any:
    return _client(root).post(f"/api/calc/{op}", json={"args": args, **body})


def _assert_error(response: Any, status: int, code: str, field: str | None) -> dict[str, Any]:
    """四项一起断言：状态码 + ok + code + field。"""
    assert response.status_code == status, response.text
    body = response.json()
    assert body["ok"] is False
    assert body["error"]["code"] == code
    assert body["error"]["field"] == field
    assert body["error"]["message"]
    return body


# --------------------------------------------------------------------------- #
# 1. 算子目录
# --------------------------------------------------------------------------- #

def test_ops_catalog_shape(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/api/calc/ops")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["calc_version"] == CALC_VERSION
    names = {op["name"] for op in body["ops"]}
    assert {"pe_ttm", "peg", "yoy", "ttm", "moving_average", "earnings_surprise"} <= names
    # convert_amount 的首参是 CalcResult，JSON 表达不了，不进 HTTP 目录
    assert "convert_amount" not in names
    pe = next(op for op in body["ops"] if op["name"] == "pe_ttm")
    assert pe["unit_requirement"]
    assert {param["name"] for param in pe["params"]} == {"market_cap", "net_profit_ttm"}
    for param in pe["params"]:
        assert param["type"] == "number"
        assert param["required"] is True
        assert param["default"] is None
    ma = next(op for op in body["ops"] if op["name"] == "moving_average")
    period = next(param for param in ma["params"] if param["name"] == "period")
    assert period["type"] == "integer"
    assert period["required"] is False
    assert period["default"] == 5


# --------------------------------------------------------------------------- #
# 2. 成功：value 与算子直算一致，calc_id 可复算
# --------------------------------------------------------------------------- #

def test_pe_ttm_success_matches_operator(tmp_path: Path) -> None:
    args = {"market_cap": 184200000000.0, "net_profit_ttm": 10000000000.0}
    response = _post(tmp_path, "pe_ttm", args, data_fingerprint=FP)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["op"] == "pe_ttm"
    direct = valuation.pe_ttm(**args)
    assert body["calculation"]["value"] == direct.value
    assert body["calculation"]["unit"] == "倍"
    assert body["calculation"]["display"] == direct.display
    assert body["calc_id"] == calc_id("pe_ttm", args, data_fp=FP)
    assert body["calc_id"].startswith("c_")


def test_fingerprint_defaults_and_extra_fields_ignored(tmp_path: Path) -> None:
    args = {"market_cap": 100.0, "net_profit_ttm": 10.0}
    response = _post(tmp_path, "pe_ttm", args, unexpected_field="ignored")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["calc_id"] == calc_id("pe_ttm", args, data_fp="none")


def test_sequence_operator_success(tmp_path: Path) -> None:
    rows = [{"date": f"2026-11-{day:02d}", "close": 10.0 + day} for day in range(1, 7)]
    response = _post(tmp_path, "moving_average", {"rows": rows, "period": 5})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["calculation"]["value"] == pytest.approx(14.0)
    assert body["calculation"]["unit"] == "元"


# --------------------------------------------------------------------------- #
# 3. 无意义域：HTTP 200 + ok:false + not_meaningful
# --------------------------------------------------------------------------- #

def test_pe_ttm_non_positive_profit_is_200_not_meaningful(tmp_path: Path) -> None:
    args = {"market_cap": 100.0, "net_profit_ttm": -5.0}
    body = _assert_error(
        _post(tmp_path, "pe_ttm", args), 200, "not_meaningful", None
    )
    assert body["op"] == "pe_ttm"
    assert body["calc_id"] == calc_id("pe_ttm", args, data_fp="none")


def test_ttm_missing_quarter_is_200_not_meaningful(tmp_path: Path) -> None:
    _assert_error(
        _post(tmp_path, "ttm", {"single_quarters": [1.0, None, 3.0, 4.0]}),
        200,
        "not_meaningful",
        None,
    )


# --------------------------------------------------------------------------- #
# 4. 参数非法：HTTP 400 + bad_argument + field 指名
# --------------------------------------------------------------------------- #

def test_missing_required_arg_names_field(tmp_path: Path) -> None:
    _assert_error(
        _post(tmp_path, "pe_ttm", {"market_cap": 100.0}),
        400,
        "bad_argument",
        "net_profit_ttm",
    )


def test_unknown_arg_cap_unit_is_400(tmp_path: Path) -> None:
    _assert_error(
        _post(
            tmp_path,
            "pe_ttm",
            {"market_cap": 100.0, "net_profit_ttm": 10.0, "cap_unit": "USD"},
        ),
        400,
        "bad_argument",
        "cap_unit",
    )


def test_wrong_type_names_field(tmp_path: Path) -> None:
    _assert_error(
        _post(tmp_path, "pe_ttm", {"market_cap": "一百亿", "net_profit_ttm": 10.0}),
        400,
        "bad_argument",
        "market_cap",
    )


def test_operator_domain_rejection_maps_to_400_with_field(tmp_path: Path) -> None:
    # 市值 <= 0 是算子自己的 bad_argument（field=market_cap），不是路由层拦的
    _assert_error(
        _post(tmp_path, "pe_ttm", {"market_cap": -1.0, "net_profit_ttm": 10.0}),
        400,
        "bad_argument",
        "market_cap",
    )


def test_percent_misuse_maps_to_400_with_field(tmp_path: Path) -> None:
    # ttm_yoy=25 疑似百分数误传：算子 bad_argument 带 field=ttm_yoy
    _assert_error(
        _post(tmp_path, "peg", {"pe": 20.0, "ttm_yoy": 25.0}),
        400,
        "bad_argument",
        "ttm_yoy",
    )


def test_non_object_args_and_bad_json_are_400(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.post("/api/calc/pe_ttm", json={"args": [1, 2]})
    _assert_error(response, 400, "bad_argument", "args")
    broken = client.post(
        "/api/calc/pe_ttm", content="{not json", headers={"content-type": "application/json"}
    )
    _assert_error(broken, 400, "bad_argument", "body")


# --------------------------------------------------------------------------- #
# 5. 未知算子：404 + 可用算子列表
# --------------------------------------------------------------------------- #

def test_unknown_op_returns_404_with_available_ops(tmp_path: Path) -> None:
    body = _assert_error(_post(tmp_path, "no_such_op", {}), 404, "unknown_op", None)
    assert "pe_ttm" in body["available_ops"]
    assert "moving_average" in body["available_ops"]


# --------------------------------------------------------------------------- #
# 6. 内部错误：500 + internal_error
# --------------------------------------------------------------------------- #

def test_operator_exception_is_500() -> None:
    def boom(*, market_cap: float, net_profit_ttm: float) -> Any:
        raise RuntimeError("boom")

    registry = dict(calc_api._REGISTRY)
    registry["pe_ttm"] = calc_api._OpSpec(boom, "valuation", "test")
    plane = calc_api._CalcPlane(registry)
    response = plane.invoke(
        "pe_ttm", {"args": {"market_cap": 100.0, "net_profit_ttm": 10.0}}
    )
    assert response.status_code == 500
    body = json.loads(response.body)
    assert body["ok"] is False
    assert body["error"]["code"] == "internal_error"
    assert body["error"]["field"] is None
    assert body["calc_id"] == calc_id(
        "pe_ttm", {"market_cap": 100.0, "net_profit_ttm": 10.0}, data_fp="none"
    )


# --------------------------------------------------------------------------- #
# 7. 严格 JSON：响应树任何位置不出现 NaN / Infinity
# --------------------------------------------------------------------------- #

def test_responses_are_strict_json(tmp_path: Path) -> None:
    responses = [
        _client(tmp_path).get("/api/calc/ops"),
        _post(tmp_path, "pe_ttm", {"market_cap": 100.0, "net_profit_ttm": 10.0}),
        _post(tmp_path, "pe_ttm", {"market_cap": 100.0, "net_profit_ttm": -1.0}),
        _post(tmp_path, "no_such_op", {}),
    ]
    for response in responses:
        text = response.text
        assert "NaN" not in text
        assert "Infinity" not in text
        json.loads(text, parse_constant=lambda token: pytest.fail(f"非严格 JSON: {token}"))


# --------------------------------------------------------------------------- #
# 8. 闸门：非回环 403、Host 白名单 403
# --------------------------------------------------------------------------- #

def test_non_loopback_and_bad_host_are_403(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LDST_WEB_ALLOW_REMOTE", "1")
    monkeypatch.setenv("LDST_WEB_TOKEN", "0123456789abcdef0123")

    response = _client(tmp_path, client=REMOTE).get("/api/calc/ops")
    assert response.status_code == 403
    rebinding = _client(tmp_path).get("/api/calc/ops", headers={"host": "evil.example.com"})
    assert rebinding.status_code == 403
    assert _client(tmp_path).get("/api/calc/ops").status_code == 200


def test_registry_covers_all_calc_business_modules() -> None:
    """目录与算子模块同步：每个业务模块的公开算子都登记（convert_amount 除外）。"""
    from ladderstock.calc import earnings, growth, indicators, money, periods

    expected = {
        "amount",
        "pe_ttm", "pe_deducted_x4", "pe_forward", "peg", "pe_digestion_years", "forward_cagr",
        "yoy", "qoq", "gross_margin", "expense_ratio", "debt_ratio",
        "cumulative_to_single", "ttm", "ttm_yoy",
        "moving_average", "rsi", "macd", "atr", "bollinger", "chip_concentration",
        "earnings_reaction", "earnings_surprise",
    }
    assert set(calc_api._REGISTRY) == expected
    for module in (money, valuation, growth, periods, indicators, earnings):
        public = {
            name
            for name, member in vars(module).items()
            if not name.startswith("_")
            and callable(member)
            and getattr(member, "__module__", "") == module.__name__
        }
        public -= {"CalcResult", "KlineInputError", "parse_kline_rows", "convert", "convert_amount"}
        assert public <= expected, f"{module.__name__} 有算子未登记: {public - expected}"