"""F9 接线：一轮回答 → 账本记录 → 到期回访判定（纯逻辑 + 假行情）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ladderstock.ai import ledger, recall
from ladderstock.data import chat_store


def _blocks(
    *,
    tone: str = "positive",
    price: float = 100.0,
    as_of: str = "2026-11-20",
    falsifier: str | None = None,
    text: str | None = None,
):
    # 中性/告警的样例文案也必须是中性的：`record_from_turn` 会用正文兜底判方向，
    # 正文写着「偏多」而 tone 是 neutral 时应当落账（见 test_neutral_tone_falls_back…）。
    if text is None:
        text = "量能没有放大，方向暂看不出来" if tone in ("neutral", "warning") else "偏多，主要看现金流改善"
    conclusion = {"text": text, "tone": tone}
    if falsifier:
        conclusion["falsifier"] = falsifier
    blocks = [
        {"type": "conclusion", "payload": conclusion},
        {
            "type": "metric",
            "payload": {
                "items": [
                    {"label": "最新价", "value": price, "unit": "元", "as_of": as_of},
                    {"label": "市盈率", "value": 21.4, "as_of": as_of},
                ]
            },
        },
        {"type": "citation", "payload": {"sources": []}},
    ]
    return blocks


# --------------------------------------------------------------------------- #
# record_from_turn
# --------------------------------------------------------------------------- #


def test_record_from_bullish_turn() -> None:
    record = ledger.record_from_turn(
        "600519 一个月怎么看",
        _blocks(),
        symbol="600519",
        session_id="s1",
        seq=3,
    )
    assert record is not None
    assert record["direction"] == "bullish"
    assert record["base_price"] == 100.0
    assert record["base_date"] == "2026-11-20"
    assert record["horizon_days"] == 20
    assert record["due_date"] == "2026-12-10"
    assert record["symbol"] == "600519"
    assert record["session_id"] == "s1" and record["seq"] == 3


def test_neutral_and_warning_are_not_recorded() -> None:
    assert ledger.record_from_turn("q", _blocks(tone="neutral")) is None
    assert ledger.record_from_turn("q", _blocks(tone="warning")) is None


def test_neutral_tone_falls_back_to_conclusion_text() -> None:
    """真机常见形态：tone=neutral，正文却写着方向——只认 tone 会让判断落不了账。"""
    blocks = [
        {
            "type": "conclusion",
            "payload": {"text": "一、方向判断：本月内偏多的证据略占优，但不是强势多头。", "tone": "neutral"},
        },
        {"type": "metric", "payload": {"items": [{"label": "最新价", "value": 1485.75, "as_of": "2026-11-20"}]}},
    ]
    record = ledger.record_from_turn("q", blocks, symbol="600519")
    assert record is not None
    assert record["direction"] == "bullish"
    assert record["base_price"] == 1485.75


def test_text_vote_picks_stronger_side() -> None:
    """对称正文（「偏多与偏空势均力敌，略微偏向多头」）按票数取多的一侧。"""
    text = "偏多与偏空的条件势均力敌。偏多的依据：价格在高位。偏空的依据：冲高未破。总体上略偏多。"
    assert ledger.direction_from_text(text) == "bullish"
    assert ledger.direction_from_text("偏空 偏空 偏空 偏多") == "bearish"


def test_text_vote_tie_or_silent_is_neutral() -> None:
    assert ledger.direction_from_text("偏多偏空") == "neutral"
    assert ledger.direction_from_text("量能没有放大") == "neutral"
    assert ledger.direction_from_text("") == "neutral"


def test_balanced_narrative_is_not_recorded() -> None:
    blocks = [
        {"type": "conclusion", "payload": {"text": "多空对称", "tone": "neutral"}},
        {"type": "narrative", "payload": {"text": "偏多一条，偏空一条。"}},
        {"type": "metric", "payload": {"items": [{"label": "最新价", "value": 100.0}]}},
    ]
    assert ledger.record_from_turn("q", blocks) is None


def test_no_metric_means_no_baseline_so_no_record() -> None:
    blocks = [{"type": "conclusion", "payload": {"text": "看多", "tone": "positive"}}]
    assert ledger.record_from_turn("q", blocks) is None


def test_horizon_reads_explicit_period() -> None:
    assert ledger.record_from_turn("600519 一周内能反弹吗", _blocks())["horizon_days"] == 5
    assert ledger.record_from_turn("600519 一个季度怎么样", _blocks())["horizon_days"] == 60


def test_falsifier_is_carried_over() -> None:
    record = ledger.record_from_turn("q", _blocks(falsifier="跌破 90 则本条失效"))
    assert record["falsifier"] == "跌破 90 则本条失效"


def test_negative_tone_is_bearish() -> None:
    record = ledger.record_from_turn("q", _blocks(tone="negative"))
    assert record["direction"] == "bearish"


# --------------------------------------------------------------------------- #
# run_recall
# --------------------------------------------------------------------------- #


@pytest.fixture()
def conn(tmp_path: Path):
    connection = chat_store.connect(tmp_path)
    assert connection is not None
    try:
        yield connection
    finally:
        connection.close()


def _seed(conn, *, record_id="r1", direction="bullish", base=100.0, falsifier=None, due="2026-11-20"):
    chat_store.insert_record(
        conn,
        record_id=record_id,
        question="q",
        direction=direction,
        horizon_days=5,
        symbol="600519",
        base_price=base,
        base_date="2026-11-13",
        falsifier=falsifier,
        due_date=due,
    )


def _bars_fetcher(bars):
    def fetch(symbol: str, base_date: str):
        assert symbol == "600519"
        assert base_date == "2026-11-13"
        return bars

    return fetch


def test_bullish_expiry_hit(conn) -> None:
    _seed(conn)
    stats = recall.run_recall(
        conn,
        as_of="2026-11-20",
        fetch_bars=_bars_fetcher([("2026-11-16", 101.0), ("2026-11-20", 103.0)]),
    )
    assert stats["hit"] == 1
    record = chat_store.list_records(conn, status=chat_store.RECORD_HIT)[0]
    assert record["outcome"]["pct_change"] == pytest.approx(0.03)


def test_bearish_but_price_up_is_miss(conn) -> None:
    _seed(conn, direction="bearish")
    stats = recall.run_recall(
        conn,
        as_of="2026-11-20",
        fetch_bars=_bars_fetcher([("2026-11-20", 104.0)]),
    )
    assert stats["miss"] == 1


def test_flip_wins_over_direction(conn) -> None:
    _seed(conn, falsifier="跌破 95 则本条失效")
    stats = recall.run_recall(
        conn,
        as_of="2026-11-20",
        fetch_bars=_bars_fetcher([("2026-11-16", 94.0), ("2026-11-20", 120.0)]),
    )
    assert stats["flipped"] == 1
    record = chat_store.list_records(conn, status=chat_store.RECORD_FLIPPED)[0]
    assert record["outcome"]["flip_date"] == "2026-11-16"


def test_fetch_failure_keeps_record_open(conn) -> None:
    _seed(conn)

    def boom(symbol: str, base_date: str):
        raise RuntimeError("peer down")

    stats = recall.run_recall(conn, as_of="2026-11-20", fetch_bars=boom)
    assert stats["skipped"] == 1
    assert chat_store.record_stats(conn)["open"] == 1


def test_empty_bars_keeps_record_open(conn) -> None:
    _seed(conn)
    stats = recall.run_recall(conn, as_of="2026-11-20", fetch_bars=lambda *_: [])
    assert stats["skipped"] == 1
    assert chat_store.record_stats(conn)["open"] == 1


def test_non_price_falsifier_closes_as_undecided(conn) -> None:
    _seed(conn, falsifier="毛利率跌破 30% 则本条失效")
    stats = recall.run_recall(
        conn,
        as_of="2026-11-20",
        fetch_bars=_bars_fetcher([("2026-11-20", 103.0)]),
    )
    assert stats["undecided"] == 1
    assert chat_store.record_stats(conn)["open"] == 0


def test_missing_symbol_closes_as_undecided(conn) -> None:
    chat_store.insert_record(
        conn,
        record_id="r2",
        question="q",
        direction="bullish",
        horizon_days=5,
        base_price=100.0,
        base_date="2026-11-13",
        due_date="2026-11-20",
    )
    stats = recall.run_recall(conn, as_of="2026-11-20", fetch_bars=lambda *_: [("2026-11-20", 110.0)])
    assert stats["undecided"] == 1


def test_round_trip_from_blocks_to_verdict(conn) -> None:
    """一轮回答落账 → 到期判 hit：F9 的两半在同一张表上闭环。"""
    record = ledger.record_from_turn("600519 一个月怎么看", _blocks(price=100.0), symbol="600519")
    chat_store.insert_record(conn, **record)
    stats = recall.run_recall(
        conn,
        as_of=record["due_date"],
        fetch_bars=lambda symbol, base: [("2026-12-09", 99.0), (record["due_date"], 112.0)],
    )
    assert stats["hit"] == 1
    assert chat_store.record_stats(conn)["hit_rate"] == 1.0


def test_not_due_yet_is_skipped(conn) -> None:
    _seed(conn, due="2026-12-31")
    stats = recall.run_recall(conn, as_of="2026-11-20", fetch_bars=lambda *_: [("2026-11-20", 110.0)])
    assert stats["due"] == 0
    assert chat_store.record_stats(conn)["open"] == 1
