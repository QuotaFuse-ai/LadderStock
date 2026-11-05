"""QUOT-433 / DEC-064：`GET /api/portfolio/perf` 接口契约。

钉住 issue 验收里点名的几件事：

1. **平面 JSON、回环独占、不进信封**：顶层键固定，递归里不出现
   `holdings` / `positions` / `trades`，响应带 `no-store`；
2. **空态**：本机没有流水时 200 + `groups=[]` + 说明，不建库、不报 500；
3. **闸门**：非回环 / 坏 `Host` 一律 403；
4. **只读本机日线**：有流水有日线就算出 TWR；没有日线就 `ok=false` + `gaps`，不补价；
5. **参数**：`days` 只裁剪走势，非法值 422。
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.data import duckdb_store, sqlite_store
from ladderstock.web.radar import POSITIONS_DENIED, RadarPlane
from ladderstock.web.server import create_app

LOOPBACK = ("127.0.0.1", 54321)
REMOTE = ("10.1.2.3", 54321)
SAME_ORIGIN = "http://127.0.0.1:5950"
BROWSER_HEADERS = {"origin": SAME_ORIGIN}

CODE = "600000"

TOP_LEVEL_KEYS = {"as_of", "groups", "skipped", "gaps", "sources", "notes"}
GROUP_KEYS = {
    "market",
    "currency",
    "label",
    "ok",
    "reason",
    "start",
    "end",
    "span_days",
    "symbols",
    "excluded",
    "twr",
    "twr_annualized",
    "xirr",
    "mwr_period",
    "invested",
    "proceeds",
    "dividends",
    "fees",
    "end_value",
    "gain",
    "notes",
    "warnings",
    "series",
}


def _buy(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "symbol": CODE,
        "name": "浦发银行",
        "type": "buy",
        "trade_date": "2025-01-02",
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


def _private_keys(node: Any) -> list[str]:
    found: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).lower() in {"holdings", "positions", "trades", "journal", "watchlist"}:
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


def test_empty_ledger_returns_empty_groups_without_creating_the_database(
    tmp_path: Path,
) -> None:
    response = _client(tmp_path).get("/api/portfolio/perf")

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == TOP_LEVEL_KEYS
    assert body["groups"] == []
    assert body["skipped"] == []
    assert body["gaps"] == []
    assert body["as_of"] is None
    assert any("还没有交易流水" in note for note in body["notes"])
    assert response.headers["cache-control"] == "no-store"
    assert not sqlite_store.db_path(tmp_path).exists(), "只读空态不能建库"
    assert _private_keys(body) == []


def test_flat_json_keeps_the_group_shape(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _seed_trade(client)
    _seed_daily_k(
        tmp_path,
        [
            (CODE, "2025-01-02", 10.0),
            (CODE, "2025-01-03", 11.0),
            (CODE, "2025-01-04", 12.0),
        ],
    )

    body = client.get("/api/portfolio/perf").json()

    assert set(body) == TOP_LEVEL_KEYS
    assert body["sources"] == ["daily_k"]
    assert body["as_of"] == "2025-01-04"
    assert len(body["groups"]) == 1
    group = body["groups"][0]
    assert set(group) == GROUP_KEYS
    assert group["ok"] is True
    assert group["market"] == "A股"
    assert group["currency"] == "CNY"
    assert group["twr"] == pytest.approx(0.2)
    assert group["invested"] == pytest.approx(1000)
    assert group["end_value"] == pytest.approx(1200)
    assert group["gain"] == pytest.approx(200)
    assert group["series"]["dates"] == ["2025-01-02", "2025-01-03", "2025-01-04"]
    assert group["series"]["twr_index"] == pytest.approx([1.0, 1.1, 1.2])
    assert _private_keys(body) == []


# --------------------------------------------------------------------------- #
# 缺行情：不补价，组仍返回 200
# --------------------------------------------------------------------------- #


def test_missing_daily_cache_marks_the_group_uncomputable(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _seed_trade(client)

    response = client.get("/api/portfolio/perf")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["groups"][0]["ok"] is False
    assert body["groups"][0]["reason"]
    assert body["groups"][0]["end_value"] is None
    assert [row["symbol"] for row in body["gaps"]] == [CODE]
    assert body["sources"] == []


# --------------------------------------------------------------------------- #
# 闸门
# --------------------------------------------------------------------------- #


def test_non_loopback_is_forbidden(tmp_path: Path) -> None:
    response = _client(tmp_path, client=REMOTE).get("/api/portfolio/perf")

    assert response.status_code == 403
    assert response.json()["detail"] == POSITIONS_DENIED


def test_bad_host_header_is_forbidden(tmp_path: Path) -> None:
    response = _client(tmp_path).get(
        "/api/portfolio/perf", headers={"host": "evil.example.com"}
    )

    assert response.status_code == 403


# --------------------------------------------------------------------------- #
# 参数
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("days", ["-1", "99999", "abc"])
def test_invalid_days_is_rejected(tmp_path: Path, days: str) -> None:
    response = _client(tmp_path).get(f"/api/portfolio/perf?days={days}")

    assert response.status_code == 422


def test_days_zero_means_all_history(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _seed_trade(client)
    _seed_daily_k(tmp_path, [(CODE, "2025-01-02", 10.0), (CODE, "2025-01-03", 11.0)])

    body = client.get("/api/portfolio/perf?days=0").json()

    assert body["groups"][0]["ok"] is True
    assert len(body["groups"][0]["series"]["dates"]) == 2


def test_days_trims_the_series_only(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _seed_trade(client)
    start = date(2025, 1, 2)
    _seed_daily_k(
        tmp_path,
        [
            (CODE, (start + timedelta(days=index)).isoformat(), 10.0 + index * 0.1)
            for index in range(60)
        ],
    )

    group = client.get("/api/portfolio/perf?days=30").json()["groups"][0]

    assert len(group["series"]["dates"]) == 30
    assert group["start"] == "2025-01-02", "指标区间不跟着 days 缩"
    assert group["span_days"] == 59
    assert any("走势只画最近 30 天" in note for note in group["notes"])
