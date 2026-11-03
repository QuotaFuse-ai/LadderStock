"""上游限流（`QFSD-7001 rate_limited`）：退避 → 昨日池降级 → 整轮判降级（QUOT-349）。

三件事都要钉住，因为验收口径就是这三条：

1. **按上游建议的时长退避**：`retry_after_s` 说几秒就等几秒，次数与单次时长都有硬上限；
   等不起（建议超过 `max_wait_sec`）就**不假装在退避**，直接降级并按建议时长设冷却。
2. **退完仍取不到 → 回放本地昨日池**，结果里带明确的 stale 标记（数据日 + 原因），
   已落库的数据一个字节都不动。
3. **限流不是任务失败**：sync 汇总退出码仍是 0，调度单任务落 `degraded`。

peer 全部由 `tests/fixtures/fake_peers.py` 的离线桩扮演，不发网络请求；
等待由注入的 `sleeper` 记录，用例不真睡（也不靠真睡来证明等过）。
"""

from __future__ import annotations

import copy
import json
from datetime import date, datetime
from typing import Any

import pytest
from fixtures.fake_peers import (
    FakeTransport,
    kline_records,
    make_registry,
    rate_limited_payload,
    tickhub_payload,
)
from fixtures.intraday_stubs import StubRegistry

from ladderstock.cli.schedule import run_job
from ladderstock.cli.sync import run_sync
from ladderstock.core import intraday
from ladderstock.data import duckdb_store, watermark
from ladderstock.data.provider_registry import SourceUnavailable

#: 本地缓存里的那一天（= 降级时要回放的「昨日池」）
CACHED_DAY = date(2026, 10, 24)
#: 本轮想取的那一天（上游限流，取不到）
TODAY = date(2026, 10, 25)


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    """缓存回放 / 冷却都只落 tmp，别碰仓库 data/。"""
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    return tmp_path


def _cache_yesterday_pool(root) -> None:
    """把「昨日池」写进本地库：降级回放读的就是这里。"""
    duckdb_store.write_dataset(
        "daily_k", kline_records(CACHED_DAY.isoformat()), source="tickhub_kline", root=root
    )


def _always_paced(retry_after_s: float | None = 5) -> FakeTransport:
    """主备都持续限流：模拟整个 peer 在一个 pacing 窗口里。"""
    return FakeTransport(
        handlers={
            "tencent_kline": lambda _p: rate_limited_payload(retry_after_s),
            "tdx_daily_package": lambda _p: rate_limited_payload(retry_after_s),
        }
    )


def transport_called(registry) -> list[str]:
    return [call.tool for call in registry.transport.calls]


# --------------------------------------------------------------------------- #
# 1. 退避：按上游建议的时长等，次数与时长都有上限
# --------------------------------------------------------------------------- #

def test_backoff_waits_the_seconds_the_peer_asked_for(tmp_path) -> None:
    state = {"n": 0}

    def paced_once(_params: dict[str, Any]) -> dict[str, Any]:
        state["n"] += 1
        if state["n"] == 1:
            return rate_limited_payload(3)
        return tickhub_payload(kline_records())

    slept: list[float] = []
    transport = FakeTransport(handlers={"tencent_kline": paced_once})
    registry = make_registry(transport, tmp_path, sleeper=slept.append)

    outcome = registry.fetch("daily_k")

    assert slept == [3.0], "上游建议 3 秒就等 3 秒：不打折、不加抖动、也不立刻重发"
    assert transport.tools_called == ["tencent_kline", "tencent_kline"]
    # 退避后拿到的是当天的真数据：**不算降级**，只是轨迹里要看得见被 pacing 过
    assert outcome.rate_limited is True
    assert outcome.degraded is False
    assert outcome.from_cache is False
    assert "tickhub_kline/tencent_kline:rate_limited" in outcome.trail
    assert "tickhub_kline/tencent_kline:ok" in outcome.trail
    paced_events = [
        event for event in registry.health.events() if event["kind"] == "rate_limited"
    ]
    assert any("QFSD-7001" in (event.get("detail") or "") for event in paced_events)
    # 退避成功后不该继续背着冷却闸门
    assert registry.health.cooldown_until("tickhub_kline") is None


def test_backoff_budget_is_shared_and_bounded_then_replays_yesterday_pool(tmp_path) -> None:
    _cache_yesterday_pool(tmp_path)
    slept: list[float] = []
    registry = make_registry(_always_paced(5), tmp_path, sleeper=slept.append)

    outcome = registry.fetch("daily_k", trade_date=TODAY)

    # 默认预算 2 次，而且是**一次取数共享**的（主备两个工具用同一份）：
    # 主源等满两次后，备源不再等——盘中高频轮询不能被一次限流拖住整轮
    assert slept == [5.0, 5.0]
    assert transport_called(registry) == ["tencent_kline"] * 3 + ["tdx_daily_package"]
    assert outcome.rate_limited is True
    assert outcome.from_cache is True and outcome.degraded is True
    assert outcome.source == "local_cache"
    assert outcome.envelope.freshness == "cached"
    assert outcome.envelope.source_status == "degraded"
    assert any(step.startswith("cache:") for step in outcome.trail)


def test_replayed_pool_carries_an_explicit_stale_marker(tmp_path) -> None:
    _cache_yesterday_pool(tmp_path)
    registry = make_registry(_always_paced(5), tmp_path, sleeper=lambda _s: None)

    envelope = registry.fetch("daily_k", trade_date=TODAY).envelope

    stale = envelope.payload["stale"]
    assert stale["as_of"] == CACHED_DAY.isoformat()
    assert stale["requested_as_of"] == TODAY.isoformat()
    assert stale["reason"] == "rate_limited"
    assert envelope.data_as_of == CACHED_DAY, "数据日要说自己是 24 号的，不冒充今天"
    assert "限流降级" in envelope.source_detail
    assert len(envelope.payload["records"]) == 2, "昨日池整批回放在结果里"
    assert duckdb_store.dataset_rows("daily_k", root=tmp_path) == 2, "回放不重写已落库数据"


def test_a_wait_we_cannot_afford_is_not_attempted_and_cools_the_source_down(tmp_path) -> None:
    _cache_yesterday_pool(tmp_path)
    slept: list[float] = []
    registry = make_registry(_always_paced(120), tmp_path, sleeper=slept.append)

    outcome = registry.fetch("daily_k", trade_date=TODAY)

    # 120 秒 > max_wait_sec（默认 15）：等不起就直说等不起，不假装退避了一半
    assert slept == []
    assert transport_called(registry) == ["tencent_kline", "tdx_daily_package"]
    assert outcome.rate_limited is True and outcome.from_cache is True
    cooldowns = {row["source"]: row for row in registry.health.active_cooldowns()}
    assert cooldowns["tickhub_kline"]["remaining_sec"] > 100, (
        "冷却时长按上游说的 120 秒来，不是照抄配置的 60 秒"
    )


def test_a_cooldown_we_set_by_rate_limiting_still_degrades(tmp_path) -> None:
    """下一轮整条链都在冷却里：一次调用都没发出去，也不能说成「源坏了 / 今天没数据」。

    冷却是上一轮限流设的，本轮仍然在同一个 pacing 窗口里 —— 判 `failed` / `missing`
    会把一次 pacing 伪装成事故，看板也跟着变空白。
    """
    _cache_yesterday_pool(tmp_path)
    registry = make_registry(_always_paced(5), tmp_path, sleeper=lambda _s: None)
    registry.fetch("daily_k", trade_date=TODAY)
    before = len(registry.transport.calls)

    outcome = registry.fetch("daily_k", trade_date=TODAY)

    assert len(registry.transport.calls) == before, "冷却期里不该再打 peer"
    assert [s for s in outcome.trail if s.endswith(":ok")] == [], outcome.trail
    assert sum(1 for s in outcome.trail if s.endswith(":cooldown")) == 2, outcome.trail
    assert outcome.rate_limited is True
    assert outcome.from_cache is True and outcome.degraded is True
    assert outcome.envelope.payload["stale"]["reason"] == "rate_limited"


def test_absurd_retry_after_is_capped_so_a_dataset_never_freezes(tmp_path) -> None:
    _cache_yesterday_pool(tmp_path)

    registry = make_registry(_always_paced(99_999), tmp_path, sleeper=lambda _s: None)

    registry.fetch("daily_k", trade_date=TODAY)

    cooldowns = {row["source"]: row for row in registry.health.active_cooldowns()}
    assert cooldowns["tickhub_kline"]["remaining_sec"] <= 900


def test_missing_retry_after_advice_is_not_guessed(tmp_path) -> None:
    _cache_yesterday_pool(tmp_path)
    slept: list[float] = []
    registry = make_registry(_always_paced(None), tmp_path, sleeper=slept.append)

    outcome = registry.fetch("daily_k", trade_date=TODAY)

    # 上游只说「限流」没说等多久：不猜时长，按配置的冷却处理并直接降级
    assert slept == []
    assert outcome.rate_limited is True
    assert outcome.from_cache is True
    cooldowns = {row["source"]: row for row in registry.health.active_cooldowns()}
    assert cooldowns["tickhub_kline"]["remaining_sec"] > 50, "回落到 sources.yaml 的 cooldown_sec"


def test_rate_limit_budget_is_configurable(tmp_path) -> None:
    sync = copy.deepcopy(_sync_doc())
    sync["sync"]["rate_limit"] = {"max_retries": 1, "max_wait_sec": 2}
    _cache_yesterday_pool(tmp_path)
    slept: list[float] = []
    registry = make_registry(
        _always_paced(1), tmp_path, sync=sync, sleeper=slept.append
    )

    registry.fetch("daily_k", trade_date=TODAY)

    assert slept == [1.0], "配置说只退避 1 次就 1 次，不按工具数翻倍"


def test_allow_cache_false_still_raises_and_says_it_was_rate_limiting(tmp_path) -> None:
    _cache_yesterday_pool(tmp_path)
    registry = make_registry(_always_paced(5), tmp_path, sleeper=lambda _s: None)

    with pytest.raises(SourceUnavailable) as excinfo:
        registry.fetch("daily_k", trade_date=TODAY, allow_cache=False)

    assert excinfo.value.rate_limited is True, (
        "无缓存可降级时也要把「是限流」带出去，调用方才分得清 failed 与 degraded"
    )
    assert "rate_limited" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# 3. 限流导致的降级不算任务失败
# --------------------------------------------------------------------------- #

def test_sync_round_stays_degraded_with_exit_code_zero(isolated_data_dir) -> None:
    """`ldst sync` 汇总：限流降级落 `degraded`，退出码不翻成 4/8。"""
    _cache_yesterday_pool(isolated_data_dir)
    registry = make_registry(_always_paced(5), isolated_data_dir, sleeper=lambda _s: None)
    store = watermark.WatermarkStore(root=isolated_data_dir)

    payload = run_sync(registry, store, ["daily_k"])

    assert payload["summary"]["exit_code"] == 0
    assert payload["summary"]["degraded"] == 1
    assert payload["summary"]["failed"] == 0
    row = payload["results"][0]
    assert row["status"] == "degraded"
    assert "QFSD-7001" in row["detail"], "日志/台账要说清是限流，不是「源坏了」"
    assert row["watermark_after"] == row["watermark_before"], "降级不推进水位"


def test_rate_limited_snapshot_job_is_degraded_and_keeps_the_stale_pool(tmp_path) -> None:
    """调度侧：盘中快照撞到限流 → 任务 `degraded`（不是 failed），看板仍有时点可读。"""
    registry = StubRegistry(
        from_cache=True,
        rate_limited=True,
        data_as_of={"yesterday_limit_up": date(2026, 10, 29)},
    )
    job = {
        "id": "snapshot_1000",
        "name": "10:00 盘中快照",
        "kind": "snapshot",
        "trigger": "10:00",
        "enabled": True,
    }

    result = run_job(
        job,
        now=datetime(2026, 10, 30, 10, 0, 5),
        timezone="Asia/Shanghai",
        root=tmp_path,
        registry=registry,
    )

    assert result["status"] == "degraded", result
    snapshot = json.loads(
        intraday.snapshot_path("10:00", date(2026, 10, 30), tmp_path).read_text(
            encoding="utf-8"
        )
    )
    stale = snapshot["source"]["pool_stale"]
    assert stale["as_of"] == "2026-10-29"
    assert stale["reason"] == "rate_limited"
    assert snapshot["yesterday_limit_up"]["fresh"] > 0, "今天的报价照读，池子才是旧的"
    status = intraday.read_status(date(2026, 10, 30), tmp_path)
    assert status["slots"]["10:00"]["status"] == "degraded"


def test_stale_pool_is_only_accepted_for_rate_limiting(tmp_path) -> None:
    """日期对不上又**不是**限流回放 → 照旧 missing：不拿旧池子冒充今天的读数。"""
    registry = StubRegistry(from_cache=True, data_as_of={"yesterday_limit_up": date(2026, 10, 29)})

    result = intraday.run_slot(
        "10:00", now=datetime(2026, 10, 30, 10, 0, 5), timezone="Asia/Shanghai",
        root=tmp_path, registry=registry,
    )

    assert result["status"] == "missing"
    assert not intraday.snapshot_path("10:00", date(2026, 10, 30), tmp_path).exists()


def test_rate_limited_quotes_degrade_without_writing_an_empty_snapshot(tmp_path) -> None:
    """报价整批被限流打回：没有一条今天的读数就不写快照，但任务是 `degraded` 不是 failed。"""
    registry = StubRegistry()
    registry.errors["spot_quote"] = SourceUnavailable(
        "spot_quote 的生态链路全部不可用：rate_limited", rate_limited=True
    )

    result = intraday.run_slot(
        "10:00", now=datetime(2026, 10, 30, 10, 0, 5), timezone="Asia/Shanghai",
        root=tmp_path, registry=registry,
    )

    assert result["status"] == "degraded", result
    assert "QFSD-7001" in result["detail"]
    assert not intraday.snapshot_path("10:00", date(2026, 10, 30), tmp_path).exists()
    status = intraday.read_status(date(2026, 10, 30), tmp_path)
    assert status["slots"]["10:00"]["status"] == "degraded"


def _sync_doc() -> dict:
    from fixtures.fake_peers import SYNC_DOC

    return copy.deepcopy(SYNC_DOC)
