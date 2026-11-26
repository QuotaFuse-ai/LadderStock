"""`plan_tick` 任务：把用户交待的长期目标按频率推进。

两条必须守住的行为：
- 没有到期任务时**静默**（`skipped + quiet`），不能每半小时往告警日志里灌一行；
- 有到期任务时逐条跑 agent、落研究卡、写收件箱，失败不拖垮其余任务。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from ladderstock.ai import plans
from ladderstock.cli import schedule as sched

NOW = datetime(2026, 11, 16, 17, 0, 0)


@pytest.fixture()
def data_root(tmp_path: Path) -> Path:
    return tmp_path


def _make_plan(root: Path) -> plans.Plan:
    plan = plans.maybe_create("帮我盯住 600036 一周", data_root=root, now=NOW)
    assert plan is not None
    return plan


def test_skips_silently_when_nothing_due(data_root: Path) -> None:
    outcome = sched._run_plan_tick({}, now=NOW, timezone="Asia/Shanghai", root=data_root)
    assert outcome["status"] == "skipped"
    assert outcome.get("quiet") is True


def test_runs_due_plan_and_writes_digest(data_root: Path, monkeypatch) -> None:
    _make_plan(data_root)

    async def fake_ask(**kwargs):
        assert "持续跟踪" in kwargs["question"]
        return {
            "reply": "今天资金净流入 3.2 亿，比昨天放大。",
            "sources": [],
            "used_tools": True,
            "model": "openai:test",
        }

    monkeypatch.setattr("ladderstock.web.assistant.ask_agent", fake_ask)

    outcome = sched._run_plan_tick({}, now=NOW, timezone="Asia/Shanghai", root=data_root)
    assert outcome["status"] in ("ok", "degraded")
    assert len(outcome["results"]) == 1

    digest = data_root / "research_digest" / NOW.date().isoformat()
    files = list(digest.glob("plan-*.json"))
    assert files, "推进结果必须落研究卡"
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["symbols"] == ["600036"]
    assert "3.2 亿" in payload["reply"]

    # 任务状态也要跟着走：runs=1、last_summary 有内容
    reloaded = plans.load(data_root)[0]
    assert reloaded.runs == 1
    assert "3.2 亿" in reloaded.last_summary


def test_second_tick_same_day_does_not_rerun(data_root: Path, monkeypatch) -> None:
    _make_plan(data_root)
    calls: list[str] = []

    async def fake_ask(**kwargs):
        calls.append(kwargs["question"])
        return {"reply": "无变化", "sources": [], "used_tools": True}

    monkeypatch.setattr("ladderstock.web.assistant.ask_agent", fake_ask)

    sched._run_plan_tick({}, now=NOW, timezone="Asia/Shanghai", root=data_root)
    second = sched._run_plan_tick({}, now=NOW, timezone="Asia/Shanghai", root=data_root)
    assert len(calls) == 1
    assert second["status"] == "skipped"


def test_agent_failure_is_recorded_not_raised(data_root: Path, monkeypatch) -> None:
    _make_plan(data_root)

    async def boom(**kwargs):
        raise RuntimeError("通道断了")

    monkeypatch.setattr("ladderstock.web.assistant.ask_agent", boom)

    outcome = sched._run_plan_tick({}, now=NOW, timezone="Asia/Shanghai", root=data_root)
    assert outcome["status"] == "failed"
    reloaded = plans.load(data_root)[0]
    assert reloaded.runs == 1
    assert "通道断了" in reloaded.last_summary
    # 失败不占「今天已交付」的名额 → 下一轮还能重试
    assert reloaded.last_run_at == ""
    assert reloaded.due(NOW) is True


def test_runner_disables_plan_creation(data_root: Path, monkeypatch) -> None:
    """推进本身不许再建任务：推进提问里带「持续跟踪：600000」，否则每次都繁殖一条。"""
    _make_plan(data_root)
    seen: dict[str, object] = {}

    async def fake_ask(**kwargs):
        seen.update(kwargs)
        return {"reply": "无变化", "sources": [], "used_tools": True}

    monkeypatch.setattr("ladderstock.web.assistant.ask_agent", fake_ask)

    sched._run_plan_tick({}, now=NOW, timezone="Asia/Shanghai", root=data_root)
    assert seen.get("remember_plan") is False
    assert len(plans.load(data_root)) == 1


def test_allow_plan_create_switch_enables_it(data_root: Path, monkeypatch) -> None:
    """显式打开开关时才允许在推进里续建任务（`allow_plan_create: true`）。"""
    _make_plan(data_root)
    seen: dict[str, object] = {}

    async def fake_ask(**kwargs):
        seen.update(kwargs)
        return {"reply": "无变化", "sources": [], "used_tools": True}

    monkeypatch.setattr("ladderstock.web.assistant.ask_agent", fake_ask)

    sched._run_plan_tick(
        {"allow_plan_create": True}, now=NOW, timezone="Asia/Shanghai", root=data_root
    )
    assert seen.get("remember_plan") is True
