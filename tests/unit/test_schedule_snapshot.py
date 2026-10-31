"""调度表接入 6 时点快照（QUOT-286）。

钉住三件事：

1. `config/schedule.yaml` 与内置默认表同口径：6 条 `kind: snapshot`，触发时刻
   就是快照时点（`09:25 / 09:35 / 10:00 / 11:30 / 14:00 / 15:00`）；
2. schema / 结构自检都认 `snapshot`，未知 kind 仍然报错；
3. `run_job` 把 trigger 当时点、把调度表时区传下去，窗口外记 `skipped`。
"""

from __future__ import annotations

import copy
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
    validate_table,
)
from ladderstock.core import intraday
from ladderstock.errors import LadderStockError

SNAPSHOT_TRIGGERS = ["09:25", "09:35", "10:00", "11:30", "14:00", "15:00"]


def snapshot_jobs(table: dict[str, Any]) -> list[dict[str, Any]]:
    return [job for job in table["jobs"] if job.get("kind") == "snapshot"]


def test_repo_schedule_declares_six_snapshot_jobs(repo: Path) -> None:
    table, warnings, _source = load_schedule()

    assert warnings == []
    jobs = snapshot_jobs(table)
    assert [job["trigger"] for job in jobs] == SNAPSHOT_TRIGGERS
    assert [job["id"] for job in jobs] == [
        "snapshot_0925",
        "snapshot_0935",
        "snapshot_1000",
        "snapshot_1130",
        "snapshot_1400",
        "snapshot_1500",
    ]
    assert all(job["enabled"] for job in jobs)


def test_builtin_defaults_mirror_the_repo_schedule() -> None:
    builtin = snapshot_jobs(_default_table())

    assert [job["trigger"] for job in builtin] == SNAPSHOT_TRIGGERS
    assert len(DEFAULT_JOBS) == 8


def test_schema_accepts_snapshot_kind() -> None:
    validate_table(_default_table())


def test_schema_rejects_unknown_kind() -> None:
    table = _default_table()
    table["jobs"].append(
        {
            "id": "scrape_quotes",
            "name": "直连行情",
            "kind": "scrape",
            "trigger": "10:01",
            "enabled": True,
        }
    )

    with pytest.raises(LadderStockError) as excinfo:
        validate_table(table)
    assert "scrape" in str(excinfo.value)


def test_structural_fallback_knows_snapshot() -> None:
    table = _default_table()
    assert _structural_problems(table) == []

    broken = copy.deepcopy(table)
    broken["jobs"][1]["kind"] = "scrape"
    problems = _structural_problems(broken)
    assert any("snapshot" in problem for problem in problems)


def test_run_job_dispatches_snapshot_job(tmp_path: Path) -> None:
    from fixtures.intraday_stubs import StubRegistry

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
        registry=StubRegistry(),
    )

    assert result["status"] == "ok", result
    assert result["id"] == "snapshot_1000"
    assert result["trigger"] == "10:00"
    assert result["date"] == "2026-10-30"
    assert result["slot"] == "10:00"
    assert result["file"] == "1000.json"
    assert intraday.snapshot_path("10:00", date(2026, 10, 30), tmp_path).is_file()
    assert "finished_at" in result and "elapsed_ms" in result


def test_run_job_snapshot_outside_window_is_skipped(tmp_path: Path) -> None:
    job = {
        "id": "snapshot_1000",
        "name": "10:00 盘中快照",
        "kind": "snapshot",
        "trigger": "10:00",
        "enabled": True,
    }

    result = run_job(
        job,
        now=datetime(2026, 10, 30, 10, 30, 0),
        timezone="Asia/Shanghai",
        root=tmp_path,
        registry=object(),  # 窗口外不应该碰到 registry
    )

    assert result["status"] == "skipped"
    assert "不补跑" in result["detail"]
    assert not intraday.snapshot_path("10:00", date(2026, 10, 30), tmp_path).exists()


def test_schedule_schema_enum_contains_snapshot() -> None:
    schema = json.loads(
        (
            Path(__file__).resolve().parents[2] / "config" / "schema" / "schedule.schema.json"
        ).read_text(encoding="utf-8")
    )
    kinds = schema["properties"]["jobs"]["items"]["properties"]["kind"]["enum"]
    assert kinds == ["notify", "reconcile", "snapshot"]
