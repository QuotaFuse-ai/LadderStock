"""`GET /api/indices` 端到端 fail-fast（QUOT-571）。

`/api/indices` 的验收口径是「peer 挂了也要在 3 秒内回 200 的降级结果」，所以光在
`core/indices.py` 单测里钉机制不够——这里从 HTTP 面走完整链路（RadarPlane → 注册表 →
传输桩），量真实耗时：

| 场景 | 期望 |
|---|---|
| 美股 peer 拒绝连接 | < 3 秒回 200；美/港四格 missing + 原因，A 股四格照出 |
| 美股 peer 一直不回（timeout 配成 10 秒） | 同上：3 秒内回来，不等上游超时 |

桩里的 `timeout_sec=10` 是刻意留的「慢上游」配置：若哪一层 fail-fast 退化成串行等
超时，这里的耗时断言就会先红。
"""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from fixtures.fake_peers import (
    SOURCES_DOC,
    SYNC_DOC,
    FakeTransport,
    make_registry,
    tickhub_payload,
)

from ladderstock.data.provider_registry import SourceUnavailable
from ladderstock.web.radar import RadarPlane
from ladderstock.web.server import create_app

LOOPBACK = ("127.0.0.1", 54321)

#: 前端横幅阈值：超过它首页就会挂「部分数据源不可用」
BANNER_THRESHOLD_SEC = 3.0

#: 慢上游的 timeout 配置——比 fail-fast 预算大一个量级，专门用来暴露「串行等超时」
SLOW_TIMEOUT_SEC = 10

EXPECTED_CN = ["sh000001", "sh000300", "sz399001", "sz399006"]
EXPECTED_US_HK = ["DJI", "IXIC", "HSI", "HSTECH"]

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "peer_responses" / "index_quote.json"


def _sources_doc() -> dict[str, Any]:
    sources = copy.deepcopy(SOURCES_DOC)
    sources["sources"].extend(
        [
            {
                "id": "tickhub_index_quote",
                "dataset": "index_quote",
                "via": "tickhub",
                "tool": "index_quote",
                "primary": True,
                "fallback": [],
                "timeout_sec": SLOW_TIMEOUT_SEC,
                "retries": 0,
                "cooldown_sec": 60,
            },
            {
                "id": "tickhub_us_index_quote",
                "dataset": "index_quote_us",
                "via": "tickhub_us",
                "tool": "th_us_get_quote",
                "primary": True,
                "fallback": [],
                "timeout_sec": SLOW_TIMEOUT_SEC,
                "retries": 0,
                "cooldown_sec": 60,
            },
            {
                "id": "tickhub_hk_index_quote",
                "dataset": "index_quote_hk",
                "via": "tickhub_us",
                "tool": "th_us_get_quote",
                "primary": True,
                "fallback": [],
                "timeout_sec": SLOW_TIMEOUT_SEC,
                "retries": 0,
                "cooldown_sec": 60,
            },
        ]
    )
    return sources


def _client(tmp_path: Path, *, us_error: Exception) -> tuple[TestClient, FakeTransport]:
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))["data"]
    handlers = {
        "index_quote": lambda params: tickhub_payload(
            rows, source="tencent", fetched_at="2026-11-03T22:47:19+08:00"
        )
    }
    transport = FakeTransport(
        handlers=handlers,
        errors={"th_us_get_quote": us_error},
        tools=["index_quote", "th_us_get_quote"],
    )
    registry = make_registry(
        transport, tmp_path, sources=_sources_doc(), sync=copy.deepcopy(SYNC_DOC)
    )
    plane = RadarPlane(data_root=tmp_path, registry=registry)
    app = create_app(data_root=tmp_path, dist_dir=tmp_path / "no-dist", radar_plane=plane)
    return TestClient(app, client=LOOPBACK), transport


def _wrapped(nested: type[BaseException]) -> SourceUnavailable:
    """复刻 `HttpMcpTransport._post` 的包装：httpx 异常 → `SourceUnavailable(transport_nested=…)`。

    生产里裸 `TimeoutError` / `ConnectionError` 到不了调用方——`_call_source` 只认这个
    包出来的形态。桩要抛这一形态，端到端才走的是真实链路。
    """
    return SourceUnavailable(
        f"tickhub_us 不可达（{nested.__name__}，http://127.0.0.1:8002/mcp）",
        transport_nested=nested,
    )


@pytest.mark.parametrize(
    "us_error",
    [
        _wrapped(httpx.ConnectError),
        _wrapped(httpx.ReadTimeout),
        _wrapped(httpx.ConnectTimeout),
    ],
    ids=["refused", "silent", "connect_timeout"],
)
def test_us_peer_down_degrades_within_three_seconds(tmp_path: Path, us_error: Exception) -> None:
    client, transport = _client(tmp_path, us_error=us_error)

    started = time.monotonic()
    response = client.get("/api/indices")
    elapsed = time.monotonic() - started

    assert response.status_code == 200
    assert elapsed < BANNER_THRESHOLD_SEC, f"/api/indices 用了 {elapsed:.2f} 秒"
    payload = response.json()["payload"]
    assert payload["data_source_status"] == "partial"
    cells = {cell["code"]: cell for cell in payload["cells"]}
    assert len(cells) == len(EXPECTED_CN) + len(EXPECTED_US_HK)
    for code in EXPECTED_CN:
        assert cells[code]["available"] is True, code
        assert cells[code]["price"] is not None, code
    for code in EXPECTED_US_HK:
        assert cells[code]["available"] is False, code
        assert cells[code]["reason"], code
        assert cells[code]["price"] is None, code
    # 块断路器：第一格就断（生产形态），后面三格不再逐格等
    assert 1 <= len(transport.tools_called) - 1 < len(EXPECTED_US_HK)


def test_health_bus_short_circuits_at_endpoint(tmp_path: Path) -> None:
    """`RadarPlane(health_bus=bus)` 接上后，已确认宕机的 peer 在 `/api/indices` 零请求。"""
    from ladderstock.supervisor.health_events import EVENT_CRASH, HealthEventBus

    bus = HealthEventBus()
    bus.publish(
        module="tickhub_us", event=EVENT_CRASH, health="down", state="down", reason="进程退出"
    )
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))["data"]
    transport = FakeTransport(
        handlers={
            "index_quote": lambda params: tickhub_payload(
                rows, source="tencent", fetched_at="2026-11-03T22:47:19+08:00"
            ),
            "th_us_get_quote": lambda params: pytest.fail("短路后不该再打 tickhub_us"),
        },
        tools=["index_quote", "th_us_get_quote"],
    )
    registry = make_registry(
        transport, tmp_path, sources=_sources_doc(), sync=copy.deepcopy(SYNC_DOC)
    )
    plane = RadarPlane(data_root=tmp_path, registry=registry, health_bus=bus)
    app = create_app(data_root=tmp_path, dist_dir=tmp_path / "no-dist", radar_plane=plane)
    client = TestClient(app, client=LOOPBACK)

    response = client.get("/api/indices")

    assert response.status_code == 200
    payload = response.json()["payload"]
    cells = {cell["code"]: cell for cell in payload["cells"]}
    for code in EXPECTED_US_HK:
        assert cells[code]["available"] is False, code
        assert cells[code]["reason"], code
    for code in EXPECTED_CN:
        assert cells[code]["available"] is True, code
    # 美股 / 港股一次 `th_us_get_quote` 都没发
    assert "th_us_get_quote" not in transport.tools_called


class _EngineWithBus:
    """生产装配形态：自愈引擎挂着真事件总线，`create_app` 要把它接到内部雷达面。"""

    def __init__(self, bus: Any) -> None:
        self.event_bus = bus

    def snapshot(self) -> dict[str, Any]:
        return {"ok": True, "engine": None, "modules": []}

    def start(self) -> None:
        pass

    def stop(self, timeout: float | None = None) -> None:
        pass

    def reset(self, module: str) -> dict[str, Any] | None:
        return None


def test_create_app_wires_engine_bus_into_internal_radar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """不注入 radar_plane 时（生产装配），`create_app` 把引擎事件总线接到内部雷达面，
    已确认宕机的 peer 在 `/api/indices` 零请求——短路在端点真实生效。"""
    from ladderstock.supervisor.health_events import EVENT_CRASH, HealthEventBus
    from ladderstock.web import server as server_module

    bus = HealthEventBus()
    bus.publish(
        module="tickhub_us", event=EVENT_CRASH, health="down", state="down", reason="进程退出"
    )
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))["data"]
    transport = FakeTransport(
        handlers={
            "index_quote": lambda params: tickhub_payload(
                rows, source="tencent", fetched_at="2026-11-03T22:47:19+08:00"
            ),
            "th_us_get_quote": lambda params: pytest.fail("短路后不该再打 tickhub_us"),
        },
        tools=["index_quote", "th_us_get_quote"],
    )
    registry = make_registry(
        transport, tmp_path, sources=_sources_doc(), sync=copy.deepcopy(SYNC_DOC)
    )
    # 不传 radar_plane：走生产分支，雷达面由 create_app 内部建。用 monkeypatch 抓住
    # 内部实例，注入测试注册表（否则它按 config/sources.yaml 现建、打真实网络）。
    built: dict[str, Any] = {}
    real_plane = server_module.RadarPlane

    def _factory(*args: Any, **kwargs: Any) -> Any:
        kwargs["registry"] = registry
        plane = real_plane(*args, **kwargs)
        built["plane"] = plane
        return plane

    monkeypatch.setattr(server_module, "RadarPlane", _factory)
    app = create_app(
        data_root=tmp_path,
        dist_dir=tmp_path / "no-dist",
        supervisor_engine=_EngineWithBus(bus),
    )
    client = TestClient(app, client=LOOPBACK)

    response = client.get("/api/indices")

    assert response.status_code == 200
    payload = response.json()["payload"]
    cells = {cell["code"]: cell for cell in payload["cells"]}
    for code in EXPECTED_US_HK:
        assert cells[code]["available"] is False, code
    assert "th_us_get_quote" not in transport.tools_called
    # create_app 把引擎总线接到了内部雷达面
    assert built["plane"]._health_bus is bus
