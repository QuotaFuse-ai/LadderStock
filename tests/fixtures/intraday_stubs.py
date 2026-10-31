"""盘中快照用例的离线注册表桩（QUOT-286）。

只实现快照用到的四个读取口（`yesterday_limit_up` / `spot_quote` / `sentiment` /
`limit_pool`），响应包成真实的 `LadderEnvelope` + `FetchOutcome`，不发任何网络请求。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ladderstock.core.models import LadderEnvelope
from ladderstock.data.provider_registry import FetchOutcome

FIXTURES = Path(__file__).resolve().parent / "peer_responses"


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def quote_row(
    code: str,
    *,
    price: float,
    prev_close: float,
    open_price: float,
    change_pct: float,
    limit_up: float,
    timestamp: str,
    name: str = "",
) -> dict[str, Any]:
    return {
        "code": code,
        "name": name,
        "price": price,
        "prev_close": prev_close,
        "open": open_price,
        "change_pct": change_pct,
        "limit_up": limit_up,
        "timestamp": timestamp,
        "is_stale": False,
    }


def yesterday_records() -> list[dict[str, Any]]:
    """三只昨日涨停股：两只首板 + 一只四板（与 fixture 同代码）。"""
    return [
        {"code": "000530", "name": "冰山冷热", "y_streak": 1, "limit_up_days": 2},
        {"code": "000701", "name": "厦门信达", "y_streak": 1, "limit_up_days": 2},
        {"code": "001219", "name": "青岛食品", "y_streak": 4, "limit_up_days": 5},
    ]


def quote_records(day: date = date(2026, 10, 30), stamp: str = "100003") -> list[dict[str, Any]]:
    """三只昨日涨停股在 `day` 的报价（默认 10:00:03）。"""
    prefix = day.strftime("%Y%m%d")
    return [
        quote_row(
            "000530",
            name="冰山冷热",
            price=7.92,
            prev_close=8.23,
            open_price=8.02,
            change_pct=-3.77,
            limit_up=9.05,
            timestamp=f"{prefix}{stamp}",
        ),
        quote_row(
            "000701",
            name="厦门信达",
            price=5.68,
            prev_close=5.62,
            open_price=5.60,
            change_pct=1.07,
            limit_up=6.18,
            timestamp=f"{prefix}{stamp}",
        ),
        quote_row(
            "001219",
            name="青岛食品",
            price=19.94,
            prev_close=18.13,
            open_price=18.60,
            change_pct=9.98,
            limit_up=19.94,
            timestamp=f"{prefix}{stamp}",
        ),
    ]


def sentiment_records(day: date = date(2026, 10, 30)) -> dict[str, Any]:
    return {
        "date": day.strftime("%Y%m%d"),
        "limit_up": 47,
        "broken": 31,
        "limit_down": 82,
        "seal_rate": 0.6026,
        "broken_rate": 0.3974,
        "max_streak": 5,
        "leaders": [{"code": "001219", "name": "青岛食品", "streak": 5}],
    }


def limit_pool_records(day: date = date(2026, 10, 30)) -> list[dict[str, Any]]:
    """今日涨停池（适配器展开后的行形状，`streak` 为今日连板数）。"""
    return [
        {"code": "603703", "name": "盛洋科技", "streak": 2, "trade_date": day.isoformat()},
        {"code": "600261", "name": "阳光照明", "streak": 2, "trade_date": day.isoformat()},
        {"code": "603131", "name": "上海沪工", "streak": 3, "trade_date": day.isoformat()},
    ]


def default_payloads(day: date = date(2026, 10, 30)) -> dict[str, Any]:
    return {
        "yesterday_limit_up": yesterday_records(),
        "spot_quote": quote_records(day),
        "sentiment": sentiment_records(day),
        "limit_pool": limit_pool_records(day),
    }


@dataclass
class StubRegistry:
    """按数据集返回固定载荷；`errors` 里的数据集直接抛异常（模拟 peer 不可用）。"""

    payloads: dict[str, Any] = field(default_factory=default_payloads)
    day: date = date(2026, 10, 30)
    errors: dict[str, Exception] = field(default_factory=dict)
    used_fallback: bool = False
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def _outcome(self, dataset: str, params: dict[str, Any] | None) -> FetchOutcome:
        self.calls.append((dataset, dict(params or {})))
        failure = self.errors.get(dataset)
        if failure is not None:
            raise failure
        if dataset not in self.payloads:
            raise LookupError(f"桩里没有 {dataset}")
        envelope = LadderEnvelope(
            domain="review",
            topic=dataset,
            data_as_of=self.day,
            payload={
                "dataset": dataset,
                "tool": f"{dataset}_stub",
                "records": self.payloads[dataset],
            },
            source="tickhub_cn",
            source_detail=f"{dataset}_stub@stub",
            fetched_at=datetime.now().astimezone(),
            freshness="realtime",
            confidence="high",
            source_status="ok",
        )
        return FetchOutcome(
            envelope=envelope,
            source="stub",
            used_fallback=self.used_fallback,
            from_cache=False,
            trail=[f"{dataset}_stub@stub"],
        )

    def fetch(
        self,
        dataset: str,
        *,
        trade_date: date | None = None,
        params: dict[str, Any] | None = None,
        allow_cache: bool = True,
    ) -> FetchOutcome:
        return self._outcome(dataset, params)

    def fetch_live(
        self,
        dataset: str,
        *,
        trade_date: date | None = None,
        params: dict[str, Any] | None = None,
        allow_cache: bool = True,
    ) -> FetchOutcome:
        return self._outcome(dataset, params)

    def datasets_called(self) -> list[str]:
        return [name for name, _ in self.calls]
