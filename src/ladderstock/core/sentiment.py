r"""情绪温度、晋级率与封板率（程序直出，DEC-006 闸门 5 / DEC-007 §2.7）。

口径固定、可复现，AI 不参与任何数字：

| 指标 | 定义 |
|---|---|
| 晋级率 | 昨日涨停股中今日继续涨停的比例 = \|今日涨停 ∩ 昨日涨停\| / \|昨日涨停\| |
| 封板率 | 今日涨停 / (今日涨停 + 今日炸板) |
| 连板高度 | 涨停池里最大的连板数（来自 `core/ladder.py`） |
| 情绪温度 | `0.45×晋级率 + 0.35×封板率 + 0.20×min(1, 最高板/6)`，归一到 0–100 |

任一分项缺数据时按**可用分项重新归一权重**，并在 `missing` 里说明缺了什么，
绝不拿默认值冒充真实读数。

计数兜底（D-06）：某个池里一条 `code` 都解析不出时按**行数**计，同时把无 `code`
的行数写进 `unknown`，让读者知道这个数是行数代理而不是去重股票数。

输出里的比率与温度**统一为 0–100 的百分数**（保留 1 位小数），便于直接展示。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from ladderstock.core import ladder as ladder_module
from ladderstock.core.ladder import dataset_snapshot, pick

TOP_BOARD_NORM = 6  # 6 板视为满分高度
CODE_KEYS = ladder_module.CODE_KEYS

TEMPERATURE_FORMULA = (
    "温度 = 0.45×晋级率 + 0.35×封板率 + 0.20×min(1, 最高板/6)，缺失分项按可用权重重新归一"
)


def _codes(records: list[dict[str, Any]]) -> set[str]:
    codes: set[str] = set()
    for record in records:
        code = str(pick(record, CODE_KEYS) or "").strip()
        if code:
            codes.add(code)
    return codes


def _codeless(records: list[dict[str, Any]]) -> int:
    """取不到 `code` 的记录条数（D-06：这些行会参与计数兜底，必须显式可见）。"""
    return sum(1 for record in records if not str(pick(record, CODE_KEYS) or "").strip())


def _percent(value: float) -> float:
    return round(value * 100.0, 1)


def compute_sentiment(day: date | str, *, root: Any = None) -> dict[str, Any]:
    """计算某交易日的情绪指标；数据不足时 `available=False` 并说明原因。"""
    limit_rows, limit_backend = dataset_snapshot("limit_pool", day, root)
    broken_rows, broken_backend = dataset_snapshot("broken_board", day, root)
    yesterday_rows, yesterday_backend = dataset_snapshot("yesterday_limit_up", day, root)
    ladder = ladder_module.compute_ladder(day, root=root, records=limit_rows)

    missing: list[str] = []
    if not limit_rows:
        missing.append("limit_pool")
    if not yesterday_rows:
        missing.append("yesterday_limit_up")
    if not broken_rows:
        missing.append("broken_board")

    if not limit_rows:
        # D-05：早退也要给出完整的 `missing`（三池按固定顺序），否则「三池全缺」时
        # 只看到 limit_pool，排障会被误导。
        return {
            "date": str(day),
            "available": False,
            "reason": "涨停池为空，无法计算情绪指标（先运行 ldst sync）",
            "temperature": None,
            "promotion_rate": None,
            "seal_rate": None,
            "limit_up_count": 0,
            "broken_count": len(_codes(broken_rows)) or len(broken_rows),
            "yesterday_limit_up_count": len(_codes(yesterday_rows)),
            "promoted_count": 0,
            "max_board": ladder.get("height"),
            "gap": ladder.get("gap", False),
            "gap_levels": ladder.get("gap_levels", []),
            "unknown": {
                "limit_pool": 0,
                "broken_board": _codeless(broken_rows),
            },
            "missing": missing,
            "formula": TEMPERATURE_FORMULA,
            "source_detail": _source_detail(limit_backend, broken_backend, yesterday_backend),
        }

    today_codes = _codes(limit_rows)
    yesterday_codes = _codes(yesterday_rows)
    broken_codes = _codes(broken_rows)
    promoted = today_codes & yesterday_codes
    promotion = (
        _percent(len(promoted) / len(yesterday_codes)) if yesterday_codes else None
    )

    # D-06：保留「一条 code 都解析不出时按行数兜底」——此时计数是行数代理而非
    # 去重股票数，`unknown` 把这些无 code 行显式标出来，读者不会被兜底数字误导。
    limit_count = len(today_codes) or len(limit_rows)
    broken_count = len(broken_codes) or len(broken_rows)
    total = limit_count + broken_count
    seal = _percent(limit_count / total) if total else None

    height = ladder.get("height")
    height_component = (
        min(1.0, float(height) / TOP_BOARD_NORM) if isinstance(height, int) and height > 0 else None
    )

    parts: list[tuple[float, float]] = []
    if promotion is not None:
        parts.append((0.45, promotion))
    if seal is not None:
        parts.append((0.35, seal))
    if height_component is not None:
        parts.append((0.20, height_component * 100.0))

    temperature: float | None = None
    if parts:
        weight = sum(weight for weight, _ in parts)
        temperature = round(sum(weight * value for weight, value in parts) / weight, 1)

    return {
        "date": str(day),
        "available": True,
        "reason": None,
        "temperature": temperature,
        "promotion_rate": promotion,
        "seal_rate": seal,
        "limit_up_count": limit_count,
        "broken_count": broken_count,
        "yesterday_limit_up_count": len(yesterday_codes),
        "promoted_count": len(promoted),
        "max_board": height,
        "gap": ladder.get("gap", False),
        "gap_levels": ladder.get("gap_levels", []),
        "unknown": {
            "limit_pool": _codeless(limit_rows),
            "broken_board": _codeless(broken_rows),
        },
        "missing": missing,
        "formula": TEMPERATURE_FORMULA,
        "source_detail": _source_detail(limit_backend, broken_backend, yesterday_backend),
    }


def _source_detail(limit_backend: str, broken_backend: str, yesterday_backend: str) -> str:
    return (
        f"limit_pool: {limit_backend}; broken_board: {broken_backend}; "
        f"yesterday_limit_up: {yesterday_backend}"
    )


def narrative_metrics(sentiment: dict[str, Any]) -> dict[str, Any]:
    """给 AI 的指标快照（只读、扁平，避免把整张天梯塞进 prompt）。"""
    return {
        "temperature": sentiment.get("temperature"),
        "promotion_rate": sentiment.get("promotion_rate"),
        "seal_rate": sentiment.get("seal_rate"),
        "limit_up_count": sentiment.get("limit_up_count"),
        "broken_count": sentiment.get("broken_count"),
        "yesterday_limit_up_count": sentiment.get("yesterday_limit_up_count"),
        "max_board": sentiment.get("max_board"),
        "gap": sentiment.get("gap"),
        "gap_levels": sentiment.get("gap_levels"),
        "missing": sentiment.get("missing"),
    }


__all__ = [
    "TEMPERATURE_FORMULA",
    "compute_sentiment",
    "narrative_metrics",
]
