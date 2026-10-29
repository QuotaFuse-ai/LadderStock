"""本地缓存回放的唯一读入口（DEC-007 §2.5）。

顺序固定：**Parquet 日分区优先 → DuckDB 兜底**，两条路都读不到才算缓存缺失。
provider_registry 在「peer 全挂」时调这里回放；`review show` / `sentiment show`
后续要读历史，也只走这个函数，不自己去拼 DuckDB SQL。

读回的每条记录都是原始 payload（adapter 不做字段填充，这里同样不补），
另附 `_source` / `_ingested_at` 两个下划线前缀的元数据，便于溯源。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb

from ladderstock.core.boundary import owner_of
from ladderstock.data import duckdb_store


@dataclass(frozen=True)
class CacheSnapshot:
    """一次缓存回放的完整结果。"""

    dataset: str
    records: list[dict[str, Any]] = field(default_factory=list)
    as_of: date | None = None
    backend: str = "none"
    cached_at: datetime | None = None

    @property
    def empty(self) -> bool:
        return not self.records


def read_dataset(
    dataset: str,
    day: date | str | None = None,
    *,
    limit: int | None = None,
    root: Path | None = None,
) -> list[dict[str, Any]]:
    """按日期读回数据集原始记录；`day` 为 None 时读全部历史。"""
    return snapshot(dataset, day, limit=limit, root=root).records


def snapshot(
    dataset: str,
    day: date | str | None = None,
    *,
    limit: int | None = None,
    root: Path | None = None,
) -> CacheSnapshot:
    owner_of(dataset)
    when = _as_date(day)
    if day is not None and when is None:
        raise ValueError(f"非法日期：{day!r}")

    partition = duckdb_store.partition_path(dataset, when, root=root) if when else None
    if partition is not None and partition.is_file():
        records = _read_parquet(partition, limit=limit)
        return CacheSnapshot(
            dataset=dataset,
            records=records,
            as_of=_max_day(records) or when,
            backend="parquet",
            cached_at=_max_ingested(records) or _mtime(partition),
        )

    records = duckdb_store.query_dataset(dataset, when, limit=limit, root=root)
    return CacheSnapshot(
        dataset=dataset,
        records=records,
        as_of=_max_day(records) or when,
        backend="duckdb" if records else "none",
        cached_at=_max_ingested(records),
    )


def latest_day(dataset: str, *, root: Path | None = None) -> date | None:
    """数据集在本地缓存的最后一天（用于降级回放「昨天」）。"""
    owner_of(dataset)
    path = duckdb_store.db_path(root)
    if not path.is_file():
        return None
    conn = duckdb_store.connect(path, read_only=True)
    try:
        if not duckdb_store.table_exists(conn, dataset):
            return None
        row = conn.execute(f'SELECT MAX(trade_date) FROM "{dataset}"').fetchone()
    finally:
        duckdb_store.close(conn)
    return _as_date(row[0]) if row and row[0] else None


def has_partition(dataset: str, day: date | str, *, root: Path | None = None) -> bool:
    owner_of(dataset)
    when = _as_date(day)
    if when is None:
        return False
    return duckdb_store.partition_path(dataset, when, root=root).is_file()


# --------------------------------------------------------------------------- #
# 内部
# --------------------------------------------------------------------------- #

def _read_parquet(path: Path, *, limit: int | None) -> list[dict[str, Any]]:
    sql_path = str(path).replace("\\", "/").replace("'", "''")
    tail = f" LIMIT {int(limit)}" if limit else ""
    conn = duckdb.connect()
    try:
        rows = conn.execute(
            "SELECT code, trade_date, ingested_at, source, payload "
            f"FROM read_parquet('{sql_path}') ORDER BY trade_date, code{tail}"
        ).fetchall()
    finally:
        conn.close()
    return [_decode(row) for row in rows]


def _decode(row: tuple[Any, ...]) -> dict[str, Any]:
    code, trade_date, ingested_at, source, payload = row
    try:
        record: dict[str, Any] = json.loads(payload) if payload else {}
    except json.JSONDecodeError:
        record = {"payload": payload}
    record.setdefault("code", code or "")
    record.setdefault("trade_date", str(trade_date))
    record.setdefault("_ingested_at", str(ingested_at or ""))
    record.setdefault("_source", source or "")
    return record


def _max_day(records: list[dict[str, Any]]) -> date | None:
    days = [_as_date(record.get("trade_date")) for record in records]
    valid = [day for day in days if day is not None]
    return max(valid) if valid else None


def _max_ingested(records: list[dict[str, Any]]) -> datetime | None:
    stamps: list[datetime] = []
    for record in records:
        raw = record.get("_ingested_at")
        if not raw:
            continue
        try:
            stamps.append(datetime.fromisoformat(str(raw)))
        except ValueError:
            continue
    return max(stamps) if stamps else None


def _mtime(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).astimezone()
    except OSError:
        return None


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None
