"""周度事实包（R11 / QUOT-149）：手算聚合值 + 可复算 + 不含模型输出。

三条验收线对应 issue 原文：

1. **稳定可复算**：同一份缓存连跑两次 `build_facts()`，序列化结果逐字节相同；
2. **口径与每日复盘一致**：单日指标全部来自 `review.pipeline`（夹具里只灌缓存，
   期望值按 `core/sentiment.py` / `core/ladder.py` 的算式手算，见
   `tests/fixtures/weekly_cache.py`）；
3. **不含模型输出**：事实包序列化后找不到 checkpoint 里的叙述正文，也没有 `ai` 键。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fixtures import weekly_cache as cache

from ladderstock.ai.checkpoint import SECTIONS
from ladderstock.review import pipeline as review_pipeline
from ladderstock.weekly import facts as facts_module

WEEK = cache.WEEK


@pytest.fixture()
def pack(tmp_path: Path) -> dict[str, Any]:
    cache.seed_week(tmp_path)
    return facts_module.build_facts(WEEK, root=tmp_path)


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _days(*texts: str) -> list[date]:
    return [date.fromisoformat(text) for text in texts]


# --------------------------------------------------------------------------- #
# 1. 骨架：周期、session_days、missing_days
# --------------------------------------------------------------------------- #

def test_week_shape(pack: dict[str, Any]) -> None:
    expected = cache.EXPECTED
    assert pack["week"] == expected["week"]
    assert pack["range"] == expected["range"]
    assert pack["weekdays"] == [
        "2026-10-26",
        "2026-10-27",
        "2026-10-28",
        "2026-10-29",
        "2026-10-30",
    ]
    assert pack["session_days"] == expected["session_days"]
    assert [item["date"] for item in pack["missing_days"]] == expected["missing_day_dates"]
    assert all(item["has_report"] is False for item in pack["missing_days"])
    assert pack["available"] is expected["available"]
    assert pack["reason"] is None


def test_day_snapshots_fall_back_to_recompute(tmp_path: Path) -> None:
    """没有当日复盘产物时用同一批函数补算，并如实标 `recomputed`（不复制公式）。"""
    cache.seed_week(tmp_path)
    snapshot = facts_module.day_snapshots(date.fromisoformat(cache.MON), root=tmp_path)
    assert snapshot["has_report"] is False
    assert set(snapshot["origins"]) == set(SECTIONS)
    assert set(snapshot["origins"].values()) == {"recomputed"}
    assert snapshot["available"] is True


def test_daily_artifact_wins_over_recompute(tmp_path: Path) -> None:
    """产物在 → 直接用：口径与当天发布的复盘逐字一致，补算只用于填缺的段。"""
    cache.seed_week(tmp_path)
    document = {
        "date": cache.MON,
        "sections": [
            {
                "name": "sentiment",
                "metrics": {"available": True, "temperature": 11.1, "promotion_rate": 1.0},
            }
        ],
    }
    review_pipeline.write_report(document, cache.MON, root=tmp_path)

    snapshot = facts_module.day_snapshots(date.fromisoformat(cache.MON), root=tmp_path)
    assert snapshot["origins"]["sentiment"] == "artifact"
    assert snapshot["metrics"]["sentiment"]["temperature"] == 11.1
    # 产物里没有的段仍走补算，不空着
    assert snapshot["origins"]["leader"] == "recomputed"
    assert snapshot["metrics"]["leader"]["height"] == 2


# --------------------------------------------------------------------------- #
# 2. 情绪与天梯聚合
# --------------------------------------------------------------------------- #

def test_sentiment_series_and_aggregates(pack: dict[str, Any]) -> None:
    expected = cache.EXPECTED["sentiment"]
    series = {row["date"]: row for row in pack["sentiment"]["series"]}
    for day, metrics in cache.DAY_METRICS.items():
        for key, value in metrics.items():
            assert series[day][key] == pytest.approx(value), f"{day}.{key}"
        assert series[day]["available"] is True

    temperature = pack["sentiment"]["temperature"]
    assert temperature["first"] == pytest.approx(expected["temperature_first"])
    assert temperature["last"] == pytest.approx(expected["temperature_last"])
    assert temperature["change"] == pytest.approx(expected["temperature_change"])
    assert temperature["avg"] == pytest.approx(expected["temperature_avg"])
    assert temperature["samples"] == expected["temperature_samples"]
    assert pack["sentiment"]["promotion_rate"]["avg"] == pytest.approx(expected["promotion_avg"])
    assert pack["sentiment"]["promotion_rate"]["change"] == pytest.approx(
        expected["promotion_change"]
    )
    assert pack["sentiment"]["seal_rate"]["avg"] == pytest.approx(expected["seal_avg"])
    assert pack["sentiment"]["limit_up_total"] == expected["limit_up_total"]
    assert pack["sentiment"]["broken_total"] == expected["broken_total"]
    assert pack["sentiment"]["gap_days"] == expected["gap_days"]


def test_missing_days_do_not_pollute_averages(pack: dict[str, Any]) -> None:
    """周四周五没数据：逐日序列给 null，均值/首末只算有读数的三天（D-05 不补默认值）。"""
    series = {row["date"]: row for row in pack["sentiment"]["series"]}
    assert series["2026-10-29"]["available"] is False
    assert series["2026-10-29"]["temperature"] is None
    assert pack["sentiment"]["temperature"]["samples"] == 3


def test_ladder_height_and_leaders(pack: dict[str, Any]) -> None:
    ladder = pack["ladder"]
    expected = cache.EXPECTED["ladder"]
    assert ladder["height"]["first"] == expected["height_first"]
    assert ladder["height"]["last"] == expected["height_last"]
    assert ladder["height"]["change"] == expected["height_change"]
    assert ladder["height"]["avg"] == expected["height_avg"]
    assert ladder["peak_height"] == expected["peak_height"]
    assert ladder["count_total"] == expected["count_total"]
    assert ladder["gap_days"] == expected["gap_days"]
    assert ladder["unknown_board_total"] == expected["unknown_board_total"]
    # 龙头：逐日最高板个股合并，同一代码只留连板数最高的那一天（600010 只在周二进过榜）
    assert [
        (item["code"], item["boards"], item["date"]) for item in ladder["leaders"]
    ] == list(cache.EXPECTED["leaders"])


# --------------------------------------------------------------------------- #
# 3. 涨跌分布 / 主线题材
# --------------------------------------------------------------------------- #

def test_breadth_distribution_is_hand_checkable(pack: dict[str, Any]) -> None:
    breadth = pack["breadth"]
    expected = cache.EXPECTED["breadth"]
    assert breadth["available"] is True
    counts = {item["band"]: item["count"] for item in breadth["distribution"]}
    assert counts == cache.BREADTH_COUNTS
    assert sum(counts.values()) == expected["samples"] == breadth["samples"]
    assert breadth["up_count"] == expected["up_count"]
    assert breadth["down_count"] == expected["down_count"]
    assert breadth["flat_count"] == expected["flat_count"]
    assert breadth["up_share_pct"] == pytest.approx(expected["up_share_pct"])
    share = {item["band"]: item["share_pct"] for item in breadth["distribution"]}
    assert share["涨≥9%"] == pytest.approx(18.2)


def test_breadth_only_counts_session_days(tmp_path: Path) -> None:
    """涨跌分布只统计有读数的那几天（样本数来自三个 session day）。"""
    cache.seed_week(tmp_path)
    breadth = facts_module.breadth_metrics(_days(*cache.EXPECTED["session_days"]), root=tmp_path)
    assert [row["date"] for row in breadth["days"]] == cache.EXPECTED["session_days"]
    assert breadth["samples"] == 11


def test_theme_streak_and_days_on_top(pack: dict[str, Any]) -> None:
    themes = pack["themes"]
    expected = cache.EXPECTED["themes"]
    assert themes["available"] is True
    assert themes["window"] == expected["window"]
    assert themes["observed"] == expected["observed"]
    assert [
        (item["name"], item["days_on_top"], item["streak_days"], item["avg_change_pct"])
        for item in themes["themes"]
    ] == list(cache.THEME_ORDER)
    top = themes["themes"][0]
    assert top["name"] == expected["top_theme"]
    assert top["streak_days"] == expected["top_streak"]
    assert top["first_date"] == cache.MON and top["last_date"] == cache.WED
    assert top["days"] == [cache.MON, cache.TUE, cache.WED]


# --------------------------------------------------------------------------- #
# 4. 待验证项结算（含跨周带入）
# --------------------------------------------------------------------------- #

def test_validation_settlement(pack: dict[str, Any]) -> None:
    validation = pack["validation"]
    assert validation["available"] is True
    for key, value in cache.VALIDATION_TOTALS.items():
        assert validation[key] == value, key
    assert validation["pass_rate"] == pytest.approx(cache.EXPECTED["validation"]["pass_rate"])
    assert [
        (item["generated_date"], item["id"]) for item in validation["items"]
    ] == list(cache.VALIDATION_ITEMS)
    # 上周尾「未结算」的那条不算本周的账
    assert "w43-pending" not in {item["id"] for item in validation["items"]}


def test_validation_reports_missing_when_nothing_generated(tmp_path: Path) -> None:
    cache.seed_week(tmp_path)
    empty = facts_module.validation_metrics(
        "2026-W48",
        _days("2026-11-23", "2026-11-24", "2026-11-25", "2026-11-26", "2026-11-27"),
        root=tmp_path,
    )
    assert empty["available"] is False
    assert empty["conditions"] == 0
    assert "没有待验证项" in str(empty["reason"])


# --------------------------------------------------------------------------- #
# 5. AI 完成度：只读状态，不读叙述
# --------------------------------------------------------------------------- #

def test_ai_coverage_counts_statuses_only(pack: dict[str, Any]) -> None:
    coverage = pack["ai_coverage"]
    for key, value in cache.AI_COVERAGE_TOTALS.items():
        assert coverage[key] == value, key
    assert [row["date"] for row in coverage["days"]] == [cache.MON, cache.TUE]


def test_fact_pack_contains_no_model_output(pack: dict[str, Any]) -> None:
    """要求 2：事实包里没有模型输出——既没有 `ai`/`narrative` 键，也没有叙述正文。"""
    text = _dumps(pack)
    assert cache.CHECKPOINT_NARRATIVE not in text
    assert "narrative" not in text
    assert '"ai"' not in text
    assert "generated_at" not in text


# --------------------------------------------------------------------------- #
# 6. 可复算 / 非法输入
# --------------------------------------------------------------------------- #

def test_build_facts_is_byte_identical_across_runs(tmp_path: Path) -> None:
    cache.seed_week(tmp_path)
    first = _dumps(facts_module.build_facts(WEEK, root=tmp_path))
    second = _dumps(facts_module.build_facts(WEEK, root=tmp_path))
    assert first == second


def test_empty_week_gives_none_plus_reason(tmp_path: Path) -> None:
    """整周没数据：available=false + reason，各分项如实给 null，不拿 0 冒充读数。"""
    pack = facts_module.build_facts(WEEK, root=tmp_path)
    assert pack["available"] is False
    assert pack["session_days"] == []
    assert pack["sentiment"]["temperature"]["avg"] is None
    assert pack["breadth"]["available"] is False
    assert pack["themes"]["available"] is False
    assert pack["validation"]["available"] is False
    assert set(pack["missing_inputs"]) == {
        "state/review",
        "state/ai_checkpoint",
        "state/validation",
    }
    assert pack["caliber"] == facts_module.CALIBER


def test_illegal_week_is_rejected_before_path_join(tmp_path: Path) -> None:
    """周标签会进文件名：非法输入必须抛，不能拼出目录穿越路径。"""
    with pytest.raises(ValueError):
        facts_module.build_facts("../../etc/passwd", root=tmp_path)
