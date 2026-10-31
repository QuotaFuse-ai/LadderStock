r"""涨停样本（limit_pool）只读投影（程序直出，AI 不参与）。

只做一件事：**把本地涨停池缓存原样投影成「涨停样本」页要的行**——不采集、
不估算、不补空值、不重算指标。数据固定走 `data/archive.py`（Parquet 日分区
→ DuckDB 兜底），读取一律经 `core/ladder.py` 的 `dataset_snapshot()`，不自拼 SQL。

与天梯（`core/ladder.py`）共享同一份口径，页面两边看到的行必须同源：

- 先按 `code` 去重（`dedupe_by_code`，保留首条）；
- 连板数按 `BOARD_KEYS` 优先级取第一个可解析的值，字段容忍度完全一致；
- 封板时间（`FIRST_SEAL_KEYS` / `SEAL_KEYS`）**原样透传** TickHub 的 `HHMMSS`
  字符串，不做时区换算，取不到就是 `null`。

三条「不猜」的红线：

1. `streak` 取不到就是 `null`，**不补 1**；无 `code` 或无连板数的行不进 `rows`，
   只计入 `source_detail` 里的 unknown 计数（与天梯的 `unknown_board` 同源）；
2. `trade_date` 只取行内值，**不用请求日期顶替**——缓存里那一行属于哪个交易日，
   就是哪个交易日；
3. `reason` 只在缓存行里有时才给。`ths_limit_up_pool` 是 `limit_pool` 数据集的
   **备用工具**（`core/boundary.py`），落库仍是 `limit_pool` 表，不是独立数据集，
   所以这里**不为了填原因去调上游**，行里没有就是 `null`。

排序与天梯一致：`streak` 降序、`code` 升序。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from ladderstock.core.ladder import (
    BOARD_KEYS,
    CODE_KEYS,
    FIRST_SEAL_KEYS,
    NAME_KEYS,
    SEAL_KEYS,
    as_int,
    dataset_snapshot,
    dedupe_by_code,
    pick,
)

#: 涨停原因 / 题材。主工具 `limit_up_pool` 不带这一列，备工具 `ths_limit_up_pool`
#: 带 `reason`；两者落的是同一张 `limit_pool` 表，所以按别名容忍，取不到就是 null。
REASON_KEYS: tuple[str, ...] = ("reason", "limit_up_reason", "涨停原因", "解释")

#: 行内交易日：缓存写入时统一补过 `trade_date`（`archive._decode` /
#: `duckdb_store._decode`），历史行也可能用中文键，按别名容忍。
TRADE_DATE_KEYS: tuple[str, ...] = ("trade_date", "交易日期", "date")

NO_ROWS_EMPTY = "涨停池为空，当日尚未同步（先运行 ldst sync）"
NO_ROWS_NO_BOARD = "涨停池记录缺少可解析的连板数，无法给出涨停样本"


def as_day_text(value: Any) -> str | None:
    """行内交易日的文本形态（`YYYY-MM-DD`）；取不到或不可解析一律 `null`。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    candidate = text[:10]
    try:
        return date.fromisoformat(candidate).isoformat()
    except ValueError:
        return None


def project_limit_pool(
    records: list[dict[str, Any]], *, day: date | str, backend: str
) -> dict[str, Any]:
    """把涨停池记录投影成「涨停样本」载荷（纯函数，便于注入 records 做单测）。"""
    raw_total = len(records)
    unique_records = dedupe_by_code(records)
    dropped = raw_total - len(unique_records)

    parts = [f"{raw_total} 条"]
    if dropped:
        parts.append(f"重复 code 去重 {dropped} 条")

    rows: list[dict[str, Any]] = []
    unknown = 0
    for record in unique_records:
        code = str(pick(record, CODE_KEYS) or "").strip()
        streak = as_int(pick(record, BOARD_KEYS))
        # 与天梯同一口径：无 code 或无连板数的行不进样本，只留计数，不造行
        if not code or streak is None or streak < 1:
            unknown += 1
            continue
        rows.append(
            {
                "code": code,
                "name": str(pick(record, NAME_KEYS) or "").strip(),
                # D-01：取不到就是 null，不补 1
                "streak": streak,
                "first_seal_time": pick(record, FIRST_SEAL_KEYS),
                "last_seal_time": pick(record, SEAL_KEYS),
                "reason": pick(record, REASON_KEYS),
                "trade_date": as_day_text(pick(record, TRADE_DATE_KEYS)),
            }
        )
    if unknown:
        parts.append(f"无连板数或无代码 {unknown} 条")
    detail = f"limit_pool: {backend}（{'，'.join(parts)}）"

    if not rows:
        return {
            "date": str(day),
            "available": False,
            "reason": NO_ROWS_EMPTY if not records else NO_ROWS_NO_BOARD,
            "source_detail": detail,
            "count": 0,
            "rows": [],
        }

    rows.sort(key=lambda row: (-row["streak"], row["code"]))
    return {
        "date": str(day),
        "available": True,
        "reason": None,
        "source_detail": detail,
        "count": len(rows),
        "rows": rows,
    }


def compute_limit_pool(
    day: date | str,
    *,
    root: Any = None,
    records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """读某交易日的涨停池缓存并投影；缓存不可用时如实 `available=False`。"""
    backend = "given"
    if records is None:
        records, backend = dataset_snapshot("limit_pool", day, root)
    return project_limit_pool(records, day=day, backend=backend)
