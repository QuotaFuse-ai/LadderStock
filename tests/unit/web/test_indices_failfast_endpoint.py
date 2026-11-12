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

import pytest
from fastapi.testclient import TestClient
from fixtures.fake_peers import (
    SOURCES_DOC,
    SYNC_DOC,
    FakeTransport,
    make_registry,
    tickhub_payload,
)

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


@pytest.mark.parametrize(
    "us_error",
    [ConnectionError("tickhub_us 拒绝连接"), TimeoutError("tickhub_us 不回包")],
    ids=["refused", "silent"],
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
    # 块断路器：第一格就断，后面三格不再逐格等
    assert 1 <= len(transport.tools_called) - 1 < len(EXPECTED_US_HK)
