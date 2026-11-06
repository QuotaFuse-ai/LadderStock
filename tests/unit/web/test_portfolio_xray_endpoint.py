"""QUOT-437：`GET /api/portfolio/xray` 接口契约。

钉住 issue 验收里点名的几件事：

1. **平面 JSON、回环独占、不进信封**：响应带 `no-store`，递归里不出现
   `holdings` / `positions` / `trades`；
2. **空态**：本机没有流水时 200 + 空 items + 说明，不建库、不报 500；
3. **缺价不进分母**：没有现价的持仓 `weight=None` 并列进 `gaps`；
4. **行业只认标注**：`positions.industry` 为空归「未标注」，不按代码猜；
5. **闸门**：非回环 / 坏 `Host` 一律 403。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from ladderstock.data import duckdb_store, sqlite_store
from ladderstock.web.radar import POSITIONS_DENIED, RadarPlane
from ladderstock.web.server import create_app

LOOPBACK = ("127.0.0.1", 54321)
REMOTE = ("10.1.2.3", 54321)
BROWSER_HEADERS = {"origin": "http://127.0.0.1:5950"}

TOP_LEVEL_KEYS = {
    "ok",
    "local_only",
    "store",
    "price_source",
    "price_lookup",
    "as_of",
    "total_value",
    "items",
    "top1",
    "top3",
    "top5",
    "industry_buckets",
    "industry_labeled_count",
    "industry_unlabeled_count",
    "metrics",
    "gaps",
    "notes",
}


def _buy(symbol: str, shares: float = 100, price: float = 10) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "name": f"股票{symbol}",
        "type": "buy",
        "trade_date": "2025-01-02",
        "shares": shares,
        "price": price,
        "fee": 0,
    }


def _client(root: Path, *, client: tuple[str, int] = LOOPBACK) -> TestClient:
    plane = RadarPlane(data_root=root)
    app = create_app(data_root=root, dist_dir=root / "no-dist", radar_plane=plane)
    return TestClient(app, client=client)


def _seed_trade(client: TestClient, body: dict[str, Any]) -> None:
    response = client.post(
        "/api/portfolio/ledger", json=body, headers=BROWSER_HEADERS
    )
    assert response.status_code == 200, response.text


def _seed_industry(client: TestClient, code: str, industry: str) -> None:
    response = client.post(
        "/api/positions",
        json={"code": code, "cost": 1, "qty": 1, "industry": industry},
        headers=BROWSER_HEADERS,
    )
    assert response.status_code == 200, response.text


def _seed_close(root: Path, rows: list[tuple[str, str, float]]) -> None:
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


def test_empty_ledger_returns_an_empty_view_without_creating_the_database(
    tmp_path: Path,
) -> None:
    response = _client(tmp_path).get("/api/portfolio/xray")

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == TOP_LEVEL_KEYS
    assert body["items"] == []
    assert body["top1"] is None
    assert body["total_value"] is None
    assert body["industry_buckets"] == []
    assert body["price_lookup"]["date"] is None
    assert body["metrics"]["corr"]["available"] is False
    assert body["notes"]
    assert response.headers["cache-control"] == "no-store"
    assert not sqlite_store.db_path(tmp_path).exists(), "只读空态不能建库"
    assert _private_keys(body) == []


def test_weights_and_industries_come_from_local_data(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _seed_trade(client, _buy("600000", shares=100, price=10))
    _seed_trade(client, _buy("000001", shares=200, price=5))
    _seed_industry(client, "600000", "银行")
    _seed_close(
        tmp_path,
        [
            ("600000", "2025-01-02", 10.0),
            ("600000", "2025-01-03", 12.0),
            ("000001", "2025-01-02", 5.0),
            ("000001", "2025-01-03", 4.0),
        ],
    )

    body = client.get("/api/portfolio/xray").json()

    assert set(body) == TOP_LEVEL_KEYS
    assert body["ok"] is True
    assert body["price_source"] == "daily_k"
    assert body["as_of"] == "2025-01-03"
    assert body["total_value"] == 2000  # 100×12 + 200×4
    by_symbol = {item["symbol"]: item for item in body["items"]}
    assert by_symbol["600000"]["market_value"] == 1200
    assert by_symbol["600000"]["weight"] == 0.6
    assert by_symbol["000001"]["weight"] == 0.4
    assert body["top1"] == 0.6
    assert body["top3"] == 1.0
    buckets = {row["industry"]: row for row in body["industry_buckets"]}
    assert buckets["银行"]["weight"] == 0.6
    assert buckets["未标注"]["weight"] == 0.4
    assert body["industry_labeled_count"] == 1
    assert body["industry_unlabeled_count"] == 1
    assert _private_keys(body) == []


def test_missing_price_is_not_counted_as_zero_weight(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _seed_trade(client, _buy("600000", shares=100, price=10))
    _seed_trade(client, _buy("000001", shares=100, price=10))
    _seed_close(tmp_path, [("600000", "2025-01-02", 10.0)])

    body = client.get("/api/portfolio/xray").json()

    by_symbol = {item["symbol"]: item for item in body["items"]}
    assert by_symbol["600000"]["weight"] == 1.0
    assert by_symbol["000001"]["market_value"] is None
    assert by_symbol["000001"]["weight"] is None
    assert body["total_value"] == 1000
    gap = next(row for row in body["gaps"] if row["symbol"] == "000001")
    assert gap["affects"] == "weight"
    assert "没算进权重分母" in gap["reason"]


def test_industries_are_never_guessed_from_the_code(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _seed_trade(client, _buy("600000"))
    _seed_close(tmp_path, [("600000", "2025-01-02", 10.0)])

    body = client.get("/api/portfolio/xray").json()

    assert [row["industry"] for row in body["industry_buckets"]] == ["未标注"]
    assert body["industry_labeled_count"] == 0
    assert body["industry_unlabeled_count"] == 1


def test_non_loopback_is_forbidden(tmp_path: Path) -> None:
    response = _client(tmp_path, client=REMOTE).get("/api/portfolio/xray")

    assert response.status_code == 403
    assert response.json()["detail"] == POSITIONS_DENIED


def test_bad_host_header_is_forbidden(tmp_path: Path) -> None:
    response = _client(tmp_path).get(
        "/api/portfolio/xray", headers={"host": "evil.example.com"}
    )

    assert response.status_code == 403
