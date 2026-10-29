"""情绪指标：温度公式、晋级率、封板率与缺数据归一（M3 / DEC-007 §2.7）。

指标只由程序计算：每个期望值都能从原始记录手算复核。
"""

from __future__ import annotations

from typing import Any

import pytest

from ladderstock.core import sentiment as sentiment_module

DAY = "2026-10-27"

LIMIT_ROWS = [
    {"code": "A", "consecutive_boards": 5},
    {"code": "B", "consecutive_boards": 3},
    {"code": "C", "consecutive_boards": 2},
    {"code": "D", "consecutive_boards": 1},
    {"code": "E", "consecutive_boards": 1},
]
BROKEN_ROWS = [{"code": "F"}, {"code": "G"}]
YESTERDAY_ROWS = [{"code": "A"}, {"code": "B"}, {"code": "F"}, {"code": "G"}]


def _patch(monkeypatch: pytest.MonkeyPatch, mapping: dict[str, list[dict[str, Any]]]) -> None:
    def fake_snapshot(dataset: str, day: Any, root: Any = None):
        return mapping.get(dataset, []), "parquet"

    monkeypatch.setattr(sentiment_module, "dataset_snapshot", fake_snapshot)


def test_temperature_and_rates(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(
        monkeypatch,
        {
            "limit_pool": LIMIT_ROWS,
            "broken_board": BROKEN_ROWS,
            "yesterday_limit_up": YESTERDAY_ROWS,
        },
    )
    result = sentiment_module.compute_sentiment(DAY)

    assert result["available"] is True
    # 晋级 2/4；封板 5/(5+2)
    assert result["promoted_count"] == 2
    assert result["promotion_rate"] == 50.0
    assert result["seal_rate"] == 71.4
    assert result["max_board"] == 5
    assert result["gap_levels"] == [4]
    # 0.45×50 + 0.35×71.4 + 0.20×min(1,5/6)×100
    assert result["temperature"] == pytest.approx(64.2, abs=0.1)
    assert result["missing"] == []


def test_missing_limit_pool_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, {"broken_board": BROKEN_ROWS})
    result = sentiment_module.compute_sentiment(DAY)

    assert result["available"] is False
    assert result["temperature"] is None
    assert "limit_pool" in result["missing"]
    assert "sync" in result["reason"]


def test_empty_limit_pool_reports_every_missing_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """D-05：早退也要给出完整 `missing`，三池按固定顺序。"""
    _patch(monkeypatch, {"broken_board": BROKEN_ROWS})
    result = sentiment_module.compute_sentiment(DAY)

    assert result["missing"] == ["limit_pool", "yesterday_limit_up"]
    # 炸板池有数据 -> 不在 missing 里，但去重计数照样给出
    assert result["broken_count"] == 2


def test_counts_flag_records_without_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """D-06：计数兜底时，无 `code` 的行数必须写进 `unknown`，读者才知道是行数代理。"""
    _patch(
        monkeypatch,
        {
            "limit_pool": [{"consecutive_boards": 2}, {"name": "无代码"}],
            "broken_board": [{"code": "F"}],
            "yesterday_limit_up": YESTERDAY_ROWS,
        },
    )
    result = sentiment_module.compute_sentiment(DAY)

    # 一条 code 都解析不出 -> 退回行数
    assert result["limit_up_count"] == 2
    assert result["unknown"] == {"limit_pool": 2, "broken_board": 0}


def test_missing_yesterday_renormalizes_weights(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(
        monkeypatch,
        {"limit_pool": LIMIT_ROWS, "broken_board": BROKEN_ROWS},
    )
    result = sentiment_module.compute_sentiment(DAY)

    assert result["available"] is True
    assert result["promotion_rate"] is None
    assert "yesterday_limit_up" in result["missing"]
    # 只剩封板率(0.35) 与高度(0.20) 两项，按可用权重重归一
    expected = (0.35 * 71.4 + 0.20 * (5 / 6) * 100) / (0.35 + 0.20)
    assert result["temperature"] == pytest.approx(round(expected, 1), abs=0.1)


def test_missing_broken_board_keeps_seal_from_limit_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(
        monkeypatch,
        {"limit_pool": LIMIT_ROWS, "yesterday_limit_up": YESTERDAY_ROWS},
    )
    result = sentiment_module.compute_sentiment(DAY)

    assert result["seal_rate"] == 100.0
    assert "broken_board" in result["missing"]


def test_narrative_metrics_is_flat_read_only_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(
        monkeypatch,
        {
            "limit_pool": LIMIT_ROWS,
            "broken_board": BROKEN_ROWS,
            "yesterday_limit_up": YESTERDAY_ROWS,
        },
    )
    snapshot = sentiment_module.narrative_metrics(sentiment_module.compute_sentiment(DAY))
    assert set(snapshot) == {
        "temperature",
        "promotion_rate",
        "seal_rate",
        "limit_up_count",
        "broken_count",
        "yesterday_limit_up_count",
        "max_board",
        "gap",
        "gap_levels",
        "missing",
    }
    # 快照里没有原始行情记录，AI 看不到个股明细
    assert not any(
        isinstance(value, list) and value and isinstance(value[0], dict)
        for value in snapshot.values()
    )
