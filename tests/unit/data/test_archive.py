"""缓存回放的唯一读入口：Parquet 日分区优先 → DuckDB 兜底（DEC-007 §2.5）。"""

from __future__ import annotations

from datetime import date

from ladderstock.data import archive, duckdb_store


def _seed(tmp_path) -> None:
    duckdb_store.write_dataset(
        "daily_k",
        [
            {"code": "600000", "trade_date": "2026-10-23", "close": 10.0},
            {"code": "600000", "trade_date": "2026-10-24", "close": 10.5},
        ],
        source="tickhub_kline",
        root=tmp_path,
    )


def test_reads_parquet_partition_first(tmp_path) -> None:
    _seed(tmp_path)

    snapshot = archive.snapshot("daily_k", date(2026, 10, 24), root=tmp_path)

    assert snapshot.backend == "parquet"
    assert snapshot.as_of == date(2026, 10, 24)
    assert [record["code"] for record in snapshot.records] == ["600000"]
    assert snapshot.cached_at is not None


def test_falls_back_to_duckdb_when_partition_missing(tmp_path) -> None:
    _seed(tmp_path)
    partition = duckdb_store.partition_path("daily_k", date(2026, 10, 24), root=tmp_path)
    partition.unlink()

    assert archive.has_partition("daily_k", date(2026, 10, 24), root=tmp_path) is False
    snapshot = archive.snapshot("daily_k", date(2026, 10, 24), root=tmp_path)
    assert snapshot.backend == "duckdb"
    assert len(snapshot.records) == 1


def test_missing_day_is_empty_not_an_error(tmp_path) -> None:
    _seed(tmp_path)

    snapshot = archive.snapshot("daily_k", date(2026, 1, 5), root=tmp_path)

    assert snapshot.backend == "none"
    assert snapshot.empty is True
    assert archive.read_dataset("daily_k", date(2026, 1, 5), root=tmp_path) == []


def test_read_all_history_when_day_is_none(tmp_path) -> None:
    _seed(tmp_path)

    records = archive.read_dataset("daily_k", root=tmp_path)

    assert len(records) == 2


def test_records_carry_provenance_metadata(tmp_path) -> None:
    _seed(tmp_path)

    record = archive.read_dataset("daily_k", date(2026, 10, 24), root=tmp_path)[0]

    assert record["_source"] == "tickhub_kline"
    assert record["_ingested_at"]
    assert record["trade_date"] == "2026-10-24"


def test_latest_day_uses_duckdb(tmp_path) -> None:
    _seed(tmp_path)

    assert archive.latest_day("daily_k", root=tmp_path) == date(2026, 10, 24)


def test_latest_day_without_database_returns_none(tmp_path) -> None:
    assert archive.latest_day("daily_k", root=tmp_path) is None


def test_limit_is_applied(tmp_path) -> None:
    _seed(tmp_path)

    records = archive.read_dataset("daily_k", root=tmp_path, limit=1)

    assert len(records) == 1
