"""QUOT-400：`GET /api/research/{code}/overview` 的契约测试。

概览**不依赖六段产物**：没跑过研究也必须能出数——数据只读本机缓存
（`daily_k` / `spot_quote` / `finance_indicators`，走 `archive` 回放），
读不到的块显式 `null` + reason，不补 0、不联网、不编造。

信封照 data 面同款四段：`payload / data_source_status / data_as_of / meta`；
`data_source_status` 只看主指标（K 线）：有根 → `ok`，一根没有 → `missing`。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.data import duckdb_store
from ladderstock.web.research import OVERVIEW_BARS
from ladderstock.web.server import create_app

CODE = "600000"
BASE_URL = "http://127.0.0.1:5950"
LOOPBACK = ("127.0.0.1", 54321)

#: 「跑过五段、report 待补」之外，这些用例不落任何研究产物——概览必须独立于六段


class _NullControlPlane:
    """占位：这些用例只关心概览接口，不构造真实控制面。"""

    peers: dict[str, Any] = {}


def _client(root: Path) -> TestClient:
    app = create_app(
        data_root=root,
        dist_dir=root / "no-dist",
        control_plane=_NullControlPlane(),
    )
    return TestClient(app, base_url=BASE_URL, client=LOOPBACK)


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
# 1. 有数：K 线 ≥120 根 + 快照 + 财务指标
# --------------------------------------------------------------------------- #

def test_overview_returns_kline_quote_and_financials(tmp_path: Path) -> None:
    duckdb_store.write_dataset("daily_k", _seed_days(130), root=tmp_path)
    duckdb_store.write_dataset(
        "spot_quote",
        [
            {
                "code": CODE,
                "name": "浦发银行",
                "price": 10.5,
                "prev_close": 10.2,
                "change_pct": 2.94,
                "volume": 781231,
                "amount": 77566.0,
                "timestamp": "20261104104221",
            }
        ],
        root=tmp_path,
    )
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

    response = _client(tmp_path).get(f"/api/research/{CODE}/overview")

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
    assert quote["price"] == 10.5 and quote["change_pct"] == 2.94
    assert quote["amount"] == 77566.0 and quote["timestamp"] == "20261104104221"

    financials = payload["financials"]
    assert financials["report_name"] == "2026三季报"
    assert financials["report_date"] == "2026-09-30"
    assert financials["roe"] == 10.1175 and financials["revenue_yoy"] == 5.34


def test_overview_date_param_cuts_kline(tmp_path: Path) -> None:
    _seed_cache(tmp_path)

    response = _client(tmp_path).get(
        f"/api/research/{CODE}/overview", params={"date": "2026-06-10"}
    )

    payload = response.json()["payload"]
    assert payload["as_of"] == "2026-06-10"
    bars = payload["kline"]["bars"]
    assert bars[-1]["date"] == "2026-06-10", "date 是截断点，不是研究运行日"
    assert all(bar["date"] <= "2026-06-10" for bar in bars)


def test_overview_works_without_any_research_artifacts(tmp_path: Path) -> None:
    """本单核心：没跑过研究的代码也能出概览（200 + 真实 K 线），不 404。"""
    assert not (tmp_path / "research").exists()
    _seed_cache(tmp_path)

    response = _client(tmp_path).get(f"/api/research/{CODE}/overview")

    assert response.status_code == 200
    assert response.json()["payload"]["kline"]["count"] == 120


# --------------------------------------------------------------------------- #
# 2. 缺数：null + reason，不补 0、不编造
# --------------------------------------------------------------------------- #

def test_overview_missing_everything_is_200_missing_state(tmp_path: Path) -> None:
    """三个数据集都没缓存 → 200 + missing 态：K 线空数组、quote/financials null + reason。"""
    response = _client(tmp_path).get(f"/api/research/{CODE}/overview")

    assert response.status_code == 200
    body = response.json()
    assert body["data_source_status"] == "missing"
    assert body["data_as_of"] is None, "没有可信数据时 data_as_of 不拿请求日期顶替"

    payload = body["payload"]
    assert payload["data_source_status"] == "missing"
    assert payload["kline"]["bars"] == [] and payload["kline"]["count"] == 0
    assert "daily_k" in (payload["kline"]["reason"] or "")
    assert payload["quote"] is None and "spot_quote" in (payload["quote_reason"] or "")
    assert payload["financials"] is None
    assert "finance_indicators" in (payload["financials_reason"] or "")


def test_overview_missing_quote_and_financials_keeps_kline(tmp_path: Path) -> None:
    """只有 K 线：主指标 ok，quote/financials 各自 null + reason，整页不降级成空态。"""
    _seed_cache(tmp_path, kline=_seed_days(30))

    response = _client(tmp_path).get(f"/api/research/{CODE}/overview")

    payload = response.json()["payload"]
    assert response.json()["data_source_status"] == "ok"
    assert payload["kline"]["count"] == 30
    assert "不足" in (payload["kline"]["reason"] or ""), "缓存不足 120 根要如实说明"
    assert payload["quote"] is None and payload["financials"] is None
    assert payload["name"] is None


def test_overview_unknown_code_is_404(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/api/research/999999/overview")

    assert response.status_code == 404
    assert "未知证券代码" in response.json()["detail"]


def test_overview_bad_date_is_422(tmp_path: Path) -> None:
    response = _client(tmp_path).get(
        f"/api/research/{CODE}/overview", params={"date": "20261030"}
    )

    assert response.status_code == 422
    assert "YYYY-MM-DD" in response.json()["detail"]
