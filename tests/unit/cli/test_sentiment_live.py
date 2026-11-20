"""`_run_sentiment_live` 的接线：把「此刻的涨停 / 炸板」钉成当日实时快照。

为什么单独测这条：`limit_up_pool` 只给最近一个已收盘交易日，15:35 之前当天没有
涨停池归档，情绪温度页只能靠这份实时快照。这里钉住三件事——时段外的空转、
「不是今天的数据不许落盘」的守卫、以及落盘后 `compute_sentiment` 真能读到。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from ladderstock.cli.schedule import _run_sentiment_live

CST = ZoneInfo("Asia/Shanghai")
JOB = {"id": "test-sentiment-live", "name": "实时情绪", "kind": "sentiment_live"}


def _registry(records: dict[str, Any], source: str = "tickhub_cn") -> Any:
    class _Outcome:
        envelope = SimpleNamespace(payload={"records": dict(records)}, source=source)

    class _Registry:
        def __init__(self, root: Any = None) -> None:
            self.root = root

        def fetch_live(self, *_args: Any, **_kwargs: Any) -> Any:
            return _Outcome()

    return _Registry


def test_writes_today_snapshot_inside_trading_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "ladderstock.data.provider_registry.ProviderRegistry",
        _registry(
            {
                "date": "20261117",
                "limit_up": 24,
                "broken": 10,
                "limit_down": 25,
                "seal_rate": 0.7059,
                "max_streak": 3,
            }
        ),
    )
    outcome = _run_sentiment_live(
        JOB,
        now=datetime(2026, 11, 17, 10, 30, tzinfo=CST),
        root=tmp_path,
    )

    assert outcome["status"] == "ok"
    assert outcome["records"]["limit_up"] == 24
    path = tmp_path / "live" / "sentiment" / "2026-11-17.json"
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["date"] == "2026-11-17"
    assert body["records"]["broken"] == 10
    assert body["fetched_at"].startswith("2026-11-17T10:30")


def test_refuses_snapshot_from_another_day(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """接口给的是昨天的读数就不许落盘——拿昨天的数当今天就是造假。"""
    monkeypatch.setattr(
        "ladderstock.data.provider_registry.ProviderRegistry",
        _registry({"date": "20261116", "limit_up": 32, "broken": 4}),
    )
    outcome = _run_sentiment_live(
        JOB,
        now=datetime(2026, 11, 17, 10, 30, tzinfo=CST),
        root=tmp_path,
    )

    assert outcome["status"] == "degraded"
    assert "20261116" in outcome["detail"]
    assert not (tmp_path / "live" / "sentiment" / "2026-11-17.json").exists()


def test_skips_outside_trading_window(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "ladderstock.data.provider_registry.ProviderRegistry",
        _registry({"date": "20261117", "limit_up": 24, "broken": 10}),
    )
    outcome = _run_sentiment_live(
        JOB,
        now=datetime(2026, 11, 17, 20, 30, tzinfo=CST),
        root=tmp_path,
    )

    assert outcome["status"] == "skipped"
    assert outcome["quiet"] is True
    assert not (tmp_path / "live" / "sentiment").exists()


def test_snapshot_feeds_compute_sentiment(tmp_path: Path) -> None:
    """落盘之后，情绪指标真能从这份快照读出来（盘中有数，不是空页）。"""
    from ladderstock.core import live_sentiment
    from ladderstock.core.sentiment import compute_sentiment

    live_sentiment.write_snapshot(
        "2026-11-17",
        {"date": "20261117", "limit_up": 24, "broken": 10, "max_streak": 3, "limit_down": 25},
        fetched_at=datetime(2026, 11, 17, 10, 30, tzinfo=CST),
        root=tmp_path,
    )
    result = compute_sentiment("2026-11-17", root=tmp_path)

    assert result["available"] is True
    assert result["mode"] == "live"
    assert result["seal_rate"] == 70.6
    assert result["promotion_rate"] is None
