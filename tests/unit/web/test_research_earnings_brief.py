"""QUOT-446：`GET /api/research/{code}/earnings-brief` 的契约测试。

财报前瞻只读本机缓存：一致预期取 `consensus_forecast` 最新快照，实际值取
`finance_indicators` 最近 N 期，预期值取同报告期的 `earnings_preview` 预告。
**发布日 / 反应日 / 当天涨跌**没有数据源时必须 `null` + reason，不拿报告期末
或抓取日顶替；缺少某块就单独给 reason，整页不降级成空态。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.data import duckdb_store
from ladderstock.web.server import create_app

CODE = "600000"
BASE_URL = "http://127.0.0.1:5950"
LOOPBACK = ("127.0.0.1", 54321)

PERIODS: list[str] = [
    "2024-12-31",
    "2025-03-31",
    "2025-06-30",
    "2025-09-30",
    "2025-12-31",
    "2026-03-31",
    "2026-06-30",
    "2026-09-30",
]


class _NullControlPlane:
    """占位：这些用例只关心只读查询接口，不构造真实控制面。"""

    peers: dict[str, Any] = {}


def _client(root: Path) -> TestClient:
    app = create_app(
        data_root=root,
        dist_dir=root / "no-dist",
        control_plane=_NullControlPlane(),
    )
    return TestClient(app, base_url=BASE_URL, client=LOOPBACK)


def _finance_rows() -> list[dict[str, Any]]:
    return [
        {
            "code": CODE,
            "trade_date": day,
            "report_name": f"{day[:7]} 报告",
            "eps": 1.00 + index * 0.1,
            "bps": 9.5 + index * 0.1,
            "revenue": 1000.0 + index,
            "net_profit": 100.0 + index,
            "roe": 10.0 + index,
            "revenue_yoy": 5.0 + index,
            "profit_yoy": 2.0 + index,
        }
        for index, day in enumerate(PERIODS)
    ]


def _preview_rows() -> list[dict[str, Any]]:
    """两期预告：2026-09-30 有两条（同日不同公告日，取最新），2026-06-30 一条。"""
    return [
        {
            "code": CODE,
            "trade_date": "2026-10-10",
            "notice_date": "2026-10-10",
            "report_date": "2026-09-30",
            "indicator": "净利润",
            "forecast_type": "预增",
            "amount_upper": 100.0,
            "change_pct_upper": 20.0,
        },
        {
            "code": CODE,
            "trade_date": "2026-10-20",
            "notice_date": "2026-10-20",
            "report_date": "2026-09-30",
            "indicator": "净利润",
            "forecast_type": "略增",
            "amount_upper": 120.0,
            "change_pct_upper": 25.0,
        },
        {
            "code": CODE,
            "trade_date": "2026-07-15",
            "notice_date": "2026-07-15",
            "report_date": "2026-06-30",
            "indicator": "净利润",
            "forecast_type": "预增",
            "amount_upper": 80.0,
            "change_pct_upper": 10.0,
        },
    ]


def _consensus_row() -> dict[str, Any]:
    return {
        "code": CODE,
        "trade_date": "2026-11-04",
        "as_of": "2026-11-04",
        "name": "浦发银行",
        "current_year": 2026,
        "eps_current_year": 1.53,
        "eps_next_year": 1.64,
        "analyst_count": 12,
        "ratings": {"买入": 8, "增持": 4},
        "consensus": [{"year": 2026, "eps": 1.53}],
        "history": [{"as_of": "2026-10-01", "eps_current_year": 1.50}],
    }


def _seed_full(root: Path) -> None:
    duckdb_store.write_dataset("finance_indicators", _finance_rows(), root=root)
    duckdb_store.write_dataset("earnings_preview", _preview_rows(), root=root)
    duckdb_store.write_dataset("consensus_forecast", [_consensus_row()], root=root)


# --------------------------------------------------------------------------- #
# 1. 有数：一致预期 + 最近 6 期明细（默认值、排序、实际/预期对应）
# --------------------------------------------------------------------------- #

def test_earnings_brief_returns_consensus_and_six_periods(tmp_path: Path) -> None:
    _seed_full(tmp_path)

    response = _client(tmp_path).get(f"/api/research/{CODE}/earnings-brief")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"payload", "data_source_status", "data_as_of", "meta"}
    assert body["data_source_status"] == "ok"
    assert body["data_as_of"] == "2026-11-04", "一致预期快照晚于最新报告期"
    assert body["meta"]["topic"] == "research_earnings_brief"

    payload = body["payload"]
    assert payload["code"] == CODE
    assert payload["name"] == "浦发银行"
    assert payload["periods"] == 6 == len(payload["earnings"])
    days = [item["report_date"] for item in payload["earnings"]]
    assert days == sorted(days, reverse=True)
    assert days[0] == "2026-09-30"
    assert days[-1] == "2025-06-30", "默认只取最近 6 期，更早的不返回"

    latest = payload["earnings"][0]
    assert latest["actual"]["eps"] == pytest.approx(1.00 + 7 * 0.1)
    assert latest["actual"]["roe"] == 17.0
    assert latest["forecast"] is not None
    assert latest["forecast"]["notice_date"] == "2026-10-20", "同报告期多条取最新公告"
    assert latest["forecast"]["forecast_type"] == "略增"
    assert latest["forecast_reason"] is None

    assert "finance_indicators" in (latest["publish_date_reason"] or "")
    assert "报告披露日" in latest["publish_date_reason"]
    assert latest["publish_date"] is None
    assert latest["reaction_date"] is None and "发布日" in latest["reaction_date_reason"]
    assert latest["reaction_change_pct"] is None
    assert "发布日" in latest["reaction_change_pct_reason"]

    with_forecast = [item for item in payload["earnings"] if item["forecast"]]
    assert [item["report_date"] for item in with_forecast] == ["2026-09-30", "2026-06-30"]
    no_forecast = payload["earnings"][2]
    assert no_forecast["report_date"] == "2026-03-31"
    assert no_forecast["forecast"] is None
    assert "earnings_preview" in no_forecast["forecast_reason"]

    consensus = payload["consensus"]
    assert consensus["as_of"] == "2026-11-04"
    assert consensus["analyst_count"] == 12
    assert consensus["eps_current_year"] == 1.53 and consensus["eps_next_year"] == 1.64
    assert consensus["consensus"] == [{"year": 2026, "eps": 1.53}]

    stats = payload["stats"]
    assert stats["periods"] == 6
    assert stats["with_forecast"] == 2
    assert stats["with_publish_date"] == 0
    assert stats["with_reaction"] == 0
    assert stats["latest_report_date"] == "2026-09-30"
    assert stats["latest_report_eps"] == pytest.approx(1.00 + 7 * 0.1)
    assert stats["consensus_analyst_count"] == 12
    assert stats["consensus_eps_current_year"] == 1.53
    assert stats["consensus_eps_next_year"] == 1.64


def test_earnings_brief_periods_param_truncates(tmp_path: Path) -> None:
    _seed_full(tmp_path)

    payload = _client(tmp_path).get(
        f"/api/research/{CODE}/earnings-brief", params={"periods": 2}
    ).json()["payload"]

    assert payload["periods"] == 2
    assert [item["report_date"] for item in payload["earnings"]] == [
        "2026-09-30",
        "2026-06-30",
    ]
    assert payload["stats"]["periods"] == 2
    assert payload["stats"]["with_forecast"] == 2


# --------------------------------------------------------------------------- #
# 2. 缺数：各块独立 null + reason，不补 0、不编造
# --------------------------------------------------------------------------- #

def test_earnings_brief_missing_everything_is_200_missing_state(tmp_path: Path) -> None:
    response = _client(tmp_path).get(f"/api/research/{CODE}/earnings-brief")

    assert response.status_code == 200
    body = response.json()
    assert body["data_source_status"] == "missing"
    assert body["data_as_of"] is None
    payload = body["payload"]
    assert payload["consensus"] is None
    assert "consensus_forecast" in (payload["consensus_reason"] or "")
    assert payload["earnings"] == []
    assert "finance_indicators" in (payload["earnings_reason"] or "")
    assert payload["stats"]["periods"] == 0
    assert payload["stats"]["latest_report_date"] is None


def test_earnings_brief_without_finance_keeps_consensus(tmp_path: Path) -> None:
    duckdb_store.write_dataset("consensus_forecast", [_consensus_row()], root=tmp_path)

    body = _client(tmp_path).get(f"/api/research/{CODE}/earnings-brief").json()

    assert body["data_source_status"] == "ok", "有一致预期就不算 missing"
    assert body["data_as_of"] == "2026-11-04"
    payload = body["payload"]
    assert payload["consensus"] is not None
    assert payload["consensus_reason"] is None
    assert payload["earnings"] == []
    assert "finance_indicators" in (payload["earnings_reason"] or "")
    assert payload["stats"]["with_forecast"] == 0


def test_earnings_brief_without_consensus_keeps_earnings(tmp_path: Path) -> None:
    duckdb_store.write_dataset("finance_indicators", _finance_rows(), root=tmp_path)

    body = _client(tmp_path).get(f"/api/research/{CODE}/earnings-brief").json()

    assert body["data_source_status"] == "ok"
    assert body["data_as_of"] == "2026-09-30", "没有快照时退到最新报告期"
    payload = body["payload"]
    assert payload["consensus"] is None
    assert "consensus_forecast" in (payload["consensus_reason"] or "")
    assert len(payload["earnings"]) == 6
    assert payload["stats"]["consensus_analyst_count"] is None


# --------------------------------------------------------------------------- #
# 3. 参数与代码校验
# --------------------------------------------------------------------------- #

def test_earnings_brief_unknown_code_is_404(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/api/research/999999/earnings-brief")

    assert response.status_code == 404
    assert "未知证券代码" in response.json()["detail"]


def test_earnings_brief_periods_out_of_range_is_422(tmp_path: Path) -> None:
    client = _client(tmp_path)
    path = f"/api/research/{CODE}/earnings-brief"
    for periods in (0, 13, "abc"):
        response = client.get(path, params={"periods": periods})
        assert response.status_code == 422, periods
