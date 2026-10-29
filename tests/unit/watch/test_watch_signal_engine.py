"""盯盘信号引擎单测：3 分钟阈值命中/未命中、封板→开板、去重、缺口不硬算。

全部离线：样本在用例里构造，不读网络、不读仓库 `data/`。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from ladderstock.watch.signal import (
    KIND_OPEN,
    KIND_SEAL,
    KIND_THRESHOLD_DOWN,
    KIND_THRESHOLD_UP,
    MinuteBar,
    SignalInputError,
    WatchEvent,
    dedupe,
    detect_signals,
    load_bars,
    parse_bars,
)

BASE = datetime(2026, 10, 22, 9, 30)


def series(*prices: float, code: str = "600000", limit: float | None = None) -> list[MinuteBar]:
    """按分钟递推的样本序列（每根间隔 1 分钟）。"""
    return [
        MinuteBar(ts=BASE + timedelta(minutes=index), code=code, price=price, limit_price=limit)
        for index, price in enumerate(prices)
    ]


def kinds(events: list[WatchEvent]) -> list[str]:
    return [event.kind for event in events]


# --------------------------------------------------------------------------- #
# 阈值命中 / 未命中
# --------------------------------------------------------------------------- #

def test_threshold_up_fires_once_on_crossing() -> None:
    """10 → 10.4（相对 3 分钟前 +4%）只报一次，之后持续越阈不重复。"""
    events = detect_signals(series(10.0, 10.0, 10.0, 10.4, 10.45, 10.5), threshold=3.0)
    assert kinds(events) == [KIND_THRESHOLD_UP]
    assert events[0].value == pytest.approx(4.0)
    assert events[0].ts == BASE + timedelta(minutes=3)


def test_threshold_miss_is_silent() -> None:
    """最高只到 +2.5%，阈值 3% 时一条都不报。"""
    events = detect_signals(series(10.0, 10.1, 10.2, 10.25), threshold=3.0)
    assert events == []


def test_threshold_down_carries_negative_value() -> None:
    events = detect_signals(series(10.0, 10.0, 10.0, 9.6), threshold=3.0)
    assert kinds(events) == [KIND_THRESHOLD_DOWN]
    assert events[0].value == pytest.approx(-4.0)


def test_recross_after_pullback_reports_again() -> None:
    """回落到阈值下再冲上去，是新的一次越阈（不是刷屏，也不是吞掉）。"""
    events = detect_signals(
        series(10.0, 10.0, 10.0, 10.4, 10.0, 10.0, 10.0, 10.5), threshold=3.0
    )
    ups = [event for event in events if event.kind == KIND_THRESHOLD_UP]
    assert len(ups) == 2
    assert [event.minute for event in ups] == [
        BASE + timedelta(minutes=3),
        BASE + timedelta(minutes=7),
    ]


def test_threshold_must_be_positive() -> None:
    with pytest.raises(ValueError):
        detect_signals(series(10.0, 10.1), threshold=0)


# --------------------------------------------------------------------------- #
# 3 分钟窗口：必须恰好相差 3 分钟
# --------------------------------------------------------------------------- #

def test_window_is_exactly_three_minutes() -> None:
    events = detect_signals(series(10.0, 10.0, 10.0, 10.4), threshold=3.0)
    assert kinds(events) == [KIND_THRESHOLD_UP]


def test_gap_never_borrows_stale_price() -> None:
    """09:30 与 09:34 之间缺 09:31–09:33：宁可漏报，也不拿 4 分钟前的价格凑涨跌。"""
    bars = [
        MinuteBar(ts=BASE, code="600000", price=10.0),
        MinuteBar(ts=BASE + timedelta(minutes=4), code="600000", price=12.0),
    ]
    assert detect_signals(bars, threshold=3.0) == []


def test_missing_base_bar_suppresses_event() -> None:
    """09:36 的基准该是 09:33，但样本里没有 09:33：宁可不报也不拿 09:32 凑。"""
    bars = [
        MinuteBar(ts=BASE + timedelta(minutes=1), code="600000", price=10.0),
        MinuteBar(ts=BASE + timedelta(minutes=2), code="600000", price=10.0),
        MinuteBar(ts=BASE + timedelta(minutes=6), code="600000", price=10.5),
    ]
    assert detect_signals(bars, threshold=3.0) == []


# --------------------------------------------------------------------------- #
# 封板 / 开板
# --------------------------------------------------------------------------- #

def test_seal_then_open() -> None:
    bars = [
        MinuteBar(ts=BASE, code="000001", price=10.8, limit_price=11.0),
        MinuteBar(ts=BASE + timedelta(minutes=1), code="000001", price=11.0, limit_price=11.0),
        MinuteBar(ts=BASE + timedelta(minutes=2), code="000001", price=10.95, limit_price=11.0),
    ]
    events = detect_signals(bars, threshold=3.0)
    assert kinds(events) == [KIND_SEAL, KIND_OPEN]
    assert events[0].value == pytest.approx(11.0)


def test_staying_sealed_reports_once() -> None:
    bars = [
        MinuteBar(ts=BASE + timedelta(minutes=index), code="000001", price=11.0, limit_price=11.0)
        for index in range(4)
    ]
    assert kinds(detect_signals(bars, threshold=3.0)) == [KIND_SEAL]


def test_no_limit_price_means_no_seal_events() -> None:
    bars = series(10.0, 11.0, 11.0, 11.0)
    seal_like = [
        event
        for event in detect_signals(bars, threshold=3.0)
        if event.kind in (KIND_SEAL, KIND_OPEN)
    ]
    assert seal_like == []


def test_first_bar_already_sealed_is_reported() -> None:
    bars = [
        MinuteBar(ts=BASE, code="000001", price=11.0, limit_price=11.0),
        MinuteBar(ts=BASE + timedelta(minutes=1), code="000001", price=11.0, limit_price=11.0),
    ]
    assert kinds(detect_signals(bars, threshold=3.0)) == [KIND_SEAL]


# --------------------------------------------------------------------------- #
# 去重：同 code 同 kind 同分钟只报一次
# --------------------------------------------------------------------------- #

def test_duplicate_pushes_in_same_minute_collapse() -> None:
    bars = [
        MinuteBar(ts=BASE, code="600000", price=10.0),
        MinuteBar(ts=BASE + timedelta(minutes=1), code="600000", price=10.0),
        MinuteBar(ts=BASE + timedelta(minutes=2), code="600000", price=10.0),
        MinuteBar(ts=BASE + timedelta(minutes=3), code="600000", price=10.4),
        # 同一分钟多路推送：后到覆盖先到，且不会多出一条事件
        MinuteBar(
            ts=BASE + timedelta(minutes=3, seconds=40), code="600000", price=10.41
        ),
    ]
    events = detect_signals(bars, threshold=3.0)
    assert len(events) == 1
    assert events[0].kind == KIND_THRESHOLD_UP


def test_dedupe_keeps_first_of_same_key() -> None:
    first = WatchEvent(ts=BASE, code="600000", kind=KIND_THRESHOLD_UP, value=3.1, source_detail="a")
    second = WatchEvent(
        ts=BASE + timedelta(seconds=20),
        code="600000",
        kind=KIND_THRESHOLD_UP,
        value=3.2,
        source_detail="b",
    )
    other = WatchEvent(ts=BASE, code="600001", kind=KIND_THRESHOLD_UP, value=3.3, source_detail="a")
    assert dedupe([first, second, other]) == [first, other]


def test_events_carry_five_field_contract() -> None:
    events = detect_signals(series(10.0, 10.0, 10.0, 10.4), threshold=3.0)
    assert set(events[0].as_dict()) == {"ts", "code", "kind", "value", "source_detail"}


# --------------------------------------------------------------------------- #
# 样本装载与校验
# --------------------------------------------------------------------------- #

def test_parse_bars_rejects_missing_code() -> None:
    with pytest.raises(SignalInputError):
        parse_bars([{"ts": "2026-10-22T09:30:00", "price": 10.0}])


def test_parse_bars_rejects_bad_timestamp() -> None:
    with pytest.raises(SignalInputError):
        parse_bars([{"ts": "昨天", "code": "600000", "price": 10.0}])


def test_parse_bars_rejects_non_numeric_price() -> None:
    with pytest.raises(SignalInputError):
        parse_bars([{"ts": "2026-10-22T09:30:00", "code": "600000", "price": "十块"}])


def test_parse_bars_accepts_limit_price() -> None:
    bars = parse_bars(
        [
            {
                "ts": "2026-10-22T09:30:00",
                "code": "600000",
                "price": 10.0,
                "limit_price": 11.0,
            }
        ]
    )
    assert bars[0].sealed is False


def test_load_bars_from_jsonl_and_json(tmp_path: Path) -> None:
    rows = [
        {"ts": "2026-10-22T09:30:00", "code": "600000", "price": 10.0},
        {"ts": "2026-10-22T09:31:00", "code": "600000", "price": 10.1},
    ]
    jsonl = tmp_path / "bars.jsonl"
    jsonl.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    assert len(load_bars(jsonl)) == 2

    payload = tmp_path / "bars.json"
    payload.write_text(json.dumps({"bars": rows}), encoding="utf-8")
    assert len(load_bars(payload)) == 2


def test_load_bars_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_bars(tmp_path / "nope.jsonl")
