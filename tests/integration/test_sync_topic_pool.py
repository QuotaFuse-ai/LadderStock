"""赛道池入参契约（QUOT-328）：`topic` 必填的 Wire 数据集按赛道 slug 逐个取数。

真实 Wire 的 `wire_get_digest(topic, date=None)` 里 `topic` 是**必填赛道 slug**
（`ai` / `semiconductor` / …，见 Wire 的 `config/topics.yaml`），
**没有「空参 = 取全部赛道」的调用形状**——空参直接吃
`1 validation error for wire_get_digestArguments / topic Field required`，
于是 `theme_news` 永远 `unavailable`，QUOT-311 / QUOT-313 的题材字段被整条挡掉。
中文赛道名（`半导体`）上游判「未知赛道」，所以池子里只放 slug。

第二个形状缺陷也在这里钉住：真实返回是 `{"structuredContent": {"result": "<JSON 字符串>"}}`
（Wire 工具回的是 `envelope.model_dump_json()`），拆不掉这层壳的话，**参数修对了落库仍是空壳**。

peer 全部由 `tests/fixtures/fake_peers.py` 的离线桩扮演，不发任何网络请求。
"""

from __future__ import annotations

import copy
from typing import Any

import pytest
from fixtures.fake_peers import (
    SYNC_DOC,
    FakeTransport,
    make_registry,
    wire_digest_envelope,
    wire_digest_tool_result,
)

from ladderstock.cli.sync import FANOUT_FAILURE_STOP, MAX_POOL_CODES, run_sync
from ladderstock.data import duckdb_store, watermark
from ladderstock.data.provider_registry import SourceUnavailable
from ladderstock.errors import EXIT_CONFIG, EXIT_USAGE, LadderStockError

DAY = "2026-11-01"
POOL = ["ai", "semiconductor"]


@pytest.fixture()
def data_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    return tmp_path


def _store(root) -> watermark.WatermarkStore:
    return watermark.WatermarkStore(root=root)


def _sync_doc(topics: list[str] | None, *, fallback: list[str] | None = None) -> dict[str, Any]:
    doc = copy.deepcopy(SYNC_DOC)
    dataset = doc["sync"]["datasets"]["theme_news"]
    dataset["topics"] = topics if topics is not None else []
    if fallback is not None:
        dataset["fallback"] = fallback
    return doc


def _digest_transport(
    *,
    day: str = DAY,
    fail_topics: tuple[str, ...] = (),
    status: str = "ok",
    raw_shell: bool = True,
) -> FakeTransport:
    """`wire_get_digest` 逐赛道回真实形状。

    `raw_shell=True` 回 `{"structuredContent": {"result": "<json>"}}`（真实 FastMCP 外壳），
    `False` 直接回解开的信封（桩的另一半形状，锁两条路都能落库）。
    """
    build = wire_digest_tool_result if raw_shell else wire_digest_envelope

    def handler(params: dict[str, Any]) -> dict[str, Any]:
        topic = str(params["topic"])
        if topic in fail_topics:
            raise SourceUnavailable(f"未知赛道: {topic}")
        return build(topic, day, status=status)

    return FakeTransport(handlers={"wire_get_digest": handler})


# --------------------------------------------------------------------------- #
# 扇出：一个 slug 一次调用
# --------------------------------------------------------------------------- #

def test_topic_pool_fans_out_one_call_per_slug(data_home) -> None:
    transport = _digest_transport()
    registry = make_registry(transport, data_home, sync=_sync_doc(POOL))

    payload = run_sync(registry, _store(data_home), ["theme_news"])

    row = payload["results"][0]
    assert [call.tool for call in transport.calls] == ["wire_get_digest"] * 2
    assert [call.params for call in transport.calls] == [{"topic": p} for p in POOL]
    assert row["status"] == "ok"
    # 2 个赛道各 1 行，合并成一次落库（replace 模式下同一天多行不互相覆盖）
    assert row["rows"] == 2
    assert duckdb_store.dataset_rows("theme_news") == 2
    assert row["days"] == [DAY]
    assert "赛道池 2 个" in row["detail"]


def test_written_rows_carry_the_narrative_not_an_empty_shell(data_home) -> None:
    """拆壳的回归：落库的 payload 必须有 `topic` / `summary`，不是 `{}` 空壳。"""
    transport = _digest_transport()
    registry = make_registry(transport, data_home, sync=_sync_doc(POOL))

    run_sync(registry, _store(data_home), ["theme_news"])

    assert duckdb_store.dataset_rows("theme_news", root=data_home) == 2
    # `query_dataset` 已把 payload 列解成 dict 摊平返回
    payloads = duckdb_store.query_dataset("theme_news", root=data_home)
    assert sorted(p["topic"] for p in payloads) == sorted(POOL)
    assert all(p["summary"] for p in payloads)
    assert all(p["items"] for p in payloads)


def test_plain_envelope_without_the_shell_also_lands(data_home) -> None:
    transport = _digest_transport(raw_shell=False)
    registry = make_registry(transport, data_home, sync=_sync_doc(POOL))

    payload = run_sync(registry, _store(data_home), ["theme_news"])

    assert payload["results"][0]["rows"] == 2


# --------------------------------------------------------------------------- #
# 空池：一次调用都不发
# --------------------------------------------------------------------------- #

def test_empty_topic_pool_issues_no_calls_and_skips(data_home) -> None:
    transport = _digest_transport()
    registry = make_registry(transport, data_home, sync=_sync_doc([]))

    payload = run_sync(registry, _store(data_home), ["theme_news"])

    assert transport.calls == []
    row = payload["results"][0]
    assert row["status"] == "skipped"  # 可选数据集：不判 failed（DEC-006 阶梯）
    assert payload["summary"]["exit_code"] == 0
    assert "赛道池为空" in row["detail"]
    # detail 要说清去哪配、以及为什么不能空参
    assert "sync.datasets.theme_news.topics" in row["detail"]
    assert "topic 必填" in row["detail"]
    assert duckdb_store.dataset_rows("theme_news", root=data_home) == 0


def test_topic_pool_over_cap_is_a_usage_error(data_home) -> None:
    """上限是 peer 侧并发/最小间隔决定的，超了要拆批而不是悄悄截断（与代码池同一口径）。"""
    topics = [f"track{i}" for i in range(MAX_POOL_CODES + 1)]
    transport = _digest_transport()
    registry = make_registry(transport, data_home, sync=_sync_doc(topics))

    with pytest.raises(LadderStockError) as excinfo:
        run_sync(registry, _store(data_home), ["theme_news"])

    assert excinfo.value.code == EXIT_USAGE
    assert transport.calls == []


def test_blank_topic_is_a_config_error_not_a_call(data_home) -> None:
    from ladderstock.data.adapters.wire import tool_params

    with pytest.raises(LadderStockError) as excinfo:
        tool_params("wire_get_digest", topic="  ")
    assert excinfo.value.code == EXIT_CONFIG


# --------------------------------------------------------------------------- #
# 链路守卫：`event_id` 维度的备源不进按日链路（DEC-043）
# --------------------------------------------------------------------------- #

def test_non_topic_fallback_in_chain_is_a_config_error(data_home) -> None:
    transport = _digest_transport()
    registry = make_registry(
        transport, data_home, sync=_sync_doc(POOL, fallback=["wire_timeline"])
    )

    with pytest.raises(LadderStockError) as excinfo:
        run_sync(registry, _store(data_home), ["theme_news"])

    assert excinfo.value.code == EXIT_CONFIG
    assert "event_id" in str(excinfo.value)
    assert transport.calls == []


def test_explicit_empty_fallback_is_not_refilled_from_sources(data_home) -> None:
    """`sync.yaml` 的 `fallback: []` 是声明，不回填 `sources.yaml`——否则 `wire_timeline`
    会悄悄回到链路里，主源失败时就是一次注定失败的 `event_id` 调用。"""
    transport = _digest_transport()
    registry = make_registry(transport, data_home, sync=_sync_doc(POOL))

    assert [spec.tool for spec in registry.chain("theme_news")] == ["wire_get_digest"]


# --------------------------------------------------------------------------- #
# 部分失败：一个坏 slug 不废掉整池
# --------------------------------------------------------------------------- #

def test_one_unknown_slug_does_not_drop_the_rest(data_home) -> None:
    """冷却是**跨轮**闸门：单个入参被上游拒了不等于 peer 不健康。
    照旧吃冷却，就是「1 个坏赛道废掉整池」——本轮实测到的形态。"""
    topics = ["no-such-track-xyz", *POOL]
    transport = _digest_transport(fail_topics=("no-such-track-xyz",))
    registry = make_registry(transport, data_home, sync=_sync_doc(topics))
    store = _store(data_home)

    payload = run_sync(registry, store, ["theme_news"])

    row = payload["results"][0]
    assert len(transport.calls) == 3, "失败后剩余赛道必须仍然被发起"
    assert row["status"] == "degraded"
    assert row["rows"] == 2
    assert "冷却" not in row["detail"]
    assert "成功 2 个" in row["detail"]
    assert "失败 1 个" in row["detail"]
    assert "no-such-track-xyz" in row["detail"]
    assert store.value("theme_news") == DAY


def test_fanout_stops_after_consecutive_failures(data_home) -> None:
    """peer 真不健康时不能把整池撞一遍：连续失败到上限就止损，剩余如实报「未发起调用」。"""
    topics = [f"track-{i}" for i in range(FANOUT_FAILURE_STOP + 2)]
    transport = _digest_transport(fail_topics=tuple(topics))
    registry = make_registry(transport, data_home, sync=_sync_doc(topics))

    payload = run_sync(registry, _store(data_home), ["theme_news"])

    row = payload["results"][0]
    assert len(transport.calls) == FANOUT_FAILURE_STOP
    assert row["status"] == "skipped"  # 可选数据集
    assert "止损" in row["detail"]
    assert "未发起调用" in row["detail"]


# --------------------------------------------------------------------------- #
# 降级：上游没有的字段就是 null，不冒 0
# --------------------------------------------------------------------------- #

def test_partial_digest_is_reported_degraded_not_ok(data_home) -> None:
    """Wire 的 LLM 上游不可达时回 `status=partial` + 兜底 summary：
    同步台账必须记 `degraded`，不能因为「有行落下来」就报 ok。"""
    transport = _digest_transport(status="partial")
    registry = make_registry(transport, data_home, sync=_sync_doc(POOL))
    store = _store(data_home)

    payload = run_sync(registry, store, ["theme_news"])

    row = payload["results"][0]
    assert row["status"] == "degraded"
    assert row["rows"] == 2  # 叙事仍然落库（有真实 items），不是丢弃
    assert store.value("theme_news") == DAY
