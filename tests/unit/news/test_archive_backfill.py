"""研报补归档：`news.sqlite` 里已有的条目要用本地库回填，不再依赖重新采集。

真实缺口：库里 47 条 `research_report` 采到了、查询层也查得到，但 `data/archive`
没有分区、`market.duckdb` 没有表——历史仍是「采了没存」。回填只读本地库，幂等。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ladderstock.data import duckdb_store
from ladderstock.news import store as news_store
from ladderstock.news.models import NewsItem
from ladderstock.news.stream import backfill_archive


@pytest.fixture()
def data_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    return tmp_path


def _item(hash_: str, *, day: str = "2026-11-19", dataset: str = "research_report") -> NewsItem:
    return NewsItem(
        id=f"id-{hash_}",
        source=dataset,
        title=f"研报 {hash_}",
        url=f"https://example.invalid/{hash_}",
        published_at=day,
        related_codes=("600000",),
        summary="摘要",
        content_hash=hash_,
        dedup_key=hash_,
        provenance={"dataset": dataset},
    )


def _seed(items: list[NewsItem]) -> None:
    conn = news_store.connect()
    assert conn is not None
    try:
        news_store.upsert_items(conn, items)
    finally:
        news_store.close(conn)


def test_backfill_writes_existing_items_to_archive(data_home) -> None:
    _seed([_item("b1"), _item("b2", day="2026-11-18")])

    outcome = backfill_archive(datasets=["research_report"])

    assert outcome["dry_run"] is False
    assert outcome["candidates"] == {"research_report": 2}
    assert outcome["written"] == 2
    assert outcome["errors"] == []
    rows = duckdb_store.query_dataset("research_report", None, root=data_home)
    assert len(rows) == 2
    assert (Path(data_home) / "archive" / "2026" / "11" / "19" / "research_report.parquet").is_file()
    assert (Path(data_home) / "archive" / "2026" / "11" / "18" / "research_report.parquet").is_file()


def test_backfill_is_idempotent(data_home) -> None:
    _seed([_item("b1"), _item("b2")])

    backfill_archive(datasets=["research_report"])
    backfill_archive(datasets=["research_report"])

    assert len(duckdb_store.query_dataset("research_report", None, root=data_home)) == 2


def test_backfill_dry_run_counts_without_writing(data_home) -> None:
    _seed([_item("b1")])

    outcome = backfill_archive(datasets=["research_report"], dry_run=True)

    assert outcome["dry_run"] is True
    assert outcome["candidates"] == {"research_report": 1}
    assert outcome["written"] == 0
    assert not (Path(data_home) / "archive").exists() or not list(
        (Path(data_home) / "archive").rglob("*.parquet")
    )


def test_backfill_filters_by_dataset(data_home) -> None:
    _seed([_item("b1", dataset="research_report"), _item("f1", dataset="flash_cls")])

    outcome = backfill_archive(datasets=["research_report"])

    assert outcome["candidates"] == {"research_report": 1}
    assert "flash_cls" not in outcome["datasets"]
