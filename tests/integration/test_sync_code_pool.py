"""代码池入参契约（QUOT-233 第 2 步）：`code` 必填的数据集逐只取数、合并落库。

真实 `tencent_kline` 空参会被 peer 拒回 `QFSD-9001`，所以这三个数据集
（`daily_k` / `fund_flow` / `dragon_tiger_seats`）必须带代码池，池子来源只有
`--codes` > `config/sync.yaml` 的 `codes` > 本机自选 `watchlist`，
**三处都为空时一次调用都不发**。

peer 全部由 `tests/fixtures/fake_peers.py` 的离线桩扮演，不发任何网络请求；
桩返回用真实形状 `kline_tool_payload`（`data` 是对象、K 线在 `klines` 里）。
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from fixtures.fake_peers import (
    SYNC_DOC,
    FakeTransport,
    kline_tool_payload,
    limit_records,
    make_registry,
    tickhub_payload,
)
from typer.testing import CliRunner

from ladderstock.cli.main import app
from ladderstock.cli.sync import MAX_POOL_CODES, run_sync, watchlist_codes
from ladderstock.data import duckdb_store, sqlite_store, watermark
from ladderstock.data.provider_registry import SourceUnavailable
from ladderstock.errors import EXIT_USAGE, LadderStockError

DAY1 = "2026-10-23"
DAY2 = "2026-10-24"
POOL = ["600000", "000001"]


@pytest.fixture()
def data_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    return tmp_path


def _store(root) -> watermark.WatermarkStore:
    return watermark.WatermarkStore(root=root)


def _sync_doc(codes: list[str] | None) -> dict[str, Any]:
    doc = copy.deepcopy(SYNC_DOC)
    doc["sync"]["datasets"]["daily_k"]["codes"] = codes or []
    return doc


def _pool_transport(
    *,
    codes: list[str] = POOL,
    days: list[str] | None = None,
    fail_codes: tuple[str, ...] = (),
) -> FakeTransport:
    """`tencent_kline` 逐只回真实形状；`fail_codes` 让主备同时失败（真·单只失败）。"""
    wanted = days or [DAY1, DAY2]

    def handler(params: dict[str, Any]) -> dict[str, Any]:
        code = str(params["code"])
        if code in fail_codes:
            raise SourceUnavailable(f"{code} 上游无数据")
        start = params.get("start")
        return kline_tool_payload(code, [d for d in wanted if not start or d >= start])

    return FakeTransport(
        handlers={
            "tencent_kline": handler,
            "tdx_daily_package": handler,
            "limit_up_pool": lambda _p: tickhub_payload(limit_records(DAY2)),
        }
    )


def _watchlist(root: Path, codes: list[str]) -> None:
    conn = sqlite_store.connect(root)
    try:
        for code in codes:
            conn.execute(
                "INSERT INTO watchlist (code, note, created_at) VALUES (?, '', ?)",
                (code, "2026-10-24T09:00:00+08:00"),
            )
    finally:
        sqlite_store.close(conn)


# --------------------------------------------------------------------------- #
# 扇出：一只代码一次调用，合并落库
# --------------------------------------------------------------------------- #

def test_pool_fans_out_one_call_per_code_and_merges_rows(data_home) -> None:
    transport = _pool_transport()
    registry = make_registry(transport, data_home, sync=_sync_doc(POOL))
    store = _store(data_home)

    payload = run_sync(registry, store, ["daily_k"])

    row = payload["results"][0]
    assert row["status"] == "ok"
    assert [call.params["code"] for call in transport.calls] == POOL
    assert all(call.params["period"] == "daily" for call in transport.calls)
    assert all(call.params["adjust"] == "qfq" for call in transport.calls)
    # 2 只 × 2 天，合并成一次落库
    assert row["rows"] == 4
    assert duckdb_store.dataset_rows("daily_k") == 4
    assert store.value("daily_k") == DAY2
    assert "代码池 2 只" in row["detail"]


def test_incremental_start_is_sent_per_code(data_home) -> None:
    transport = _pool_transport()
    registry = make_registry(transport, data_home, sync=_sync_doc(POOL))
    store = _store(data_home)
    run_sync(registry, store, ["daily_k"])

    transport.calls.clear()
    payload = run_sync(registry, store, ["daily_k"])

    assert [call.params["start"] for call in transport.calls] == [DAY2, DAY2]
    # 增量只回水位当天（重叠由 (code, trade_date) upsert 折叠），行数不增长
    assert payload["results"][0]["rows"] == 2
    assert duckdb_store.dataset_rows("daily_k") == 4


def test_full_mode_omits_start(data_home) -> None:
    transport = _pool_transport()
    registry = make_registry(transport, data_home, sync=_sync_doc(POOL))
    store = _store(data_home)
    run_sync(registry, store, ["daily_k"])
    transport.calls.clear()

    run_sync(registry, store, ["daily_k"], full=True)

    assert all("start" not in call.params for call in transport.calls)


# --------------------------------------------------------------------------- #
# 池子来源与优先级
# --------------------------------------------------------------------------- #

def test_cli_codes_override_configured_pool(data_home) -> None:
    transport = _pool_transport()
    registry = make_registry(transport, data_home, sync=_sync_doc(POOL))

    payload = run_sync(registry, _store(data_home), ["daily_k"], codes=["002172"])

    assert [call.params["code"] for call in transport.calls] == ["002172"]
    assert "命令行 --codes" in payload["results"][0]["detail"]


def test_watchlist_is_the_fallback_pool(data_home) -> None:
    """配置没写 codes 时用本机自选；池子内容不进日志，只进数量。"""
    _watchlist(data_home, ["600000", "300750"])
    transport = _pool_transport()
    registry = make_registry(transport, data_home, sync=_sync_doc([]))

    payload = run_sync(registry, _store(data_home), ["daily_k"])

    assert [call.params["code"] for call in transport.calls] == ["600000", "300750"]
    detail = payload["results"][0]["detail"]
    assert "本机自选(watchlist)" in detail
    assert "300750" not in detail


def test_configured_pool_wins_over_watchlist(data_home) -> None:
    _watchlist(data_home, ["300750"])
    transport = _pool_transport()
    registry = make_registry(transport, data_home, sync=_sync_doc(["600000"]))

    run_sync(registry, _store(data_home), ["daily_k"])

    assert [call.params["code"] for call in transport.calls] == ["600000"]


def test_watchlist_codes_tolerate_a_missing_private_db(tmp_path) -> None:
    """自选只读代码：不能因为「查一下有没有自选」就在磁盘上建一个空隐私库。"""
    assert watchlist_codes(tmp_path) == []
    assert not sqlite_store.db_path(tmp_path).exists()


# --------------------------------------------------------------------------- #
# 空池 / 超限 / 空返回
# --------------------------------------------------------------------------- #

def test_empty_pool_calls_nothing_and_fails(data_home) -> None:
    transport = _pool_transport()
    registry = make_registry(transport, data_home, sync=_sync_doc([]))

    payload = run_sync(registry, _store(data_home), ["daily_k"])

    assert transport.calls == []
    row = payload["results"][0]
    assert row["status"] == "failed"
    assert payload["summary"]["exit_code"] == 4
    assert "代码池为空" in row["detail"]
    # detail 要把三个来源和上游错误码都点出来，否则没人知道去哪配
    assert "--codes" in row["detail"]
    assert "sync.datasets.daily_k.codes" in row["detail"]
    assert "QFSD-9001" in row["detail"]


def test_pool_over_cap_is_a_usage_error(data_home) -> None:
    transport = _pool_transport()
    codes = [f"{i:06d}" for i in range(MAX_POOL_CODES + 1)]
    registry = make_registry(transport, data_home, sync=_sync_doc(codes))

    with pytest.raises(LadderStockError) as excinfo:
        run_sync(registry, _store(data_home), ["daily_k"])

    assert excinfo.value.code == EXIT_USAGE
    assert transport.calls == []


def test_rows_without_code_are_not_written(data_home) -> None:
    """上游回空 `code`：行级代码补不出来，整批丢弃 → 状态 empty，不覆盖已有数据。"""
    duckdb_store.write_dataset(
        "daily_k", [{"code": "600000", "trade_date": DAY1}], root=data_home
    )
    before = duckdb_store.dataset_rows("daily_k")

    transport = FakeTransport(handlers={"tencent_kline": lambda _p: kline_tool_payload("", [DAY2])})
    registry = make_registry(transport, data_home, sync=_sync_doc(["600000"]))
    payload = run_sync(registry, _store(data_home), ["daily_k"])

    assert payload["results"][0]["status"] == "empty"
    assert duckdb_store.dataset_rows("daily_k") == before


# --------------------------------------------------------------------------- #
# 部分失败与缓存回放
# --------------------------------------------------------------------------- #

def test_one_code_failing_does_not_drop_the_others(data_home) -> None:
    transport = _pool_transport(fail_codes=("000001",))
    registry = make_registry(transport, data_home, sync=_sync_doc(POOL))
    store = _store(data_home)

    payload = run_sync(registry, store, ["daily_k"])

    row = payload["results"][0]
    assert row["status"] == "degraded"
    assert row["rows"] == 2  # 只有 600000 的两天进了这一批
    assert payload["summary"]["exit_code"] == 0
    assert duckdb_store.dataset_rows("daily_k") == 2
    assert "成功 1 只" in row["detail"]
    assert "失败 1 只" in row["detail"]


def test_all_codes_failed_replays_local_cache(data_home) -> None:
    duckdb_store.write_dataset("daily_k", [{"code": "600000", "trade_date": DAY1}], root=data_home)
    transport = FakeTransport(
        errors={
            "tencent_kline": TimeoutError("timed out"),
            "tdx_daily_package": ConnectionError("refused"),
        }
    )
    store = _store(data_home)

    registry = make_registry(transport, data_home, sync=_sync_doc(POOL))
    payload = run_sync(registry, store, ["daily_k"])

    row = payload["results"][0]
    assert payload["summary"]["exit_code"] == 0
    assert row["status"] == "degraded"
    assert row["from_cache"] is True
    assert store.value("daily_k") is None
    assert duckdb_store.dataset_rows("daily_k") == 1


# --------------------------------------------------------------------------- #
# 非代码池数据集不受影响
# --------------------------------------------------------------------------- #

def test_datasets_without_code_pool_still_send_no_code(data_home) -> None:
    transport = _pool_transport()
    registry = make_registry(transport, data_home, sync=_sync_doc(POOL))

    run_sync(registry, _store(data_home), ["limit_pool"])

    assert transport.calls[-1].params == {}


# --------------------------------------------------------------------------- #
# CLI 接线
# --------------------------------------------------------------------------- #

def test_cli_accepts_comma_separated_codes(repo, data_home, monkeypatch) -> None:
    transport = _pool_transport()
    monkeypatch.setattr(
        "ladderstock.data.provider_registry.build_transport", lambda _peers: transport
    )

    result = CliRunner().invoke(
        app, ["sync", "--dataset", "daily_k", "--codes", "600000,000001"]
    )

    assert result.exit_code == 0, result.stderr
    assert [call.params["code"] for call in transport.calls] == ["600000", "000001"]
