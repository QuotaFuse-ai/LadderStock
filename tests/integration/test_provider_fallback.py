"""主源 → 同 peer 备源 → 本地缓存回放，以及四条治理规则（DEC-007 §2.4/§2.5）。

全部使用 `tests/fixtures/fake_peers.py` 里的离线桩：不发请求、不出现真实域名。
"""

from __future__ import annotations

import copy
import threading
import time
from datetime import date

import pytest
from fixtures.fake_peers import (
    SOURCES_DOC,
    FakeTransport,
    kline_records,
    make_registry,
    tickhub_payload,
)

from ladderstock.core.boundary import BoundaryViolation
from ladderstock.data import duckdb_store
from ladderstock.data.provider_registry import SourceUnavailable


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """隔离数据目录：缓存回放 / 冷却记录都只落在 tmp，别碰仓库 data/。"""
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    return tmp_path


def _ok_kline(_params: dict) -> dict:
    return tickhub_payload(kline_records())


# --------------------------------------------------------------------------- #
# 降级链
# --------------------------------------------------------------------------- #

def test_primary_timeout_falls_back_to_same_peer_tool(tmp_path) -> None:
    transport = FakeTransport(
        handlers={"tdx_daily_package": _ok_kline},
        errors={"tencent_kline": TimeoutError("timed out")},
    )
    registry = make_registry(transport, tmp_path)

    outcome = registry.fetch("daily_k")

    assert outcome.used_fallback is True
    assert outcome.from_cache is False
    assert outcome.source == "tickhub_tdx_package"
    assert outcome.envelope.source_status == "degraded"
    assert transport.tools_called == ["tencent_kline", "tdx_daily_package"]
    assert "tickhub_kline/tencent_kline:timeout" in outcome.trail
    assert "tickhub_tdx_package/tdx_daily_package:ok" in outcome.trail
    # 失败写冷却、降级写事件，供 sources health 复盘
    assert registry.health.cooldown_until("tickhub_kline") is not None
    kinds = [event["kind"] for event in registry.health.events()]
    assert "failure" in kinds and "fallback" in kinds


def test_cooldown_skips_known_bad_primary(tmp_path) -> None:
    transport = FakeTransport(
        handlers={
            "tencent_kline": _ok_kline,
            "tdx_daily_package": _ok_kline,
        }
    )
    registry = make_registry(transport, tmp_path)
    registry.health.set_cooldown("tickhub_kline", 60, reason="timeout", tool="tencent_kline")

    outcome = registry.fetch("daily_k")

    assert transport.tools_called == ["tdx_daily_package"]
    assert "tickhub_kline/tencent_kline:cooldown" in outcome.trail
    assert outcome.used_fallback is True


def test_retries_are_bounded_before_switching(tmp_path) -> None:
    sources = copy.deepcopy(SOURCES_DOC)
    for item in sources["sources"]:
        if item["id"] == "tickhub_kline":
            item["retries"] = 2

    transport = FakeTransport(
        handlers={"tdx_daily_package": _ok_kline},
        errors={"tencent_kline": TimeoutError("timed out")},
    )
    registry = make_registry(transport, tmp_path, sources=sources)

    registry.fetch("daily_k")

    # retries=2 → 主源 3 次后放弃，然后才走备源
    assert transport.tools_called == ["tencent_kline"] * 3 + ["tdx_daily_package"]


def test_all_tools_fail_replays_local_cache(tmp_path) -> None:
    duckdb_store.write_dataset(
        "daily_k", kline_records(), source="tickhub_kline", root=tmp_path
    )
    transport = FakeTransport(
        errors={
            "tencent_kline": TimeoutError("timed out"),
            "tdx_daily_package": ConnectionError("refused"),
        }
    )
    registry = make_registry(transport, tmp_path)

    outcome = registry.fetch("daily_k", trade_date=date(2026, 10, 24))

    assert outcome.from_cache is True
    assert outcome.source == "local_cache"
    assert outcome.envelope.freshness == "cached"
    assert outcome.envelope.source_status == "degraded"
    assert len(outcome.envelope.payload["records"]) == 2
    assert any(item.startswith("cache:") for item in outcome.trail)


def test_no_peer_and_no_cache_raises_source_unavailable(tmp_path) -> None:
    transport = FakeTransport(
        errors={
            "tencent_kline": TimeoutError("timed out"),
            "tdx_daily_package": TimeoutError("timed out"),
        }
    )
    registry = make_registry(transport, tmp_path)

    with pytest.raises(SourceUnavailable):
        registry.fetch("daily_k")

    assert registry.health.active_cooldowns()


def test_cache_replay_can_be_disabled(tmp_path) -> None:
    duckdb_store.write_dataset("daily_k", kline_records(), root=tmp_path)
    transport = FakeTransport(
        errors={"tencent_kline": TimeoutError(), "tdx_daily_package": TimeoutError()}
    )
    registry = make_registry(transport, tmp_path)

    with pytest.raises(SourceUnavailable):
        registry.fetch("daily_k", trade_date=date(2026, 10, 24), allow_cache=False)


# --------------------------------------------------------------------------- #
# 格式漂移（peer 活着但返回形状变了）
# --------------------------------------------------------------------------- #

def test_malformed_primary_payload_switches_to_same_peer_fallback(tmp_path) -> None:
    """主源返回 `ok: true` 但没有 `data`（形状漂移）→ 按失败处理并切同 peer 备源。

    关键点：**不猜测填充**。畸形返回不能当成空结果写库，否则当日数据会被静默清空。
    """

    def broken(_params: dict) -> dict:
        return {"ok": True, "data": None, "meta": {"source": "stub"}}

    transport = FakeTransport(
        handlers={"tencent_kline": broken, "tdx_daily_package": _ok_kline}
    )
    registry = make_registry(transport, tmp_path)

    outcome = registry.fetch("daily_k")

    assert outcome.used_fallback is True
    assert outcome.source == "tickhub_tdx_package"
    assert outcome.envelope.source_status == "degraded"
    assert "tickhub_kline/tencent_kline:unavailable" in outcome.trail
    assert len(outcome.envelope.payload["records"]) == 2
    # 形状违约照样进冷却 + 失败事件，`sources health` 能看到原因
    assert registry.health.cooldown_until("tickhub_kline") is not None
    failures = [event for event in registry.health.events() if event["kind"] == "failure"]
    assert any("缺 data" in (event.get("detail") or "") for event in failures)


def test_peer_reports_ok_false_is_an_error_not_an_empty_result(tmp_path) -> None:
    """`ok: false` 是 peer 明确报错：带 code/message 抛出，绝不转成空数据集。"""

    def refused(_params: dict) -> dict:
        return {
            "ok": False,
            "code": "UPSTREAM_TIMEOUT",
            "message": "上游超时",
            "suggestion": "稍后重试",
        }

    transport = FakeTransport(
        handlers={"tencent_kline": refused, "tdx_daily_package": _ok_kline}
    )
    registry = make_registry(transport, tmp_path)

    outcome = registry.fetch("daily_k")

    assert outcome.source == "tickhub_tdx_package"
    assert "tickhub_kline/tencent_kline:unavailable" in outcome.trail
    detail = " ".join(
        event.get("detail") or "" for event in registry.health.events()
    )
    assert "上游超时" in detail


def test_all_sources_malformed_replays_cache_instead_of_writing_garbage(tmp_path) -> None:
    """主备都漂移时回放本地缓存（degraded），已有缓存不被覆盖成空。"""

    def broken(_params: dict) -> dict:
        return {"ok": True, "meta": {"source": "stub"}}  # 缺 data 字段

    duckdb_store.write_dataset("daily_k", kline_records(), source="tickhub_kline", root=tmp_path)
    transport = FakeTransport(handlers={"tencent_kline": broken, "tdx_daily_package": broken})
    registry = make_registry(transport, tmp_path)

    outcome = registry.fetch("daily_k", trade_date=date(2026, 10, 24))

    assert outcome.from_cache is True
    assert outcome.envelope.source_status == "degraded"
    assert len(outcome.envelope.payload["records"]) == 2
    assert duckdb_store.dataset_rows("daily_k", root=tmp_path) == 2


# --------------------------------------------------------------------------- #
# 跨 peer 备源
# --------------------------------------------------------------------------- #

def test_cross_peer_fallback_is_a_boundary_violation(tmp_path) -> None:
    """备源只能是同 peer 的另一个工具；跨 peer 等于换供应商采集（DEC-006 闸门 3）。"""
    sources = copy.deepcopy(SOURCES_DOC)
    sources["sources"].append(
        {
            "id": "wire_kline_proxy",
            "dataset": "daily_k",
            "via": "wire",
            "tool": "wire_get_digest",
            "primary": False,
            "fallback": [],
            "timeout_sec": 1,
            "retries": 0,
            "cooldown_sec": 60,
        }
    )
    sync = copy.deepcopy(_sync_doc())
    sync["sync"]["datasets"]["daily_k"]["fallback"] = ["wire_kline_proxy"]

    registry = make_registry(
        FakeTransport(handlers={"tencent_kline": _ok_kline}), tmp_path, sources=sources, sync=sync
    )

    with pytest.raises(BoundaryViolation):
        registry.fetch("daily_k")


# --------------------------------------------------------------------------- #
# 治理：in-flight 合并 / TTL 缓存
# --------------------------------------------------------------------------- #

def test_concurrent_requests_are_merged(tmp_path) -> None:
    started = threading.Event()
    release = threading.Event()
    calls: list[dict] = []

    def slow(params: dict) -> dict:
        calls.append(params)
        started.set()
        assert release.wait(5), "慢调用没有被放行"
        return tickhub_payload(kline_records())

    transport = FakeTransport(handlers={"tencent_kline": slow})
    registry = make_registry(transport, tmp_path)

    outcomes: list = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            outcomes.append(registry.fetch("daily_k"))
        except BaseException as exc:  # noqa: BLE001 - 收集后统一断言
            errors.append(exc)

    first = threading.Thread(target=worker)
    first.start()
    assert started.wait(5)

    second = threading.Thread(target=worker)
    second.start()
    time.sleep(0.1)  # 让第二个线程进到 merge 分支
    release.set()
    first.join(10)
    second.join(10)

    assert not errors
    assert len(calls) == 1, "并发同参数请求只应打一次 peer"
    assert len(outcomes) == 2
    trails = [outcome.trail for outcome in outcomes]
    assert any("inflight:merged" in trail for trail in trails)


def test_ttl_cache_serves_second_call(tmp_path) -> None:
    transport = FakeTransport(handlers={"tencent_kline": _ok_kline})
    registry = make_registry(transport, tmp_path, ttl_sec=60.0)

    first = registry.fetch("daily_k")
    second = registry.fetch("daily_k")

    assert len(transport.calls) == 1
    assert first.envelope.freshness == "realtime"
    assert second.envelope.freshness == "cached"
    assert second.trail == ["ttl_cache:hit"]


def test_different_params_do_not_share_cache(tmp_path) -> None:
    transport = FakeTransport(handlers={"tencent_kline": _ok_kline})
    registry = make_registry(transport, tmp_path, ttl_sec=60.0)

    registry.fetch("daily_k")
    registry.fetch("daily_k", params={"since": "2026-10-24"})

    assert len(transport.calls) == 2
    assert transport.calls[1].params == {"since": "2026-10-24"}


# --------------------------------------------------------------------------- #
# 检视面
# --------------------------------------------------------------------------- #

def test_describe_marks_chain_and_tool_registration(tmp_path) -> None:
    registry = make_registry(FakeTransport(), tmp_path)

    rows = {row["source"]: row for row in registry.describe()}

    assert rows["tickhub_kline"]["in_chain"] is True
    assert rows["tickhub_kline"]["primary"] is True
    assert rows["tickhub_kline"]["fallback"] == "tickhub_tdx_package"
    # DEC-008：wire_timeline 已登记为 theme_news 的同 peer 备源，检视面应标记为合规
    assert rows["wire_timeline"]["tool_registered"] is True


def test_peer_rows_count_sources(tmp_path) -> None:
    registry = make_registry(FakeTransport(), tmp_path)

    rows = {row["peer"]: row for row in registry.peer_rows()}

    assert set(rows) == {"tickhub", "wire", "senate"}
    assert rows["tickhub"]["sources"] == 4


def _sync_doc() -> dict:
    from fixtures.fake_peers import SYNC_DOC

    return copy.deepcopy(SYNC_DOC)
