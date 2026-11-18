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

from datetime import date, timedelta
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


def _previous_pool_day(day: date | str, root: Any, *, span: int = 12) -> date | None:
    """`day` 之前最近一个有 `limit_pool` 分区的交易日（跳过周末与缺数据的日期）。"""
    from ladderstock.data import archive

    try:
        target = date.fromisoformat(str(day))
    except ValueError:
        return None
    cursor = target - timedelta(days=1)
    for _ in range(span):
        try:
            if archive.has_partition("limit_pool", cursor, root=root):
                return cursor
        except Exception:  # noqa: BLE001 - 分区探测失败按「没有」处理
            return None
        cursor -= timedelta(days=1)
    return None


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
        # 盘中（15:35 收盘同步之前）当天没有涨停池归档：先看本机有没有当日实时快照。
        # `market_sentiment` 在盘中有当日读数，页面据此显示「今天此刻」而不是一片空白。
        live = _live_metrics(day, root)
        if live is not None:
            return live
        # D-05：早退也要给出完整的 `missing`（三池按固定顺序），否则「三池全缺」时
        # 只看到 limit_pool，排障会被误导。
        return {
            "date": str(day),
            "available": False,
            "reason": "这一天的涨停池还没有归档：交易日 15:35 收盘后自动同步，历史日期打开页面会自动补取",
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
    promotion = _percent(len(promoted) / len(yesterday_codes)) if yesterday_codes else None

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


def _live_metrics(day: date | str, root: Any = None) -> dict[str, Any] | None:
    """盘中读数。两级口径，谁有真数据就用谁：

    1. **当日实时涨停池全集**（`data/live/limit_pool/<date>.json`）：一旦盘中的
       `limit_up_pool` 给到当日全量（含 `streak`），晋级率、每一档家数、梯队断层
       **全部能真算**——这时页面与收盘归档口径一致，只是数据来自盘中实时；
    2. 只有 `market_sentiment` 的家数与龙头名单：封板率、最高板能算，晋级率与断层
       算不出来（要当日涨停全集），缺的那几项写进 `missing`，绝不拿 0 或上一交易日
       的值顶替。
    """
    from ladderstock.core import live_limit_pool, live_sentiment

    body = live_sentiment.read_snapshot(day, root)
    if body is None:
        return None
    records = body.get("records") or {}
    limit_up = records.get("limit_up")
    broken = records.get("broken")
    limit_down = records.get("limit_down")
    max_streak = records.get("max_streak")
    leaders = records.get("leaders") if isinstance(records.get("leaders"), list) else []
    if not isinstance(limit_up, int) or not isinstance(broken, int):
        return None

    fetched_at = str(body.get("fetched_at") or "")
    pool = live_limit_pool.read_snapshot(day, root)
    pool_rows = live_limit_pool.rows_of(pool)
    if pool_rows:
        return _live_full_metrics(
            day,
            root,
            pool_rows=pool_rows,
            pool_meta=pool or {},
            sentiment=records,
            broken=broken,
            limit_down=limit_down,
            fetched_at=fetched_at,
            sentiment_source=str(body.get("source") or "market_sentiment"),
        )

    total = limit_up + broken
    seal = _percent(limit_up / total) if total else None
    height = int(max_streak) if isinstance(max_streak, int) else None
    height_component = (
        min(1.0, float(height) / TOP_BOARD_NORM) if isinstance(height, int) and height > 0 else None
    )

    parts: list[tuple[float, float]] = []
    if seal is not None:
        parts.append((0.35, seal))
    if height_component is not None:
        parts.append((0.20, height_component * 100.0))
    temperature: float | None = None
    if parts:
        weight = sum(weight for weight, _ in parts)
        temperature = round(sum(weight * value for weight, value in parts) / weight, 1)

    cause = (
        f"盘中实时（{fetched_at}）：涨停 {limit_up} 家 / 炸板 {broken} 家 / 跌停 "
        f"{limit_down if isinstance(limit_down, int) else '—'} 家 / 封板率 "
        f"{seal if seal is not None else '—'}% / 最高 {height if height else '—'} 板。"
        "晋级率与梯队断层要当日涨停全集，15:35 收盘同步后才出。"
    )

    return {
        "date": str(day),
        "available": True,
        "mode": "live",
        "reason": cause,
        "temperature": temperature,
        "promotion_rate": None,
        "seal_rate": seal,
        "limit_up_count": limit_up,
        "broken_count": broken,
        "limit_down_count": limit_down if isinstance(limit_down, int) else None,
        "yesterday_limit_up_count": None,
        "promoted_count": None,
        "max_board": height,
        "gap": False,
        "gap_levels": [],
        "leaders": [
            {
                "code": str(item.get("code") or ""),
                "name": str(item.get("name") or ""),
                "streak": item.get("streak"),
            }
            for item in leaders
            if isinstance(item, dict) and str(item.get("code") or "").strip()
        ],
        "unknown": {"limit_pool": 0, "broken_board": 0},
        "missing": ["promotion_rate", "ladder_gap"],
        "formula": TEMPERATURE_FORMULA,
        "source_detail": f"实时快照 {body.get('source') or 'market_sentiment'} @ {fetched_at}"
        "（当日 archive 归档未生成，收盘同步后自动切回归档口径）",
    }


def _live_full_metrics(
    day: date | str,
    root: Any,
    *,
    pool_rows: list[dict[str, Any]],
    pool_meta: dict[str, Any],
    sentiment: dict[str, Any],
    broken: int,
    limit_down: Any,
    fetched_at: str,
    sentiment_source: str,
) -> dict[str, Any]:
    """盘中拿到当日涨停全集时的完整口径（与收盘归档同一套算法）。"""
    today_codes = _codes(pool_rows)
    limit_count = len(today_codes) or len(pool_rows)
    total = limit_count + broken
    seal = _percent(limit_count / total) if total else None

    ladder = ladder_module.compute_ladder(day, root=None, records=pool_rows)
    height = ladder.get("height")
    gap = bool(ladder.get("gap"))
    gap_levels = ladder.get("gap_levels", [])

    yesterday_rows, yesterday_backend = dataset_snapshot("yesterday_limit_up", day, root)
    yesterday_codes = _codes(yesterday_rows)
    yesterday_from = f"yesterday_limit_up 分区（{yesterday_backend}）"
    if not yesterday_codes:
        # 当日 `yesterday_limit_up` 分区要收盘同步才有；盘中改用**前一交易日的
        # limit_pool 全集**——「昨日涨停股」本来就是那一池，口径相同、出处不同，写进说明。
        prev_day = _previous_pool_day(day, root)
        if prev_day is not None:
            yesterday_rows, yesterday_backend = dataset_snapshot("limit_pool", prev_day, root)
            yesterday_codes = _codes(yesterday_rows)
            yesterday_from = f"前一交易日 {prev_day} 的 limit_pool（{yesterday_backend}）"
    promoted = today_codes & yesterday_codes
    promotion = _percent(len(promoted) / len(yesterday_codes)) if yesterday_codes else None

    missing: list[str] = []
    if promotion is None:
        missing.append("yesterday_limit_up")

    parts: list[tuple[float, float]] = []
    if promotion is not None:
        parts.append((0.45, promotion))
    if seal is not None:
        parts.append((0.35, seal))
    if isinstance(height, int) and height > 0:
        parts.append((0.20, min(1.0, float(height) / TOP_BOARD_NORM) * 100.0))
    temperature: float | None = None
    if parts:
        weight = sum(weight for weight, _ in parts)
        temperature = round(sum(weight * value for weight, value in parts) / weight, 1)

    pool_at = str(pool_meta.get("fetched_at") or fetched_at)
    cause = (
        f"盘中实时（涨停池 {pool_at}）：涨停 {limit_count} 家 / 炸板 {broken} 家 / 跌停 "
        f"{limit_down if isinstance(limit_down, int) else '—'} 家 / 封板率 "
        f"{seal if seal is not None else '—'}% / 最高 {height if height else '—'} 板 / "
        f"晋级率 {promotion if promotion is not None else '—'}%。"
        "盘中按当日实时涨停全集算，收盘同步后按归档重算。"
    )
    return {
        "date": str(day),
        "available": True,
        "mode": "live",
        "reason": cause,
        "temperature": temperature,
        "promotion_rate": promotion,
        "seal_rate": seal,
        "limit_up_count": limit_count,
        "broken_count": broken,
        "limit_down_count": limit_down if isinstance(limit_down, int) else None,
        "yesterday_limit_up_count": len(yesterday_codes),
        "promoted_count": len(promoted),
        "max_board": height,
        "gap": gap,
        "gap_levels": gap_levels,
        "leaders": [
            {
                "code": str(item.get("code") or ""),
                "name": str(item.get("name") or ""),
                "streak": item.get("streak"),
            }
            for item in sorted(
                pool_rows,
                key=lambda row: (
                    -(row.get("streak") if isinstance(row.get("streak"), int) else 0),
                    str(row.get("code") or ""),
                ),
            )[:10]
        ],
        "levels": ladder.get("levels") or [],
        "unknown": {"limit_pool": _codeless(pool_rows), "broken_board": 0},
        "missing": missing,
        "formula": TEMPERATURE_FORMULA,
        "source_detail": (
            f"盘中实时涨停池 {pool_meta.get('source') or 'limit_up_pool'} @ {pool_at}"
            f"（{len(pool_rows)} 只，含连板数）；封板率分母用实时情绪 {sentiment_source}"
            f" @ {fetched_at} 的炸板 {broken} 家；昨日涨停来自 {yesterday_from}"
            "（收盘同步后按归档重算）"
        ),
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
