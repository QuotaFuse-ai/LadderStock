"""QUOT-466：`GET /api/portfolio/replay` 接口契约。

钉住 issue 验收里点名的接口行为：

1. **平面 JSON、回环独占、不进信封**：顶层键固定，`no-store`，响应里除了
   情景内的 `holdings` 不出现 `positions` / `trades` / `journal` / `watchlist`；
2. **参数**：缺省 / `all` → 四段情景；单个 id → 只返回那一段；其余（含空串）422；
3. **闸门**：非回环 / 坏 `Host` 一律 403；
4. **空态**：本机没有流水时 200 + `groups=[]`，不建库、不报 500；
5. **路径不变量**：首日 = 1.0，`days == len(path.dates)`，`worst_day_date` 在路径里。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.core.portfolio_replay import SCENARIOS
from ladderstock.data import duckdb_store, sqlite_store
from ladderstock.web.radar import POSITIONS_DENIED, RadarPlane
from ladderstock.web.server import create_app

LOOPBACK = ("127.0.0.1", 54321)
REMOTE = ("10.1.2.3", 54321)
SAME_ORIGIN = "http://127.0.0.1:5950"
BROWSER_HEADERS = {"origin": SAME_ORIGIN}

CODE = "600000"

TOP_LEVEL_KEYS = {"scenario", "as_of", "groups", "notes", "sources"}
GROUP_KEYS = {"market", "currency", "label", "total_value", "input", "scenarios"}
SCENARIO_KEYS = {
    "id",
    "label",
    "start",
    "end",
    "unavailable",
    "benchmark",
    "benchmark_error",
    "portfolio",
    "coverage",
    "included",
    "holdings",
    "gaps",
    "path",
}
PORTFOLIO_KEYS = {
    "return",
    "max_drawdown",
    "max_drawdown_peak",
    "max_drawdown_trough",
    "worst_day",
    "worst_day_date",
    "days",
    "included_value",
    "value_change",
}


def _buy(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "symbol": CODE,
        "name": "浦发银行",
        "type": "buy",
        "trade_date": "2015-01-05",
        "shares": 100,
        "price": 10,
        "fee": 0,
    }
    body.update(overrides)
    return body


def _client(root: Path, *, client: tuple[str, int] = LOOPBACK) -> TestClient:
    plane = RadarPlane(data_root=root)
    app = create_app(data_root=root, dist_dir=root / "no-dist", radar_plane=plane)
    return TestClient(app, client=client)


def _seed_trade(client: TestClient, body: dict[str, Any] | None = None) -> None:
    response = client.post(
        "/api/portfolio/ledger", json=body or _buy(), headers=BROWSER_HEADERS
    )
    assert response.status_code == 200, response.text


def _seed_daily_k(root: Path, rows: list[tuple[str, str, float]]) -> None:
    duckdb_store.write_dataset(
        "daily_k",
        [
            {"code": code, "trade_date": day, "close": close}
            for code, day, close in rows
        ],
        source="test",
        root=root,
    )


def _seed_scenario_2015(root: Path) -> None:
    _seed_daily_k(
        root,
        [
            (CODE, "2015-06-12", 10.0),
            (CODE, "2015-07-01", 9.0),
            (CODE, "2015-08-26", 8.0),
        ],
    )


def _private_keys(node: Any) -> list[str]:
    """情景内的 `holdings` 是本单契约字段，不算隐私泄漏。"""
    found: list[str] = []
    forbidden = {"positions", "trades", "journal", "watchlist"}

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).lower() in forbidden:
                    found.append(str(key))
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(node)
    return found


# --------------------------------------------------------------------------- #
# 空态与结构
# --------------------------------------------------------------------------- #


def test_empty_ledger_returns_200_without_creating_the_database(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/api/portfolio/replay")

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == TOP_LEVEL_KEYS
    assert body["scenario"] == "all"
    assert body["groups"] == []
    assert body["as_of"] is None
    assert "还没有交易流水" in body["notes"][0]
    assert body["sources"] == []
    assert response.headers["cache-control"] == "no-store"
    assert not sqlite_store.db_path(tmp_path).exists(), "只读空态不能建库"


def test_flat_json_has_fixed_top_level_keys_and_no_store(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _seed_trade(client)
    _seed_scenario_2015(tmp_path)

    response = client.get("/api/portfolio/replay")
    body = response.json()

    assert response.status_code == 200, response.text
    assert set(body) == TOP_LEVEL_KEYS
    assert body["scenario"] == "all"
    assert body["as_of"] == "2015-08-26"
    assert body["sources"] == ["daily_k"]
    assert response.headers["cache-control"] == "no-store"
    assert _private_keys(body) == []

    group = body["groups"][0]
    assert set(group) == GROUP_KEYS
    assert group["market"] == "a_share"
    assert group["currency"] == "CNY"
    assert group["label"] == "A 股 · 人民币"
    assert group["input"] == 1
    assert group["total_value"] == pytest.approx(800.0)

    scenario = next(row for row in group["scenarios"] if row["id"] == "2015-crash")
    assert set(scenario) == SCENARIO_KEYS
    assert set(scenario["portfolio"]) == PORTFOLIO_KEYS
    assert scenario["unavailable"] is None
    assert scenario["benchmark"] is None
    assert scenario["benchmark_error"] == "本机没有历史指数序列"
    assert scenario["portfolio"]["return"] == pytest.approx(-0.2)
    assert scenario["coverage"] == pytest.approx(1.0)
    assert scenario["included"] == 1
    assert scenario["holdings"][0]["symbol"] == CODE
    assert scenario["gaps"] == []


def test_path_invariants(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _seed_trade(client)
    _seed_scenario_2015(tmp_path)

    group = client.get("/api/portfolio/replay?scenario=2015-crash").json()["groups"][0]
    scenario = group["scenarios"][0]

    assert scenario["path"]["portfolio"][0] == pytest.approx(1.0)
    assert scenario["portfolio"]["days"] == len(scenario["path"]["dates"])
    assert scenario["portfolio"]["worst_day_date"] in scenario["path"]["dates"]
    assert scenario["holdings"][0]["first_date"] == scenario["path"]["dates"][0]
    assert scenario["holdings"][0]["last_date"] == scenario["path"]["dates"][-1]


# --------------------------------------------------------------------------- #
# 参数
# --------------------------------------------------------------------------- #


def test_default_and_all_return_four_scenarios_in_order(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _seed_trade(client)
    _seed_scenario_2015(tmp_path)

    for query in ("", "?scenario=all"):
        body = client.get(f"/api/portfolio/replay{query}").json()
        ids = [row["id"] for row in body["groups"][0]["scenarios"]]
        assert ids == [scenario.id for scenario in SCENARIOS]


def test_single_scenario_returns_only_that_one(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _seed_trade(client)
    _seed_scenario_2015(tmp_path)

    body = client.get("/api/portfolio/replay?scenario=2015-crash").json()

    assert body["scenario"] == "2015-crash"
    assert [row["id"] for row in body["groups"][0]["scenarios"]] == ["2015-crash"]


@pytest.mark.parametrize("scenario", ["", "nope", "2015", "2015-crash "])
def test_invalid_scenario_is_rejected(tmp_path: Path, scenario: str) -> None:
    response = _client(tmp_path).get(
        "/api/portfolio/replay", params={"scenario": scenario}
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "2015-crash" in detail


# --------------------------------------------------------------------------- #
# 闸门
# --------------------------------------------------------------------------- #


def test_non_loopback_is_forbidden(tmp_path: Path) -> None:
    response = _client(tmp_path, client=REMOTE).get("/api/portfolio/replay")

    assert response.status_code == 403
    assert response.json()["detail"] == POSITIONS_DENIED


def test_bad_host_header_is_forbidden(tmp_path: Path) -> None:
    response = _client(tmp_path).get(
        "/api/portfolio/replay", headers={"host": "evil.example.com"}
    )

    assert response.status_code == 403
