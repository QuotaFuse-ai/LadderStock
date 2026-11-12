"""盘中高频任务（QUOT-352）：间隔触发器 + 30s 关键快照 + 60s 资金流批量。

钉住四件事：

1. `trigger: "Ns"` 是间隔触发，`HH:MM` 仍是定点触发，二者在同一个调度表里共存；
2. 高频任务只在连续竞价时段（09:30–11:30 / 13:00–15:00）出网，时段外一次上游都不打；
3. 高频快照与 6 个定点时点**分目录、分文件名**，互不覆盖；
4. `sync_batch` 把 `sync_one` 的降级如实映射成 degraded，不把限流降级记成失败。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from ladderstock.cli.schedule import (
    DEFAULT_JOBS,
    _default_table,
    _structural_problems,
    load_schedule,
    run_job,
    trigger_interval_seconds,
    validate_table,
)
from ladderstock.core import intraday
from ladderstock.data.provider_registry import SourceUnavailable
from ladderstock.errors import LadderStockError

DAY = date(2026, 10, 30)  # 周五


def fast_jobs(table: dict[str, Any]) -> list[dict[str, Any]]:
    return [job for job in table["jobs"] if job.get("kind") in ("snapshot_fast", "sync_batch")]


def test_trigger_interval_seconds_accepts_seconds_and_rejects_cron() -> None:
    assert trigger_interval_seconds("30s") == 30
    assert trigger_interval_seconds("60s") == 60
    assert trigger_interval_seconds("09:05") is None
    assert trigger_interval_seconds("0s") is None
    assert trigger_interval_seconds("abc") is None
    assert trigger_interval_seconds(None) is None


def test_repo_schedule_declares_fast_jobs(repo: Path) -> None:
    table, warnings, _source = load_schedule()

    assert warnings == []
    jobs = fast_jobs(table)
    assert [(job["id"], job["kind"], job["trigger"]) for job in jobs] == [
        ("intraday_fast", "snapshot_fast", "30s"),
        ("fund_flow_fast", "sync_batch", "60s"),
    ]
    assert jobs[1]["dataset"] == "fund_flow_realtime"
    assert all(job["enabled"] for job in jobs)


def test_builtin_defaults_mirror_the_fast_jobs() -> None:
    assert len(DEFAULT_JOBS) == 15
    assert fast_jobs(_default_table()) == fast_jobs(load_schedule()[0])


def test_schema_accepts_fast_kinds_and_interval_trigger() -> None:
    validate_table(_default_table())


def test_schema_rejects_zero_interval() -> None:
    table = _default_table()
    table["jobs"].append(
        {
            "id": "too_fast",
            "name": "0 秒轮询",
            "kind": "snapshot_fast",
            "trigger": "0s",
            "enabled": True,
        }
    )

    with pytest.raises(LadderStockError) as excinfo:
        validate_table(table)
    assert "trigger" in str(excinfo.value)


def test_schema_requires_dataset_for_sync_batch() -> None:
    table = _default_table()
    for job in table["jobs"]:
        if job.get("kind") == "sync_batch":
            job.pop("dataset")

    with pytest.raises(LadderStockError) as excinfo:
        validate_table(table)
    assert "dataset" in str(excinfo.value)


def test_structural_fallback_knows_fast_kinds() -> None:
    problems = _structural_problems(_default_table())
    assert problems == []

    broken = _default_table()
    broken["jobs"][7]["kind"] = "scrape"
    assert any("snapshot_fast" in problem for problem in _structural_problems(broken))


def test_run_job_dispatches_fast_snapshot(tmp_path: Path) -> None:
    from fixtures.intraday_stubs import StubRegistry

    job = {
        "id": "intraday_fast",
        "name": "盘中 30s 关键快照",
        "kind": "snapshot_fast",
        "trigger": "30s",
        "enabled": True,
    }

    result = run_job(
        job,
        now=datetime(2026, 10, 30, 10, 0, 3),
        timezone="Asia/Shanghai",
        root=tmp_path,
        registry=StubRegistry(),
    )

    assert result["status"] == "ok", result
    assert result["slot"] == intraday.FAST_SLOT
    assert result["file"] == "100003.json"
    fast_dir = intraday.fast_snapshot_dir(DAY, tmp_path)
    assert (fast_dir / "100003.json").is_file()
    # 6 个定点时点的文件命名不受影响：高频读数不写 <HHMM>.json
    assert not intraday.snapshot_path("10:00", DAY, tmp_path).exists()

    rounds = (fast_dir / "rounds.jsonl").read_text(encoding="utf-8").splitlines()
    entry = json.loads(rounds[-1])
    assert entry["status"] == "ok"
    assert entry["stocks"] == 3
    assert entry["fresh"] == 3
    assert isinstance(entry["elapsed_ms"], int)


def test_run_fast_outside_session_skips_without_touching_registry(tmp_path: Path) -> None:
    job = {
        "id": "intraday_fast",
        "name": "盘中 30s 关键快照",
        "kind": "snapshot_fast",
        "trigger": "30s",
        "enabled": True,
    }

    result = run_job(
        job,
        now=datetime(2026, 10, 30, 12, 0, 0),  # 午休
        timezone="Asia/Shanghai",
        root=tmp_path,
        registry=object(),
    )

    assert result["status"] == "skipped"
    assert "连续竞价时段" in result["detail"]
    assert not intraday.fast_snapshot_dir(DAY, tmp_path).exists()


def test_run_fast_weekend_is_skipped(tmp_path: Path) -> None:
    result = intraday.run_fast(
        now=datetime(2026, 10, 31, 10, 0, 0),  # 周六
        timezone="Asia/Shanghai",
        root=tmp_path,
        registry=object(),
    )

    assert result["status"] == "skipped"
    assert "周末" in result["detail"]


def test_run_fast_rate_limited_is_degraded_not_failed(tmp_path: Path) -> None:
    from fixtures.intraday_stubs import StubRegistry

    stub = StubRegistry(
        errors={"yesterday_limit_up": SourceUnavailable("QFSD-7001", rate_limited=True)}
    )

    result = intraday.run_fast(
        now=datetime(2026, 10, 30, 10, 0, 3),
        timezone="Asia/Shanghai",
        root=tmp_path,
        registry=stub,
    )

    assert result["status"] == "degraded"
    assert "QFSD-7001" in result["detail"]
    entry = json.loads(
        (intraday.fast_snapshot_dir(DAY, tmp_path) / "rounds.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[-1]
    )
    assert entry["status"] == "degraded"


def test_run_job_sync_batch_skips_outside_session(tmp_path: Path) -> None:
    job = {
        "id": "fund_flow_fast",
        "name": "盘中 60s 资金流批量",
        "kind": "sync_batch",
        "trigger": "60s",
        "enabled": True,
        "dataset": "fund_flow_realtime",
    }

    result = run_job(
        job,
        now=datetime(2026, 10, 30, 15, 30, 0),  # 收盘后
        timezone="Asia/Shanghai",
        root=tmp_path,
        registry=object(),
    )

    assert result["status"] == "skipped"
    assert "连续竞价时段" in result["detail"]


def test_run_job_sync_batch_maps_degraded_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ladderstock.cli import sync as sync_module

    calls: list[str] = []

    def fake_sync_one(registry: Any, store: Any, dataset: str, **_: Any) -> dict[str, Any]:
        calls.append(dataset)
        return {"dataset": dataset, "status": "degraded", "detail": "限流降级", "rows": 118}

    monkeypatch.setattr(sync_module, "sync_one", fake_sync_one)

    job = {
        "id": "fund_flow_fast",
        "name": "盘中 60s 资金流批量",
        "kind": "sync_batch",
        "trigger": "60s",
        "enabled": True,
        "dataset": "fund_flow_realtime",
    }

    result = run_job(
        job,
        now=datetime(2026, 10, 30, 10, 1, 0),
        timezone="Asia/Shanghai",
        root=tmp_path,
        registry=object(),
    )

    assert calls == ["fund_flow_realtime"]
    assert result["status"] == "degraded"
    assert result["rows"] == 118
