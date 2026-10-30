"""增量同步契约：水位驱动 `since`、同日重跑不增长、`--full` 忽略水位、断点续跑。

所有 peer 都由 `tests/fixtures/fake_peers.py` 的离线桩扮演，不出现真实域名与网络请求。
"""

from __future__ import annotations

import json

import pytest
from fixtures.fake_peers import (
    FakeTransport,
    kline_records,
    limit_records,
    make_registry,
    tickhub_payload,
)
from typer.testing import CliRunner

from ladderstock.cli.main import app
from ladderstock.cli.sync import run_sync
from ladderstock.data import duckdb_store, watermark

DAY1 = "2026-10-24"
DAY2 = "2026-10-25"

#: daily_k 走代码池分支后的真实入参（`tencent_kline` 的 code 必填，口径显式带上）
KLINE_PARAMS = {"code": "600000", "period": "daily", "adjust": "qfq"}


@pytest.fixture()
def data_home(tmp_path, monkeypatch):
    """`sync_one` 落库走 `data_dir()`，所以用环境变量把数据目录钉在 tmp。"""
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    return tmp_path


def _store(root) -> watermark.WatermarkStore:
    return watermark.WatermarkStore(root=root)


def _clear_cooldowns(root) -> None:
    """清掉持久化冷却，等价于「等冷却窗口到期后再续跑」。

    冷却按设计写进 `<data_dir>/state/health.json`（DEC-007 §2.4），
    所以第一次失败后立刻重跑仍会被冷却挡住——这是正确行为，不是缺陷。
    """
    (root / "state" / "health.json").unlink(missing_ok=True)


def _kline_transport() -> FakeTransport:
    return FakeTransport(
        handlers={"tencent_kline": lambda _params: tickhub_payload(kline_records(DAY1))}
    )


# --------------------------------------------------------------------------- #
# 水位
# --------------------------------------------------------------------------- #

def test_first_run_is_full_then_next_run_sends_since(data_home) -> None:
    transport = _kline_transport()
    registry = make_registry(transport, data_home)
    store = _store(data_home)

    first = run_sync(registry, store, ["daily_k"])

    assert first["summary"]["exit_code"] == 0
    assert first["results"][0]["status"] == "ok"
    assert first["results"][0]["watermark_before"] is None
    assert first["results"][0]["watermark_after"] == DAY1
    assert transport.calls[0].params == KLINE_PARAMS
    assert store.value("daily_k") == DAY1

    second = run_sync(registry, store, ["daily_k"])

    # 增量水位落到工具真正的入参名上：tencent_kline 没有 since，只有 start（含当日）
    assert transport.calls[1].params == {**KLINE_PARAMS, "start": DAY1}
    assert second["results"][0]["watermark_before"] == DAY1
    assert second["results"][0]["mode"] == "incremental"


def test_rerun_on_same_day_does_not_grow_rows(data_home) -> None:
    transport = _kline_transport()
    registry = make_registry(transport, data_home)
    store = _store(data_home)

    run_sync(registry, store, ["daily_k"])
    rows_after_first = duckdb_store.dataset_rows("daily_k")
    run_sync(registry, store, ["daily_k"])

    assert rows_after_first == 2
    assert duckdb_store.dataset_rows("daily_k") == 2


def test_full_mode_ignores_watermark_but_does_not_rewind_it(data_home) -> None:
    transport = _kline_transport()
    registry = make_registry(transport, data_home)
    store = _store(data_home)
    run_sync(registry, store, ["daily_k"])

    payload = run_sync(registry, store, ["daily_k"], full=True)

    assert transport.calls[-1].params == KLINE_PARAMS
    assert payload["mode"] == "full"
    assert payload["results"][0]["mode"] == "full"
    assert store.value("daily_k") == DAY1


def test_new_trading_day_advances_watermark(data_home) -> None:
    state = {"day": DAY1}
    transport = FakeTransport(
        handlers={"tencent_kline": lambda _params: tickhub_payload(kline_records(state["day"]))}
    )
    registry = make_registry(transport, data_home)
    store = _store(data_home)

    run_sync(registry, store, ["daily_k"])
    state["day"] = DAY2
    run_sync(registry, store, ["daily_k"])

    assert store.value("daily_k") == DAY2
    assert duckdb_store.dataset_rows("daily_k") == 4
    assert transport.calls[1].params == {**KLINE_PARAMS, "start": DAY1}


def test_each_dataset_has_its_own_watermark(data_home) -> None:
    transport = FakeTransport(
        handlers={
            "tencent_kline": lambda _params: tickhub_payload(kline_records(DAY2)),
            "limit_up_pool": lambda _params: tickhub_payload(limit_records(DAY1)),
        }
    )
    registry = make_registry(transport, data_home)
    store = _store(data_home)

    payload = run_sync(registry, store, ["daily_k", "limit_pool"])

    assert payload["summary"]["exit_code"] == 0
    assert store.value("daily_k") == DAY2
    assert store.value("limit_pool") == DAY1


def test_replace_dataset_rerun_does_not_duplicate(data_home) -> None:
    transport = FakeTransport(
        handlers={"limit_up_pool": lambda _params: tickhub_payload(limit_records(DAY1))}
    )
    registry = make_registry(transport, data_home)
    store = _store(data_home)

    run_sync(registry, store, ["limit_pool"])
    run_sync(registry, store, ["limit_pool"])

    assert duckdb_store.dataset_rows("limit_pool") == 3


# --------------------------------------------------------------------------- #
# 失败与退出码
# --------------------------------------------------------------------------- #

def test_partial_failure_exits_8_and_can_resume(data_home) -> None:
    broken = FakeTransport(
        handlers={"tencent_kline": lambda _params: tickhub_payload(kline_records(DAY1))},
        errors={"limit_up_pool": TimeoutError("timed out")},
    )
    registry = make_registry(broken, data_home)
    store = _store(data_home)

    first = run_sync(registry, store, ["daily_k", "limit_pool"])

    assert first["summary"]["exit_code"] == 8
    assert first["summary"]["failed"] == 1
    assert first["summary"]["ok"] == 1
    statuses = {row["dataset"]: row["status"] for row in first["results"]}
    assert statuses == {"daily_k": "ok", "limit_pool": "failed"}
    assert store.value("limit_pool") is None

    healthy = FakeTransport(
        handlers={
            "tencent_kline": lambda _params: tickhub_payload(kline_records(DAY1)),
            "limit_up_pool": lambda _params: tickhub_payload(limit_records(DAY1)),
        }
    )
    _clear_cooldowns(data_home)
    resumed = run_sync(make_registry(healthy, data_home), store, ["limit_pool"])

    assert resumed["summary"]["exit_code"] == 0
    # 续跑只补没跑完的数据集，已完成的不重复写
    assert duckdb_store.dataset_rows("daily_k") == 2
    assert duckdb_store.dataset_rows("limit_pool") == 3


def test_all_sources_down_exits_4(data_home) -> None:
    transport = FakeTransport(
        errors={
            "tencent_kline": TimeoutError("timed out"),
            "tdx_daily_package": ConnectionError("refused"),
        }
    )
    payload = run_sync(make_registry(transport, data_home), _store(data_home), ["daily_k"])

    assert payload["summary"]["exit_code"] == 4
    assert payload["results"][0]["status"] == "failed"


def test_optional_dataset_is_skipped_without_failing_sync(data_home) -> None:
    transport = FakeTransport(
        errors={
            "wire_get_digest": ConnectionError("refused"),
            "wire_get_timeline": ConnectionError("refused"),
        }
    )
    payload = run_sync(make_registry(transport, data_home), _store(data_home), ["theme_news"])

    assert payload["summary"]["exit_code"] == 0
    assert payload["summary"]["skipped"] == 1
    assert payload["results"][0]["status"] == "skipped"
    assert payload["results"][0]["optional"] is True


def test_cache_replay_does_not_rewrite_or_advance_watermark(data_home) -> None:
    duckdb_store.write_dataset(
        "daily_k", kline_records(DAY1), source="tickhub_kline", root=data_home
    )
    transport = FakeTransport(
        errors={
            "tencent_kline": TimeoutError("timed out"),
            "tdx_daily_package": ConnectionError("refused"),
        }
    )
    store = _store(data_home)

    payload = run_sync(make_registry(transport, data_home), store, ["daily_k"])

    row = payload["results"][0]
    assert payload["summary"]["exit_code"] == 0
    assert row["status"] == "degraded"
    assert row["from_cache"] is True
    assert row["rows"] == 0
    assert store.value("daily_k") is None
    assert duckdb_store.dataset_rows("daily_k") == 2


def test_empty_response_does_not_delete_existing_day(data_home) -> None:
    duckdb_store.write_dataset("daily_k", kline_records(DAY1), root=data_home)
    transport = FakeTransport(handlers={"tencent_kline": lambda _params: tickhub_payload([])})
    store = _store(data_home)

    payload = run_sync(make_registry(transport, data_home), store, ["daily_k"])

    assert payload["results"][0]["status"] == "empty"
    assert payload["summary"]["exit_code"] == 0
    assert store.value("daily_k") is None
    assert duckdb_store.dataset_rows("daily_k") == 2


# --------------------------------------------------------------------------- #
# CLI 接线
# --------------------------------------------------------------------------- #

def test_cli_sync_dataset_succeeds_and_prints_json(repo, data_home, monkeypatch) -> None:
    transport = _kline_transport()
    monkeypatch.setattr(
        "ladderstock.data.provider_registry.build_transport", lambda _peers: transport
    )

    result = CliRunner().invoke(app, ["sync", "--dataset", "daily_k", "--codes", "600000"])

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["summary"]["exit_code"] == 0
    assert payload["results"][0]["dataset"] == "daily_k"
    assert payload["results"][0]["status"] == "ok"
    assert transport.calls[0].params == KLINE_PARAMS


def test_cli_sync_status_reports_watermarks(repo, data_home, monkeypatch) -> None:
    transport = _kline_transport()
    monkeypatch.setattr(
        "ladderstock.data.provider_registry.build_transport", lambda _peers: transport
    )
    CliRunner().invoke(app, ["sync", "--dataset", "daily_k", "--codes", "600000"])

    result = CliRunner().invoke(app, ["sync", "status"])

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert [row["dataset"] for row in payload["watermarks"]] == ["daily_k"]
    assert payload["data_dir"] == str(data_home)
