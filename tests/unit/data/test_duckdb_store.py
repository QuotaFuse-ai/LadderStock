"""DuckDB 落库与 Parquet 日分区：幂等、整日覆盖、失败回滚（DEC-007 §2.3）。"""

from __future__ import annotations

from datetime import date

import duckdb
import pytest

from ladderstock.core.boundary import BoundaryViolation
from ladderstock.data import duckdb_store


def _kline(code: str, day: str, close: float = 10.0) -> dict[str, object]:
    return {"code": code, "trade_date": day, "close": close}


def _rows(dataset: str, tmp_path) -> list[dict]:
    return duckdb_store.query_dataset(dataset, root=tmp_path)


def test_append_mode_upserts_on_code_and_trade_date(tmp_path) -> None:
    first = duckdb_store.write_dataset(
        "daily_k", [_kline("600000", "2026-10-24", 10.0), _kline("000001", "2026-10-24", 12.0)],
        root=tmp_path,
    )
    assert first.rows == 2
    assert first.total_rows == 2

    # 同一天重跑：按 (code, trade_date) 覆盖，不新增行
    second = duckdb_store.write_dataset(
        "daily_k", [_kline("600000", "2026-10-24", 10.9), _kline("000001", "2026-10-24", 12.1)],
        root=tmp_path,
    )
    assert second.rows == 2
    assert duckdb_store.dataset_rows("daily_k", root=tmp_path) == 2

    codes = {row["code"]: row["close"] for row in _rows("daily_k", tmp_path)}
    assert codes["600000"] == 10.9


def test_append_across_days_accumulates(tmp_path) -> None:
    duckdb_store.write_dataset("daily_k", [_kline("600000", "2026-10-24")], root=tmp_path)
    duckdb_store.write_dataset("daily_k", [_kline("600000", "2026-10-25")], root=tmp_path)

    days = sorted({row["trade_date"] for row in _rows("daily_k", tmp_path)})
    assert days == ["2026-10-24", "2026-10-25"]


def test_replace_mode_covers_whole_day_without_folding_rows(tmp_path) -> None:
    duckdb_store.write_dataset(
        "limit_pool",
        [
            {"code": "600519", "trade_date": "2026-10-24", "seats": 1},
            {"code": "600519", "trade_date": "2026-10-24", "seats": 2},
        ],
        root=tmp_path,
    )
    # replace 不按 code 折叠：一天多行是正常形态
    assert duckdb_store.dataset_rows("limit_pool", root=tmp_path) == 2

    duckdb_store.write_dataset(
        "limit_pool", [{"code": "600519", "trade_date": "2026-10-24", "seats": 9}], root=tmp_path
    )
    rows = _rows("limit_pool", tmp_path)
    assert len(rows) == 1
    assert rows[0]["seats"] == 9


def test_replace_keeps_other_days(tmp_path) -> None:
    duckdb_store.write_dataset(
        "limit_pool",
        [
            {"code": "600519", "trade_date": "2026-10-23"},
            {"code": "600519", "trade_date": "2026-10-24"},
        ],
        root=tmp_path,
    )
    duckdb_store.write_dataset(
        "limit_pool", [{"code": "300750", "trade_date": "2026-10-24"}], root=tmp_path
    )

    rows = _rows("limit_pool", tmp_path)
    assert sorted(row["trade_date"] for row in rows) == ["2026-10-23", "2026-10-24"]
    assert {row["code"] for row in rows if row["trade_date"] == "2026-10-24"} == {"300750"}


def test_empty_batch_never_deletes_existing_day(tmp_path) -> None:
    duckdb_store.write_dataset("daily_k", [_kline("600000", "2026-10-24")], root=tmp_path)
    result = duckdb_store.write_dataset("daily_k", [], root=tmp_path)

    assert result.empty is True
    assert result.rows == 0
    assert duckdb_store.dataset_rows("daily_k", root=tmp_path) == 1


def test_invalid_record_leaves_table_untouched(tmp_path) -> None:
    duckdb_store.write_dataset("daily_k", [_kline("600000", "2026-10-24")], root=tmp_path)

    with pytest.raises(TypeError):
        duckdb_store.write_dataset(
            "daily_k", [_kline("600001", "2026-10-25"), "坏行"], root=tmp_path  # type: ignore[list-item]
        )
    assert duckdb_store.dataset_rows("daily_k", root=tmp_path) == 1


def test_transaction_rolls_back_partial_write(tmp_path) -> None:
    duckdb_store.write_dataset("daily_k", [_kline("600000", "2026-10-24")], root=tmp_path)
    conn = duckdb_store.connect(duckdb_store.db_path(tmp_path))
    try:

        def broken() -> None:
            conn.execute('DELETE FROM "daily_k"')
            raise RuntimeError("磁盘写入失败")

        with pytest.raises(RuntimeError):
            duckdb_store._transaction(conn, broken)
    finally:
        duckdb_store.close(conn)

    assert duckdb_store.dataset_rows("daily_k", root=tmp_path) == 1


def test_parquet_partition_layout(tmp_path) -> None:
    result = duckdb_store.write_dataset(
        "daily_k", [_kline("600000", "2026-10-24"), _kline("000001", "2026-10-24")], root=tmp_path
    )

    expected = tmp_path / "archive" / "2026" / "10" / "24" / "daily_k.parquet"
    assert duckdb_store.partition_path("daily_k", date(2026, 10, 24), root=tmp_path) == expected
    assert result.partitions == [str(expected)]
    assert expected.is_file()
    assert not expected.with_suffix(".parquet.tmp").exists()

    count = duckdb.connect().execute(
        f"SELECT COUNT(*) FROM read_parquet('{str(expected).replace(chr(92), '/')}')"
    ).fetchone()
    assert count == (2,)


def test_mode_must_match_catalog(tmp_path) -> None:
    # daily_k 登记为 append，按 replace 写入说明调用方绕开了归属目录
    with pytest.raises(ValueError):
        duckdb_store.write_dataset("daily_k", [_kline("600000", "2026-10-24")], mode="replace")


def test_unknown_dataset_is_rejected(tmp_path) -> None:
    with pytest.raises(BoundaryViolation):
        duckdb_store.write_dataset("not_a_dataset", [_kline("600000", "2026-10-24")], root=tmp_path)


def test_record_day_accepts_compact_dates() -> None:
    assert duckdb_store.record_day({"date": 20261024}) == date(2026, 10, 24)
    assert duckdb_store.record_day({"trade_date": "2026-10-24T15:00:00"}) == date(2026, 10, 24)
    assert duckdb_store.record_day({}, date(2026, 10, 24)) == date(2026, 10, 24)
