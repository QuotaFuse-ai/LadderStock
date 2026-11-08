"""自选现价的新鲜度判定（QUOT-478）。

钉住 §5.1 的三档口径与「判不出就回 `None`」的诚实出口：

| 场景 | 期望 |
|---|---|
| 盘中 + 时点落在本次交易会话内 | `realtime` |
| 收盘后 / 周末拿到上一交易日的收盘快照 | `archive` |
| 明确走缓存回放（上游 cache_hit / 本地回放） | `cached` |
| 判不出（停牌股、没取到价、时点解析不了、未来时点） | `None` |

外加 `fetch_prices` 的缓存证据管道：信封 `freshness=="cached"` 且该批确实贡献了读数
→ `PriceStrip.cache_replay` 为 `True`；取了个空批次不算（页面上没有一条价来自那条路）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from ladderstock.core.models import LadderEnvelope
from ladderstock.core.watch_prices import (
    DATASET,
    PriceStrip,
    fetch_prices,
    judge_freshness,
)
from ladderstock.data.provider_registry import FetchOutcome

TZ = timezone(timedelta(hours=8))

#: 2026-11-04 是周三（交易日的基准）
WED = datetime(2026, 11, 4, 10, 50, tzinfo=TZ)
#: 2026-11-06 是周五，2026-11-08 是周日，2026-11-09 是下一交易日（周一）
FRI_CLOSE = "2026-11-06T16:14:48"
SAT = datetime(2026, 11, 7, 11, 0, tzinfo=TZ)
SUN = datetime(2026, 11, 8, 17, 2, tzinfo=TZ)
MON = datetime(2026, 11, 9, 8, 0, tzinfo=TZ)


# --------------------------------------------------------------------- 判定


def test_intraday_same_session_is_realtime() -> None:
    """盘中 + 时点在本次会话内（同一自然日、不早于开盘）→ `realtime`。"""
    verdict = judge_freshness("2026-11-04T10:42:21", priced=True, now=WED)
    assert verdict == "realtime"


def test_intraday_opening_minute_is_realtime() -> None:
    """开盘那一分钟也是会话内（左闭右开）。"""
    now = datetime(2026, 11, 4, 9, 30, 30, tzinfo=TZ)
    assert judge_freshness("2026-11-04T09:30:05", priced=True, now=now) == "realtime"


def test_after_close_snapshot_is_archive() -> None:
    """收盘后拿到同日的收盘快照 → `archive`（不是 `realtime`）。"""
    now = datetime(2026, 11, 4, 16, 14, tzinfo=TZ)
    assert judge_freshness("2026-11-04T16:14:48", priced=True, now=now) == "archive"


def test_previous_day_snapshot_is_archive() -> None:
    """次日开盘前读到上一交易日的收盘快照 → `archive`。"""
    now = MON
    assert judge_freshness(FRI_CLOSE, priced=True, now=now) == "archive"


def test_cache_replay_in_session_is_cached() -> None:
    """明确的缓存回放证据 + 盘中 → `cached`，不因为时点在会话内就标实时。"""
    verdict = judge_freshness(
        "2026-11-04T10:42:21", priced=True, cache_replay=True, now=WED
    )
    assert verdict == "cached"


def test_archive_is_not_overridden_by_cache_evidence() -> None:
    """非交易时段的过去读数判 `archive`：缓存只是递送路径，不改读数性质。"""
    verdict = judge_freshness(FRI_CLOSE, priced=True, cache_replay=True, now=MON)
    assert verdict == "archive"


def test_undecidable_is_none() -> None:
    """盘中但时点属于上一会话（停牌股）→ 判不出，回 `None` 而不是猜。"""
    verdict = judge_freshness(
        "2026-11-03T15:00:00", priced=True, now=WED
    )
    assert verdict is None


def test_no_price_is_none() -> None:
    """没取到价 → `None`（不给默认值 `realtime`）。"""
    assert judge_freshness("2026-11-04T10:42:21", priced=False, now=WED) is None


def test_missing_or_garbage_timestamp_is_none() -> None:
    """时点缺失 / 解析不了 → `None`。"""
    assert judge_freshness(None, priced=True, now=WED) is None
    assert judge_freshness("", priced=True, now=WED) is None
    assert judge_freshness("not-a-timestamp", priced=True, now=WED) is None


def test_future_timestamp_is_none() -> None:
    """未来时点（超出时钟容忍）→ 判不出，不给 `realtime`。"""
    now = datetime(2026, 11, 4, 9, 35, tzinfo=TZ)
    assert judge_freshness("2026-11-04T10:42:21", priced=True, now=now) is None


def test_small_clock_skew_is_tolerated() -> None:
    """peer 时钟略快（5 分钟内）不算未来数据，仍按会话内读实时。"""
    now = datetime(2026, 11, 4, 10, 40, tzinfo=TZ)
    assert judge_freshness("2026-11-04T10:42:21", priced=True, now=now) == "realtime"


# --------------------------------------------------------------------- 周末


@pytest.mark.parametrize("now", [SAT, SUN])
def test_weekend_reads_previous_close_as_archive(now: datetime) -> None:
    """周末 A 股不开市：周五收盘快照读「存档」，不因时刻落在上午窗口就判盘中。"""
    assert judge_freshness(FRI_CLOSE, priced=True, now=now) == "archive"


@pytest.mark.parametrize("now", [SAT, SUN])
def test_weekend_cache_evidence_still_reads_archive(now: datetime) -> None:
    """周末即便取数路径是缓存回放，读到的仍是周五收盘的存档。"""
    verdict = judge_freshness(FRI_CLOSE, priced=True, cache_replay=True, now=now)
    assert verdict == "archive"


def test_weekend_no_reading_is_none() -> None:
    """周末没有读数 → `None`，不因为「周末大概是上一交易日收盘」就补一个 `archive`。"""
    assert judge_freshness(None, priced=True, now=SUN) is None


# ------------------------------------------------------- fetch_prices 证据管道


class _StubRegistry:
    """只回一个固定信封的最小 registry。"""

    def __init__(self, envelope: LadderEnvelope) -> None:
        self._envelope = envelope
        self.calls: list[dict[str, Any]] = []

    def fetch_live(self, dataset: str, *, params: dict[str, Any]) -> FetchOutcome:
        self.calls.append({"dataset": dataset, "params": params})
        return FetchOutcome(
            envelope=self._envelope,
            source="stub",
            used_fallback=False,
            from_cache=self._envelope.freshness == "cached",
        )


def _envelope(*, freshness: str, rows: list[dict[str, Any]]) -> LadderEnvelope:
    return LadderEnvelope(
        domain="review",
        topic="watch_prices",
        data_as_of=None,
        payload={"records": rows},
        source="stub",
        source_detail="stub",
        fetched_at=datetime(2026, 11, 4, 10, 50, tzinfo=TZ),
        freshness=freshness,
        confidence="medium",
        source_status="ok",
    )


def _row(code: str, stamp: str = "20261104104221") -> dict[str, Any]:
    return {
        "code": code,
        "name": f"自选{code}",
        "market": "sz",
        "price": 12.34,
        "change": 0.2,
        "change_pct": 1.6,
        "prev_close": 12.14,
        "timestamp": stamp,
    }


def test_fetch_prices_flags_cache_replay_when_envelope_is_cached() -> None:
    """信封 `freshness=="cached"` 且该批取到了价 → `cache_replay` 坐实。"""
    registry = _StubRegistry(_envelope(freshness="cached", rows=[_row("000530")]))
    strip = fetch_prices(registry, ["000530"])

    assert isinstance(strip, PriceStrip)
    assert strip.cache_replay is True
    assert strip.status == "ok"
    assert registry.calls[0]["dataset"] == DATASET


def test_fetch_prices_does_not_flag_cache_replay_for_live_envelope() -> None:
    """实时信封 → 不带缓存证据。"""
    registry = _StubRegistry(_envelope(freshness="realtime", rows=[_row("000530")]))
    strip = fetch_prices(registry, ["000530"])
    assert strip.cache_replay is False


def test_fetch_prices_empty_batch_is_not_cache_evidence() -> None:
    """缓存信封但该批没取到任何价 → 不算缓存证据（页面上没有一条价来自那条路）。"""
    registry = _StubRegistry(_envelope(freshness="cached", rows=[]))
    strip = fetch_prices(registry, ["000530"])

    assert strip.cache_replay is False
    assert strip.status == "missing"
