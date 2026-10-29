"""明日验证条件与收盘对账（程序直出，DEC-006 闸门 5）。

两条命令共用本模块：

- `ldst validate --date`：读当日复盘里生成的**明日验证条件**（人读文本 + 机器可判定阈值）；
- `ldst validate reconcile --date`：拿下一交易日实际指标逐条判定，写出对账结果。

条件的阈值全部由当日程序指标推导（例如"情绪温度不低于今日-15"），
AI 只负责叙述，不参与阈值生成，也不会改写判定结果。

落盘位置：`<data_dir>/state/validation/<date>.json`（原子写）。
"""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from ladderstock.cli.paths import data_dir
from ladderstock.core import ladder as ladder_module
from ladderstock.core import sentiment as sentiment_module

OPERATORS: tuple[str, ...] = (">=", "<=", ">", "<", "==")


# --------------------------------------------------------------------------- #
# 路径与读写
# --------------------------------------------------------------------------- #

def validation_path(day: date | str, root: Any = None) -> Path:
    base = Path(root) if root is not None else data_dir()
    return base / "state" / "validation" / f"{day}.json"


def write_document(path: Path, document: dict[str, Any]) -> None:
    """原子写入：临时文件 + `os.replace`，避免前端/CLI 读到半截 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    os.replace(temp, path)


def load_validation(day: date | str, root: Any = None) -> dict[str, Any] | None:
    path = validation_path(day, root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# 条件生成
# --------------------------------------------------------------------------- #

def _threshold(value: float, floor: float, step: float = 15.0) -> float:
    return round(max(floor, value - step), 1)


def build_conditions(
    day: date | str,
    sentiment: dict[str, Any],
    ladder: dict[str, Any],
) -> list[dict[str, Any]]:
    """由当日指标推导明日可判定的条件；缺指标的条件直接不生成。"""
    conditions: list[dict[str, Any]] = []

    temperature = sentiment.get("temperature")
    if isinstance(temperature, (int, float)):
        floor = _threshold(float(temperature), floor=30.0)
        conditions.append(
            {
                "id": "temperature_floor",
                "text": f"明日情绪温度不低于 {floor}（今日 {temperature}）",
                "metric": "temperature",
                "operator": ">=",
                "value": floor,
            }
        )

    promotion = sentiment.get("promotion_rate")
    if isinstance(promotion, (int, float)):
        floor = _threshold(float(promotion), floor=10.0)
        conditions.append(
            {
                "id": "promotion_floor",
                "text": f"明日晋级率不低于 {floor}%（今日 {promotion}%）",
                "metric": "promotion_rate",
                "operator": ">=",
                "value": floor,
            }
        )

    seal = sentiment.get("seal_rate")
    if isinstance(seal, (int, float)):
        floor = _threshold(float(seal), floor=40.0)
        conditions.append(
            {
                "id": "seal_floor",
                "text": f"明日封板率不低于 {floor}%（今日 {seal}%）",
                "metric": "seal_rate",
                "operator": ">=",
                "value": floor,
            }
        )

    height = ladder.get("height")
    if isinstance(height, int) and height >= 1:
        floor = max(2, height - 1)
        conditions.append(
            {
                "id": "height_floor",
                "text": f"明日最高板不低于 {floor} 板（今日 {height} 板）",
                "metric": "max_board",
                "operator": ">=",
                "value": floor,
            }
        )

    gap_levels = ladder.get("gap_levels") or []
    if gap_levels:
        levels = "、".join(f"{level} 板" for level in gap_levels)
        conditions.append(
            {
                "id": "gap_fill",
                "text": f"明日梯队回补断层：{levels}",
                "metric": "gap_levels",
                "operator": "not_contains",
                "value": gap_levels,
            }
        )

    return conditions


def evaluate(condition: dict[str, Any], metrics: dict[str, Any]) -> bool | None:
    """按条件里的 metric/operator/value 判定；指标缺失返回 None（不计入通过）。"""
    metric = condition.get("metric")
    operator = condition.get("operator")
    target = condition.get("value")

    if not isinstance(metric, str):
        return None

    if metric == "gap_levels":
        actual = metrics.get("gap_levels")
        if not isinstance(actual, list):
            return None
        if operator == "not_contains":
            return not (set(target or []) & set(actual))
        return None

    actual = metrics.get(metric)
    if not isinstance(actual, (int, float)) or isinstance(actual, bool):
        return None
    if not isinstance(target, (int, float)) or isinstance(target, bool):
        return None

    if operator == ">=":
        return float(actual) >= float(target)
    if operator == "<=":
        return float(actual) <= float(target)
    if operator == ">":
        return float(actual) > float(target)
    if operator == "<":
        return float(actual) < float(target)
    if operator == "==":
        return float(actual) == float(target)
    return None


# --------------------------------------------------------------------------- #
# 对账
# --------------------------------------------------------------------------- #

def session_days(*, root: Any = None) -> list[date]:
    """本地已有的交易日（以涨停池为准，升序）。"""
    records, _backend = ladder_module.dataset_snapshot("limit_pool", None, root)
    days: set[date] = set()
    for record in records:
        day = _record_day(record)
        if day is not None:
            days.add(day)
    return sorted(days)


def _record_day(record: dict[str, Any]) -> date | None:
    from ladderstock.data.duckdb_store import record_day

    return record_day(record)


def next_session(day: date, *, root: Any = None) -> date | None:
    for candidate in session_days(root=root):
        if candidate > day:
            return candidate
    return None


def reconcile(day: date | str, *, root: Any = None) -> dict[str, Any]:
    """拿下一交易日实际指标判定当日生成的条件。

    返回文档写入 `<data_dir>/state/validation/<date>.json` 的 `reconcile` 段。
    下一交易日尚无数据时 `status=partial`（CLI 退出码 8），不伪造判定结果。
    """
    target_day = day if isinstance(day, date) else date.fromisoformat(str(day))
    document = load_validation(target_day, root)
    if document is None:
        raise FileNotFoundError(
            f"{target_day} 还没有验证条件（先运行 ldst review --date {target_day}）"
        )

    conditions = [item for item in document.get("conditions") or [] if isinstance(item, dict)]
    followed = next_session(target_day, root=root)
    if followed is None:
        result: dict[str, Any] = {
            "status": "partial",
            "reason": "本地还没有下一交易日的涨停池数据，无法对账",
            "next_session": None,
            "results": [],
            "generated_at": _now_iso(),
        }
        document["reconcile"] = result
        write_document(validation_path(target_day, root), document)
        return result

    sentiment = sentiment_module.compute_sentiment(followed, root=root)
    if not sentiment.get("available"):
        result = {
            "status": "partial",
            "reason": f"下一交易日 {followed} 指标不可用：{sentiment.get('reason')}",
            "next_session": followed.isoformat(),
            "results": [],
            "generated_at": _now_iso(),
        }
        document["reconcile"] = result
        write_document(validation_path(target_day, root), document)
        return result

    rows: list[dict[str, Any]] = []
    unknown = 0
    for condition in conditions:
        passed = evaluate(condition, sentiment)
        if passed is None:
            unknown += 1
        metric = condition.get("metric")
        rows.append(
            {
                "id": condition.get("id"),
                "text": condition.get("text"),
                "metric": metric,
                "operator": condition.get("operator"),
                "target": condition.get("value"),
                "actual": sentiment.get(metric) if isinstance(metric, str) else None,
                "passed": passed,
            }
        )

    status = "partial" if unknown else "ok"
    result = {
        "status": status,
        "reason": None if not unknown else f"{unknown} 条条件缺指标，未能判定",
        "next_session": followed.isoformat(),
        "checked": len(rows),
        "passed": sum(1 for row in rows if row["passed"] is True),
        "failed": sum(1 for row in rows if row["passed"] is False),
        "unknown": unknown,
        "results": rows,
        "generated_at": _now_iso(),
    }
    document["reconcile"] = result
    write_document(validation_path(target_day, root), document)
    return result
