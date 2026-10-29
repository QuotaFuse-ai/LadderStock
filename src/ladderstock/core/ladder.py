"""连板天梯与梯队断层（程序直出，DEC-006 闸门 5）。

本模块只做两件事：**从本地缓存读涨停池**、**算天梯结构**。数值不得来自 AI，
也不得为了"更好看"而补字段——peer 返回里没有连板数就如实标记缺失。

数据来源固定走 `data/archive.py`（Parquet 日分区 → DuckDB 兜底），
不新增任何采集入口（DEC-007 §2.6）。

字段容忍：TickHub 不同工具对连板数的命名不一致（`consecutive_boards` /
`limit_up_days` / `连板数` …），这里按优先级取第一个可解析的值；都没有就
把该股记入 `unknown_board`，不猜。

口径（METRICS.md §3.4，D-01/D-04）：

- 涨停池先按 `code` 去重（保留首条），与情绪侧 `_codes` 的集合口径对齐；
- 每只股票（层级组与 `leader_candidates`）带 `first_seal_time` / `seal_time`，
  字段缺失如实给 `null`，不猜。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from ladderstock.data import archive

BOARD_KEYS: tuple[str, ...] = (
    "consecutive_boards",
    "consecutive_limit_up",
    "limit_up_days",
    "board_count",
    "boards",
    "streak",
    "连板数",
)
NAME_KEYS: tuple[str, ...] = ("name", "stock_name", "sec_name", "名称")
CODE_KEYS: tuple[str, ...] = ("code", "symbol", "stock_code", "sec_code", "ts_code")
#: 首次封板时间（TickHub 涨停池为 `first_seal_time`，HHMMSS 字符串）
FIRST_SEAL_KEYS: tuple[str, ...] = (
    "first_seal_time",
    "首次封板时间",
    "first_limit_time",
    "first_limit_up_time",
)
#: 末次封板时间（TickHub 涨停池为 `last_seal_time`）；对外统一叫 `seal_time`
SEAL_KEYS: tuple[str, ...] = (
    "seal_time",
    "last_seal_time",
    "封板时间",
    "last_limit_time",
)


def pick(record: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return value
    return None


def as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value == int(value) else None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text.rstrip("板"))
    except ValueError:
        return None
    return int(number) if number == int(number) else None


def as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().rstrip("%")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def dedupe_by_code(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按 `code` 去重（保留首条）；无 `code` 的记录原样保留，交给 `unknown_board` 计数。

    与情绪侧 `_codes` 的集合口径对齐（D-04）：同一只股票的重复行不得放大天梯的
    `count` / 层级 / `leader_candidates`。
    上游若合法重复（如同一代码多市场前缀）需在此调整去重键。
    """
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for record in records:
        code = str(pick(record, CODE_KEYS) or "").strip()
        if code:
            if code in seen:
                continue
            seen.add(code)
        unique.append(record)
    return unique


def cache_meta(datasets: list[str], day: date | str, *, root: Any = None) -> dict[str, Any]:
    """取若干数据集缓存快照的时点信息（D-02 溯源）。

    `data_as_of` / `backend` 只看**首个数据集**（主指标的依据）：它没有记录时
    `data_as_of` 一律 `None`——`archive.snapshot()` 会退化成「请求日期」，
    那是猜不是读，溯源不能拿它冒充数据时点。`cached_at` 取有记录者里最新的一条。
    读不到就如实给 `None`，不补造。仅供 CLI 装信封使用，不参与指标计算。
    """
    meta: dict[str, Any] = {"data_as_of": None, "cached_at": None, "backend": None}
    for index, dataset in enumerate(datasets):
        try:
            snapshot = archive.snapshot(dataset, day, root=root)
        except Exception:  # noqa: BLE001 - 溯源信息缺失不应让命令失败
            continue
        if index == 0:
            meta["backend"] = snapshot.backend
            if snapshot.records:
                meta["data_as_of"] = snapshot.as_of
        if snapshot.records and snapshot.cached_at is not None and (
            meta["cached_at"] is None or snapshot.cached_at > meta["cached_at"]
        ):
            meta["cached_at"] = snapshot.cached_at
    return meta


def dataset_snapshot(
    dataset: str, day: date | str | None, root: Any = None
) -> tuple[list[dict[str, Any]], str]:
    """读数据集并返回 `(records, backend)`；数据集不可读时返回空列表。"""
    try:
        snapshot = archive.snapshot(dataset, day, root=root)
    except Exception:  # noqa: BLE001 - 缓存损坏不应让整条复盘崩掉
        return [], "error"
    return snapshot.records, snapshot.backend


def compute_ladder(
    day: date | str,
    *,
    root: Any = None,
    records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """按涨停池计算连板天梯、梯队高度与断层。"""
    backend = "given"
    if records is None:
        records, backend = dataset_snapshot("limit_pool", day, root)

    raw_total = len(records)
    # D-04：同一 `code` 只保留首条，去重后 count / 层级 / 龙头同源。
    # 上游若合法重复（如同一代码多市场前缀）需在此调整去重键。
    unique_records = dedupe_by_code(records)
    dropped = raw_total - len(unique_records)
    detail = f"limit_pool: {backend}（{raw_total} 条）"
    if dropped:
        detail = f"limit_pool: {backend}（{raw_total} 条，重复 code 去重 {dropped} 条）"

    rows: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    for record in unique_records:
        code = str(pick(record, CODE_KEYS) or "").strip()
        name = str(pick(record, NAME_KEYS) or "").strip()
        boards = as_int(pick(record, BOARD_KEYS))
        if boards is None or boards < 1:
            unknown.append({"code": code, "name": name})
            continue
        rows.append(
            {
                "code": code,
                "name": name,
                "boards": boards,
                # D-01：封板时间原样透传（TickHub 为 HHMMSS 字符串），取不到即 null
                "first_seal_time": pick(record, FIRST_SEAL_KEYS),
                "seal_time": pick(record, SEAL_KEYS),
            }
        )

    if not rows:
        return {
            "date": str(day),
            "available": False,
            "reason": "涨停池没有可解析的连板数据（可能尚未同步）" if not records else
            "涨停池记录缺少连板数字段",
            "source_detail": detail,
            "height": None,
            "count": 0,
            "ladder": [],
            "gap": False,
            "gap_levels": [],
            "leader_candidates": [],
            "unknown_board": len(unknown),
        }

    rows.sort(key=lambda row: (-row["boards"], row["code"]))
    height = max(row["boards"] for row in rows)
    present = {row["boards"] for row in rows}
    gap_levels = [level for level in range(1, height) if level not in present]

    ladder: list[dict[str, Any]] = []
    for level in sorted(present, reverse=True):
        stocks = [dict(row) for row in rows if row["boards"] == level]
        ladder.append({"boards": level, "count": len(stocks), "stocks": stocks})

    leaders = [dict(row) for row in rows if row["boards"] == height][:3]

    return {
        "date": str(day),
        "available": True,
        "reason": None,
        "source_detail": detail,
        "height": height,
        "count": len(rows),
        "ladder": ladder,
        "gap": bool(gap_levels),
        "gap_levels": gap_levels,
        "leader_candidates": leaders,
        "unknown_board": len(unknown),
    }


def summary(ladder: dict[str, Any]) -> str:
    """给人类看的一行结论（程序生成，不是 AI 叙述）。"""
    if not ladder.get("available"):
        return "连板天梯：数据缺失"
    gap = ""
    if ladder["gap"]:
        gap = "，断层 " + "/".join(f"{level}板" for level in ladder["gap_levels"])
    return f"连板天梯：最高 {ladder['height']} 板，{ladder['count']} 只涨停{gap}"
