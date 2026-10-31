"""周度事实包（R11）：把一周的每日产物聚合成**确定性**数字。

三条硬规矩（口径全文见 `docs/specs/SPEC-WEEKLY.md`）：

1. **不复制公式**：单日指标一律走每日复盘那同一批函数
   （`review.pipeline.load_report` / `collect_metrics` → `core/sentiment` /
   `core/ladder` / `core/pool` 的字段容忍表与去重口径）。本模块只做**聚合**
   （求和、均值、序列、连续天数），不重新定义任何单日指标。
2. **不含模型输出**：事实包里没有 `ai` / `narrative` 键，也不取
   `state/ai_checkpoint/<date>.json` 里的叙述正文——那里只读**段落状态**
   （`pending/ok/degraded/failed`），用来算「本周 AI 完成率」。
3. **可复算**：本模块**不调 `now()`**、不用随机数、排序全部有确定的次键，
   同一份本机缓存连跑两次 `build_facts()`，序列化结果逐字节相同
   （`generated_at` 装在信封层，见 `pipeline.py`，不进事实包）。

算得出就给数，算不出就给 `None` + `reason`，缺数据如实进 `missing_days` /
`missing_inputs`，绝不拿默认值冒充读数（与每日复盘 D-05 / D-06 同一口径）。
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from ladderstock.ai import checkpoint as ai_checkpoint
from ladderstock.ai.checkpoint import SECTIONS
from ladderstock.core import ladder as ladder_module
from ladderstock.core import pool as pool_module
from ladderstock.core import validation as validation_module
from ladderstock.review import pipeline as review_pipeline
from ladderstock.weekly import week as week_module

#: 涨跌分布的输入数据集（个股日 K，`change_pct` 走 `core/pool.py` 同一张容忍表）
BREADTH_DATASET = "daily_k"
#: 主线题材的输入数据集（概念板块排行）
THEME_DATASET = "concept_board"

#: 主线题材窗口：每日按涨幅取前 N 名，题材在窗口内的天数即「持续天数」
THEME_WINDOW = 10
#: 周度输出给 AI 与页面的题材条数上限
THEME_TOP_N = 10
#: 周度龙头输出上限（本周最高板个股）
LEADER_TOP_N = 5
#: 上周尾条件「在本周结算」最多回看几个日历日（周五之后跨周末）
CARRY_LOOKBACK_DAYS = 5

#: 涨跌分布分箱，按顺序命中一次；单位 %，区间为 `[low, high)`
BREADTH_BANDS: tuple[tuple[str, float, float], ...] = (
    ("跌≥9%", float("-inf"), -9.0),
    ("跌5~9%", -9.0, -5.0),
    ("跌2~5%", -5.0, -2.0),
    ("跌0~2%", -2.0, 0.0),
    ("平盘", 0.0, 0.0),
    ("涨0~2%", 0.0, 2.0),
    ("涨2~5%", 2.0, 5.0),
    ("涨5~9%", 5.0, 9.0),
    ("涨≥9%", 9.0, float("inf")),
)
FLAT_BAND = "平盘"

#: 公式口径原文（原样进事实包，读者不必翻代码就知道这个数怎么来的）
CALIBER: dict[str, str] = {
    "temperature": "复用 core/sentiment.py：温度 = 0.45×晋级率 + 0.35×封板率 "
    "+ 0.20×min(1, 最高板/6)，缺失分项按可用权重重新归一",
    "promotion_rate": "复用 core/sentiment.py：昨日涨停股中今日继续涨停的比例",
    "ladder_height": "复用 core/ladder.py：涨停池按 code 去重后的最大连板数",
    "breadth": f"本周 {BREADTH_DATASET} 个股涨跌幅分箱计数（{BREADTH_BANDS[0][0]} … "
    f"{BREADTH_BANDS[-1][0]}），同日同代码只计首条，与天梯 D-04 去重口径一致",
    "theme_streak": f"本周 {THEME_DATASET} 每日涨幅前 {THEME_WINDOW} 名记为一个窗口；"
    "days_on_top = 进入窗口的交易日数，streak_days = 本周内的最长连续交易日数",
    "validation": "复用 core/validation.py 的 `reconcile` 结果：settled = 已有对账结论的"
    "条件数，未对账的进 pending，判定不出的进 unknown",
    "ai_coverage": "读 state/ai_checkpoint/<date>.json 的段落状态计数（只读，不重跑模型）",
}


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #

def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _round(value: Any, digits: int = 1) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _usable(values: list[Any]) -> list[float]:
    """序列里的可用读数（`None` / 布尔 / 文本一律剔除，不补 0）。"""
    return [
        float(value)
        for value in values
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]


def _avg(values: list[Any]) -> float | None:
    numbers = _usable(values)
    if not numbers:
        return None
    return _round(sum(numbers) / len(numbers))


def _sum_or_none(values: list[Any]) -> int | None:
    numbers = [
        int(value)
        for value in values
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    return sum(numbers) if numbers else None


def _trend(values: list[Any]) -> dict[str, Any]:
    """序列 → 首末值、变化、均值、样本数；没有可用读数就逐字段给 `None`。"""
    numbers = _usable(values)
    if not numbers:
        return {"first": None, "last": None, "change": None, "avg": None, "samples": 0}
    return {
        "first": _round(numbers[0]),
        "last": _round(numbers[-1]),
        "change": _round(numbers[-1] - numbers[0]),
        "avg": _round(sum(numbers) / len(numbers)),
        "samples": len(numbers),
    }


def _band(change_pct: float) -> str:
    """涨跌分箱：按 `BREADTH_BANDS` 顺序命中一次（`平盘` 是 `[0, 0]` 单点）。"""
    for label, low, high in BREADTH_BANDS:
        if low == high:
            if change_pct == low:
                return label
        elif low <= change_pct < high:
            return label
    return BREADTH_BANDS[-1][0]


# --------------------------------------------------------------------------- #
# 单日快照：优先复用当日复盘产物，缺的段落用同一批函数补算
# --------------------------------------------------------------------------- #

def day_snapshots(day: date, *, root: Any = None) -> dict[str, Any]:
    """某交易日的五段指标快照 + 来源标记（`artifact` / `recomputed`）。

    产物在 → 直接用，口径与当天发布的复盘**逐字一致**；产物缺某段 → 用
    `review.pipeline.collect_metrics()` 补算那一段，不写第二套公式。
    """
    report = review_pipeline.load_report(day, root=root)
    metrics: dict[str, dict[str, Any]] = {name: {} for name in SECTIONS}
    origins: dict[str, str] = {}
    for section in (report or {}).get("sections") or []:
        if not isinstance(section, dict):
            continue
        name = str(section.get("name") or "")
        snapshot = section.get("metrics")
        if name in metrics and isinstance(snapshot, dict) and snapshot:
            metrics[name] = dict(snapshot)
            origins[name] = "artifact"
    gaps = [name for name in SECTIONS if not metrics[name]]
    if gaps:
        computed = review_pipeline.collect_metrics(day, root=root)
        for name in gaps:
            snapshot = computed.get(name)
            metrics[name] = dict(snapshot) if isinstance(snapshot, dict) else {}
            origins[name] = "recomputed"
    sentiment = metrics["sentiment"]
    return {
        "date": day,
        "metrics": metrics,
        "origins": origins,
        "has_report": bool(report),
        "available": bool(sentiment.get("available")),
        "reason": sentiment.get("reason"),
    }


def checkpoint_status(day: date, root: Any = None) -> dict[str, Any] | None:
    """当日 AI checkpoint 的段落状态（只读状态，**不读叙述正文**）。"""
    document = _read_json(ai_checkpoint.checkpoint_path(day, root))
    if document is None:
        return None
    sections = document.get("sections")
    if not isinstance(sections, dict):
        return None
    statuses = {
        name: str((sections.get(name) or {}).get("status") or "pending")
        for name in SECTIONS
        if isinstance(sections.get(name), dict)
    }
    return {
        "date": day.isoformat(),
        "statuses": statuses,
        "done": sum(1 for status in statuses.values() if status in ("ok", "degraded")),
        "degraded": sum(1 for status in statuses.values() if status == "degraded"),
        "failed": sum(1 for status in statuses.values() if status == "failed"),
        "pending": sum(1 for status in statuses.values() if status == "pending"),
    }


# --------------------------------------------------------------------------- #
# 分项聚合
# --------------------------------------------------------------------------- #

def breadth_metrics(days: list[date], root: Any = None) -> dict[str, Any]:
    """涨跌分布：本周逐日 `daily_k` 个股涨跌幅分箱计数。"""
    labels = [label for label, _low, _high in BREADTH_BANDS]
    totals = dict.fromkeys(labels, 0)
    per_day: list[dict[str, Any]] = []
    samples = 0
    backends: set[str] = set()
    for day in days:
        records, backend = ladder_module.dataset_snapshot(BREADTH_DATASET, day, root)
        backends.add(backend)
        rows = [
            value
            for value in (
                ladder_module.as_float(ladder_module.pick(record, pool_module.CHANGE_PCT_KEYS))
                for record in ladder_module.dedupe_by_code(records)
            )
            if value is not None
        ]
        if not rows:
            per_day.append(
                {
                    "date": day.isoformat(),
                    "available": False,
                    "reason": (
                        f"本地缓存没有 {BREADTH_DATASET} 数据或记录里没有涨跌幅字段"
                        f"（先运行 ldst sync --dataset {BREADTH_DATASET}）"
                    ),
                    "samples": 0,
                    "bands": dict.fromkeys(labels, 0),
                }
            )
            continue
        counts = dict.fromkeys(labels, 0)
        for value in rows:
            counts[_band(value)] += 1
        for label, count in counts.items():
            totals[label] += count
        samples += len(rows)
        per_day.append(
            {
                "date": day.isoformat(),
                "available": True,
                "reason": None,
                "samples": len(rows),
                "bands": counts,
            }
        )
    distribution = [
        {
            "band": label,
            "count": totals[label],
            "share_pct": _round(totals[label] * 100.0 / samples) if samples else None,
        }
        for label in labels
    ]
    result: dict[str, Any] = {
        "available": bool(samples),
        "reason": (
            None
            if samples
            else f"本周没有 {BREADTH_DATASET} 涨跌幅样本（先运行 ldst sync --dataset "
            f"{BREADTH_DATASET}），涨跌分布不估算"
        ),
        "bands": [label for label, _low, _high in BREADTH_BANDS],
        "days": per_day,
        "distribution": distribution,
        "up_count": sum(totals[label] for label in labels if label.startswith("涨")),
        "down_count": sum(totals[label] for label in labels if label.startswith("跌")),
        "flat_count": totals[FLAT_BAND],
        "samples": samples,
    }
    if samples:
        result["up_share_pct"] = _round(result["up_count"] * 100.0 / samples)
        result["source_detail"] = (
            f"{BREADTH_DATASET}: {'/'.join(sorted(backends)) or 'none'}（{samples} 个涨跌幅样本）"
        )
    return result


def theme_metrics(days: list[date], root: Any = None) -> dict[str, Any]:
    """主线题材持续天数：逐日涨幅前 `THEME_WINDOW` 名，统计入窗天数与最长连续。"""
    dates = [day.isoformat() for day in days]
    windows: list[list[str]] = []
    rows: list[dict[str, Any]] = []
    changes: dict[str, list[float]] = {}
    backends: set[str] = set()
    for day in days:
        records, backend = ladder_module.dataset_snapshot(THEME_DATASET, day, root)
        backends.add(backend)
        index = pool_module.theme_board_index(records)
        # 名次键：涨幅降序、同涨幅按名称升序——不依赖 dict 插入顺序，结果可复算
        ranked = sorted(index.items(), key=lambda item: (-item[1], item[0]))
        windows.append([name for name, _change in ranked[:THEME_WINDOW]])
        rows.append({"date": day.isoformat(), "rows": len(index)})
        for name, change in ranked:
            changes.setdefault(name, []).append(change)

    if not any(windows):
        return {
            "available": False,
            "reason": (
                f"本周没有 {THEME_DATASET} 数据（先运行 ldst sync --dataset {THEME_DATASET}）"
            ),
            "window": THEME_WINDOW,
            "days": rows,
            "themes": [],
        }

    themes: list[dict[str, Any]] = []
    for name in sorted({name for window in windows for name in window}):
        hits = [index for index, window in enumerate(windows) if name in window]
        streak = 0
        best_streak = 0
        for position in range(len(windows)):
            if position in hits:
                streak += 1
                best_streak = max(best_streak, streak)
            else:
                streak = 0
        values = changes.get(name) or []
        themes.append(
            {
                "name": name,
                "days_on_top": len(hits),
                "streak_days": best_streak,
                "first_date": dates[hits[0]],
                "last_date": dates[hits[-1]],
                "best_change_pct": _round(max(values)) if values else None,
                "avg_change_pct": _avg(values),
                "days": [dates[index] for index in hits],
            }
        )
    # 排序键全为确定值：连续天数 → 入窗天数 → 平均涨幅 → 名称
    themes.sort(
        key=lambda item: (
            -int(item["streak_days"]),
            -int(item["days_on_top"]),
            -(item["avg_change_pct"] if item["avg_change_pct"] is not None else 0.0),
            str(item["name"]),
        )
    )
    return {
        "available": True,
        "reason": None,
        "window": THEME_WINDOW,
        "days": rows,
        "themes": themes[:THEME_TOP_N],
        "observed": len(themes),
        "source_detail": f"{THEME_DATASET}: {'/'.join(sorted(backends)) or 'none'}",
    }


def _condition_rows(
    document: dict[str, Any], generated_date: str
) -> list[dict[str, Any]]:
    """一份验证文档 → 逐条结算行（`settled` 只认 `reconcile.results`）。"""
    reconcile = document.get("reconcile")
    reconcile = reconcile if isinstance(reconcile, dict) else None
    results = {
        str(row.get("id")): row
        for row in (reconcile or {}).get("results") or []
        if isinstance(row, dict)
    }
    rows: list[dict[str, Any]] = []
    for condition in document.get("conditions") or []:
        if not isinstance(condition, dict):
            continue
        row = results.get(str(condition.get("id")))
        settled = isinstance(row, dict)
        verdict = row.get("passed") if isinstance(row, dict) else None
        rows.append(
            {
                "generated_date": generated_date,
                "id": condition.get("id"),
                "text": condition.get("text"),
                "metric": condition.get("metric"),
                "operator": condition.get("operator"),
                "target": condition.get("value"),
                "actual": row.get("actual") if isinstance(row, dict) else None,
                "settled": settled,
                "passed": verdict if settled else None,
                "next_session": (reconcile or {}).get("next_session"),
                "reconcile_status": (reconcile or {}).get("status"),
            }
        )
    return rows


def validation_metrics(week: str, weekdays: list[date], root: Any = None) -> dict[str, Any]:
    """待验证项结算：本周生成的条件 + 上周尾条件在本周的对账结果。"""
    start = week_module.week_start(week)
    candidates = list(weekdays)
    # 上周五的条件在本周首个交易日结算，属于「本周结算结果」；只认已对账的那批
    for offset in range(1, CARRY_LOOKBACK_DAYS + 1):
        candidates.append(start - timedelta(days=offset))

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for day in sorted(set(candidates), key=lambda item: item.isoformat()):
        key = day.isoformat()
        if key in seen:
            continue
        seen.add(key)
        document = validation_module.load_validation(day, root=root)
        if not isinstance(document, dict):
            continue
        rows = _condition_rows(document, key)
        if not rows:
            continue
        inside = week_module.in_week(week, day)
        # 本周外的日子：只带进「已在本周结算完」的条目，未结算的不算本周的账
        items.extend(
            row
            for row in rows
            if inside or (row["settled"] and row["next_session"] and _settled_inside(week, row))
        )

    items.sort(key=lambda item: (str(item["generated_date"]), str(item["id"]), str(item["metric"])))
    settled_rows = [row for row in items if row["settled"]]
    passed = sum(1 for row in settled_rows if row["passed"] is True)
    failed = sum(1 for row in settled_rows if row["passed"] is False)
    unknown = sum(1 for row in settled_rows if row["passed"] is None)
    available = bool(items)
    return {
        "available": available,
        "reason": (
            None
            if available
            else "本周没有待验证项落盘（先运行 ldst review --date <交易日>，"
            "条件随复盘生成）"
        ),
        "conditions": len(items),
        "settled": len(settled_rows),
        "passed": passed,
        "failed": failed,
        "unknown": unknown,
        "pending": len(items) - len(settled_rows),
        "pass_rate": _round(passed * 100.0 / len(settled_rows)) if settled_rows else None,
        "items": items,
    }


def _settled_inside(week: str, row: dict[str, Any]) -> bool:
    """对账落点（`next_session`）是否落在本周内。"""
    try:
        settled_day = date.fromisoformat(str(row["next_session"])[:10])
    except ValueError:
        return False
    return week_module.in_week(week, settled_day)


# --------------------------------------------------------------------------- #
# 事实包
# --------------------------------------------------------------------------- #

def build_facts(week_tag: Any, *, root: Any = None) -> dict[str, Any]:
    """聚合某一周的**确定性事实包**（不含模型输出、不含 `now()`）。"""
    week = week_module.normalize_week(week_tag)
    start, end = week_module.week_bounds(week)
    weekdays = week_module.weekday_days(week)
    iso_year, iso_week = week_module.parse_week(week)

    snapshots = {day: day_snapshots(day, root=root) for day in weekdays}
    statuses = {day: checkpoint_status(day, root) for day in weekdays}
    validations = {day: validation_module.load_validation(day, root=root) for day in weekdays}
    session_days = [day for day in weekdays if snapshots[day]["available"]]

    def metric(day: date, section: str, name: str) -> Any:
        return snapshots[day]["metrics"][section].get(name)

    sentiment_series = [
        {
            "date": day.isoformat(),
            "available": bool(metric(day, "sentiment", "available")),
            "temperature": metric(day, "sentiment", "temperature"),
            "promotion_rate": metric(day, "sentiment", "promotion_rate"),
            "seal_rate": metric(day, "sentiment", "seal_rate"),
            "limit_up_count": metric(day, "sentiment", "limit_up_count"),
            "broken_count": metric(day, "sentiment", "broken_count"),
            "max_board": metric(day, "sentiment", "max_board"),
            "gap_levels": metric(day, "sentiment", "gap_levels") or [],
            "missing": metric(day, "sentiment", "missing") or [],
            "source": snapshots[day]["origins"].get("sentiment"),
            "reason": metric(day, "sentiment", "reason"),
        }
        for day in weekdays
    ]
    ladder_series = [
        {
            "date": day.isoformat(),
            "available": bool(metric(day, "leader", "available")),
            "height": metric(day, "leader", "height"),
            "count": metric(day, "leader", "count"),
            "gap": bool(metric(day, "leader", "gap")),
            "gap_levels": metric(day, "leader", "gap_levels") or [],
            "unknown_board": metric(day, "leader", "unknown_board"),
            "reason": metric(day, "leader", "reason"),
        }
        for day in weekdays
    ]
    capital_series = [
        {
            "date": day.isoformat(),
            "available": bool(metric(day, "capital", "available")),
            "records": metric(day, "capital", "records"),
            "reason": metric(day, "capital", "reason"),
        }
        for day in weekdays
    ]
    theme_series = [
        {
            "date": day.isoformat(),
            "available": bool(metric(day, "theme", "available")),
            "industry_rows": metric(day, "theme", "industry_rows"),
            "concept_rows": metric(day, "theme", "concept_rows"),
            "news_rows": metric(day, "theme", "news_rows"),
            "reason": metric(day, "theme", "reason"),
        }
        for day in weekdays
    ]
    dragon_series = [
        {
            "date": day.isoformat(),
            "available": bool(metric(day, "dragon_tiger", "available")),
            "board_rows": metric(day, "dragon_tiger", "board_rows"),
            "seat_rows": metric(day, "dragon_tiger", "seat_rows"),
            "reason": metric(day, "dragon_tiger", "reason"),
        }
        for day in weekdays
    ]

    heights = [row["height"] for row in ladder_series if isinstance(row["height"], int)]
    peak_height = max(heights) if heights else None

    # 本周龙头：逐日 `leader_candidates`（当日最高板个股，core/ladder.py 已截前 3）合并，
    # 同一代码只留连板数最高的那一天（按周一次遍历、严格更大才替换，并列自动保留更早一天）
    peak_stocks: dict[str, dict[str, Any]] = {}
    for day in weekdays:
        for stock in metric(day, "leader", "leader_candidates") or []:
            if not isinstance(stock, dict) or not stock.get("code"):
                continue
            code = str(stock["code"])
            current = peak_stocks.get(code)
            if current is None or int(stock.get("boards") or 0) > int(
                current.get("boards") or 0
            ):
                peak_stocks[code] = {**stock, "date": day.isoformat()}
    leaders = [
        {
            "code": stock.get("code"),
            "name": stock.get("name"),
            "boards": stock.get("boards"),
            "date": stock["date"],
        }
        for stock in sorted(
            peak_stocks.values(),
            key=lambda item: (-int(item.get("boards") or 0), str(item.get("code"))),
        )[:LEADER_TOP_N]
    ]

    ai_rows = [row for row in (statuses[day] for day in weekdays) if row]
    missing_inputs: list[str] = []
    if not any(snapshot["has_report"] for snapshot in snapshots.values()):
        missing_inputs.append("state/review")
    if not ai_rows:
        missing_inputs.append("state/ai_checkpoint")
    if not any(document for document in validations.values()):
        missing_inputs.append("state/validation")

    breadth = breadth_metrics(session_days, root)
    themes = theme_metrics(session_days, root)

    return {
        "week": week,
        "iso_year": iso_year,
        "iso_week": iso_week,
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        "weekdays": [day.isoformat() for day in weekdays],
        "session_days": [day.isoformat() for day in session_days],
        "missing_days": [
            {
                "date": day.isoformat(),
                "reason": snapshots[day]["reason"] or "当日没有可用情绪指标",
                "has_report": snapshots[day]["has_report"],
            }
            for day in weekdays
            if not snapshots[day]["available"]
        ],
        "available": bool(session_days),
        "reason": (
            None
            if session_days
            else f"{week} 没有可用单日指标：本周 5 个工作日的涨停池都是空的"
            "（先运行 ldst sync，或等本周收盘后再生成）"
        ),
        "sentiment": {
            "series": sentiment_series,
            "temperature": _trend([row["temperature"] for row in sentiment_series]),
            "promotion_rate": _trend([row["promotion_rate"] for row in sentiment_series]),
            "seal_rate": _trend([row["seal_rate"] for row in sentiment_series]),
            "limit_up_total": _sum_or_none([row["limit_up_count"] for row in sentiment_series]),
            "broken_total": _sum_or_none([row["broken_count"] for row in sentiment_series]),
            "gap_days": sum(1 for row in sentiment_series if row["gap_levels"]),
        },
        "ladder": {
            "series": ladder_series,
            "height": _trend(heights),
            "peak_height": peak_height,
            "count_total": _sum_or_none([row["count"] for row in ladder_series]),
            "gap_days": sum(1 for row in ladder_series if row["gap"]),
            "unknown_board_total": _sum_or_none([row["unknown_board"] for row in ladder_series]),
            "leaders": leaders,
        },
        "breadth": breadth,
        "themes": themes,
        "capital": {
            "series": capital_series,
            "days_available": sum(1 for row in capital_series if row["available"]),
            "records_total": _sum_or_none([row["records"] for row in capital_series]),
        },
        "theme_rows": {
            "series": theme_series,
            "days_available": sum(1 for row in theme_series if row["available"]),
        },
        "dragon_tiger": {
            "series": dragon_series,
            "days_available": sum(1 for row in dragon_series if row["available"]),
            "board_rows_total": _sum_or_none([row["board_rows"] for row in dragon_series]),
            "seat_rows_total": _sum_or_none([row["seat_rows"] for row in dragon_series]),
        },
        "validation": validation_metrics(week, weekdays, root),
        "ai_coverage": {
            "days": ai_rows,
            "days_with_checkpoint": len(ai_rows),
            "sections_expected": len(ai_rows) * len(SECTIONS),
            "sections_done": sum(row["done"] for row in ai_rows),
            "sections_degraded": sum(row["degraded"] for row in ai_rows),
            "sections_failed": sum(row["failed"] for row in ai_rows),
            "sections_pending": sum(row["pending"] for row in ai_rows),
        },
        "missing_inputs": missing_inputs,
        "caliber": dict(CALIBER),
        "source_detail": _source_detail(session_days, breadth, themes),
    }


def _source_detail(days: list[date], breadth: dict[str, Any], themes: dict[str, Any]) -> str:
    """本周数字来自哪些缓存后端（只有后端名与条数，不含时间戳——事实包必须可复算）。"""
    parts: list[str] = []
    if days:
        rows, backend = ladder_module.dataset_snapshot("limit_pool", days[-1], None)
        parts.append(f"limit_pool: {backend}（周末日 {days[-1].isoformat()} 共 {len(rows)} 条）")
    for source in (breadth, themes):
        detail = source.get("source_detail")
        if detail:
            parts.append(str(detail))
    return "；".join(parts)


__all__ = [
    "BREADTH_BANDS",
    "BREADTH_DATASET",
    "CALIBER",
    "LEADER_TOP_N",
    "THEME_DATASET",
    "THEME_TOP_N",
    "THEME_WINDOW",
    "breadth_metrics",
    "build_facts",
    "checkpoint_status",
    "day_snapshots",
    "theme_metrics",
    "validation_metrics",
]
