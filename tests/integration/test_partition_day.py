"""分区日口径：`partition_by: local_day` 的数据集按本地采集日分区，原数据日留在字段里。

背景（2026-11-20 的实测）：`theme_news` 原来是 `watermark_field: data_as_of` + `mode: replace`，
07:30 采到的 2,188 条被写进上游给的数据日（11-18）目录，天天整块覆盖前一天，
于是 11-20 永远没有分区——看目录像断档，其实每天都在采。
"""

from __future__ import annotations

import copy
from datetime import date, datetime

import pytest
from fixtures.fake_peers import (
    SYNC_DOC,
    FakeTransport,
    make_registry,
    wire_digest_tool_result,
)

from ladderstock.cli.sync import stamp_partition_day, sync_one
from ladderstock.data import duckdb_store, watermark

#: 上游给的数据日（摘要算出来的那天），不是采集发生的那天
UPSTREAM_DAY = "2026-10-24"


@pytest.fixture()
def data_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    return tmp_path


def test_stamp_keeps_source_day_in_data_as_of() -> None:
    day = date(2026, 11, 20)
    records = [
        {"topic": "ai", "date": UPSTREAM_DAY, "summary": "…"},
        {"topic": "macro", "trade_date": UPSTREAM_DAY, "summary": "…"},
        {"topic": "no-day", "summary": "…"},
        "not-a-dict",  # 非对象原样放过，交给写库层报错
    ]

    stamped = stamp_partition_day(records, day)

    assert stamped[0]["trade_date"] == "2026-11-20"
    assert stamped[0]["data_as_of"] == UPSTREAM_DAY
    # `date` 原值保留，只是不再是分区判据（trade_date 在 record_day 里更优先）
    assert stamped[0]["date"] == UPSTREAM_DAY
    assert stamped[1]["trade_date"] == "2026-11-20"
    assert stamped[1]["data_as_of"] == UPSTREAM_DAY
    assert stamped[2]["trade_date"] == "2026-11-20"
    assert "data_as_of" not in stamped[2]
    assert stamped[3] == "not-a-dict"


def test_stamp_does_not_mutate_input() -> None:
    record = {"date": UPSTREAM_DAY, "topic": "ai"}
    record_id = id(record)

    stamped = stamp_partition_day([record], date(2026, 11, 20))

    assert id(stamped[0]) != record_id
    assert record == {"date": UPSTREAM_DAY, "topic": "ai"}


def test_sync_partitions_on_local_day(data_home) -> None:
    sync_doc = copy.deepcopy(SYNC_DOC)
    sync_doc["sync"]["datasets"]["theme_news"]["partition_by"] = "local_day"
    transport = FakeTransport(
        handlers={
            "wire_get_digest": lambda params: wire_digest_tool_result(params["topic"], UPSTREAM_DAY)
        }
    )
    registry = make_registry(transport, data_home, sync=sync_doc)
    store = watermark.WatermarkStore(root=data_home)

    result = sync_one(registry, store, "theme_news")

    assert result["status"] == "ok", result
    today = datetime.now().astimezone().date()
    partition = data_home / "archive" / f"{today:%Y}" / f"{today:%m}" / f"{today:%d}" / "theme_news.parquet"
    assert partition.is_file(), f"分区应落在本地采集日 {today}：{partition}"

    rows = duckdb_store.query_dataset("theme_news", today, root=data_home)
    assert rows, "分区日应当能查到记录"
    assert {row["trade_date"] for row in rows} == {today.isoformat()}
    # 原数据日没有被丢掉，仍然可追溯
    assert {str(row.get("data_as_of")) for row in rows} == {UPSTREAM_DAY}


def test_sync_without_partition_by_keeps_upstream_day(data_home) -> None:
    """没加 `partition_by` 的数据集行为不变，避免这条改动外溢到别的数据集。"""
    transport = FakeTransport(
        handlers={
            "wire_get_digest": lambda params: wire_digest_tool_result(params["topic"], UPSTREAM_DAY)
        }
    )
    registry = make_registry(transport, data_home)
    store = watermark.WatermarkStore(root=data_home)

    result = sync_one(registry, store, "theme_news")

    assert result["status"] == "ok", result
    upstream = date.fromisoformat(UPSTREAM_DAY)
    partition = data_home / "archive" / f"{upstream:%Y}" / f"{upstream:%m}" / f"{upstream:%d}" / "theme_news.parquet"
    assert partition.is_file(), "原有口径：分区跟着 data_as_of 走"
