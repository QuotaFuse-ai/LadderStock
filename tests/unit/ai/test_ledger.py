"""F9 观点账本：方向识别、期限、阈值解析、判定规则。"""

from __future__ import annotations

from ladderstock.ai import ledger


def test_parse_direction_from_tone() -> None:
    assert ledger.parse_direction("偏多") == ledger.BULLISH
    assert ledger.parse_direction("偏空") == ledger.BEARISH
    assert ledger.parse_direction("中性") == ledger.NEUTRAL
    assert ledger.parse_direction(None, "整体看多，但要防回撤") == ledger.BULLISH


def test_horizon_reads_explicit_terms() -> None:
    assert ledger.horizon_for("明天买吗") == ledger.DEFAULT_HORIZON
    assert ledger.horizon_for("一周内能不能到 1500") == 5
    assert ledger.horizon_for("一个月怎么看") == 20
    assert ledger.horizon_for("一个季度值不值得拿") == 60


def test_parse_threshold_below_and_above() -> None:
    assert ledger.parse_threshold("跌破 1400 则本条失效") == {"kind": "below", "value": 1400.0}
    assert ledger.parse_threshold("站上 1600 说明判断错了") == {"kind": "above", "value": 1600.0}


def test_parse_threshold_non_price_is_manual() -> None:
    assert ledger.parse_threshold("毛利率低于 30% 则失效") == {"kind": "manual"}
    assert ledger.parse_threshold("视情况而定") is None


def test_judge_hit_for_bullish() -> None:
    record = {"direction": "bullish", "base_price": 100.0, "due_date": "2026-11-25", "falsifier": None}
    bars = [("2026-11-20", 101.0), ("2026-11-25", 104.0)]
    verdict = ledger.judge(record, bars)
    assert verdict["status"] == "hit"
    assert verdict["pct_change"] == 0.04


def test_judge_miss_for_bullish() -> None:
    record = {"direction": "bullish", "base_price": 100.0, "due_date": "2026-11-25"}
    bars = [("2026-11-25", 96.0)]
    assert ledger.judge(record, bars)["status"] == "miss"


def test_judge_bearish_hit() -> None:
    record = {"direction": "bearish", "base_price": 100.0, "due_date": "2026-11-25"}
    bars = [("2026-11-25", 92.0)]
    assert ledger.judge(record, bars)["status"] == "hit"


def test_flip_wins_over_direction() -> None:
    """先跌破翻转阈值、后来又涨回去：仍是 flipped，不能算命中。"""
    record = {
        "direction": "bullish",
        "base_price": 100.0,
        "due_date": "2026-11-25",
        "falsifier": "跌破 95 则本条失效",
    }
    bars = [("2026-11-21", 94.0), ("2026-11-25", 106.0)]
    verdict = ledger.judge(record, bars)
    assert verdict["status"] == "flipped"
    assert verdict["flip_date"] == "2026-11-21"


def test_not_due_is_undecided() -> None:
    record = {"direction": "bullish", "base_price": 100.0, "due_date": "2026-12-31"}
    bars = [("2026-11-25", 110.0)]
    assert ledger.judge(record, bars)["status"] == "undecided"


def test_missing_base_price_is_undecided() -> None:
    record = {"direction": "bullish", "base_price": None, "due_date": "2026-11-25"}
    assert ledger.judge(record, [("2026-11-25", 120.0)])["status"] == "undecided"


def test_manual_threshold_is_undecided() -> None:
    record = {
        "direction": "bullish",
        "base_price": 100.0,
        "due_date": "2026-11-25",
        "falsifier": "毛利率低于 30% 则失效",
    }
    assert ledger.judge(record, [("2026-11-25", 130.0)])["status"] == "undecided"


def test_neutral_never_judged() -> None:
    record = {"direction": "neutral", "base_price": 100.0, "due_date": "2026-11-25"}
    assert ledger.judge(record, [("2026-11-25", 130.0)])["status"] == "undecided"


def test_due_date_for_adds_horizon() -> None:
    assert ledger.due_date_for("2026-11-20", 5) == "2026-11-25"
