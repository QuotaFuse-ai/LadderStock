"""15:35 收盘全量同步任务（QUOT-347，kind=`sync_close`）。

钉住四件事：

1. 注册面：schema / 结构自检 / 内置默认表 / 仓库 `schedule.yaml` 都认 `sync_close`，
   且默认表多了 `15:35` 那一条（共 9 条）；
2. 分发面：`run_job` 把 `sync_close` 路由到收盘同步，按 `CLOSE_SYNC_DATASETS` 的
   **顺序**复用 `sync.run_sync`，复盘失败只把状态降级、不把同步带崩；
3. 边界：仅交易日动作——周末整轮 `skipped`，不建目录、不碰 registry；单轮同步抛
   配置类错误只折进 `failed`，不影响 15:05 对账（两者是独立触发点）；
4. 落库面（真链路，离线桩）：交易日的候选池数据集落进 `archive/YYYY/MM/DD/` 的
   Parquet 日分区；同日重复触发不产生重复分区；`fund_flow` 条数随收盘刷新增长。
"""

from __future__ import annotations

import copy
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fixtures.fake_peers import (
    FakeTransport,
    make_registry,
    tickhub_payload,
)

from ladderstock.cli.schedule import (
    CLOSE_SYNC_DATASETS,
    DEFAULT_JOBS,
    _default_table,
    _structural_problems,
    load_schedule,
    run_job,
    validate_table,
)
from ladderstock.errors import EXIT_CONFIG, EXIT_OK, EXIT_PARTIAL, EXIT_SOURCE, LadderStockError

#: 2026-11-04 是周三（交易日），15:35 触发。
TRADING_DAY = "2026-11-04"
TRADING_MOMENT = datetime(2026, 11, 4, 15, 35, 0)
#: 2026-11-07 是周六（非交易日）。
WEEKEND_MOMENT = datetime(2026, 11, 7, 15, 35, 0)

SYNC_CLOSE_JOB = {
    "id": "close_sync",
    "name": "15:35 收盘全量同步",
    "kind": "sync_close",
    "trigger": "15:35",
    "enabled": True,
}

# --------------------------------------------------------------------------- #
# 收盘三数据集的离线配置（sources/sync 的最小子集，够走真 run_sync）
# --------------------------------------------------------------------------- #

_CLOSE_SOURCES: dict[str, Any] = {
    "peers": {
        "tickhub": {
            "product": "TickHub-CN",
            "cli": "th-cn",
            "mcp_name": "th-cn",
            "url_env": "LDST_TEST_FAKE_TICKHUB_URL",
            "default_url": "http://127.0.0.1:1/mcp",
            "timeout_sec": 1,
        }
    },
    "sources": [
        {
            "id": "tickhub_limit_pool",
            "dataset": "limit_pool",
            "via": "tickhub",
            "tool": "limit_up_pool",
            "primary": True,
            "fallback": [],
            "timeout_sec": 1,
            "retries": 0,
            "cooldown_sec": 60,
        },
        {
            "id": "tickhub_concept",
            "dataset": "concept_board",
            "via": "tickhub",
            "tool": "concept_boards",
            "primary": True,
            "fallback": [],
            "timeout_sec": 1,
            "retries": 0,
            "cooldown_sec": 60,
        },
        {
            "id": "tickhub_fund_flow",
            "dataset": "fund_flow",
            "via": "tickhub",
            "tool": "stock_fund_flow",
            "primary": True,
            "fallback": [],
            "timeout_sec": 1,
            "retries": 0,
            "cooldown_sec": 60,
        },
    ],
}

_CLOSE_SYNC: dict[str, Any] = {
    "sync": {
        "datasets": {
            "limit_pool": {
                "primary": "tickhub_limit_pool",
                "fallback": [],
                "watermark_field": "trade_date",
                "mode": "replace",
            },
            "concept_board": {
                "primary": "tickhub_concept",
                "fallback": [],
                "watermark_field": "trade_date",
                "mode": "replace",
            },
            # 代码池数据集：`stock_fund_flow` 的 code 必填，池子写在配置里，
            # 裸 `ldst sync` 才会真正逐只发调用（否则判「代码池为空」failed）。
            "fund_flow": {
                "primary": "tickhub_fund_flow",
                "fallback": [],
                "watermark_field": "trade_date",
                "mode": "replace",
                "codes": ["600000", "000001"],
            },
        },
        "cold_hot": {"intraday_cache_ttl_sec": 0, "history_backend": "duckdb"},
    }
}


def _limit_rows(day: str) -> list[dict[str, Any]]:
    return [
        {"code": "600519", "trade_date": day, "limit_times": 3},
        {"code": "300750", "trade_date": day, "limit_times": 2},
    ]


def _concept_rows(day: str) -> list[dict[str, Any]]:
    return [
        {"board_code": "885571", "name": "CPO", "trade_date": day, "count": 12},
        {"board_code": "885562", "name": "AI 算力", "trade_date": day, "count": 8},
    ]


def _fund_row(code: str, day: str, main_net: float) -> dict[str, Any]:
    return {"code": code, "name": code, "trade_date": day, "main_net": main_net}


def _close_transport(day: str, fund_flow_rows: dict[str, int] | None = None) -> FakeTransport:
    """三数据集的离线桩；`fund_flow_rows` 控制每只票当日回几行（模拟收盘刷新增长）。"""

    def fund_handler(params: dict[str, Any]) -> dict[str, Any]:
        code = str(params.get("code") or "")
        count = (fund_flow_rows or {}).get(code, 1)
        rows = [_fund_row(f"{code}-{i}", day, 100.0 + i) for i in range(count)]
        return tickhub_payload(rows)

    return FakeTransport(
        handlers={
            "limit_up_pool": lambda _params: tickhub_payload(_limit_rows(day)),
            "concept_boards": lambda _params: tickhub_payload(_concept_rows(day)),
            "stock_fund_flow": fund_handler,
        }
    )


# --------------------------------------------------------------------------- #
# 1. 注册面
# --------------------------------------------------------------------------- #

def test_schema_accepts_sync_close_kind() -> None:
    table = _default_table()
    validate_table(table)  # 不抛即通过（新 kind 已进 enum）


def test_schedule_schema_enum_contains_sync_close() -> None:
    import json

    schema = json.loads(
        (
            Path(__file__).resolve().parents[2] / "config" / "schema" / "schedule.schema.json"
        ).read_text(encoding="utf-8")
    )
    kinds = schema["properties"]["jobs"]["items"]["properties"]["kind"]["enum"]
    assert "sync_close" in kinds


def test_default_table_declares_sync_close_at_1535() -> None:
    assert len(DEFAULT_JOBS) == 9
    jobs = [job for job in _default_table()["jobs"] if job.get("kind") == "sync_close"]
    assert len(jobs) == 1
    assert jobs[0]["trigger"] == "15:35"
    assert jobs[0]["enabled"] is True


def test_repo_schedule_declares_sync_close(repo: Path) -> None:
    table, warnings, _source = load_schedule()
    assert warnings == []
    jobs = [job for job in table["jobs"] if job.get("kind") == "sync_close"]
    assert [job["trigger"] for job in jobs] == ["15:35"]


def test_structural_fallback_knows_sync_close() -> None:
    table = _default_table()
    assert _structural_problems(table) == []

    broken = copy.deepcopy(table)
    broken["jobs"][-1]["kind"] = "scrape"
    problems = _structural_problems(broken)
    assert any("sync_close" in problem for problem in problems)


# --------------------------------------------------------------------------- #
# 2 / 3. 分发面与边界（run_sync、generate 用桩替换，只验接线）
# --------------------------------------------------------------------------- #

def _patch_sync(monkeypatch: pytest.MonkeyPatch, result: dict[str, Any]) -> list[list[str]]:
    """替换 `run_sync`，记录它收到的数据集顺序；返回调用记录。"""
    calls: list[list[str]] = []

    def fake_run_sync(registry, store, datasets, **kwargs):  # noqa: ANN001, ARG001
        calls.append(list(datasets))
        return result

    monkeypatch.setattr("ladderstock.cli.sync.run_sync", fake_run_sync)
    return calls


def _patch_generate_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ladderstock.review.pipeline.generate",
        lambda day, root=None: ({}, SimpleNamespace(failed=False, degraded=False)),
    )


def _sync_payload(code: int) -> dict[str, Any]:
    """按 `run_sync` 的真实行形状造桩——字段名对不上就该让测试红。"""
    return {
        "summary": {"exit_code": code, "ok": code == EXIT_OK},
        "results": [
            {
                "dataset": d,
                "status": "ok",
                "days": [TRADING_DAY],
                "rows": 10 * i,
                "total_rows": 100 * i,
            }
            for i, d in enumerate(CLOSE_SYNC_DATASETS, start=1)
        ],
    }


def test_run_job_skips_non_trading_day(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: Any, **kwargs: Any) -> None:  # noqa: ANN001
        raise AssertionError("非交易日不应构造 registry / 跑同步")

    monkeypatch.setattr("ladderstock.data.provider_registry.ProviderRegistry", boom)
    result = run_job(SYNC_CLOSE_JOB, now=WEEKEND_MOMENT, timezone="Asia/Shanghai", root=tmp_path)

    assert result["status"] == "skipped"
    assert "非交易日" in result["detail"]
    assert not (tmp_path / "archive").exists()


@pytest.mark.parametrize(
    ("exit_code", "expected"),
    [(EXIT_OK, "ok"), (EXIT_PARTIAL, "degraded"), (EXIT_SOURCE, "failed")],
)
def test_run_job_maps_sync_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exit_code: int, expected: str
) -> None:
    calls = _patch_sync(monkeypatch, _sync_payload(exit_code))
    _patch_generate_ok(monkeypatch)

    result = run_job(
        SYNC_CLOSE_JOB, now=TRADING_MOMENT, timezone="Asia/Shanghai", root=tmp_path
    )

    assert result["status"] == expected, result
    assert calls == [list(CLOSE_SYNC_DATASETS)]
    assert result["date"] == TRADING_DAY


def test_run_job_detail_quotes_real_row_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """明细里的计数必须来自 `run_sync` 真实字段，不能报恒为 0 的幽灵指标。"""
    _patch_sync(monkeypatch, _sync_payload(EXIT_OK))
    _patch_generate_ok(monkeypatch)

    result = run_job(
        SYNC_CLOSE_JOB, now=TRADING_MOMENT, timezone="Asia/Shanghai", root=tmp_path
    )

    assert "concept_board=ok(+30/300)" in result["detail"], result
    assert "交易日 1 个" in result["detail"], result
    assert "分区 0" not in result["detail"], result


def test_run_job_review_failure_degrades_but_keeps_sync_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_sync(monkeypatch, _sync_payload(EXIT_OK))
    monkeypatch.setattr(
        "ladderstock.review.pipeline.generate",
        lambda day, root=None: ({}, SimpleNamespace(failed=True, degraded=False)),
    )

    result = run_job(
        SYNC_CLOSE_JOB, now=TRADING_MOMENT, timezone="Asia/Shanghai", root=tmp_path
    )

    assert result["status"] == "degraded"
    assert "复盘" in result["detail"]


def test_run_job_config_error_folds_to_failed_without_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def raise_boundary(*args: Any, **kwargs: Any):  # noqa: ANN001
        raise LadderStockError("主备跨 peer", EXIT_CONFIG)

    monkeypatch.setattr("ladderstock.cli.sync.run_sync", raise_boundary)
    # generate 不该被调用（同步已中止），这里让它抛断言以证明短路
    monkeypatch.setattr(
        "ladderstock.review.pipeline.generate",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不该跑到复盘")),
    )

    result = run_job(
        SYNC_CLOSE_JOB, now=TRADING_MOMENT, timezone="Asia/Shanghai", root=tmp_path
    )

    assert result["status"] == "failed"
    assert "收盘同步中止" in result["detail"]


# --------------------------------------------------------------------------- #
# 4. 落库面：真 run_sync + 离线桩，验证日分区、幂等、fund_flow 增长
# --------------------------------------------------------------------------- #

@pytest.fixture()
def data_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把数据目录钉在 tmp，让 `write_dataset` 与 archive 分区都落在测试目录。"""
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    return tmp_path


def _use_registry(monkeypatch: pytest.MonkeyPatch, registry: Any) -> None:
    """让 handler 内部 `ProviderRegistry(root=...)` 拿到注入的离线注册表。"""
    monkeypatch.setattr(
        "ladderstock.data.provider_registry.ProviderRegistry",
        lambda root=None: registry,
    )
    monkeypatch.setattr(
        "ladderstock.review.pipeline.generate",
        lambda day, root=None: ({}, SimpleNamespace(failed=False, degraded=False)),
    )


def _wire(monkeypatch: pytest.MonkeyPatch, transport: FakeTransport, root: Path) -> None:
    """用离线桩 transport 建注册表并注入。"""
    registry = make_registry(transport, root, sources=_CLOSE_SOURCES, sync=_CLOSE_SYNC)
    _use_registry(monkeypatch, registry)


def test_sync_close_writes_daily_partitions_and_idempotent(
    data_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport = _close_transport(TRADING_DAY)
    _wire(monkeypatch, transport, data_home)

    first = run_job(
        SYNC_CLOSE_JOB, now=TRADING_MOMENT, timezone="Asia/Shanghai", root=data_home
    )
    assert first["status"] == "ok", first

    from ladderstock.data import duckdb_store

    for dataset in CLOSE_SYNC_DATASETS:
        partition = duckdb_store.partition_path(dataset, date.fromisoformat(TRADING_DAY))
        assert partition.is_file(), f"{dataset} 应落 {partition}"
        assert partition.parent == duckdb_store.archive_root() / "2026" / "11" / "04"

    # 同日重复触发：分区目录不新增、fund_flow 落库总数不变（整日覆盖，非追加膨胀）
    before_total = duckdb_store.dataset_rows("fund_flow")
    _wire(
        monkeypatch,
        _close_transport(TRADING_DAY, fund_flow_rows={"600000": 1, "000001": 1}),
        data_home,
    )

    again = run_job(
        SYNC_CLOSE_JOB, now=TRADING_MOMENT, timezone="Asia/Shanghai", root=data_home
    )
    assert again["status"] == "ok", again
    assert duckdb_store.dataset_rows("fund_flow") == before_total
    partitions = list((duckdb_store.archive_root() / "2026" / "11" / "04").glob("*.parquet"))
    assert {p.name for p in partitions} == {f"{d}.parquet" for d in CLOSE_SYNC_DATASETS}


def test_sync_close_fund_flow_count_grows_on_refresh(
    data_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ladderstock.data import duckdb_store

    # 收盘前：每只票 1 行
    _wire(
        monkeypatch,
        _close_transport(TRADING_DAY, fund_flow_rows={"600000": 1, "000001": 1}),
        data_home,
    )
    run_job(SYNC_CLOSE_JOB, now=TRADING_MOMENT, timezone="Asia/Shanghai", root=data_home)
    before = duckdb_store.dataset_rows("fund_flow")

    # 收盘刷新后：同池给出更多行（replace 整日覆盖 → 条数增长）
    _wire(
        monkeypatch,
        _close_transport(TRADING_DAY, fund_flow_rows={"600000": 3, "000001": 2}),
        data_home,
    )
    result = run_job(
        SYNC_CLOSE_JOB, now=TRADING_MOMENT, timezone="Asia/Shanghai", root=data_home
    )
    after = duckdb_store.dataset_rows("fund_flow")

    assert result["status"] == "ok", result
    assert before == 2
    assert after > before
