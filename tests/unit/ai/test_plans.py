"""跨轮任务规划（`ai/plans.py`）：一句话建任务、按频率推进、到期自动收尾。

三条最容易出错的边界都在这里守着：

- **不要把所有提问都变成长任务**——没有「盯/跟踪」这类动词就一律不建；
- **不要重复建**——同一句话反复说不该堆出五条一样的任务；
- **不要永久卡死**——时间戳坏了要当成「该跑了」，而不是永远不跑。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from ladderstock.ai import plans

NOW = datetime(2026, 11, 16, 17, 0, 0)


def _fake_resolver(name: str) -> str | None:
    return {"浦发银行": "600000", "贵州茅台": "600519"}.get(name)


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("text", "days"),
    [
        ("帮我盯一周", 7),
        ("盯 3 天", 3),
        ("跟踪一个月", 30),
        ("盯两周", 14),
        ("帮我盯住老白干", None),
    ],
)
def test_parse_period_days(text: str, days: int | None) -> None:
    assert plans.parse_period_days(text) == days


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("帮我盯浦发，盘中每半小时看一次", plans.FREQUENCY_INTRADAY),
        ("盯住它，每天盘后看", plans.FREQUENCY_DAILY),
        ("有异动再告诉我", plans.FREQUENCY_ON_MOVE),
    ],
)
def test_parse_frequency(text: str, kind: str) -> None:
    assert plans.parse_frequency(text) == kind


def test_build_plan_from_code() -> None:
    plan = plans.build_plan("帮我盯住 600036，盘后看一周", now=NOW)
    assert plan is not None
    assert plan.symbols == ["600036"]
    assert plan.frequency == plans.FREQUENCY_DAILY
    assert plan.expires_at.startswith("2026-11-23")
    assert plan.status == plans.STATUS_ACTIVE


def test_build_plan_from_chinese_name() -> None:
    plan = plans.build_plan("帮我盯着浦发银行，一周", resolver=_fake_resolver, now=NOW)
    assert plan is not None
    assert plan.symbols == ["600000"]


@pytest.mark.parametrize(
    "text",
    [
        "帮我盯住浦发银行一周，盘后看，有变化就告诉我",
        "请盯住浦发银行，看三天",
        "盯着浦发银行两周",
        "帮我跟踪浦发银行一个月",
        "留意一下浦发银行，有异动告诉我",
    ],
)
def test_trigger_variants_resolve_chinese_name(text: str) -> None:
    """「盯住」不能被「盯」抢先匹配，否则候选名会多出一个「住」字、解析直接失败。"""
    plan = plans.build_plan(text, resolver=_fake_resolver, now=NOW)
    assert plan is not None, text
    assert plan.symbols == ["600000"], text


def test_build_plan_ignores_normal_question() -> None:
    assert plans.build_plan("浦发银行今天为什么涨了", now=NOW) is None
    assert plans.build_plan("什么是市盈率", now=NOW) is None
    assert plans.build_plan("", now=NOW) is None


def test_build_plan_unknown_name_returns_none() -> None:
    """名字解析不出代码、也不是可识别的主题 → 不建任务（不猜标的）。"""
    assert plans.build_plan("帮我盯着这家公司", resolver=_fake_resolver, now=NOW) is None


# --------------------------------------------------------------------------- #
# 建 / 去重 / 上限
# --------------------------------------------------------------------------- #


def test_maybe_create_persists(tmp_path: Path) -> None:
    plan = plans.maybe_create("帮我盯住 600036 一周", data_root=tmp_path, now=NOW)
    assert plan is not None
    loaded = plans.load(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].symbols == ["600036"]


def test_maybe_create_dedupes(tmp_path: Path) -> None:
    first = plans.maybe_create("帮我盯住 600036 一周", data_root=tmp_path, now=NOW)
    assert first is not None
    second = plans.maybe_create("帮我盯住 600036 一周", data_root=tmp_path, now=NOW)
    assert second is None
    assert len(plans.load(tmp_path)) == 1


def test_maybe_create_respects_max_active(tmp_path: Path) -> None:
    for index in range(plans.MAX_ACTIVE):
        assert plans.maybe_create(f"帮我盯住 6000{index:02d} 一周", data_root=tmp_path, now=NOW)
    assert plans.maybe_create("帮我盯住 600099 一周", data_root=tmp_path, now=NOW) is None
    assert len(plans.active(tmp_path)) == plans.MAX_ACTIVE


# --------------------------------------------------------------------------- #
# 推进节奏
# --------------------------------------------------------------------------- #


def test_due_daily_once_per_day(tmp_path: Path) -> None:
    plan = plans.maybe_create("帮我盯住 600036 一周", data_root=tmp_path, now=NOW)
    assert plan is not None
    assert plan.due(NOW) is True
    plans.record_run(plan, status="ok", summary="首次", now=NOW, data_root=tmp_path)
    assert plan.due(NOW + timedelta(hours=2)) is False      # 同一天不重复
    assert plan.due(NOW + timedelta(days=1)) is True        # 次日再跑


def test_due_intraday_respects_gap(tmp_path: Path) -> None:
    plan = plans.build_plan("盯住 600036，盘中每半小时", now=NOW)
    assert plan is not None
    plans.record_run(plan, status="ok", summary="x", now=NOW, data_root=tmp_path)
    assert plan.due(NOW + timedelta(minutes=10), min_gap_min=30) is False
    assert plan.due(NOW + timedelta(minutes=40), min_gap_min=30) is True


def test_expired_plan_is_not_due(tmp_path: Path) -> None:
    plan = plans.build_plan("盯住 600036 三天", now=NOW)
    assert plan is not None
    assert plan.due(NOW + timedelta(days=4)) is False


def test_paused_plan_is_not_due(tmp_path: Path) -> None:
    plan = plans.maybe_create("帮我盯住 600036 一周", data_root=tmp_path, now=NOW)
    assert plan is not None
    plans.set_status(plan.id, plans.STATUS_PAUSED, data_root=tmp_path, now=NOW)
    reloaded = plans.load(tmp_path)[0]
    assert reloaded.due(NOW) is False


def test_record_run_closes_when_expired(tmp_path: Path) -> None:
    plan = plans.maybe_create("帮我盯住 600036 一周", data_root=tmp_path, now=NOW)
    assert plan is not None
    later = NOW + timedelta(days=10)
    plans.record_run(plan, status="ok", summary="最后一次", now=later, data_root=tmp_path)
    assert plans.load(tmp_path)[0].status == plans.STATUS_DONE


def test_tick_runs_runner_and_records(tmp_path: Path) -> None:
    plan = plans.maybe_create("帮我盯住 600036 一周", data_root=tmp_path, now=NOW)
    assert plan is not None
    calls: list[str] = []

    def runner(item: plans.Plan) -> dict:
        calls.append(item.id)
        return {"status": "ok", "summary": "今天资金净流入 3.2 亿", "path": "digest.json"}

    results = plans.tick(data_root=tmp_path, now=NOW + timedelta(hours=1), runner=runner)
    assert calls == [plan.id]
    assert results[0]["status"] == "ok"
    reloaded = plans.load(tmp_path)[0]
    assert reloaded.runs == 1
    assert "3.2 亿" in reloaded.last_summary
    assert reloaded.history[0]["path"] == "digest.json"


def test_tick_swallows_runner_failure(tmp_path: Path) -> None:
    plan = plans.maybe_create("帮我盯住 600036 一周", data_root=tmp_path, now=NOW)
    assert plan is not None

    def boom(item: plans.Plan) -> dict:
        raise RuntimeError("通道断了")

    results = plans.tick(data_root=tmp_path, now=NOW + timedelta(hours=1), runner=boom)
    assert results[0]["status"] == "failed"
    reloaded = plans.load(tmp_path)[0]
    assert reloaded.runs == 1
    assert "通道断了" in reloaded.last_summary


def test_corrupt_state_treated_as_never_run(tmp_path: Path) -> None:
    plan = plans.build_plan("盯住 600036 一周", now=NOW)
    assert plan is not None
    plan.last_run_at = "坏时间戳"
    assert plan.due(NOW) is True


# --------------------------------------------------------------------------- #
# 注入与视图
# --------------------------------------------------------------------------- #


def test_render_empty(tmp_path: Path) -> None:
    assert plans.render([]) == ""


def test_render_lists_active_only(tmp_path: Path) -> None:
    plan = plans.maybe_create("帮我盯住 600036 一周", data_root=tmp_path, now=NOW)
    assert plan is not None
    plans.record_run(plan, status="ok", summary="无变化", now=NOW, data_root=tmp_path)
    plans.maybe_create("帮我盯住 600519 一周", data_root=tmp_path, now=NOW)
    done = plans.maybe_create("帮我盯住 600030 一周", data_root=tmp_path, now=NOW)
    assert done is not None
    plans.set_status(done.id, plans.STATUS_DONE, data_root=tmp_path, now=NOW)

    block = plans.render(plans.load(tmp_path))
    assert plans.SECTION_HEADER in block
    assert "600036" in block
    assert "600519" in block
    assert "600030" not in block  # 已结束的不再进 prompt


def test_file_is_valid_json(tmp_path: Path) -> None:
    plans.maybe_create("帮我盯住 600036 一周", data_root=tmp_path, now=NOW)
    raw = json.loads(plans.path_for(tmp_path).read_text(encoding="utf-8"))
    assert raw["version"] == 1
    assert isinstance(raw["plans"], list)


def test_question_for_mentions_three_steps() -> None:
    plan = plans.build_plan("盯住 600036 一周", now=NOW)
    assert plan is not None
    text = plans.question_for(plan)
    assert "先用工具取数" in text
    assert "只讲变化" in text


# --------------------------------------------------------------------------- #
# 熔断与复盘：任务不能一直静默重试
# --------------------------------------------------------------------------- #


def _one_plan(tmp_path: Path) -> plans.Plan:
    plan = plans.maybe_create("帮我盯住 600036 一周", data_root=tmp_path, now=NOW)
    assert plan is not None
    return plan


def test_consecutive_failures_trip_the_breaker(tmp_path: Path) -> None:
    """连续失败到阈值自动暂停，并把最后一次原因写给用户看。"""
    plan = _one_plan(tmp_path)
    for _ in range(plans.MAX_FAILURES):
        plans.record_run(plan, status="failed", summary="通道断了", now=NOW, data_root=tmp_path)

    reloaded = plans.load(tmp_path)[0]
    assert reloaded.status == plans.STATUS_PAUSED
    assert reloaded.failures == plans.MAX_FAILURES
    assert "通道断了" in reloaded.last_error
    assert "已自动暂停" in reloaded.last_summary
    # 熔断后不再到期：不跑，也就不会每天刷同一份错误
    assert reloaded.due(NOW + timedelta(days=1)) is False


def test_success_resets_failure_counter(tmp_path: Path) -> None:
    plan = _one_plan(tmp_path)
    plans.record_run(plan, status="failed", summary="通道断了", now=NOW, data_root=tmp_path)
    plans.record_run(plan, status="ok", summary="无变化", now=NOW, data_root=tmp_path)

    reloaded = plans.load(tmp_path)[0]
    assert reloaded.failures == 0
    assert reloaded.last_error == ""
    assert reloaded.status == plans.STATUS_ACTIVE


def test_second_failure_does_not_trip_yet(tmp_path: Path) -> None:
    """不到阈值不许停：上游偶发一次抖动不该让整条任务下线。"""
    plan = _one_plan(tmp_path)
    plans.record_run(plan, status="failed", summary="超时", now=NOW, data_root=tmp_path)
    plans.record_run(plan, status="failed", summary="超时", now=NOW, data_root=tmp_path)

    reloaded = plans.load(tmp_path)[0]
    assert reloaded.status == plans.STATUS_ACTIVE
    assert reloaded.failures == 2
    assert reloaded.due(NOW) is True  # 当天还能重试


def test_manual_resume_clears_counter(tmp_path: Path) -> None:
    """用户点「恢复」= 重新给机会：计数归零，否则恢复后第一轮立刻又被熔断。"""
    plan = _one_plan(tmp_path)
    for _ in range(plans.MAX_FAILURES):
        plans.record_run(plan, status="failed", summary="通道断了", now=NOW, data_root=tmp_path)

    resumed = plans.set_status(plan.id, plans.STATUS_ACTIVE, data_root=tmp_path, now=NOW)
    assert resumed is not None
    assert resumed.failures == 0
    assert resumed.last_error == ""
    assert resumed.due(NOW) is True


def test_review_lists_each_round(tmp_path: Path) -> None:
    """复盘要能读出「哪一天成没成、说了什么」，而不是只给一个次数。"""
    plan = _one_plan(tmp_path)
    plans.record_run(plan, status="ok", summary="10.78，+3.65%", now=NOW, data_root=tmp_path)
    plans.record_run(plan, status="failed", summary="通道断了", now=NOW, data_root=tmp_path)

    reloaded = plans.load(tmp_path)[0]
    rows = plans.review(reloaded)
    assert len(rows) == 2
    assert rows[0]["ok"] is True
    assert "10.78" in rows[0]["summary"]
    assert rows[1]["ok"] is False
    assert rows[1]["date"] == NOW.date().isoformat()
