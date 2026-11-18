"""web 进程内的定时调度：只开一个服务，盯盘与研究就按表自己跑。

之前 `config/schedule.yaml` 里的 08:45 / 16:40 / 30s 任务只有 `ldst schedule serve`
那个**独立常驻进程**会执行——只起 5951 时，实测量到的 `watch_live_state.json` 停在最后
一次手动触发的时刻。这组用例守住的正是「普通人不额外起进程也能跑」这件事。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from ladderstock.cli import schedule as sched
from ladderstock.web import schedule_runner
from ladderstock.web.schedule_runner import ScheduleRunner

SAMPLE = """\
scheduler:
  timezone: Asia/Shanghai
  misfire_grace_seconds: 300
jobs:
  - id: watch_live_intraday
    name: 盘中 30s 实时盯盘
    kind: watch_live
    alert_pct: 3
    move_pct: 1
    min_gap_min: 30
    trigger: "30s"
    enabled: true
    channel: desktop
  - id: agent_research_close
    name: 16:40 定时研究
    kind: agent_research
    question: 今天自选有什么值得注意的？
    trigger: "16:40"
    enabled: true
    channel: desktop
  - id: agent_research_off
    name: 关掉的定时研究
    kind: agent_research
    question: 我不跑
    trigger: "09:15"
    enabled: false
    channel: desktop
  - id: snapshot_0935
    name: 盘中快照
    kind: snapshot
    trigger: "09:35"
    enabled: true
    channel: console
"""


@pytest.fixture()
def cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "schedule.yaml"
    path.write_text(SAMPLE, encoding="utf-8")
    monkeypatch.setattr(sched, "schedule_config_path", lambda: path)
    return path


def _runner(tmp_path: Path) -> ScheduleRunner:
    return ScheduleRunner(data_root=tmp_path)


def test_defaults_to_whole_table(cfg: Path, tmp_path: Path) -> None:
    """默认整表全挂：研究/盯盘之外，快照这类也归 web 进程调度。"""
    runner = _runner(tmp_path)
    try:
        state = runner.start()
        assert state["running"] is True
        # enabled:false 的不进；其余按 yaml 顺序全挂
        assert state["jobs"] == ["watch_live_intraday", "agent_research_close", "snapshot_0935"]
    finally:
        runner.shutdown()


def test_kinds_env_narrows_scope(
    cfg: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`LDST_SCHEDULE_KINDS` 能把整表收窄成几类（想减负时用）。"""
    monkeypatch.setenv(schedule_runner.KINDS_ENV, "agent_research")
    runner = _runner(tmp_path)
    try:
        assert runner.start()["jobs"] == ["agent_research_close"]
    finally:
        runner.shutdown()


def test_reload_reflects_toggle(cfg: Path, tmp_path: Path) -> None:
    """页面开关后 reload 就生效，不用重启服务。"""
    runner = _runner(tmp_path)
    try:
        runner.start()
        doc = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        for job in doc["jobs"]:
            if job["id"] == "agent_research_close":
                job["enabled"] = False
            if job["id"] == "agent_research_off":
                job["enabled"] = True
        cfg.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")
        state = runner.reload()
        assert state["jobs"] == ["watch_live_intraday", "agent_research_off", "snapshot_0935"]
    finally:
        runner.shutdown()


def test_execute_persists_outcome(cfg: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sched, "run_job", lambda job, **kwargs: {"status": "ok", "detail": "3 只越阈"}
    )
    runner = _runner(tmp_path)
    runner._execute({"id": "watch_alert_morning", "kind": "watch_alert"})
    import json

    state = json.loads(
        (tmp_path / "schedule_runs" / "watch_alert_morning.json").read_text(encoding="utf-8")
    )
    assert state["status"] == "ok"
    assert state["detail"] == "3 只越阈"


def test_execute_records_failure(cfg: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(job: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("上游全挂")

    monkeypatch.setattr(sched, "run_job", boom)
    runner = _runner(tmp_path)
    runner._execute({"id": "agent_research_close", "kind": "agent_research"})
    import json

    state = json.loads(
        (tmp_path / "schedule_runs" / "agent_research_close.json").read_text(encoding="utf-8")
    )
    assert state["status"] == "failed"
    assert "上游全挂" in state["detail"]


def test_env_switch_disables_runner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(schedule_runner.RUNNER_ENV, "0")
    monkeypatch.setattr(schedule_runner, "_RUNNER", None)
    assert schedule_runner.start_runner(data_root=tmp_path) is None
