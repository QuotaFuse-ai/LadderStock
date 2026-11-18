"""条件触发（`_auto_research_on_hit`）：盯盘命中后自动起一轮研究。

守的是三件容易失控的事：

1. **不重复烧钱**——同一只票在冷却窗口内不会反复跑 agent；
2. **一次不跑太多**——命中 10 只也只研究前 N 只；
3. **不拖累提醒**——研究失败只折成 `failed` 字段，冷却不推进，下一轮还能重试。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from ladderstock.cli import schedule as sched

MOMENT = datetime(2026, 11, 16, 10, 0, 0)


def _job(**overrides) -> dict:
    base = {"id": "watch_live_intraday", "channel": "desktop", "auto_research": True}
    base.update(overrides)
    return base


def test_runs_agent_once_with_limited_symbols(monkeypatch, tmp_path: Path) -> None:
    calls: list[dict] = []

    def fake(job, *, now=None, timezone=None, root=None):
        calls.append(job)
        return {"status": "ok", "detail": "研究卡已落", "path": "digest.json"}

    monkeypatch.setattr(sched, "_run_agent_research", fake)
    out = sched._auto_research_on_hit(
        _job(auto_research_max=2),
        codes=["600036", "000001", "600519", "601318"],
        now=None,
        timezone=None,
        root=tmp_path,
        moment=MOMENT,
    )

    assert out["status"] == "ok"
    assert out["symbols"] == ["600036", "000001"]  # 命中 4 只，只研究前 2 只
    assert len(calls) == 1
    assert calls[0]["symbols"] == ["600036", "000001"]
    assert calls[0]["trigger"] == "auto"


def test_cooldown_blocks_second_run(monkeypatch, tmp_path: Path) -> None:
    calls: list[dict] = []

    def fake(job, *, now=None, timezone=None, root=None):
        calls.append(job)
        return {"status": "ok", "detail": "ok"}

    monkeypatch.setattr(sched, "_run_agent_research", fake)
    first = sched._auto_research_on_hit(
        _job(), codes=["600036"], now=None, timezone=None, root=tmp_path, moment=MOMENT
    )
    assert first["status"] == "ok"

    # 10 分钟后再命中同一只：冷却 60 分钟，不该再跑
    second = sched._auto_research_on_hit(
        _job(),
        codes=["600036"],
        now=None,
        timezone=None,
        root=tmp_path,
        moment=datetime(2026, 11, 16, 10, 10, 0),
    )
    assert second["status"] == "skipped"
    assert len(calls) == 1

    # 过了冷却窗口才允许再跑
    third = sched._auto_research_on_hit(
        _job(),
        codes=["600036"],
        now=None,
        timezone=None,
        root=tmp_path,
        moment=datetime(2026, 11, 16, 11, 5, 0),
    )
    assert third["status"] == "ok"
    assert len(calls) == 2


def test_state_file_records_timestamp(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        sched, "_run_agent_research", lambda job, **kw: {"status": "ok", "detail": "ok"}
    )
    sched._auto_research_on_hit(
        _job(), codes=["600036"], now=None, timezone=None, root=tmp_path, moment=MOMENT
    )
    state = json.loads((tmp_path / "auto_research_state.json").read_text(encoding="utf-8"))
    assert abs(float(state["600036"]) - MOMENT.timestamp()) < 1


def test_failure_does_not_advance_cooldown(monkeypatch, tmp_path: Path) -> None:
    def boom(job, *, now=None, timezone=None, root=None):
        raise RuntimeError("agent 通道断了")

    monkeypatch.setattr(sched, "_run_agent_research", boom)
    out = sched._auto_research_on_hit(
        _job(), codes=["600036"], now=None, timezone=None, root=tmp_path, moment=MOMENT
    )
    assert out["status"] == "failed"
    assert "agent 通道断了" in out["detail"]
    # 失败不写冷却：下一轮该允许重试
    assert not (tmp_path / "auto_research_state.json").exists()


def test_limit_is_clamped(monkeypatch, tmp_path: Path) -> None:
    seen: list[list[str]] = []

    def fake(job, *, now=None, timezone=None, root=None):
        seen.append(job["symbols"])
        return {"status": "ok", "detail": "ok"}

    monkeypatch.setattr(sched, "_run_agent_research", fake)
    codes = [f"60000{i}" for i in range(8)]
    sched._auto_research_on_hit(
        _job(auto_research_max=99), codes=codes, now=None, timezone=None, root=tmp_path, moment=MOMENT
    )
    assert len(seen[0]) == 5  # 上限 clamp 到 5


def test_corrupt_state_file_is_tolerated(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        sched, "_run_agent_research", lambda job, **kw: {"status": "ok", "detail": "ok"}
    )
    (tmp_path / "auto_research_state.json").write_text("not json", encoding="utf-8")
    out = sched._auto_research_on_hit(
        _job(), codes=["600036"], now=None, timezone=None, root=tmp_path, moment=MOMENT
    )
    assert out["status"] == "ok"


@pytest.mark.parametrize("question", ["", "   "])
def test_default_question_when_blank(monkeypatch, tmp_path: Path, question: str) -> None:
    captured: list[str] = []

    def fake(job, *, now=None, timezone=None, root=None):
        captured.append(job["question"])
        return {"status": "ok", "detail": "ok"}

    monkeypatch.setattr(sched, "_run_agent_research", fake)
    sched._auto_research_on_hit(
        _job(auto_research_question=question),
        codes=["600036"],
        now=None,
        timezone=None,
        root=tmp_path,
        moment=MOMENT,
    )
    assert "盘中出现了异动" in captured[0]
