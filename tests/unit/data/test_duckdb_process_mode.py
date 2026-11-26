"""同进程连接模式：只读请求遇到读写连接时收敛，配置冲突不判成「库坏了」。

15:40 收盘摘要失败的原话是
`Can't open a connection to same database file with a different configuration`
——web 进程里 `OpenEnsure` 顺手跑一轮 `sync_one`（读写），页面 GET 又在读同一个库
（只读），DuckDB 在**同进程**里不允许两种配置并存。它不是库损坏，重试也等不到。
"""

from __future__ import annotations

import duckdb
import pytest

from ladderstock.data import duckdb_store
from ladderstock.errors import EXIT_CONFIG, EXIT_DB_BUSY, LadderStockError


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "market.duckdb"
    conn = duckdb.connect(str(path))
    conn.execute("create table probe as select 42 as answer")
    conn.close()
    return path


def test_read_only_request_upgrades_when_write_connection_is_open(db) -> None:
    writer = duckdb_store.connect(db, read_only=False)
    try:
        reader = duckdb_store.connect(db, read_only=True)
        try:
            assert reader.execute("select answer from probe").fetchone() == (42,)
        finally:
            duckdb_store.close(reader)
    finally:
        duckdb_store.close(writer)

    # 读写连接关掉后，只读请求回到真正的只读模式
    reader = duckdb_store.connect(db, read_only=True)
    try:
        assert reader.execute("select answer from probe").fetchone() == (42,)
    finally:
        duckdb_store.close(reader)


def test_config_mismatch_is_reported_as_busy_not_corrupt(monkeypatch, db) -> None:
    def boom(*_args, **_kwargs):
        raise RuntimeError(
            "Connection Error: Can't open a connection to same database file with a "
            "different configuration than existing connections"
        )

    monkeypatch.setattr(duckdb_store.duckdb, "connect", boom)

    with pytest.raises(LadderStockError) as info:
        duckdb_store.connect(db, read_only=False, attempts=1)

    assert info.value.code == EXIT_DB_BUSY
    assert info.value.code != EXIT_CONFIG
    assert "配置冲突" in info.value.message
    assert "库损坏" in info.value.message


def test_other_open_failures_stay_config_errors(monkeypatch, db) -> None:
    def boom(*_args, **_kwargs):
        raise RuntimeError("IO Error: file is not a valid duckdb database file")

    monkeypatch.setattr(duckdb_store.duckdb, "connect", boom)

    with pytest.raises(LadderStockError) as info:
        duckdb_store.connect(db, read_only=True, attempts=1)

    assert info.value.code == EXIT_CONFIG
