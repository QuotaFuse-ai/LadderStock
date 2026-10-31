"""周度事实包的夹具：一周三session的缓存 + 验证文档 + 每日 checkpoint（值全部手算）。

给 `tests/unit/weekly/` 与 `tests/unit/web/test_weekly_endpoint.py` 共用——
一份输入、一份期望值，两处断言不会各写一套口径。

周期固定 `2026-W44`（周一 2026-10-26），只灌周一/周二/周三三天，周四周五留空，
这样 `missing_days` / `session_days` 两条路径同一次跑里都能断言。上周尾（10-23）
的条件也进夹具，用来钉「上周条件在本周结算」这条跨周口径。

期望值 `EXPECTED` 按 `docs/specs/SPEC-WEEKLY.md` 的算式手算，不看实现输出填的。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ladderstock.ai.checkpoint import atomic_write
from ladderstock.core import validation as validation_module
from ladderstock.data import duckdb_store

WEEK = "2026-W44"
MON = "2026-10-26"
TUE = "2026-10-27"
WED = "2026-10-28"
#: 上周周五：它的已结算条件应算进本周结算结果
CARRY = "2026-10-23"

#: 写在每日 checkpoint 里的叙述正文——事实包**绝不能**把它带出来
CHECKPOINT_NARRATIVE = "这段是模型写的叙述，绝不能出现在周度事实包里"


def _pool(code: str, name: str, boards: int, day: str) -> dict[str, Any]:
    return {"code": code, "name": name, "consecutive_boards": boards, "trade_date": day}


def _board(name: str, change_pct: float, day: str) -> dict[str, Any]:
    return {"name": name, "change_pct": change_pct, "trade_date": day}


def _k(code: str, change_pct: float, day: str) -> dict[str, Any]:
    return {"code": code, "change_pct": change_pct, "trade_date": day}


def _validation(day: str, conditions: list[dict[str, Any]], reconcile: dict[str, Any] | None):
    document: dict[str, Any] = {"date": day, "conditions": conditions}
    if reconcile is not None:
        document["reconcile"] = reconcile
    return document


def _condition(cid: str, metric: str, operator: str, value: Any) -> dict[str, Any]:
    return {
        "id": cid,
        "text": f"{metric} {operator} {value}",
        "metric": metric,
        "operator": operator,
        "value": value,
    }


def seed_week(root: Path) -> Path:
    """把 `2026-W44` 的周一/二/三写进本机缓存（只写 tmp，不碰仓库 `data/`）。"""
    root = Path(root)
    datasets: dict[str, list[dict[str, Any]]] = {
        # --- 情绪与天梯：三天的涨停池 / 昨日涨停 / 炸板池 ---
        "limit_pool": [
            _pool("600001", "甲", 2, MON),
            _pool("600002", "乙", 1, MON),
            _pool("600001", "甲", 3, TUE),
            _pool("600002", "乙", 2, TUE),
            _pool("600005", "戊", 1, TUE),
            # 并列最高板：龙头合并要留下「当天最高板的前几只」，所以给两个 3 板
            _pool("600010", "壬", 3, TUE),
            _pool("600001", "甲", 4, WED),
            _pool("600006", "己", 1, WED),
        ],
        "yesterday_limit_up": [
            _pool("600001", "甲", 1, MON),
            _pool("600003", "丙", 1, MON),
            _pool("600001", "甲", 2, TUE),
            _pool("600002", "乙", 1, TUE),
            _pool("600001", "甲", 3, WED),
            _pool("600002", "乙", 2, WED),
            _pool("600005", "戊", 1, WED),
        ],
        "broken_board": [
            _pool("600004", "丁", 0, MON),
            _pool("600007", "庚", 0, WED),
            _pool("600008", "辛", 0, WED),
        ],
        # --- 涨跌分布：个股日 K 的涨跌幅 ---
        "daily_k": [
            _k("600001", 10.0, MON),
            _k("600002", 3.0, MON),
            _k("600003", -1.0, MON),
            _k("600004", 0.0, MON),
            _k("600001", 9.9, TUE),
            _k("600005", -6.0, TUE),
            _k("600002", -10.5, TUE),
            _k("600006", 5.0, WED),
            _k("600007", 1.5, WED),
            _k("600008", 2.0, WED),
            _k("600009", -0.5, WED),
        ],
        # --- 主线题材：概念板块涨幅 ---
        "concept_board": [
            _board("AI算力", 8.0, MON),
            _board("机器人", 6.0, MON),
            _board("券商", 4.0, MON),
            _board("券商", 9.0, TUE),
            _board("AI算力", 7.0, TUE),
            _board("白酒", -2.0, TUE),
            _board("机器人", 10.0, WED),
            _board("AI算力", 3.0, WED),
        ],
        # --- 资金 / 板块 / 龙虎榜：只喂「有无读数」这一段 ---
        "fund_flow": [
            {"code": "600001", "net_inflow": 1200.0, "trade_date": MON},
            {"code": "600002", "net_inflow": -300.0, "trade_date": MON},
            {"code": "600001", "net_inflow": 800.0, "trade_date": TUE},
        ],
        "industry_board": [_board("半导体", 3.0, MON)],
        "theme_news": [
            {"title": "算力缺口", "data_as_of": MON, "trade_date": MON},
        ],
        "dragon_tiger": [
            {"code": "600001", "name": "甲", "trade_date": MON},
            {"code": "600002", "name": "乙", "trade_date": MON},
            {"code": "600001", "name": "甲", "trade_date": TUE},
        ],
        "dragon_tiger_seats": [
            {"code": "600001", "seat": "席位A", "trade_date": MON},
            {"code": "600001", "seat": "席位B", "trade_date": MON},
            {"code": "600002", "seat": "席位C", "trade_date": MON},
            {"code": "600001", "seat": "席位A", "trade_date": TUE},
        ],
    }
    for dataset, rows in datasets.items():
        duckdb_store.write_dataset(dataset, rows, source="tickhub", root=root)

    # --- 待验证项：上周尾 1 条已结算 + 1 条未结算；本周三天各若干 ---
    documents = {
        CARRY: _validation(
            CARRY,
            [
                _condition("w43-settled", "temperature", ">=", 40.0),
                _condition("w43-pending", "promotion_rate", ">=", 20.0),
            ],
            {
                "status": "ok",
                "next_session": MON,
                "results": [{"id": "w43-settled", "passed": True, "actual": 52.5}],
            },
        ),
        MON: _validation(
            MON,
            [
                _condition("x-pending", "gap_levels", "not_contains", [2]),
                _condition("x-settled-fail", "promotion_rate", ">=", 60.0),
                _condition("x-unknown", "max_board", ">=", 3),
            ],
            {
                "status": "partial",
                "next_session": TUE,
                "results": [
                    {"id": "x-settled-fail", "passed": False, "actual": 50.0},
                    {"id": "x-unknown", "passed": None, "actual": None},
                ],
            },
        ),
        TUE: _validation(TUE, [_condition("y-pending", "temperature", ">=", 70.0)], None),
    }
    for day, document in documents.items():
        validation_module.write_document(validation_module.validation_path(day, root), document)

    # --- 每日 AI checkpoint：只写段落状态（事实包只读状态，不读叙述） ---
    checkpoints = {
        MON: {
            "sentiment": "ok",
            "capital": "degraded",
            "theme": "failed",
            "dragon_tiger": "pending",
            "leader": "ok",
        },
        TUE: {name: "ok" for name in ("sentiment", "capital", "theme", "dragon_tiger", "leader")},
    }
    for day, statuses in checkpoints.items():
        sections: dict[str, Any] = {
            name: {"status": status} for name, status in statuses.items()
        }
        sections["sentiment"]["narrative"] = [CHECKPOINT_NARRATIVE]
        atomic_write(
            root / "state" / "ai_checkpoint" / f"{day}.json",
            json.dumps({"date": day, "sections": sections}, ensure_ascii=False),
        )
    return root


# --------------------------------------------------------------------------- #
# 手算期望值（SPEC-WEEKLY.md §3）
# --------------------------------------------------------------------------- #

#: 逐日情绪指标（core/sentiment.py 同一口径）：温度 / 晋级率 / 封板率 / 涨停 / 炸板
DAY_METRICS: dict[str, dict[str, Any]] = {
    MON: {"temperature": 52.5, "promotion_rate": 50.0, "seal_rate": 66.7, "limit_up_count": 2,
          "broken_count": 1, "max_board": 2},
    TUE: {"temperature": 90.0, "promotion_rate": 100.0, "seal_rate": 100.0, "limit_up_count": 4,
          "broken_count": 0, "max_board": 3},
    WED: {"temperature": 45.8, "promotion_rate": 33.3, "seal_rate": 50.0, "limit_up_count": 2,
          "broken_count": 2, "max_board": 4},
}

#: 涨跌分布九箱（合计 11 个样本：涨 6 / 跌 4 / 平 1）
BREADTH_COUNTS: dict[str, int] = {
    "跌≥9%": 1,
    "跌5~9%": 1,
    "跌2~5%": 0,
    "跌0~2%": 2,
    "平盘": 1,
    "涨0~2%": 1,
    "涨2~5%": 2,
    "涨5~9%": 1,
    "涨≥9%": 2,
}

#: 主线题材（入窗天数 / 本周最长连续 / 均涨幅），按排序键应当是这个顺序
THEME_ORDER: tuple[tuple[str, int, int, float], ...] = (
    ("AI算力", 3, 3, 6.0),
    ("券商", 2, 2, 6.5),
    ("机器人", 2, 1, 8.0),
    ("白酒", 1, 1, -2.0),
)

#: 待验证项结算（含上周尾带进来的那 1 条）
VALIDATION_TOTALS: dict[str, int] = {
    "conditions": 5,
    "settled": 3,
    "passed": 1,
    "failed": 1,
    "unknown": 1,
    "pending": 2,
}
#: 逐条顺序：(生成日, 条件 id)
VALIDATION_ITEMS: tuple[tuple[str, str], ...] = (
    (CARRY, "w43-settled"),
    (MON, "x-pending"),
    (MON, "x-settled-fail"),
    (MON, "x-unknown"),
    (TUE, "y-pending"),
)

#: 本周 AI 完成度（两天有 checkpoint：周一 3 段完成、周二 5 段完成）
AI_COVERAGE_TOTALS: dict[str, int] = {
    "days_with_checkpoint": 2,
    "sections_expected": 10,
    "sections_done": 8,
    "sections_degraded": 1,
    "sections_failed": 1,
    "sections_pending": 1,
}


__all__ = [
    "AI_COVERAGE_TOTALS",
    "BREADTH_COUNTS",
    "CARRY",
    "CHECKPOINT_NARRATIVE",
    "DAY_METRICS",
    "EXPECTED",
    "MON",
    "THEME_ORDER",
    "TUE",
    "VALIDATION_ITEMS",
    "VALIDATION_TOTALS",
    "WEEK",
    "WED",
    "seed_week",
]

#: 事实包顶层期望（缺 `EXPECTED` 就等于没有断言）
EXPECTED: dict[str, Any] = {
    "week": WEEK,
    "range": {"start": "2026-10-26", "end": "2026-11-01"},
    "session_days": [MON, TUE, WED],
    "missing_day_dates": ["2026-10-29", "2026-10-30"],
    "available": True,
    "sentiment": {
        "temperature_first": 52.5,
        "temperature_last": 45.8,
        "temperature_change": -6.7,
        "temperature_avg": 62.8,
        "temperature_samples": 3,
        "promotion_avg": 61.1,
        "promotion_change": -16.7,
        "seal_avg": 72.2,
        "limit_up_total": 8,
        "broken_total": 3,
        "gap_days": 1,
    },
    "ladder": {
        "height_first": 2,
        "height_last": 4,
        "height_change": 2,
        "height_avg": 3,
        "peak_height": 4,
        "count_total": 8,
        "gap_days": 1,
        "unknown_board_total": 0,
    },
    "breadth": {
        "samples": 11,
        "up_count": 6,
        "down_count": 4,
        "flat_count": 1,
        "up_share_pct": 54.5,
    },
    "themes": {"window": 10, "observed": 4, "top_theme": "AI算力", "top_streak": 3},
    "capital": {"days_available": 2, "records_total": 3},
    "dragon_tiger": {"days_available": 2, "board_rows_total": 3, "seat_rows_total": 4},
    "theme_rows": {"days_available": 3},
    "validation": {**VALIDATION_TOTALS, "pass_rate": 33.3},
    "leaders": (
        ("600001", 4, WED),
        ("600010", 3, TUE),
    ),
}
