"""DuckDB 历史库 + Parquet 按日分区（DEC-007 §2.3）。

布局::

    <data_dir>/market.duckdb                     # 历史库（duckdb）
    <data_dir>/archive/YYYY/MM/DD/<dataset>.parquet  # 当日快照，按数据集一个文件

幂等规则（与 DEC-007 §2.3 对齐）：

| 模式 | 数据集 | 幂等键 | 重跑行为 |
|---|---|---|---|
| `append` | `daily_k` | `(code, trade_date)` | upsert，行数不增长 |
| `replace` | 其余（`theme_news` 等） | `trade_date` | 整日覆盖 |

事务边界：

- 一次 `write_dataset()` 是**一个事务**；任一行写入失败 → 整批回滚，库里不留半份；
- 提交成功后才导出 Parquet；导出用 tmp 文件 + `os.replace` 原子替换；
- 空记录**不覆盖**已有数据（防止 peer 返回空把当日清空），由调用方按 `empty` 处理。

表结构统一为 5 列，`payload` 存原始记录 JSON——M3/M4 的计算层需要什么字段就自己解，
这里不做「猜字段」的映射补全（DEC-006 闸门 2）：:

    code        VARCHAR   -- 个股代码；整日快照（板块/情绪等）为空串
    trade_date  DATE      -- 交易日；theme_news / council_memo 存 data_as_of
    ingested_at TIMESTAMP -- 落库时间（带时区）
    source      VARCHAR   -- 实际取数的 source id
    payload     VARCHAR   -- 原始记录（JSON）

`archive.read_dataset()` 优先读 Parquet，读不到再回落到本模块的 DuckDB 查询。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb

from ladderstock.cli.paths import data_dir
from ladderstock.core.boundary import owner_of

DB_FILENAME = "market.duckdb"
ARCHIVE_DIRNAME = "archive"

COLUMNS: tuple[str, ...] = ("code", "trade_date", "ingested_at", "source", "payload")

# 记录里可能承载交易日期的键（按优先级）
DATE_KEYS: tuple[str, ...] = ("trade_date", "date", "data_as_of", "as_of", "day")
CODE_KEYS: tuple[str, ...] = ("code", "symbol", "stock_code", "ts_code", "sec_code")


def db_path(root: Path | None = None) -> Path:
    base = Path(root) if root is not None else data_dir()
    return base / DB_FILENAME


def archive_root(root: Path | None = None) -> Path:
    base = Path(root) if root is not None else data_dir()
    return base / ARCHIVE_DIRNAME


def connect(path: Path | None = None, *, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """打开历史库；父目录不存在时创建（回放只读模式下不创建）。"""
    target = Path(path) if path is not None else db_path()
    if not read_only:
        target.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(target), read_only=read_only)


def close(conn: duckdb.DuckDBPyConnection | None) -> None:
    if conn is not None:
        try:
            conn.close()
        except Exception:  # noqa: BLE001 - 关闭失败不影响主流程
            pass


# --------------------------------------------------------------------------- #
# 记录规整
# --------------------------------------------------------------------------- #

def record_day(record: dict[str, Any], default: date | None = None) -> date | None:
    """从记录里取交易日；取不到就用 `default`，都没有则返回 None。"""
    for key in DATE_KEYS:
        value = record.get(key)
        parsed = _as_date(value)
        if parsed is not None:
            return parsed
    return default


def record_code(record: dict[str, Any]) -> str:
    for key in CODE_KEYS:
        value = record.get(key)
        if value is None or value == "":
            continue
        return str(value).strip()
    return ""


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        text = value.strip()
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        text = str(int(value))
        if len(text) == 8:  # YYYYMMDD
            try:
                return date(int(text[:4]), int(text[4:6]), int(text[6:]))
            except ValueError:
                return None
    return None


# --------------------------------------------------------------------------- #
# 写入
# --------------------------------------------------------------------------- #

@dataclass
class WriteResult:
    dataset: str
    mode: str
    rows: int
    days: list[str] = field(default_factory=list)
    partitions: list[str] = field(default_factory=list)
    total_rows: int = 0
    empty: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "mode": self.mode,
            "rows": self.rows,
            "days": list(self.days),
            "partitions": list(self.partitions),
            "total_rows": self.total_rows,
            "empty": self.empty,
        }


def write_dataset(
    dataset: str,
    records: list[dict[str, Any]],
    *,
    mode: str | None = None,
    source: str = "",
    default_trade_date: date | None = None,
    conn: duckdb.DuckDBPyConnection | None = None,
    root: Path | None = None,
) -> WriteResult:
    """按数据集幂等写库并落 Parquet 分区。

    `mode` 缺省取 `DATASET_CATALOG` 里登记的模式；传入值必须与登记一致，
    否则说明调用方在绕开归属目录——直接拒绝。
    """
    binding = owner_of(dataset)
    if mode is None:
        mode = binding.mode
    if mode not in ("append", "replace"):
        raise ValueError(f"未知写入模式：{mode}")
    if mode != binding.mode:
        raise ValueError(f"{dataset} 登记模式为 {binding.mode}，不允许按 {mode} 写入")

    table = _table(dataset)

    if not records:
        return WriteResult(
            dataset=dataset,
            mode=mode,
            rows=0,
            empty=True,
            total_rows=_count_rows(conn, table, root),
        )

    rows = _normalize(records, mode=mode, default_trade_date=default_trade_date or _today())
    days = sorted({row["trade_date"] for row in rows})

    owns = conn is None
    conn = conn or connect(db_path(root))
    try:
        _transaction(
            conn,
            lambda: _write_rows(conn, table, rows, mode=mode, source=source),
        )
        partitions = [
            str(p)
            for day in days
            if (p := _export_partition(conn, table, dataset, day, root=root)) is not None
        ]
        result = WriteResult(
            dataset=dataset,
            mode=mode,
            rows=len(rows),
            days=days,
            partitions=partitions,
            total_rows=_count_rows(conn, table, root),
        )
    finally:
        if owns:
            close(conn)
    return result


def _normalize(
    records: list[dict[str, Any]], *, mode: str, default_trade_date: date
) -> list[dict[str, Any]]:
    """规整记录并生成幂等键。

    - `append`（daily_k）按 `(code, trade_date)` 去重，重跑行数不增长；
    - `replace` 整日覆盖，**不按 code 折叠**——席位、龙虎榜这类一天可能多行，折叠会丢数据。
    """
    staged: dict[tuple[str, str], dict[str, Any]] = {}
    ingested_at = datetime.now().astimezone()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise TypeError(f"第 {index} 条记录不是对象，拒绝写入：{type(record).__name__}")
        day = record_day(record, default_trade_date)
        if day is None:  # pragma: no cover - default 保证非空
            raise ValueError(f"第 {index} 条记录缺少交易日")
        code = record_code(record)
        if mode == "append":
            if code:
                key = (code, day.isoformat())
            else:  # 无代码的追加记录用整条内容区分，避免同批多行被折叠
                key = (
                    json.dumps(record, ensure_ascii=False, sort_keys=True, default=str),
                    day.isoformat(),
                )
        else:
            key = (f"#{index}", day.isoformat())
        staged[key] = {
            "code": code,
            "trade_date": day.isoformat(),
            "ingested_at": ingested_at,
            "payload": json.dumps(record, ensure_ascii=False, default=str),
        }
    return [staged[key] for key in sorted(staged, key=lambda k: (k[1], k[0]))]


def _write_rows(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    rows: list[dict[str, Any]],
    *,
    mode: str,
    source: str,
) -> None:
    conn.execute(
        f'CREATE TABLE IF NOT EXISTS {table} '
        "(code VARCHAR, trade_date DATE, ingested_at TIMESTAMP, source VARCHAR, payload VARCHAR)"
    )
    conn.execute(
        "CREATE OR REPLACE TEMP TABLE _ldst_stage "
        "(code VARCHAR, trade_date DATE, ingested_at TIMESTAMP, source VARCHAR, payload VARCHAR)"
    )
    _stage_rows(conn, rows, source=source)
    if mode == "replace":
        conn.execute(
            f"DELETE FROM {table} WHERE trade_date IN "
            "(SELECT DISTINCT trade_date FROM _ldst_stage)"
        )
    else:
        conn.execute(
            f"DELETE FROM {table} AS t WHERE EXISTS "
            "(SELECT 1 FROM _ldst_stage AS s "
            "WHERE s.code = t.code AND s.trade_date = t.trade_date)"
        )
    conn.execute(
        f"INSERT INTO {table} ({', '.join(COLUMNS)}) "
        f"SELECT {', '.join(COLUMNS)} FROM _ldst_stage"
    )


def _stage_rows(
    conn: duckdb.DuckDBPyConnection, rows: list[dict[str, Any]], *, source: str
) -> None:
    payload = [
        (row["code"], row["trade_date"], row["ingested_at"], source, row["payload"]) for row in rows
    ]
    conn.executemany(
        "INSERT INTO _ldst_stage (code, trade_date, ingested_at, source, payload) "
        "VALUES (?, ?, ?, ?, ?)",
        payload,
    )


def _transaction(conn: duckdb.DuckDBPyConnection, body: Any) -> None:
    """整批事务：失败整批回滚，不留半份（DEC-007 §2.3 第 2 条）。"""
    conn.execute("BEGIN TRANSACTION")
    try:
        body()
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


# --------------------------------------------------------------------------- #
# Parquet 分区
# --------------------------------------------------------------------------- #

def partition_path(dataset: str, day: date | str, *, root: Path | None = None) -> Path:
    owner_of(dataset)  # 未登记数据集不许落分区
    when = _as_date(day) if not isinstance(day, date) else day
    if when is None:
        raise ValueError(f"非法日期：{day!r}")
    return (
        archive_root(root)
        / f"{when.year:04d}"
        / f"{when.month:02d}"
        / f"{when.day:02d}"
        / f"{dataset}.parquet"
    )


def _export_partition(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    dataset: str,
    day: str,
    *,
    root: Path | None,
) -> Path | None:
    when = _as_date(day)
    if when is None:
        return None
    target = partition_path(dataset, when, root=root)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".parquet.tmp")
    if tmp.exists():
        tmp.unlink()
    literal = f"DATE '{when.isoformat()}'"
    sql_path = str(tmp).replace("\\", "/").replace("'", "''")
    conn.execute(
        f"COPY (SELECT {', '.join(COLUMNS)} FROM {table} WHERE trade_date = {literal}) "
        f"TO '{sql_path}' (FORMAT PARQUET)"
    )
    os.replace(tmp, target)
    return target


# --------------------------------------------------------------------------- #
# 读取
# --------------------------------------------------------------------------- #

def query_dataset(
    dataset: str,
    day: date | None = None,
    *,
    code: str | None = None,
    limit: int | None = None,
    conn: duckdb.DuckDBPyConnection | None = None,
    root: Path | None = None,
) -> list[dict[str, Any]]:
    """从 DuckDB 读回原始记录（payload 解成 dict）。"""
    owner_of(dataset)
    table = _table(dataset)
    path = db_path(root)
    if not path.is_file():
        return []
    owns = conn is None
    conn = conn or connect(path, read_only=True)
    try:
        if not _table_exists(conn, table):
            return []
        where: list[str] = []
        params: list[Any] = []
        if day is not None:
            where.append("trade_date = ?")
            params.append(day.isoformat())
        if code:
            where.append("code = ?")
            params.append(code)
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        tail = f" LIMIT {int(limit)}" if limit else ""
        rows = conn.execute(
            f"SELECT {', '.join(COLUMNS)} FROM {table}{clause} "
            f"ORDER BY trade_date, code{tail}",
            params,
        ).fetchall()
    finally:
        if owns:
            close(conn)
    return [_decode(row) for row in rows]


def dataset_rows(dataset: str, *, root: Path | None = None) -> int:
    owner_of(dataset)
    return _count_rows(None, _table(dataset), root)


def _count_rows(
    conn: duckdb.DuckDBPyConnection | None, table: str, root: Path | None
) -> int:
    owns = conn is None
    if conn is None:
        path = db_path(root)
        if not path.is_file():
            return 0
        conn = connect(path, read_only=True)
    try:
        if not _table_exists(conn, table):
            return 0
        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return int(row[0]) if row else 0
    finally:
        if owns and conn is not None:
            close(conn)


def table_exists(conn: duckdb.DuckDBPyConnection, dataset: str) -> bool:
    """数据集在历史库里是否已建表。"""
    return _table_exists(conn, _table(dataset))


def _table_exists(conn: duckdb.DuckDBPyConnection, table: str) -> bool:
    name = table.replace('"', "")
    row = conn.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?", [name]
    ).fetchone()
    return bool(row and row[0])


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


def _table(dataset: str) -> str:
    """表名加引号；数据集名已由 DATASET_CATALOG 校验，不含注入面。"""
    if not dataset.replace("_", "").isalnum():
        raise ValueError(f"非法数据集名：{dataset}")
    return f'"{dataset}"'


def _today() -> date:
    return datetime.now().astimezone().date()
