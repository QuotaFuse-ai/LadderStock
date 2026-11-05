"""QUOT-400 / QUOT-445：`GET /api/research/{code}/overview` 的契约测试。

概览**不依赖六段产物**：没跑过研究也必须能出数。K 线 / 财务指标只读本机缓存
（`daily_k` / `finance_indicators`，走 `archive` 回放），**quote 走已登记的实时链路**
（`spot_quote` → `tencent_quote_batch`，`fetch_live`）——archive 里没有 `spot_quote`
表，读不到就显式 `null` + reason，不补 0、不编造。

信封照 data 面同款四段：`payload / data_source_status / data_as_of / meta`；
`data_source_status` 只看主指标（K 线）：有根 → `ok`，一根没有 → `missing`。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.core.models import LadderEnvelope
from ladderstock.data import duckdb_store
from ladderstock.data.provider_registry import SourceUnavailable
from ladderstock.web.research import OVERVIEW_BARS, ResearchPlane
from ladderstock.web.server import create_app

CODE = "600000"
BASE_URL = "http://127.0.0.1:5950"
LOOPBACK = ("127.0.0.1", 54321)


class _NullControlPlane:
    """占位：这些用例只关心概览接口，不构造真实控制面。"""

    peers: dict[str, Any] = {}


class _FakeRegistry:
    """只记录调用参数的假 registry：`fetch_live` 形状与真件一致，不联网。"""

    def __init__(
        self,
        rows: list[dict[str, Any]] | None = None,
        exc: Exception | None = None,
    ) -> None:
        self.rows = rows or []
        self.exc = exc
        self.calls: list[dict[str, Any]] = []

    def fetch_live(
        self,
        dataset: str,
        *,
        trade_date: Any = None,
        params: dict[str, Any] | None = None,
        allow_cache: bool = True,
    ) -> Any:
        self.calls.append(
            {
                "dataset": dataset,
                "params": dict(params or {}),
                "allow_cache": allow_cache,
            }
        )
        if self.exc is not None:
            raise self.exc
        return SimpleNamespace(
            envelope=LadderEnvelope(
                domain="review",
                topic="spot_quote",
                event_id=None,
                data_as_of=date(2026, 11, 5),
                payload={
                    "dataset": "spot_quote",
                    "tool": "tencent_quote_batch",
                    "records": self.rows,
                },
                source="tickhub_cn",
                source_detail="主源 tickhub_spot_quote/tencent_quote_batch",
                fetched_at=datetime(2026, 11, 5, 8, 20, tzinfo=UTC),
                freshness="realtime",
                confidence="high",
                source_status="ok",
            )
        )


def _quote_row(**overrides: Any) -> dict[str, Any]:
    row = {
        "code": CODE,
        "name": "浦发银行",
        "price": 9.87,
        "prev_close": 10.09,
        "open": 10.09,
        "high": 10.1,
        "low": 9.82,
        "volume": 629078,
        "amount": 62343.0,
        "change": -0.22,
        "change_pct": -2.18,
        "timestamp": "20261105161450",
    }
    row.update(overrides)
    return row


def _client(root: Path, registry: _FakeRegistry) -> TestClient:
    plane = ResearchPlane(data_root=root, registry=registry)
    app = create_app(
        data_root=root,
        dist_dir=root / "no-dist",
        control_plane=_NullControlPlane(),
        research_plane=plane,
    )
    return TestClient(app, base_url=BASE_URL, client=LOOPBACK)


def _unavailable_client(root: Path) -> tuple[TestClient, _FakeRegistry]:
    registry = _FakeRegistry(exc=SourceUnavailable("tickhub 不可达"))
    return _client(root, registry), registry


def _kline(code: str, day: str, close: float) -> dict[str, Any]:
    return {
        "code": code,
        "trade_date": day,
        "open": close - 0.2,
        "high": close + 0.3,
        "low": close - 0.4,
        "close": close,
        "volume": 75000.0 + close,
        "amount": None,
    }


def _seed_days(days: int, *, code: str = CODE) -> list[dict[str, Any]]:
    """造 `days` 个连续交易日的日线行（close 逐日递增，方便断言顺序与截断）。"""
    from datetime import date, timedelta

    start = date(2026, 6, 1)
    return [
        _kline(code, (start + timedelta(days=index)).isoformat(), 10.0 + index)
        for index in range(days)
    ]


def _seed_cache(root: Path, *, kline: list[dict[str, Any]] | None = None) -> None:
    rows = kline if kline is not None else _seed_days(130)
    duckdb_store.write_dataset("daily_k", rows, root=root)


# --------------------------------------------------------------------------- #
# 1. 有数：K 线 ≥120 根 + 实时报价 + 财务指标
# --------------------------------------------------------------------------- #

def test_overview_returns_kline_live_quote_and_financials(tmp_path: Path) -> None:
    duckdb_store.write_dataset("daily_k", _seed_days(130), root=tmp_path)
    duckdb_store.write_dataset(
        "finance_indicators",
        [
            {
                "code": CODE,
                "trade_date": "2026-09-30",
                "report_name": "2026三季报",
                "eps": 4.57,
                "revenue": 264852000000.0,
                "net_profit": 116476000000.0,
                "roe": 10.1175,
                "revenue_yoy": 5.34,
                "profit_yoy": 2.38,
            }
        ],
        root=tmp_path,
    )
    registry = _FakeRegistry(rows=[_quote_row()])

    response = _client(tmp_path, registry).get(f"/api/research/{CODE}/overview")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"payload", "data_source_status", "data_as_of", "meta"}
    assert body["data_source_status"] == "ok"
    payload = body["payload"]
    assert body["data_as_of"] == payload["kline"]["bars"][-1]["date"]
    assert body["meta"]["topic"] == "research_overview"
    assert payload["code"] == CODE
    assert payload["name"] == "浦发银行"
    assert payload["data_source_status"] == "ok"
    assert payload["quote_reason"] is None and payload["financials_reason"] is None

    kline = payload["kline"]
    assert kline["count"] == OVERVIEW_BARS == 120, "缓存 130 根时截到最近 120 根"
    assert kline["reason"] is None
    assert len(kline["bars"]) == 120
    first, last = kline["bars"][0], kline["bars"][-1]
    assert set(first) == {"date", "open", "high", "low", "close", "volume"}
    assert first["date"] < last["date"], "日线升序"
    assert last["close"] == pytest.approx(10.0 + 129)

    quote = payload["quote"]
    assert quote["price"] == 9.87 and quote["change_pct"] == -2.18
    assert quote["prev_close"] == 10.09 and quote["open"] == 10.09
    assert quote["high"] == 10.1 and quote["low"] == 9.82
    assert quote["change"] == -0.22
    assert quote["volume"] == 629078, "成交量口径是「手」，上游原值"
    assert quote["amount"] == 623430000, "成交额上游是万元，对外统一「元」"
    assert quote["timestamp"] == "20261105161450"
    assert set(quote["source_detail"]) == {"peer", "tool", "fetched_at"}
    assert quote["source_detail"]["peer"] == "tickhub"
    assert quote["source_detail"]["tool"] == "tencent_quote_batch"

    financials = payload["financials"]
    assert financials["report_name"] == "2026三季报"
    assert financials["report_date"] == "2026-09-30"
    assert financials["roe"] == 10.1175 and financials["revenue_yoy"] == 5.34


def test_overview_quote_hits_live_chain_without_spot_quote_table(tmp_path: Path) -> None:
    """本单核心：库里没有 `spot_quote` 表，quote 仍能从实时链路取到。"""
    _seed_cache(tmp_path)
    registry = _FakeRegistry(rows=[_quote_row()])
    conn = duckdb_store.connect(duckdb_store.db_path(tmp_path))
    try:
        assert not duckdb_store.table_exists(conn, "spot_quote"), "库里没有 spot_quote 表"
    finally:
        duckdb_store.close(conn)

    response = _client(tmp_path, registry).get(f"/api/research/{CODE}/overview")

    assert response.status_code == 200
    assert response.json()["payload"]["quote"]["price"] == 9.87
    assert registry.calls == [
        {
            "dataset": "spot_quote",
            "params": {"codes": CODE, "max_codes": 1},
            "allow_cache": True,
        }
    ]


def test_overview_date_param_cuts_kline(tmp_path: Path) -> None:
    _seed_cache(tmp_path)
    client, _ = _unavailable_client(tmp_path)

    response = client.get(f"/api/research/{CODE}/overview", params={"date": "2026-06-10"})

    payload = response.json()["payload"]
    assert payload["as_of"] == "2026-06-10"
    bars = payload["kline"]["bars"]
    assert bars[-1]["date"] == "2026-06-10", "date 是截断点，不是研究运行日"
    assert all(bar["date"] <= "2026-06-10" for bar in bars)


def test_overview_works_without_any_research_artifacts(tmp_path: Path) -> None:
    """本单核心：没跑过研究的代码也能出概览（200 + 真实 K 线），不 404。"""
    assert not (tmp_path / "research").exists()
    _seed_cache(tmp_path)
    client, _ = _unavailable_client(tmp_path)

    response = client.get(f"/api/research/{CODE}/overview")

    assert response.status_code == 200
    assert response.json()["payload"]["kline"]["count"] == 120


# --------------------------------------------------------------------------- #
# 2. 缺数：null + reason，不补 0、不编造
# --------------------------------------------------------------------------- #

def test_overview_missing_everything_is_200_missing_state(tmp_path: Path) -> None:
    """缓存全空 + 实时链路不可用 → 200 + missing 态：quote/financials null + reason，不补 0。"""
    client, _ = _unavailable_client(tmp_path)

    response = client.get(f"/api/research/{CODE}/overview")

    assert response.status_code == 200
    body = response.json()
    assert body["data_source_status"] == "missing"
    assert body["data_as_of"] is None, "没有可信数据时 data_as_of 不拿请求日期顶替"

    payload = body["payload"]
    assert payload["data_source_status"] == "missing"
    assert payload["kline"]["bars"] == [] and payload["kline"]["count"] == 0
    assert "daily_k" in (payload["kline"]["reason"] or "")
    assert payload["quote"] is None, "取不到报价是 null，不是 0"
    assert "spot_quote" in (payload["quote_reason"] or "")
    assert payload["financials"] is None
    assert "finance_indicators" in (payload["financials_reason"] or "")


def test_overview_missing_quote_and_financials_keeps_kline(tmp_path: Path) -> None:
    """只有 K 线：主指标 ok，quote/financials 各自 null + reason，整页不降级成空态。"""
    _seed_cache(tmp_path, kline=_seed_days(30))
    client, _ = _unavailable_client(tmp_path)

    response = client.get(f"/api/research/{CODE}/overview")

    payload = response.json()["payload"]
    assert response.json()["data_source_status"] == "ok"
    assert payload["kline"]["count"] == 30
    assert "不足" in (payload["kline"]["reason"] or ""), "缓存不足 120 根要如实说明"
    assert payload["quote"] is None and payload["financials"] is None
    assert payload["name"] is None


def test_overview_live_quote_failure_degrades_to_null_with_reason(tmp_path: Path) -> None:
    """实时链路报错：quote=null + 可读 reason，K 线照常；绝不拿旧值或 0 顶替。"""
    _seed_cache(tmp_path)
    client, _ = _unavailable_client(tmp_path)

    response = client.get(f"/api/research/{CODE}/overview")

    assert response.status_code == 200
    payload = response.json()["payload"]
    assert payload["kline"]["count"] == 120
    assert payload["quote"] is None
    assert "实时 spot_quote 取数失败" in (payload["quote_reason"] or "")
    assert "tickhub 不可达" in (payload["quote_reason"] or "")


def test_overview_live_quote_without_the_code_is_null(tmp_path: Path) -> None:
    """上游没返回这只票：quote=null + 点名代码，不拿别的行凑数。"""
    _seed_cache(tmp_path)
    registry = _FakeRegistry(rows=[_quote_row(code="000001")])
    client = _client(tmp_path, registry)

    response = client.get(f"/api/research/{CODE}/overview")

    payload = response.json()["payload"]
    assert payload["quote"] is None
    assert CODE in (payload["quote_reason"] or "")
    assert "tencent_quote_batch" in (payload["quote_reason"] or "")


def test_overview_zero_price_is_null_not_zero(tmp_path: Path) -> None:
    """停牌 / 上游给 0 价：quote=null，不把 0 当现价上屏。"""
    _seed_cache(tmp_path)
    registry = _FakeRegistry(rows=[_quote_row(price=0)])
    client = _client(tmp_path, registry)

    response = client.get(f"/api/research/{CODE}/overview")

    payload = response.json()["payload"]
    assert payload["quote"] is None
    assert "现价为空" in (payload["quote_reason"] or "")


def test_overview_unknown_code_is_404(tmp_path: Path) -> None:
    client, _ = _unavailable_client(tmp_path)

    response = client.get("/api/research/999999/overview")

    assert response.status_code == 404
    assert "未知证券代码" in response.json()["detail"]


def test_overview_bad_date_is_422(tmp_path: Path) -> None:
    client, _ = _unavailable_client(tmp_path)

    response = client.get(
        f"/api/research/{CODE}/overview", params={"date": "20261030"}
    )

    assert response.status_code == 422
    assert "YYYY-MM-DD" in response.json()["detail"]
