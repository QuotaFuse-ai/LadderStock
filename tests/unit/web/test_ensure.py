"""打开即补取闸门 `OpenEnsure` 的离线单元测试（QUOT-555 / DEC-080）。

钉住治理纪律，全部用 FakeTransport + 可控时钟，**不联网、不 sleep**：

1. 新鲜只读、零外呼；冷启动才触发，且复用既有同步路径落库（补完读得到）；
2. 冷却：失败后冷却期内不重打，时钟拨过冷却才允许重试（不 hammer 上游）；
3. 单飞：同组正在补时第二个请求只读本机库；
4. 无源不自采（DEC-006 闸门 4）：未登记数据集一次外呼都不发；
5. industry 只走 base_pool 常规口径，**绝不**锚点扩池（DEC-074 付费动作仍显式）；
6. 失败如实降级、永不向调用方抛异常、不回落演示。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fixtures.ensure_peers import (
    ENSURE_CODES,
    ensure_transport,
    make_ensure_registry,
)
from fixtures.fake_peers import FakeTransport
from fixtures.news_samples import news_handlers, news_tools

from ladderstock.core.ladder import dataset_snapshot
from ladderstock.data import archive, duckdb_store
from ladderstock.industry import snapshot as industry_snapshot
from ladderstock.news import store as news_store
from ladderstock.web.ensure import GROUPS, OpenEnsure

DAY = date(2026, 10, 24)
NEWS_DAY = date(2026, 10, 30)
INDUSTRY_DATASETS = list(industry_snapshot.SNAPSHOT_DATASETS)


class FakeClock:
    """可控单调时钟：测冷却不用真睡（硬红线：不 sleep 掩盖偶发）。"""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture()
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    # 同步落库走 data_dir()（LDST_DATA_DIR），与所有既有 sync 测试同一套隔离。
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    return tmp_path


def _ensure(registry, root: Path, clock: FakeClock, *, cooldown: float = 300.0) -> OpenEnsure:
    return OpenEnsure(registry=registry, root=root, cooldown_sec=cooldown, clock=clock)


# --------------------------------------------------------------------------- #
# 1. 新鲜只读 / 冷启动触发
# --------------------------------------------------------------------------- #

def test_theme_fresh_reads_only_no_outbound(data_root: Path) -> None:
    duckdb_store.write_dataset(
        "theme_news", [{"theme": "ai", "trade_date": DAY.isoformat()}], root=data_root
    )
    transport = ensure_transport(DAY.isoformat())
    registry = make_ensure_registry(transport, data_root, datasets=["theme_news"])
    result = _ensure(registry, data_root, FakeClock()).ensure("theme", day=DAY)

    assert result.triggered is False
    assert result.status == "fresh"
    assert result.note() == ""
    assert transport.calls == []


def test_theme_cold_start_triggers_and_persists(data_root: Path) -> None:
    transport = ensure_transport(DAY.isoformat())
    registry = make_ensure_registry(transport, data_root, datasets=["theme_news"])
    result = _ensure(registry, data_root, FakeClock()).ensure("theme", day=DAY)

    assert result.triggered is True
    assert result.status == "ok"
    assert result.note()
    # 两个赛道各打一次（wire_get_digest 的 topic 必填）
    assert sorted(c.params["topic"] for c in transport.calls) == ["ai", "semiconductor"]
    records, _ = dataset_snapshot("theme_news", DAY, data_root)
    assert records, "补取后本机应读得到当日 theme_news"


def test_news_cold_start_collects_and_covers_window(data_root: Path) -> None:
    transport = FakeTransport(handlers=news_handlers(), tools=news_tools())
    from fixtures.fake_peers import make_news_registry

    registry = make_news_registry(transport, data_root)
    result = _ensure(registry, data_root, FakeClock()).ensure("news", day=NEWS_DAY)

    assert result.triggered is True
    assert result.status == "ok"
    conn = news_store.connect(data_root, create=False)
    assert conn is not None
    try:
        assert news_store.covering_run(conn, start=NEWS_DAY, end=NEWS_DAY) is not None
    finally:
        news_store.close(conn)


def test_pool_cold_start_triggers_and_persists(data_root: Path) -> None:
    transport = ensure_transport(DAY.isoformat())
    registry = make_ensure_registry(
        transport, data_root, datasets=["limit_pool", "concept_board", "fund_flow"]
    )
    result = _ensure(registry, data_root, FakeClock()).ensure("pool", day=DAY)

    assert result.triggered is True
    assert result.status == "ok"
    records, _ = dataset_snapshot("limit_pool", DAY, data_root)
    assert records


# --------------------------------------------------------------------------- #
# 2. 冷却 / 单飞 / 并发上限
# --------------------------------------------------------------------------- #

def test_cooldown_bounds_retries_while_stale(data_root: Path) -> None:
    # 上游全挂 → 数据始终陈旧：冷却期内不许重打，拨过冷却才允许重试。
    transport = ensure_transport(DAY.isoformat(), failing=("wire_get_digest",))
    registry = make_ensure_registry(transport, data_root, datasets=["theme_news"])
    clock = FakeClock()
    ensure = _ensure(registry, data_root, clock, cooldown=300.0)

    first = ensure.ensure("theme", day=DAY)
    assert first.triggered is True
    calls_after_first = len(transport.calls)
    assert calls_after_first > 0

    second = ensure.ensure("theme", day=DAY)
    assert second.triggered is False
    assert second.status == "cooldown"
    assert len(transport.calls) == calls_after_first, "冷却期内不该再打上游"

    clock.advance(301.0)
    third = ensure.ensure("theme", day=DAY)
    assert third.triggered is True
    assert len(transport.calls) > calls_after_first, "冷却过后允许重试"


def test_single_flight_second_request_reads_local(data_root: Path) -> None:
    transport = ensure_transport(DAY.isoformat())
    registry = make_ensure_registry(transport, data_root, datasets=["theme_news"])
    ensure = _ensure(registry, data_root, FakeClock())
    with ensure._lock:
        ensure._inflight.add("theme")

    result = ensure.ensure("theme", day=DAY)
    assert result.triggered is False
    assert result.status == "inflight"
    assert transport.calls == []


def test_failure_degrades_and_never_raises(data_root: Path) -> None:
    transport = ensure_transport(DAY.isoformat(), failing=("wire_get_digest",))
    registry = make_ensure_registry(transport, data_root, datasets=["theme_news"])
    result = _ensure(registry, data_root, FakeClock()).ensure("theme", day=DAY)

    assert result.triggered is True
    assert result.status in {"failed", "skipped", "degraded", "empty"}
    assert result.note()
    # 失败后本机仍空（不编数、不回落演示）
    records, _ = dataset_snapshot("theme_news", DAY, data_root)
    assert records == []


# --------------------------------------------------------------------------- #
# 3. 无源不自采 / 未知组
# --------------------------------------------------------------------------- #

def test_no_source_when_dataset_undeclared(data_root: Path) -> None:
    transport = ensure_transport(DAY.isoformat())
    registry = make_ensure_registry(transport, data_root, datasets=["limit_pool"])
    result = _ensure(registry, data_root, FakeClock()).ensure("theme", day=DAY)

    assert result.triggered is False
    assert result.status == "no_source"
    assert transport.calls == [], "未登记数据集一次外呼都不发（DEC-006 闸门 4）"


def test_unknown_group_is_disabled(data_root: Path) -> None:
    transport = ensure_transport(DAY.isoformat())
    registry = make_ensure_registry(transport, data_root, datasets=["theme_news"])
    result = _ensure(registry, data_root, FakeClock()).ensure("bogus", day=DAY)

    assert result.triggered is False
    assert result.status == "disabled"
    assert transport.calls == []


def test_groups_are_exactly_the_four_pages() -> None:
    assert GROUPS == ("theme", "news", "pool", "industry")


# --------------------------------------------------------------------------- #
# 4. industry：base_pool 常规口径，绝不锚点扩池（DEC-074 付费动作仍显式）
# --------------------------------------------------------------------------- #

def test_industry_cold_start_uses_base_pool_not_anchor_expansion(data_root: Path) -> None:
    transport = ensure_transport(DAY.isoformat())
    registry = make_ensure_registry(
        transport, data_root, datasets=INDUSTRY_DATASETS, codes=ENSURE_CODES
    )
    result = _ensure(registry, data_root, FakeClock()).ensure("industry", day=DAY)

    assert result.triggered is True
    assert result.status == "ok"
    assert archive.latest_day("sw_industry_map", root=data_root) is not None
    # 逐只数据集的代码池 == 常规 base_pool 子集，没有扩到 industry.yaml 锚点之外。
    per_code = {
        str(c.params["code"])
        for c in transport.calls
        if c.tool in {"tencent_kline", "sw_industry_history", "stock_fund_flow"}
    }
    assert per_code == set(ENSURE_CODES)


def test_industry_fresh_when_map_has_any_history(data_root: Path) -> None:
    duckdb_store.write_dataset(
        "sw_industry_map",
        [{"code": "600519", "trade_date": "2026-01-05", "l1_code": "340000"}],
        root=data_root,
    )
    transport = ensure_transport(DAY.isoformat())
    registry = make_ensure_registry(transport, data_root, datasets=INDUSTRY_DATASETS)
    result = _ensure(registry, data_root, FakeClock()).ensure("industry", day=DAY)

    assert result.triggered is False
    assert result.status == "fresh"
    assert transport.calls == []