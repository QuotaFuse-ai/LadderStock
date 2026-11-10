"""QUOT-539：`GET /api/research/{code}` 透传 StockSenate 白话结论（plain-v1）。

契约唯一依据是 QUOT-536 的交付评论：MCP `stock_senate_report_query` 的
`sections.plain` 原样放到状态载荷**顶层** `plain`，`disclaimer` 一并带上。
本文件锁四件事：

1. 有 plain 的当日报告 → 状态载荷逐字段原样带出（不重算、不评级）；
2. Senate 不可达 / 超时 / 旧快照无 plain → 照常 200，只是不带 `plain`；
3. 读取走既有 `council_memo` MCP 链路（`code` + `date`），不触发辩论；
4. 读取预算显式且可配置（`LDST_SENATE_PLAIN_TIMEOUT`，夹在 0.5–20s）。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fixtures.fake_peers import SOURCES_DOC, SYNC_DOC

from ladderstock.core.models import LadderEnvelope
from ladderstock.data.provider_registry import ProviderRegistry, SourceUnavailable
from ladderstock.research import checkpoint
from ladderstock.web.research import (
    SENATE_PLAIN_TIMEOUT_DEFAULT,
    SENATE_PLAIN_TIMEOUT_ENV,
    SENATE_PLAIN_TIMEOUT_MAX,
    SENATE_PLAIN_TIMEOUT_MIN,
    ResearchPlane,
    senate_plain_timeout,
)
from ladderstock.web.server import create_app

CODE = "600000"
DAY = "2026-10-30"
BASE_URL = "http://127.0.0.1:5950"
LOOPBACK = ("127.0.0.1", 54321)
STATUS_URL = f"/api/research/{CODE}"

DISCLAIMER = "研究结果，不构成投资建议，也不包含可执行价位、仓位或交易指令。"

#: QUOT-536 契约评论里的真实 600519 示例载荷（why / risks 各留一条）。
PLAIN_SAMPLE: dict[str, Any] = {
    "version": "plain-v1",
    "what": {
        "text": "研究团队认为暂时看不出明显偏向。多空理由大体互相抵消，团队没有给出方向性结论；"
        "对普通人来说，这通常意味着「先观察、不急着下判断」。这只是研究评级，"
        "代表团队的研究观点，不是买卖指令。",
        "evidence": [
            {
                "domain": "financial",
                "source": "local_json",
                "data_as_of": "2026-01-28",
                "detail": "financial/600519/2026-01-28.json",
            },
            {
                "domain": "dragon_tiger",
                "source": "local_json",
                "data_as_of": "2026-01-28",
                "detail": "dragon_tiger/600519/2026-01-28.json",
            },
        ],
        "neutral": False,
    },
    "why": [
        {
            "text": "基本面分析师：未明确。",
            "evidence": [
                {
                    "domain": "financial",
                    "source": "local_json",
                    "data_as_of": "2026-01-28",
                    "detail": "financial/600519/2026-01-28.json",
                }
            ],
            "neutral": False,
        }
    ],
    "risks": [
        {
            "text": "激进视角：研究结论为 偏空，需按该视角衡量可承受的不确定性。",
            "evidence": [
                {
                    "domain": "kline",
                    "source": "local_json",
                    "data_as_of": "2026-01-28",
                    "detail": "kline/600519/2026-01-28.json",
                }
            ],
            "neutral": False,
        }
    ],
    "confidence": 0.3836,
    "confidence_note": "整体置信度 0.38（0~1，越高表示系统越有把握，但它不是上涨概率）；"
    "该数值直接沿用投资经理终审结果，没有另行计算。",
    "glossary": [
        {
            "term": "研究评级",
            "note": "研究团队给出的综合意见档位，只代表研究观点，不是买卖指令。",
        }
    ],
    "risk_flags": ["hedged_language"],
    "degraded": True,
    "omitted_claims": 2,
    "redline_blocked": 1,
}


class _NullControlPlane:
    peers: dict[str, Any] = {}


class _FakeRegistry:
    """`fetch_live` 形状与真件一致：记录参数，按预设返回信封或抛异常。"""

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
        timeout: float | None = None,
    ) -> Any:
        self.calls.append(
            {
                "dataset": dataset,
                "trade_date": trade_date,
                "params": dict(params or {}),
                "allow_cache": allow_cache,
                "timeout": timeout,
            }
        )
        if self.exc is not None:
            raise self.exc
        return SimpleNamespace(envelope=self.envelope)


def _senate_envelope(
    plain: dict[str, Any] | None = None,
    *,
    disclaimer: str | None = DISCLAIMER,
) -> LadderEnvelope:
    """`council_memo` 适配器在 plain 存在时的信封形状。"""
    payload: dict[str, Any] = {
        "dataset": "council_memo",
        "tool": "stock_senate_report_query",
        "report": {},
    }
    if plain is not None:
        payload["plain"] = plain
        if disclaimer is not None:
            payload["disclaimer"] = disclaimer
    return LadderEnvelope(
        domain="review",
        topic="council_memo",
        event_id=None,
        data_as_of=date(2026, 10, 30),
        payload=payload,
        source="stock_senate",
        source_detail="stock_senate_report_query",
        fetched_at=datetime(2026, 10, 30, 20, 0, tzinfo=UTC),
        freshness="cached",
        confidence="medium",
        source_status="ok",
    )


def _seed(root: Path, *, code: str = CODE, day: str = DAY) -> None:
    """落一份「六段跑齐」的 checkpoint：状态接口只要求研究记录存在。"""
    document = checkpoint.fresh(code, day)
    for stage in checkpoint.STAGES:
        checkpoint.record(
            document,
            stage,
            status="ok",
            mode="structured",
            narrative=["…"],
            model="fake-model",
            degraded=False,
        )
    checkpoint.save(document, root)


def _client(root: Path, registry: Any) -> TestClient:
    plane = ResearchPlane(data_root=root, registry=registry)
    app = create_app(
        data_root=root,
        dist_dir=root / "no-dist",
        control_plane=_NullControlPlane(),
        research_plane=plane,
    )
    return TestClient(app, base_url=BASE_URL, client=LOOPBACK)


@pytest.fixture(autouse=True)
def _default_timeout_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SENATE_PLAIN_TIMEOUT_ENV, raising=False)


# --------------------------------------------------------------------------- #
# 1. 有 plain：原样透传
# --------------------------------------------------------------------------- #

def test_status_carries_plain_and_disclaimer_verbatim(tmp_path: Path) -> None:
    _seed(tmp_path)
    registry = _FakeRegistry(_senate_envelope(PLAIN_SAMPLE))
    client = _client(tmp_path, registry)

    response = client.get(STATUS_URL, params={"date": DAY})

    assert response.status_code == 200
    body = response.json()
    assert body["plain"] == PLAIN_SAMPLE
    assert body["disclaimer"] == DISCLAIMER
    # 六段状态本身不受影响
    assert body["complete"] is True and body["source_status"] == "ok"
    assert registry.calls == [
        {
            "dataset": "council_memo",
            "trade_date": date.fromisoformat(DAY),
            "params": {"code": CODE, "date": DAY},
            "allow_cache": False,
            "timeout": SENATE_PLAIN_TIMEOUT_DEFAULT,
        }
    ]


def test_plain_omits_disclaimer_when_senate_did_not_send_one(tmp_path: Path) -> None:
    _seed(tmp_path)
    registry = _FakeRegistry(_senate_envelope(PLAIN_SAMPLE, disclaimer=None))
    client = _client(tmp_path, registry)

    body = client.get(STATUS_URL, params={"date": DAY}).json()

    assert body["plain"] == PLAIN_SAMPLE
    assert "disclaimer" not in body


# --------------------------------------------------------------------------- #
# 2. 降级：不可达 / 无 plain —— 照常 200，只是没有 plain
# --------------------------------------------------------------------------- #

def test_status_degrades_when_senate_unreachable(tmp_path: Path) -> None:
    _seed(tmp_path)
    registry = _FakeRegistry(exc=SourceUnavailable("StockSenate 不可达（ConnectError）"))
    client = _client(tmp_path, registry)

    response = client.get(STATUS_URL, params={"date": DAY})

    assert response.status_code == 200
    body = response.json()
    assert "plain" not in body and "disclaimer" not in body
    assert body["available"] is True and body["complete"] is True


def test_status_degrades_when_snapshot_has_no_plain(tmp_path: Path) -> None:
    """旧快照（plain-v1 之前生成）没有 plain：如实空态，不编造。"""
    _seed(tmp_path)
    registry = _FakeRegistry(_senate_envelope(plain=None))
    client = _client(tmp_path, registry)

    body = client.get(STATUS_URL, params={"date": DAY}).json()

    assert "plain" not in body and "disclaimer" not in body
    assert body["complete"] is True


def test_status_degrades_on_unexpected_registry_error(tmp_path: Path) -> None:
    _seed(tmp_path)
    registry = _FakeRegistry(exc=RuntimeError("peer 返回非 JSON"))
    client = _client(tmp_path, registry)

    response = client.get(STATUS_URL, params={"date": DAY})

    assert response.status_code == 200
    assert "plain" not in response.json()


# --------------------------------------------------------------------------- #
# 3. 预算：显式、可配、有边界
# --------------------------------------------------------------------------- #

def test_timeout_env_override_and_clamp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SENATE_PLAIN_TIMEOUT_ENV, "1.5")
    assert senate_plain_timeout() == 1.5

    monkeypatch.setenv(SENATE_PLAIN_TIMEOUT_ENV, "0.01")
    assert senate_plain_timeout() == SENATE_PLAIN_TIMEOUT_MIN

    monkeypatch.setenv(SENATE_PLAIN_TIMEOUT_ENV, "99")
    assert senate_plain_timeout() == SENATE_PLAIN_TIMEOUT_MAX

    monkeypatch.setenv(SENATE_PLAIN_TIMEOUT_ENV, "abc")
    assert senate_plain_timeout() == SENATE_PLAIN_TIMEOUT_DEFAULT


def test_plane_timeout_injection_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """测试可注入预算；注入值优先于环境变量。"""
    monkeypatch.setenv(SENATE_PLAIN_TIMEOUT_ENV, "9")
    _seed(tmp_path)
    registry = _FakeRegistry(_senate_envelope(PLAIN_SAMPLE))
    plane = ResearchPlane(data_root=tmp_path, registry=registry, senate_timeout=1.25)
    app = create_app(
        data_root=tmp_path,
        dist_dir=tmp_path / "no-dist",
        control_plane=_NullControlPlane(),
        research_plane=plane,
    )
    client = TestClient(app, base_url=BASE_URL, client=LOOPBACK)

    client.get(STATUS_URL, params={"date": DAY})

    assert registry.calls[0]["timeout"] == 1.25


# --------------------------------------------------------------------------- #
# 4. 真 MCP 读取路径：适配器只摘 sections.plain，不带整份 sections
# --------------------------------------------------------------------------- #

class _RecordingTransport:
    """记录 `tools/call` 入参的传输层；返回 Senate `stock_senate_report_query` 真形状。"""

    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def call(
        self,
        peer: Any,
        tool: str,
        params: dict[str, Any],
        *,
        timeout: float,
    ) -> dict[str, Any]:
        self.calls.append(
            {"peer": peer.key, "tool": tool, "params": dict(params), "timeout": timeout}
        )
        return self.response

    def probe(self, peer: Any, *, timeout: float) -> dict[str, Any]:
        return {"peer": peer.key, "tools": ["stock_senate_report_query"]}


def _senate_tool_response(
    plain: dict[str, Any] | None,
) -> dict[str, Any]:
    """`stock_senate_report_query` 返回体（QUOT-536：`sections.plain`）。"""
    return {
        "ok": True,
        "run_id": "2026-10-30-600000-abcd1234",
        "summary": {"rating": "neutral"},
        "sections": {
            "analysts": [{"role": "fundamental", "stance": "neutral"}],
            "debate": {"rounds": 2},
            "decision": {"rating": "neutral"},
            "plain": plain,
        },
        "disclaimer": DISCLAIMER,
    }


def _real_registry(tmp_path: Path, transport: _RecordingTransport) -> ProviderRegistry:
    return ProviderRegistry(
        sources=SOURCES_DOC,
        sync=SYNC_DOC,
        transport=transport,
        root=tmp_path,
        ttl_sec=0.0,
    )


def test_mcp_path_lifts_sections_plain_only(tmp_path: Path) -> None:
    transport = _RecordingTransport(_senate_tool_response(PLAIN_SAMPLE))
    registry = _real_registry(tmp_path, transport)

    outcome = registry.fetch_live(
        "council_memo",
        trade_date=date.fromisoformat(DAY),
        params={"code": CODE, "date": DAY},
        allow_cache=False,
        timeout=2.5,
    )

    payload = outcome.envelope.payload
    assert payload["plain"] == PLAIN_SAMPLE
    assert payload["disclaimer"] == DISCLAIMER
    assert "sections" not in payload, "整份 sections 不进信封，避免 council_memo 整包膨胀"
    assert transport.calls == [
        {
            "peer": "senate",
            "tool": "stock_senate_report_query",
            "params": {"code": CODE, "date": DAY},
            "timeout": 2.5,
        }
    ]


def test_mcp_path_without_plain_keeps_envelope_plain_free(tmp_path: Path) -> None:
    transport = _RecordingTransport(_senate_tool_response(None))
    registry = _real_registry(tmp_path, transport)

    outcome = registry.fetch_live(
        "council_memo",
        trade_date=date.fromisoformat(DAY),
        params={"code": CODE, "date": DAY},
        allow_cache=False,
        timeout=2.5,
    )

    assert "plain" not in outcome.envelope.payload
    assert "disclaimer" not in outcome.envelope.payload


def test_end_to_end_status_through_real_registry(tmp_path: Path) -> None:
    """状态接口 + 真 ProviderRegistry + 假传输：一条完整读取路径。"""
    _seed(tmp_path)
    transport = _RecordingTransport(_senate_tool_response(PLAIN_SAMPLE))
    client = _client(tmp_path, _real_registry(tmp_path, transport))

    body = client.get(STATUS_URL, params={"date": DAY}).json()

    assert body["plain"] == PLAIN_SAMPLE
    assert body["disclaimer"] == DISCLAIMER
    assert transport.calls[0]["tool"] == "stock_senate_report_query"
    assert transport.calls[0]["params"] == {"code": CODE, "date": DAY}
