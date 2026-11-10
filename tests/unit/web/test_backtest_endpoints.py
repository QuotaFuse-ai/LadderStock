"""QUOT-535：回测触发面契约测试（strategies / run / jobs + 真实佣金默认值）。

钉住三件事：

1. **策略清单来自后端常量**：``GET /api/backtest/strategies`` 的 name/label/params
   与 ``backtest.strategy`` 一致，页面无需硬编码；
2. **异步生命周期**：``POST /run`` 回 202 + job_id，``GET /jobs/{id}`` 从
   queued/running 收敛到 ok（run_id / gate_passed / exit_code 齐全），运行卡可再用
   既有只读端点读回；
3. **写面闸门与参数校验**：非回环、坏 Origin、缺来源证据 → 403；未知策略 / 非法标的 /
   坏日期 / 坏策略参数 / 坏成本 → 4xx 且结构化；请求体形状不对 → 422。
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.backtest.config import load_config
from ladderstock.backtest.feed import Bar
from ladderstock.backtest.strategy import BUILTIN_NAMES
from ladderstock.web.server import create_app
from tests.unit.backtest.conftest import flat_bar, write_daily_k

LOOPBACK = ("127.0.0.1", 54321)
REMOTE = ("10.1.2.3", 54321)
WRITE_HEADERS = {"X-LDST-Client": "1"}
SAME_ORIGIN = {"Origin": "http://127.0.0.1:5950"}

START = "2024-02-01"
END = "2024-08-31"


def _bars(count: int = 180) -> list[Bar]:
    bars: list[Bar] = []
    price = 10.0
    begin = date.fromisoformat(START)
    cycle = [0.03, 0.02, 0.015, -0.01, -0.02, -0.015]
    for index in range(count):
        price = round(price * (1.0 + cycle[index % len(cycle)]), 2)
        bars.append(flat_bar((begin + timedelta(days=index)).isoformat(), price))
    return bars


@pytest.fixture()
def app_client(tmp_path: Path) -> TestClient:
    write_daily_k(tmp_path, "600000", _bars())
    app = create_app(data_root=tmp_path)
    return TestClient(app, client=LOOPBACK, headers=WRITE_HEADERS)


def _run_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "strategy": "momentum",
        "strategy_params": {"lookback": 5},
        "symbol": "600000",
        "start": START,
        "end": END,
        "min_trades": 1,
    }
    body.update(overrides)
    return body


def _wait_job(client: TestClient, job_id: str, *, timeout: float = 60.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/backtest/jobs/{job_id}")
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["status"] not in {"queued", "running"}:
            return payload
        time.sleep(0.05)
    raise AssertionError(f"任务 {job_id} 在 {timeout}s 内未收敛")


# --------------------------------------------------------------------------- #
# 1. strategies：清单 + 默认值来自后端
# --------------------------------------------------------------------------- #

def test_strategies_shape_comes_from_backend(app_client: TestClient) -> None:
    response = app_client.get("/api/backtest/strategies")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {"strategies", "config_defaults", "symbol_default"}
    assert payload["symbol_default"] == "600000"

    strategies = payload["strategies"]
    assert [item["name"] for item in strategies] == list(BUILTIN_NAMES)
    for item in strategies:
        assert set(item) == {"name", "label", "params"}
        assert item["label"], "每个策略都要有中文名"
        assert item["params"], "每个策略至少要暴露一个参数"
        for param in item["params"]:
            assert set(param) == {"key", "label", "type", "default", "min", "max"}
            assert param["type"] in {"int", "float"}
            assert isinstance(param["default"], int | float)
            assert param["min"] <= param["default"] <= param["max"]

    defaults = payload["config_defaults"]
    expected = load_config(_repo_config_path())
    assert defaults["commission"] == pytest.approx(expected.commission)
    assert defaults["commission_rate_wan"] == pytest.approx(0.9)
    assert defaults["commission_floor_yuan"] == pytest.approx(0.01)
    assert defaults["transfer_fee_wan"] == pytest.approx(0.1)
    assert defaults["stamp_duty_wan"] == pytest.approx(5.0)
    assert defaults["cost_tiers"] == list(expected.cost_tiers)
    assert len(defaults["presets"]) == 3
    assert {preset["key"] for preset in defaults["presets"]} == {
        "wan09_free5_001",
        "wan085_free5_1",
        "wan065_min5",
    }


def _repo_config_path() -> Path:
    return Path(__file__).resolve().parents[3] / "config" / "backtest.yaml"


# --------------------------------------------------------------------------- #
# 2. run → jobs 生命周期
# --------------------------------------------------------------------------- #

def test_run_jobs_lifecycle(app_client: TestClient) -> None:
    response = app_client.post("/api/backtest/run", json=_run_body())
    assert response.status_code == 202, response.text
    accepted = response.json()
    assert accepted["status"] == "queued"
    job_id = accepted["job_id"]
    assert isinstance(job_id, str) and job_id

    job = _wait_job(app_client, job_id)
    assert job["status"] == "ok", job
    assert isinstance(job["gate_passed"], bool)
    # 闸门结论决定退出码：通过 = 0，未通过 = 8（与 CLI 同口径，不是任务失败）
    assert job["exit_code"] == (0 if job["gate_passed"] else 8)
    assert job["run_id"] and job["run_id"].startswith("bt_")
    assert job["error"] is None

    # 运行卡落盘与 CLI 同源：既有只读端点能读回，且 run_id 一致
    card = app_client.get(f"/api/backtest/runs/{job['run_id']}")
    assert card.status_code == 200, card.text
    assert card.json()["run_id"] == job["run_id"]
    assert card.json()["gate"]["passed"] is job["gate_passed"]

    listing = app_client.get("/api/backtest/runs").json()
    assert job["run_id"] in {item["run_id"] for item in listing["runs"]}


def test_job_view_exposes_contract_fields(app_client: TestClient) -> None:
    job_id = app_client.post("/api/backtest/run", json=_run_body()).json()["job_id"]
    job = _wait_job(app_client, job_id)
    for key in ("status", "run_id", "gate_passed", "exit_code"):
        assert key in job
    assert set(job) >= {"job_id", "status", "run_id", "gate_passed", "exit_code"}


def test_unknown_job_is_404(app_client: TestClient) -> None:
    response = app_client.get("/api/backtest/jobs/does-not-exist")
    assert response.status_code == 404


def test_same_request_same_run_id(app_client: TestClient) -> None:
    first_id = app_client.post("/api/backtest/run", json=_run_body()).json()["job_id"]
    second_id = app_client.post("/api/backtest/run", json=_run_body()).json()["job_id"]
    first = _wait_job(app_client, first_id)
    second = _wait_job(app_client, second_id)
    assert first["run_id"] == second["run_id"]


def test_cost_override_reaches_card(app_client: TestClient) -> None:
    body = _run_body(
        commission_rate_wan=0.85,
        commission_floor_yuan=1.0,
        transfer_fee_wan=0.1,
        stamp_duty_wan=5.0,
    )
    job = _wait_job(app_client, app_client.post("/api/backtest/run", json=body).json()["job_id"])
    card = app_client.get(f"/api/backtest/runs/{job['run_id']}").json()
    assert card["config"]["commission_rate_wan"] == pytest.approx(0.85)
    assert card["config"]["commission_floor_yuan"] == pytest.approx(1.0)
    assert card["config"]["transfer_fee_wan"] == pytest.approx(0.1)
    assert card["config"]["stamp_duty_wan"] == pytest.approx(5.0)
    assert card["config_sources"]["commission_rate_wan"] == "cli"


# --------------------------------------------------------------------------- #
# 3. 参数校验：4xx + 结构化错误
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"strategy": "not_a_strategy"}, "unknown_strategy"),
        (
            {
                "strategy": "ma_cross",
                "strategy_params": {"short_window": 30, "long_window": 5},
            },
            "invalid_strategy_params",
        ),
        ({"strategy": "momentum", "strategy_params": {"bogus": 1}}, "invalid_strategy_params"),
        ({"symbol": "688001"}, "invalid_symbol"),
        ({"start": "2024-13-40"}, "invalid_date"),
        ({"start": "2024-09-01", "end": "2024-08-01"}, "invalid_period"),
        ({"commission_rate_wan": 0.0}, "invalid_cost"),
        ({"slippage": 0.0}, "invalid_cost"),
        ({"cost_tiers": [0.001, 0.0005]}, "invalid_cost"),
    ],
)
def test_invalid_parameters_are_structured_4xx(
    app_client: TestClient, overrides: dict[str, Any], code: str
) -> None:
    response = app_client.post("/api/backtest/run", json=_run_body(**overrides))
    assert 400 <= response.status_code < 500, response.text
    detail = response.json()["detail"]
    if response.status_code == 422:
        return  # 形状错误由 FastAPI 的结构化 422 负责
    assert detail["error"] == code, detail
    assert detail["message"]


def test_missing_body_fields_is_422(app_client: TestClient) -> None:
    response = app_client.post("/api/backtest/run", json={"strategy": "momentum"})
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


# --------------------------------------------------------------------------- #
# 4. 闸门：非回环 / 坏 Origin / 缺来源证据
# --------------------------------------------------------------------------- #

def test_non_loopback_client_is_rejected(tmp_path: Path) -> None:
    write_daily_k(tmp_path, "600000", _bars())
    client = TestClient(create_app(data_root=tmp_path), client=REMOTE)
    assert client.get("/api/backtest/strategies").status_code == 403


def test_write_without_source_evidence_is_rejected(tmp_path: Path) -> None:
    write_daily_k(tmp_path, "600000", _bars())
    client = TestClient(create_app(data_root=tmp_path), client=LOOPBACK)
    assert client.post("/api/backtest/run", json=_run_body()).status_code == 403


def test_cross_site_origin_is_rejected(app_client: TestClient) -> None:
    response = app_client.post(
        "/api/backtest/run", json=_run_body(), headers={"Origin": "http://evil.example"}
    )
    assert response.status_code == 403


def test_same_origin_write_is_accepted(app_client: TestClient) -> None:
    response = app_client.post("/api/backtest/run", json=_run_body(), headers=SAME_ORIGIN)
    assert response.status_code == 202, response.text
    _wait_job(app_client, response.json()["job_id"])


def test_cross_site_fetch_metadata_is_rejected(app_client: TestClient) -> None:
    response = app_client.post(
        "/api/backtest/run", json=_run_body(), headers={"Sec-Fetch-Site": "cross-site"}
    )
    assert response.status_code == 403
