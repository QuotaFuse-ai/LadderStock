"""QUOT-436：`GET /api/events/timeline` 的契约测试。

钉住五条口径：

1. 事件按日期升序；同一天保持「解禁 → 业绩预告 → 公告抽取」的来源顺序；
2. 标的集合 = 本机自选 ∪ 持仓账本（数量 > 0），同一代码只出现一次且 `origin` 写全；
3. 「财报预约披露日 / 股东大会 / 政策发布窗口」没有数据源，只进 `gaps`；
4. 上游取数失败 → 200 + 该来源一条 gap，**不补日期、不造事件**；
5. `days` 只收 7 / 30 / 90，其余 422；隐私面只接受回环来源。

时间用 `today=` 冻结给构建器；HTTP 用例只断言结构与来源，不依赖真实日期。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fixtures.fake_peers import FakeTransport, make_news_registry, tickhub_payload
from fixtures.news_samples import news_handlers, news_tools

from ladderstock.journal.portfolio import add_trade
from ladderstock.journal.watchlist import add_watch
from ladderstock.news import store as news_store
from ladderstock.news.calendar import NO_SOURCE_SECTIONS
from ladderstock.news.timeline import TIMELINE_DAYS, build_timeline, parse_timeline_days
from ladderstock.web.radar import RadarPlane
from ladderstock.web.server import create_app

LOOPBACK = ("127.0.0.1", 54321)
REMOTE = ("10.1.2.3", 54321)
CODE = "600519"
TODAY = date(2026, 11, 10)


# --------------------------------------------------------------------------- #
# 桩
# --------------------------------------------------------------------------- #

def _payload_handlers(*, lockup: dict[str, Any], earnings: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "lockup_expiry": lambda _params: tickhub_payload(lockup, source="lockup_expiry"),
        "earnings_forecast": lambda _params: tickhub_payload(
            earnings, source="earnings_forecast"
        ),
    }


def _same_day_registry(root: Path):
    """同一天同时有解禁与业绩预告，用来钉同日来源顺序。"""
    lockup = {
        "code": CODE,
        "trade_date": TODAY.isoformat(),
        "forward_days": 45,
        "history": [],
        "upcoming": [
            {
                "date": "2026-11-16",
                "type": "首发原股东限售股份",
                "able_shares_10k": 411.2,
                "ratio_pct": 0.0328,
            }
        ],
    }
    earnings = [
        {
            "code": CODE,
            "name": "贵州茅台",
            "notice_date": "2026-11-16",
            "indicator": "归属于上市公司股东的净利润",
            "forecast_type": "预增",
            "content": "预计同比增长约 8.5%。",
        },
        {
            "code": CODE,
            "name": "贵州茅台",
            "notice_date": "2026-11-12",
            "indicator": "营业收入",
            "forecast_type": "预增",
            "content": "预计同比增长约 15%。",
        },
    ]
    transport = FakeTransport(
        handlers=_payload_handlers(lockup=lockup, earnings=earnings),
        tools=["lockup_expiry", "earnings_forecast"],
    )
    return make_news_registry(transport, root)


def _failing_registry(root: Path):
    """两个结构化源都不可用：只留下本机抽取事件。"""
    transport = FakeTransport(
        handlers=news_handlers(failing=("lockup_expiry", "earnings_forecast")),
        tools=news_tools(),
    )
    return make_news_registry(transport, root)


def _client(root: Path, registry: Any, *, client: tuple[str, int] = LOOPBACK) -> TestClient:
    plane = RadarPlane(data_root=root, registry=registry)
    app = create_app(data_root=root, dist_dir=root / "no-dist", radar_plane=plane)
    return TestClient(app, client=client)


def _insert_event(
    root: Path,
    *,
    event_id: str,
    type_: str,
    event_date: str,
    date_basis: str,
    codes: list[str],
    title: str,
    snippet: str = "",
    dataset: str = "cninfo_announcements",
) -> None:
    conn = news_store.connect(root, create=True)
    assert conn is not None
    try:
        conn.execute(
            """
            INSERT INTO news_events (
                id, item_id, type, label, rule_id, event_date, title, codes, evidence,
                date_basis, extractor, ai_note, first_seen_at, updated_at
            ) VALUES (?, '', ?, '', '', ?, ?, ?, ?, ?, 'rule', NULL,
                      '2026-11-01T00:00:00+00:00', '2026-11-01T00:00:00+00:00')
            """,
            (
                event_id,
                type_,
                event_date,
                title,
                json.dumps(codes, ensure_ascii=False),
                json.dumps(
                    {"snippet": snippet, "dataset": dataset}, ensure_ascii=False
                ),
                date_basis,
            ),
        )
    finally:
        news_store.close(conn)


# --------------------------------------------------------------------------- #
# 构建器：排序 / 标的集合 / 缺口
# --------------------------------------------------------------------------- #

def test_events_sorted_ascending_and_same_day_source_order(tmp_path: Path) -> None:
    add_watch(CODE, root=tmp_path)
    data = build_timeline(30, today=TODAY, root=tmp_path, registry=_same_day_registry(tmp_path))

    dates = [event["date"] for event in data["events"]]
    assert dates == sorted(dates)

    # 2026-11-16 同一天：解禁先于业绩预告（来源顺序稳定）
    same_day = [event for event in data["events"] if event["date"] == "2026-11-16"]
    assert [event["kind"] for event in same_day] == ["lockup", "earnings"]

    # 11-12 的业绩预告排在前面
    assert data["events"][0]["date"] == "2026-11-12"
    assert data["events"][0]["kind"] == "earnings"


def test_symbols_merge_watchlist_and_holdings_with_origin(tmp_path: Path) -> None:
    add_watch(CODE, root=tmp_path)
    add_watch("000001", root=tmp_path)
    add_trade(
        {
            "symbol": "000001",
            "name": "平安银行",
            "type": "buy",
            "trade_date": "2026-10-01",
            "shares": 100,
            "price": 10.0,
        },
        root=tmp_path,
    )

    data = build_timeline(30, today=TODAY, root=tmp_path, registry=_same_day_registry(tmp_path))

    symbols = {item["symbol"]: item for item in data["symbols"]}
    assert set(symbols) == {CODE, "000001"}
    assert symbols[CODE]["origin"] == ["自选"]
    # 自选 + 持有，只出现一次；名字从账本补上
    assert symbols["000001"]["origin"] == ["自选", "持有"]
    assert symbols["000001"]["name"] == "平安银行"


def test_no_source_sections_live_in_gaps(tmp_path: Path) -> None:
    data = build_timeline(7, today=TODAY, root=tmp_path, registry=_failing_registry(tmp_path))

    joined = "\n".join(data["gaps"])
    for section in NO_SOURCE_SECTIONS:
        assert section["label"] in joined
        assert section["reason"] in joined
        assert section["ask"] in joined


def test_upstream_failure_is_a_gap_and_adds_no_events(tmp_path: Path) -> None:
    add_watch(CODE, root=tmp_path)
    data = build_timeline(
        30, today=TODAY, root=tmp_path, registry=_failing_registry(tmp_path)
    )

    assert data["events"] == []
    assert data["data_source_status"] == "partial"
    joined = "\n".join(data["gaps"])
    assert "解禁日（lockup_calendar）" in joined
    assert "业绩预告（earnings_preview）" in joined
    assert "本轮取数失败" in joined
    assert "没有补日期" in joined


def test_disclosure_only_accepts_evidence_text_and_tracked_codes(tmp_path: Path) -> None:
    add_watch(CODE, root=tmp_path)
    _insert_event(
        tmp_path,
        event_id="e-meeting",
        type_="shareholder_meeting",
        event_date="2026-11-20",
        date_basis="evidence_text",
        codes=[CODE],
        title="关于召开 2026 年第三次临时股东会的通知",
        snippet="公司定于 2026 年 11 月 20 日召开临时股东会",
    )
    _insert_event(
        tmp_path,
        event_id="e-published",
        type_="shareholder_meeting",
        event_date="2026-11-21",
        date_basis="published_at",
        codes=[CODE],
        title="只有发布时间，没有原文日期",
    )
    _insert_event(
        tmp_path,
        event_id="e-contract",
        type_="contract_award",
        event_date="2026-11-22",
        date_basis="evidence_text",
        codes=[CODE],
        title="不在时间轴的三类里",
    )
    _insert_event(
        tmp_path,
        event_id="e-other-code",
        type_="lockup_expiry",
        event_date="2026-11-23",
        date_basis="evidence_text",
        codes=["000002"],
        title="不在自选 / 持仓里",
    )

    data = build_timeline(
        30, today=TODAY, root=tmp_path, registry=_failing_registry(tmp_path)
    )

    assert [event["ref"] for event in data["events"]] == ["e-meeting"]
    event = data["events"][0]
    assert event["kind"] == "disclosure"
    assert event["symbol"] == CODE
    assert event["source"] == "cninfo_announcements"
    # 抽取事件没有落库取数时刻：如实留空，不拿发布时间顶替
    assert event["fetched_at"] is None
    assert event["estimated"] is False
    assert event["detail"] == ["原文：公司定于 2026 年 11 月 20 日召开临时股东会"]


def test_past_events_go_to_overdue_not_events(tmp_path: Path) -> None:
    add_watch(CODE, root=tmp_path)
    _insert_event(
        tmp_path,
        event_id="e-past",
        type_="shareholder_meeting",
        event_date="2026-11-05",
        date_basis="evidence_text",
        codes=[CODE],
        title="已经过去的股东会",
    )

    data = build_timeline(
        7, today=TODAY, root=tmp_path, registry=_failing_registry(tmp_path)
    )

    assert data["events"] == []
    assert [event["ref"] for event in data["overdue"]] == ["e-past"]


def test_parse_timeline_days_only_accepts_three_windows() -> None:
    assert parse_timeline_days(None) == 30
    for value in TIMELINE_DAYS:
        assert parse_timeline_days(value) == value
    for bad in (0, 10, 365, -7, "30", 30.0, True):
        with pytest.raises(ValueError):
            parse_timeline_days(bad)


# --------------------------------------------------------------------------- #
# HTTP：形状 / 失败降级 / 参数 / 闸门
# --------------------------------------------------------------------------- #

def test_endpoint_returns_flat_payload_with_no_store(tmp_path: Path) -> None:
    add_watch(CODE, root=tmp_path)
    client = _client(tmp_path, _same_day_registry(tmp_path))

    response = client.get("/api/events/timeline", params={"days": 30})

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    for key in (
        "window",
        "generated_at",
        "symbols",
        "events",
        "overdue",
        "gaps",
        "data_source_status",
    ):
        assert key in body
    assert "payload" not in body
    assert body["window"] == {"from": today_iso(), "to": end_iso(30), "days": 30}


def test_endpoint_defaults_to_thirty_days(tmp_path: Path) -> None:
    client = _client(tmp_path, _failing_registry(tmp_path))

    body = client.get("/api/events/timeline").json()

    assert body["window"]["days"] == 30


def test_endpoint_empty_state_when_no_watchlist_or_holdings(tmp_path: Path) -> None:
    client = _client(tmp_path, _failing_registry(tmp_path))

    response = client.get("/api/events/timeline", params={"days": 7})

    assert response.status_code == 200
    body = response.json()
    assert body["symbols"] == []
    assert body["events"] == []
    assert body["data_source_status"] == "missing"
    joined = "\n".join(body["gaps"])
    for section in NO_SOURCE_SECTIONS:
        assert section["label"] in joined


def test_endpoint_upstream_failure_is_200_plus_gap(tmp_path: Path) -> None:
    add_watch(CODE, root=tmp_path)
    client = _client(tmp_path, _failing_registry(tmp_path))

    response = client.get("/api/events/timeline", params={"days": 30})

    assert response.status_code == 200
    body = response.json()
    assert body["events"] == []
    assert body["data_source_status"] == "partial"
    joined = "\n".join(body["gaps"])
    assert "lockup_calendar" in joined
    assert "earnings_preview" in joined


@pytest.mark.parametrize("days", [0, 10, 365, -7])
def test_endpoint_rejects_days_outside_three_windows(tmp_path: Path, days: int) -> None:
    client = _client(tmp_path, _failing_registry(tmp_path))

    response = client.get("/api/events/timeline", params={"days": days})

    assert response.status_code == 422


def test_endpoint_rejects_non_integer_days(tmp_path: Path) -> None:
    client = _client(tmp_path, _failing_registry(tmp_path))

    response = client.get("/api/events/timeline", params={"days": "abc"})

    assert response.status_code == 422


def test_endpoint_is_loopback_only(tmp_path: Path) -> None:
    add_watch(CODE, root=tmp_path)
    client = _client(tmp_path, _same_day_registry(tmp_path), client=REMOTE)

    response = client.get("/api/events/timeline", params={"days": 30})

    assert response.status_code == 403


def today_iso() -> str:
    from ladderstock.news.timeline import beijing_today

    return beijing_today().isoformat()


def end_iso(days: int) -> str:
    from datetime import timedelta

    from ladderstock.news.timeline import beijing_today

    return (beijing_today() + timedelta(days=days)).isoformat()
