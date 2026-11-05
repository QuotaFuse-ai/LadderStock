"""QUOT-410：`GET /api/research/{code}/kline` 的端点测试。

`period / adjust / limit` 是**运行时参数**：原样透传给 `tencent_kline`（假 registry
只记录收到的调用），不沿用 sync 写死的 `daily + qfq` 口径。取不到数（peer 不可达 /
超时 / 返回空）是显式空态：`available=false` + `data_source_status="missing"` +
`reason`，200 正常响应，不编造、不补零——这些用例不联网、不构造真实控制面。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.core.models import Freshness, LadderEnvelope, SourceStatus
from ladderstock.data.provider_registry import SourceUnavailable
from ladderstock.web.research import KLINE_LIMIT_DEFAULT, KLINE_LIMIT_MAX, ResearchPlane
from ladderstock.web.server import create_app

CODE = "600000"
BASE_URL = "http://127.0.0.1:5950"
LOOPBACK = ("127.0.0.1", 54321)
KLINE_URL = f"/api/research/{CODE}/kline"


class _NullControlPlane:
    """占位：这些用例只关心 K 线端点，不构造真实控制面。"""

    peers: dict[str, Any] = {}


class _FakeRegistry:
    """只记录调用参数的假 registry：`fetch_live` 形状与真件一致，不联网。"""

    def __init__(
        self,
        envelope: LadderEnvelope | None = None,
        exc: Exception | None = None,
    ) -> None:
        self.envelope = envelope
        self.exc = exc
        self.calls: list[dict[str, Any]] = []

    def fetch_live(
        self,
        dataset: str,
        *,
        trade_date: Any = None,
        params: dict[str, Any] | None = None,
        allow_cache: bool = True,
    ) -> Any:
        self.calls.append(
            {
                "dataset": dataset,
                "params": dict(params or {}),
                "allow_cache": allow_cache,
            }
        )
        if self.exc is not None:
            raise self.exc
        return SimpleNamespace(envelope=self.envelope)


def _bar(day: str, close: float, amount: float | None = None) -> dict[str, Any]:
    """一行上游记录（`klines` 形状归一化后）：date 已改键为 trade_date，amount 可空。"""
    return {
        "code": "600000",
        "period": "daily",
        "adjust": "qfq",
        "trade_date": day,
        "open": close - 0.2,
        "high": close + 0.3,
        "low": close - 0.4,
        "close": close,
        "volume": 75000.0 + close,
        "amount": amount,
    }


def _envelope(
    records: list[dict[str, Any]],
    *,
    source_status: SourceStatus = "ok",
    freshness: Freshness = "realtime",
) -> LadderEnvelope:
    return LadderEnvelope(
        domain="review",
        topic="kline_chart",
        event_id="trace-kline-1",
        data_as_of=date(2026, 10, 30),
        payload={"dataset": "kline_chart", "tool": "tencent_kline", "records": records},
        source="tickhub_cn",
        source_detail="tencent_kline@tencent",
        fetched_at=datetime(2026, 10, 30, 19, 54, 41, tzinfo=UTC),
        freshness=freshness,
        confidence="high",
        source_status=source_status,
    )


def _client(root: Path, registry: _FakeRegistry) -> TestClient:
    plane = ResearchPlane(data_root=root, registry=registry)
    app = create_app(
        data_root=root,
        dist_dir=root / "no-dist",
        control_plane=_NullControlPlane(),
        research_plane=plane,
    )
    return TestClient(app, base_url=BASE_URL, client=LOOPBACK)


def _ok_registry(root: Path, **kwargs: Any) -> tuple[TestClient, _FakeRegistry]:
    registry = _FakeRegistry(
        _envelope(
            [
                _bar("2026-10-29", 10.5, 76010.5),
                _bar("2026-10-30", 10.8, amount=None),
            ],
            **kwargs,
        )
    )
    return _client(root, registry), registry


# --------------------------------------------------------------------------- #
# 1. 参数透传：三档周期 × 三档复权 × limit 都是运行时参数
# --------------------------------------------------------------------------- #

def test_kline_passes_default_runtime_params_through(tmp_path: Path) -> None:
    client, registry = _ok_registry(tmp_path)

    response = client.get(KLINE_URL)

    assert response.status_code == 200
    call = registry.calls[0]
    assert call["dataset"] == "kline_chart"
    assert call["params"] == {
        "code": "600000",
        "period": "daily",
        "adjust": "qfq",
        "limit": KLINE_LIMIT_DEFAULT,
    }
    assert call["allow_cache"] is False, "K 线图端点现取现回，不走缓存回放"


@pytest.mark.parametrize(
    ("period", "adjust"),
    [
        ("daily", "qfq"),
        ("daily", "hfq"),
        ("daily", "none"),
        ("weekly", "qfq"),
        ("weekly", "hfq"),
        ("weekly", "none"),
        ("monthly", "qfq"),
        ("monthly", "hfq"),
        ("monthly", "none"),
    ],
)
def test_kline_period_adjust_combinations_pass_through(
    tmp_path: Path, period: str, adjust: str
) -> None:
    client, registry = _ok_registry(tmp_path)

    response = client.get(
        KLINE_URL, params={"period": period, "adjust": adjust}
    )

    assert response.status_code == 200
    call = registry.calls[0]
    assert call["params"]["period"] == period, "period 是运行时参数，逐请求透传"
    assert call["params"]["adjust"] == adjust, "adjust 是运行时参数，逐请求透传"
    payload = response.json()["payload"]
    assert payload["period"] == period and payload["adjust"] == adjust


def test_kline_limit_is_passed_through_and_echoed(tmp_path: Path) -> None:
    client, registry = _ok_registry(tmp_path)

    response = client.get(KLINE_URL, params={"limit": 25})

    assert response.status_code == 200
    assert registry.calls[0]["params"]["limit"] == 25
    assert response.json()["payload"]["limit"] == 25


def test_kline_limit_bounds_are_enforced(tmp_path: Path) -> None:
    client, _ = _ok_registry(tmp_path)

    assert client.get(KLINE_URL, params={"limit": 0}).status_code == 422
    assert client.get(KLINE_URL, params={"limit": KLINE_LIMIT_MAX + 1}).status_code == 422
    assert client.get(KLINE_URL, params={"limit": KLINE_LIMIT_MAX}).status_code == 200


# --------------------------------------------------------------------------- #
# 2. 参数校验：非法 4xx（SPEC §2.4），不发给上游
# --------------------------------------------------------------------------- #

def test_kline_invalid_period_is_422(tmp_path: Path) -> None:
    client, registry = _ok_registry(tmp_path)

    response = client.get(KLINE_URL, params={"period": "hourly"})

    assert response.status_code == 422
    assert "period" in response.json()["detail"]
    assert registry.calls == [], "参数校验失败不发给上游"


def test_kline_invalid_adjust_is_422(tmp_path: Path) -> None:
    client, registry = _ok_registry(tmp_path)

    response = client.get(KLINE_URL, params={"adjust": "forward"})

    assert response.status_code == 422
    assert "adjust" in response.json()["detail"]
    assert registry.calls == []


@pytest.mark.parametrize("raw", ["0", "-1", "abc", "2.5", str(KLINE_LIMIT_MAX + 1)])
def test_kline_invalid_limit_is_422(tmp_path: Path, raw: str) -> None:
    client, registry = _ok_registry(tmp_path)

    response = client.get(KLINE_URL, params={"limit": raw})

    assert response.status_code == 422
    # 一部分被 FastAPI 的 Query 校验拦下（detail 是结构化数组），
    # 一部分落到本层 parse_limit（detail 是文案），两种都要点名 limit
    assert "limit" in response.text
    assert registry.calls == []


def test_kline_unknown_code_is_404(tmp_path: Path) -> None:
    client, _ = _ok_registry(tmp_path)

    response = client.get("/api/research/999999/kline")

    assert response.status_code == 404
    assert "未知证券代码" in response.json()["detail"]


def test_kline_malformed_code_is_422(tmp_path: Path) -> None:
    client, _ = _ok_registry(tmp_path)

    response = client.get("/api/research/60000/kline")

    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# 3. 信封形状：四段 + bars 投影（amount null 如实透传，不补 0）
# --------------------------------------------------------------------------- #

def test_kline_envelope_shape_and_bar_projection(tmp_path: Path) -> None:
    client, _ = _ok_registry(tmp_path)

    response = client.get(KLINE_URL)

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"payload", "data_source_status", "data_as_of", "meta"}
    assert body["data_source_status"] == "ok"
    assert body["data_as_of"] == "2026-10-30"
    assert body["meta"]["topic"] == "research_kline"
    assert body["meta"]["freshness"] == "realtime"

    payload = body["payload"]
    assert payload["code"] == CODE
    assert payload["available"] is True
    assert payload["reason"] is None
    assert payload["count"] == 2
    assert payload["source_detail"] == "tencent_kline@tencent"
    assert payload["data_source_status"] == "ok"

    first, last = payload["bars"][0], payload["bars"][-1]
    assert set(first) == {"date", "open", "high", "low", "close", "volume", "amount"}
    assert first["date"] == "2026-10-29" and first["close"] == 10.5
    assert first["amount"] == 76010.5
    assert last["date"] == "2026-10-30"
    assert last["amount"] is None, "amount 上游为 null 就透传 null，不补 0"


def test_kline_works_without_any_research_artifacts(tmp_path: Path) -> None:
    """本单核心：K 线图端点不依赖六段产物，空工作区也直接现取回数。"""
    assert not (tmp_path / "research").exists()
    client, _ = _ok_registry(tmp_path)

    response = client.get(KLINE_URL)

    assert response.status_code == 200
    assert response.json()["payload"]["count"] == 2


# --------------------------------------------------------------------------- #
# 4. 空态与降级：peer 不可达 / 超时 / 返回空 → 200 + missing，不编造
# --------------------------------------------------------------------------- #

def test_kline_source_unavailable_is_200_missing(tmp_path: Path) -> None:
    registry = _FakeRegistry(exc=SourceUnavailable("peer tickhub 不可达（连接被拒）"))
    client = _client(tmp_path, registry)

    response = client.get(KLINE_URL)

    assert response.status_code == 200, "取不到数是空态，不是 500"
    body = response.json()
    assert body["data_source_status"] == "missing"
    assert body["data_as_of"] is None, "没有可信数据时 data_as_of 不编一个出来"
    payload = body["payload"]
    assert payload["available"] is False
    assert payload["bars"] == [] and payload["count"] == 0
    assert payload["reason"] and "不可达" in payload["reason"]
    assert payload["data_source_status"] == "missing"


def test_kline_empty_upstream_return_is_200_missing(tmp_path: Path) -> None:
    registry = _FakeRegistry(_envelope([], freshness="cached"))
    client = _client(tmp_path, registry)

    response = client.get(KLINE_URL)

    assert response.status_code == 200
    payload = response.json()["payload"]
    assert response.json()["data_source_status"] == "missing"
    assert payload["available"] is False and payload["bars"] == []
    assert "没有返回任何 K 线" in (payload["reason"] or "")
    assert payload["reason"] and "period=daily" in (payload["reason"] or "")


def test_kline_degraded_when_upstream_source_status_is_not_ok(tmp_path: Path) -> None:
    client, _ = _ok_registry(tmp_path, source_status="degraded")

    response = client.get(KLINE_URL)

    assert response.status_code == 200
    body = response.json()
    assert body["data_source_status"] == "degraded"
    assert body["payload"]["data_source_status"] == "degraded"
    assert body["payload"]["available"] is True, "降级仍有数据，不装成空态"
