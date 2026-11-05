"""QUOT-446：`GET /api/research/{code}/event-study` 的契约测试。

事件回看用本地 `index_daily`（000300）做交易日历，把每类事件对齐到
「事件日前 5 / 后 20 个交易日」的窗口，算个股收益与对基准的超额。
**样本 < 8 不给均值**（`insufficient_sample`）；个股或基准缺窗口边界价时
单个事件显式 `null` + reason，整类不因此消失。
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.data import duckdb_store
from ladderstock.data.watermark import WatermarkStore
from ladderstock.web.research import BENCHMARK_CODE, EVENT_SAMPLE_MIN
from ladderstock.web.server import create_app

CODE = "600000"
BASE_URL = "http://127.0.0.1:5950"
LOOPBACK = ("127.0.0.1", 54321)
DAYS = 45
START = date(2026, 1, 5)

#: 交易日历（本地 index_daily 就是唯一权威，不要求真日历）
CALENDAR: list[str] = [(START + timedelta(days=index)).isoformat() for index in range(DAYS)]

#: 有完整窗口的事件位置：前 5 后 20 都不越界
HOLDER_DAYS = list(range(5, 18))
DRAGON_DAYS = [20, 21]
PREVIEW_DAYS = list(range(1, 7))
LOCKUP_DAYS = [43]


class _NullControlPlane:
    peers: dict[str, Any] = {}


def _client(root: Path) -> TestClient:
    app = create_app(
        data_root=root,
        dist_dir=root / "no-dist",
        control_plane=_NullControlPlane(),
    )
    return TestClient(app, base_url=BASE_URL, client=LOOPBACK)


def _stock_rows() -> list[dict[str, Any]]:
    return [
        {
            "code": CODE,
            "trade_date": day,
            "open": 10.0 + index - 0.2,
            "high": 10.0 + index + 0.3,
            "low": 10.0 + index - 0.4,
            "close": 10.0 + index,
            "volume": 1000.0,
        }
        for index, day in enumerate(CALENDAR)
    ]


def _bench_rows() -> list[dict[str, Any]]:
    """基准恒为 4000：基准收益 0，超额 = 个股收益，方便断言。"""
    return [
        {"code": BENCHMARK_CODE, "trade_date": day, "close": 4000.0}
        for day in CALENDAR
    ]


def _seed_full(root: Path) -> None:
    duckdb_store.write_dataset("daily_k", _stock_rows(), root=root)
    duckdb_store.write_dataset("index_daily", _bench_rows(), root=root)
    duckdb_store.write_dataset(
        "holder_trades",
        [
            {
                "code": CODE,
                "trade_date": CALENDAR[index],
                "notice_date": CALENDAR[index],
                "direction": "增持",
                "holder": f"股东{index}",
                "change_shares_10k": 10.0,
            }
            for index in HOLDER_DAYS
        ],
        root=root,
    )
    duckdb_store.write_dataset(
        "dragon_tiger",
        [
            {
                "code": CODE,
                "trade_date": CALENDAR[index],
                "reason": "涨幅偏离",
                "close": 10.0 + index,
                "change_pct": 9.9,
                "net_amount": 1234.0,
            }
            for index in DRAGON_DAYS
        ],
        root=root,
    )
    duckdb_store.write_dataset(
        "earnings_preview",
        [
            {
                "code": CODE,
                "trade_date": CALENDAR[index],
                "notice_date": CALENDAR[index],
                "report_date": "2026-03-31",
                "indicator": "净利润",
                "forecast_type": "预增",
            }
            for index in PREVIEW_DAYS
        ],
        root=root,
    )
    duckdb_store.write_dataset(
        "lockup_calendar",
        [
            {
                "code": CODE,
                "trade_date": CALENDAR[index],
                "date": CALENDAR[index],
                "lift_status": "解禁",
                "shares_10k": 500.0,
            }
            for index in LOCKUP_DAYS
        ],
        root=root,
    )


def _type(payload: dict[str, Any], dataset: str) -> dict[str, Any]:
    return next(item for item in payload["event_types"] if item["dataset"] == dataset)


def _expected_return(index: int) -> float:
    start, end = 10.0 + index - 5, 10.0 + index + 20
    return end / start - 1


# --------------------------------------------------------------------------- #
# 1. 有数：六类事件 + 窗口收益 + 样本够 8 才算均值
# --------------------------------------------------------------------------- #

def test_event_study_counts_windows_and_means(tmp_path: Path) -> None:
    _seed_full(tmp_path)

    response = _client(tmp_path).get(f"/api/research/{CODE}/event-study")

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"payload", "data_source_status", "data_as_of", "meta"}
    assert body["data_source_status"] == "ok"
    assert body["data_as_of"] == CALENDAR[-1]
    assert body["meta"]["topic"] == "research_event_study"

    payload = body["payload"]
    assert payload["code"] == CODE
    assert payload["window_before"] == 5 and payload["window_after"] == 20
    assert payload["benchmark"] == BENCHMARK_CODE
    assert payload["trading_days"] == DAYS
    assert payload["benchmark_rows"] == DAYS
    assert payload["counts"] == {
        "holder_trades": len(HOLDER_DAYS),
        "dragon_tiger": len(DRAGON_DAYS),
        "earnings_preview": len(PREVIEW_DAYS),
        "institution_survey": 0,
        "share_buyback": 0,
        "lockup_calendar": len(LOCKUP_DAYS),
    }
    assert payload["formula"].startswith("窗口收益 = close[end] / close[start] - 1")

    holders = _type(payload, "holder_trades")
    assert holders["label"] == "增持减持"
    assert holders["dates"] == [CALENDAR[index] for index in HOLDER_DAYS]
    assert len(holders["events"]) == len(HOLDER_DAYS)
    for offset, event in enumerate(holders["events"]):
        index = HOLDER_DAYS[offset]
        assert event["date"] == CALENDAR[index]
        assert event["detail"]["direction"] == "增持"
        assert event["window"]["start"] == CALENDAR[index - 5]
        assert event["window"]["end"] == CALENDAR[index + 20]
        assert event["window"]["stock_return"] == pytest.approx(_expected_return(index))
        assert event["window"]["benchmark_return"] == 0.0
        assert event["window"]["excess_return"] == pytest.approx(_expected_return(index))
        assert event["window"]["reason"] is None

    sample = holders["sample"]
    assert sample["required"] == EVENT_SAMPLE_MIN == 8
    assert sample["window_available"] == len(HOLDER_DAYS)
    assert sample["status"] == "ok"
    mean = sum(_expected_return(index) for index in HOLDER_DAYS) / len(HOLDER_DAYS)
    assert sample["mean_stock_return"] == pytest.approx(mean, abs=1e-6)
    assert sample["mean_benchmark_return"] == 0.0
    assert sample["mean_excess_return"] == pytest.approx(mean, abs=1e-6)
    assert sample["reason"] is None


def test_event_study_below_eight_samples_gives_no_mean(tmp_path: Path) -> None:
    _seed_full(tmp_path)

    payload = _client(tmp_path).get(f"/api/research/{CODE}/event-study").json()["payload"]

    dragons = _type(payload, "dragon_tiger")
    assert dragons["count"] == 2
    assert dragons["sample"]["window_available"] == 2 < EVENT_SAMPLE_MIN
    assert dragons["sample"]["status"] == "insufficient_sample"
    assert dragons["sample"]["mean_stock_return"] is None
    assert dragons["sample"]["mean_benchmark_return"] is None
    assert dragons["sample"]["mean_excess_return"] is None
    assert "可计算窗口 2 < 8" in dragons["sample"]["reason"]

    previews = _type(payload, "earnings_preview")
    assert previews["count"] == len(PREVIEW_DAYS)
    assert previews["sample"]["window_available"] == 2, "事件日靠左，前 5 日不齐的事件算不出窗口"
    assert previews["sample"]["status"] == "insufficient_sample"
    assert previews["sample"]["mean_excess_return"] is None


def test_event_study_window_edges_are_explained(tmp_path: Path) -> None:
    _seed_full(tmp_path)

    payload = _client(tmp_path).get(f"/api/research/{CODE}/event-study").json()["payload"]

    early = _type(payload, "earnings_preview")["events"][0]
    assert early["window"]["start"] is None and early["window"]["excess_return"] is None
    assert "只有 1 个交易日" in early["window"]["reason"]

    late = _type(payload, "lockup_calendar")["events"][0]
    assert late["window"]["end"] is None and late["window"]["excess_return"] is None
    assert "之后本地 000300 只有 1 个交易日" in late["window"]["reason"]
    assert _type(payload, "lockup_calendar")["sample"]["status"] == "insufficient_sample"


def test_event_study_window_params_are_used(tmp_path: Path) -> None:
    _seed_full(tmp_path)

    payload = _client(tmp_path).get(
        f"/api/research/{CODE}/event-study",
        params={"sources": "holder_trades", "window_before": 0, "window_after": 0},
    ).json()["payload"]

    assert payload["window_before"] == 0 and payload["window_after"] == 0
    holders = _type(payload, "holder_trades")
    for event in holders["events"]:
        assert event["window"]["start"] == event["date"] == event["window"]["end"]
        assert event["window"]["excess_return"] == 0.0
    assert holders["sample"]["status"] == "ok"
    assert holders["sample"]["mean_excess_return"] == 0.0


def test_event_study_sources_filter(tmp_path: Path) -> None:
    _seed_full(tmp_path)

    payload = _client(tmp_path).get(
        f"/api/research/{CODE}/event-study",
        params={"sources": "holder_trades,dragon_tiger"},
    ).json()["payload"]

    assert [item["dataset"] for item in payload["event_types"]] == [
        "holder_trades",
        "dragon_tiger",
    ]
    assert set(payload["counts"]) == {"holder_trades", "dragon_tiger"}


# --------------------------------------------------------------------------- #
# 2. 空态：未同步 / 真实空 / 没有基准日历
# --------------------------------------------------------------------------- #

def test_event_study_empty_types_explain_unsynced(tmp_path: Path) -> None:
    _seed_full(tmp_path)

    payload = _client(tmp_path).get(f"/api/research/{CODE}/event-study").json()["payload"]

    survey = _type(payload, "institution_survey")
    assert survey["count"] == 0 and survey["events"] == [] and survey["dates"] == []
    assert survey["sample"]["status"] == "insufficient_sample"
    assert survey["sample"]["window_available"] == 0
    assert "没有任何同步记录" in survey["sample"]["reason"]

    buyback = _type(payload, "share_buyback")
    assert "没有任何同步记录" in buyback["sample"]["reason"]


def test_event_study_empty_type_uses_last_sync_status(tmp_path: Path) -> None:
    _seed_full(tmp_path)
    WatermarkStore(root=tmp_path).record_run(
        "institution_survey", mode="full", status="empty"
    )

    payload = _client(tmp_path).get(
        f"/api/research/{CODE}/event-study", params={"sources": "institution_survey"}
    ).json()["payload"]

    survey = _type(payload, "institution_survey")
    assert "上游真实为空" in survey["sample"]["reason"]


def test_event_study_without_benchmark_is_missing(tmp_path: Path) -> None:
    duckdb_store.write_dataset("daily_k", _stock_rows(), root=tmp_path)
    duckdb_store.write_dataset(
        "holder_trades",
        [
            {
                "code": CODE,
                "trade_date": CALENDAR[index],
                "notice_date": CALENDAR[index],
                "direction": "增持",
            }
            for index in HOLDER_DAYS
        ],
        root=tmp_path,
    )

    body = _client(tmp_path).get(f"/api/research/{CODE}/event-study").json()

    assert body["data_source_status"] == "missing"
    assert body["data_as_of"] is None
    payload = body["payload"]
    assert payload["trading_days"] == 0
    event = _type(payload, "holder_trades")["events"][0]
    assert event["window"]["excess_return"] is None
    assert "index_daily" in event["window"]["reason"]


# --------------------------------------------------------------------------- #
# 3. 参数与代码校验
# --------------------------------------------------------------------------- #

def test_event_study_unknown_code_is_404(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/api/research/999999/event-study")

    assert response.status_code == 404
    assert "未知证券代码" in response.json()["detail"]


def test_event_study_bad_sources_is_422(tmp_path: Path) -> None:
    response = _client(tmp_path).get(
        f"/api/research/{CODE}/event-study", params={"sources": "holder_trades,foo"}
    )

    assert response.status_code == 422
    assert "sources 只允许" in response.json()["detail"]


def test_event_study_bad_windows_is_422(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert (
        client.get(
            f"/api/research/{CODE}/event-study", params={"window_before": 61}
        ).status_code
        == 422
    )
    assert (
        client.get(
            f"/api/research/{CODE}/event-study", params={"window_after": -1}
        ).status_code
        == 422
    )
    assert (
        client.get(
            f"/api/research/{CODE}/event-study", params={"window_before": "abc"}
        ).status_code
        == 422
    )
