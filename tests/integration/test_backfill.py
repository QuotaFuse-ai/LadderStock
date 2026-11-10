"""`ldst backfill` 端到端口径（QUOT-501）：逐只取数、一次事务落库、缺口报表、重跑去重。

全部走 `tests/fixtures/fake_peers.py` 的离线桩（真实 `klines` 形状），不拨网络；
`daily_k` / `index_daily` 落临时数据目录，断言的是**库里读回来的行数**，不是内存返回。
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from fixtures.fake_peers import (
    SOURCES_DOC,
    SYNC_DOC,
    FakeTransport,
    kline_tool_payload,
    make_registry,
)
from typer.testing import CliRunner

from ladderstock.cli.backfill import (
    apply_source_policy,
    ensure_sources_registered,
    run_backfill,
)
from ladderstock.cli.main import app
from ladderstock.data import duckdb_store, watermark
from ladderstock.data.provider_registry import (
    ProviderRegistry,
    SourceUnavailable,
    load_sync_config,
)
from ladderstock.errors import EXIT_CONFIG, LadderStockError

BENCH_DAYS = ["2022-01-04", "2022-01-05", "2022-01-06", "2022-01-07"]
STOCK_DAYS: dict[str, list[str]] = {
    "600000": BENCH_DAYS,
    "000001": ["2022-01-04", "2022-01-05", "2022-01-07"],
    "002594": BENCH_DAYS,
}

CFG: dict[str, Any] = {
    "source": "tencent",
    "stage": "阶段1测试：腾讯 qfq（窗口覆盖仅数日，用于口径验证）",
    "start": "2022-01-04",
    "end": "",
    "target_years": 5,
    "throttle_ms": 0,
    "max_consecutive_failures": 3,
    "gap_rate_threshold": 0.02,
    "report_dir": "reports",
    "benchmark": {"code": "sh000001", "name": "上证指数", "days": 2000},
    "universe": [
        {"code": "600000", "name": "浦发银行", "board": "SH主板"},
        {"code": "000001", "name": "平安银行", "board": "SZ主板"},
        {"code": "002594", "name": "比亚迪", "board": "SZ主板"},
    ],
}


@pytest.fixture()
def data_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    return tmp_path


def _docs() -> tuple[dict[str, Any], dict[str, Any]]:
    sources = copy.deepcopy(SOURCES_DOC)
    sources["sources"].append(
        {
            "id": "tickhub_index_daily",
            "dataset": "index_daily",
            "via": "tickhub",
            "tool": "index_kline",
            "primary": True,
            "fallback": [],
            "timeout_sec": 1,
            "retries": 0,
            "cooldown_sec": 60,
        }
    )
    sync = copy.deepcopy(SYNC_DOC)
    sync["sync"]["datasets"]["index_daily"] = {
        "primary": "tickhub_index_daily",
        "fallback": [],
        "watermark_field": "trade_date",
        "mode": "append",
        "codes": ["sh000001"],
    }
    return sources, sync


def _transport(
    *,
    fail_codes: tuple[str, ...] = (),
    empty_codes: tuple[str, ...] = (),
) -> FakeTransport:
    def kline(params: dict[str, Any]) -> dict[str, Any]:
        code = str(params["code"])
        if code in fail_codes:
            raise SourceUnavailable(f"{code} 上游无数据")
        days = [] if code in empty_codes else STOCK_DAYS.get(code, BENCH_DAYS)
        return kline_tool_payload(code, days)

    def index(params: dict[str, Any]) -> dict[str, Any]:
        # 真实 `index_kline` 会把 `sh000001` 归一成 `000001` 回包，这里照实模拟
        raw = str(params["code"]).strip().lower()
        bare = raw[2:] if raw.startswith(("sh", "sz")) else raw
        return kline_tool_payload(bare, BENCH_DAYS)

    return FakeTransport(handlers={"tencent_kline": kline, "index_kline": index})


def _run(
    data_home: Path,
    transport: FakeTransport,
    *,
    codes: list[str],
    cfg: dict[str, Any] | None = None,
):
    sources, sync = _docs()
    registry = make_registry(transport, data_home, sources=sources, sync=sync)
    store = watermark.WatermarkStore(root=data_home)
    return run_backfill(
        registry,
        store,
        cfg or CFG,
        codes=codes,
        start="2022-01-04",
        report_base=data_home / "reports",
    ), store


def test_backfill_writes_bars_reports_gaps_and_reruns_idempotently(data_home) -> None:
    transport = _transport()
    payload, store = _run(data_home, transport, codes=["600000", "000001"])

    assert payload["exit_code"] == 0, payload
    # 取数口径：逐只一次，daily + qfq + 窗口起点
    calls = [call.params for call in transport.calls if call.tool == "tencent_kline"]
    assert [call["code"] for call in calls] == ["600000", "000001"]
    assert all(call["period"] == "daily" and call["adjust"] == "qfq" for call in calls)
    assert all(call["start"] == "2022-01-04" for call in calls)

    # 落库口径：直接数库
    assert duckdb_store.dataset_rows("daily_k", root=data_home) == 7
    assert duckdb_store.dataset_rows("index_daily", root=data_home) == len(BENCH_DAYS)
    assert payload["daily_k"]["before_rows"] == 0
    assert payload["daily_k"]["new_rows"] == 7

    stats = {row["code"]: row for row in payload["codes"]}
    assert stats["600000"]["gap_rate"] == 0.0
    assert stats["000001"]["missing_days_in_span"] == 1
    assert stats["000001"]["gap_alert"] is True
    assert stats["000001"]["missing_ranges"]["ranges"] == ["2022-01-06"]
    assert stats["000001"]["amount_missing_rate"] == 1.0  # 桩不带 amount：如实统计
    assert payload["summary"]["gap_alert"] == 1
    assert payload["summary"]["target_met"] == 0

    md_path = Path(payload["reports"]["markdown"])
    csv_path = Path(payload["reports"]["csv"])
    markdown = md_path.read_text(encoding="utf-8")
    assert "缺口率 > 2%" in markdown
    assert "000001" in markdown
    assert "未接入停牌日历" in markdown
    assert "amount 全缺" in markdown
    assert len(csv_path.read_text(encoding="utf-8-sig").strip().splitlines()) == 4

    assert store.value("daily_k") == "2022-01-07"
    assert store.value("index_daily") == "2022-01-07"
    runs = store.runs(limit=5)
    assert any(run["dataset"] == "daily_k" and run["mode"] == "backfill" for run in runs)

    # 重跑：append 按 (code, trade_date) upsert，总行数不增长
    again, _ = _run(data_home, _transport(), codes=["600000", "000001"])
    assert again["daily_k"]["before_rows"] == 7
    assert again["daily_k"]["written"] == 7
    assert again["daily_k"]["after_rows"] == 7
    assert again["daily_k"]["new_rows"] == 0
    assert again["daily_k"]["existing_rows"] == 7
    assert duckdb_store.dataset_rows("daily_k", root=data_home) == 7


def test_single_failure_does_not_abort_batch(data_home) -> None:
    payload, _ = _run(
        data_home, _transport(fail_codes=("000001",)), codes=["600000", "000001", "002594"]
    )

    assert payload["exit_code"] == 8
    assert payload["failures"] and payload["failures"][0].startswith("000001")
    # 失败的票不拖垮其余两只：600000 + 002594 各 4 行
    assert duckdb_store.dataset_rows("daily_k", root=data_home) == 8
    statuses = {row["code"]: row["status"] for row in payload["codes"]}
    assert statuses == {"600000": "ok", "000001": "failed", "002594": "ok"}
    assert "000001" in Path(payload["reports"]["markdown"]).read_text(encoding="utf-8")


def test_consecutive_failures_stop_the_fanout_and_list_not_attempted(data_home) -> None:
    cfg = copy.deepcopy(CFG)
    cfg["max_consecutive_failures"] = 2
    payload, _ = _run(
        data_home,
        _transport(fail_codes=("600000", "000001")),
        codes=["600000", "000001", "002594"],
        cfg=cfg,
    )

    # 一只都没取到 -> 4，但失败与止损清单必须说清楚
    assert payload["exit_code"] == 4
    assert payload["not_attempted"] == ["002594"]
    assert duckdb_store.dataset_rows("daily_k", root=data_home) == 0
    markdown = Path(payload["reports"]["markdown"]).read_text(encoding="utf-8")
    assert "止损未发起" in markdown
    assert "002594" in markdown


def test_empty_result_is_reported_not_fabricated(data_home) -> None:
    payload, _ = _run(
        data_home, _transport(empty_codes=("000001",)), codes=["600000", "000001"]
    )

    assert payload["exit_code"] == 8
    statuses = {row["code"]: row["status"] for row in payload["codes"]}
    assert statuses["000001"] == "empty"
    assert duckdb_store.dataset_rows("daily_k", root=data_home) == 4


def test_eastmoney_stage_refuses_unregistered_sources(repo, monkeypatch) -> None:
    """阶段2 的东财 source 未登记时明确报配置错误，不静默退回腾讯。"""
    monkeypatch.setattr(
        "ladderstock.cli.backfill.load_sources_config",
        lambda: copy.deepcopy(SOURCES_DOC),
    )
    with pytest.raises(LadderStockError) as caught:
        ensure_sources_registered("eastmoney")
    assert caught.value.code == EXIT_CONFIG
    assert "tickhub_em_kline" in caught.value.message


def test_eastmoney_stage_resolves_registered_em_sources(repo) -> None:
    """仓库里的 sources.yaml 已登记东财出口，阶段2 的链路要能直接组出来。"""
    ensure_sources_registered("eastmoney")
    registry = ProviderRegistry(
        sync=apply_source_policy(load_sync_config(), "eastmoney")
    )
    assert [spec.id for spec in registry.chain("daily_k")] == ["tickhub_em_kline"]
    assert [spec.tool for spec in registry.chain("daily_k")] == ["em_kline"]
    assert [spec.id for spec in registry.chain("index_daily")] == ["tickhub_em_index"]


def test_backfill_command_is_registered_offline(repo, tmp_path, monkeypatch) -> None:
    """空桩下基准取不到 -> 4；不落报表、不写仓库 data/。"""
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "ladderstock.data.provider_registry.build_transport",
        lambda _peers: FakeTransport(),
    )
    result = CliRunner().invoke(app, ["backfill", "--output", "json"])
    assert result.exit_code == 4, result.stderr
    assert "reports" in result.stdout
