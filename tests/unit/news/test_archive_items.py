"""资讯归档：采到的公告/研报要落到 `data/archive` 与历史库，不能只停在 `news.sqlite`。

真实缺口：采集器每轮 `status=ok`、`items=100`，`data/archive` 下却没有 `research_report`
分区、`market.duckdb` 也没有这张表——`news_items` 里能查到的只有 47 条。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ladderstock.data import duckdb_store
from ladderstock.news.models import NewsItem
from ladderstock.news.stream import _archive_items


@pytest.fixture()
def data_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    return tmp_path


def _item(*, hash_: str, code: str = "600000", day: str = "2026-11-19", dataset: str = "research_report") -> NewsItem:
    return NewsItem(
        id=f"id-{hash_}",
        source=dataset,
        title=f"研报 {hash_}",
        url=f"https://example.invalid/{hash_}",
        published_at=day,
        related_codes=(code,),
        summary="摘要",
        content_hash=hash_,
        dedup_key=hash_,
        provenance={"dataset": dataset},
    )


def test_items_are_written_to_archive_and_duckdb(data_home) -> None:
    items = [_item(hash_="h1"), _item(hash_="h2")]

    outcome = _archive_items(items, data_home)

    assert outcome["errors"] == [], outcome
    assert outcome["datasets"] == {"research_report": 2}
    # 同票同日两篇研报不能被折叠成一条
    assert outcome["written"] == 2

    rows = duckdb_store.query_dataset("research_report", None, root=data_home)
    assert len(rows) == 2
    assert {row["content_hash"] for row in rows} == {"h1", "h2"}

    partition = Path(data_home) / "archive" / "2026" / "11" / "19" / "research_report.parquet"
    assert partition.is_file(), "分区要落在发布日那一天"


def test_rerun_is_idempotent(data_home) -> None:
    items = [_item(hash_="h1"), _item(hash_="h2")]

    _archive_items(items, data_home)
    outcome = _archive_items(items, data_home)

    assert outcome["written"] == 2
    rows = duckdb_store.query_dataset("research_report", None, root=data_home)
    assert len(rows) == 2, "重跑不能翻倍"


def test_rejected_dataset_is_reported_not_fatal(data_home) -> None:
    """未登记的数据集必须记进 errors，而不是让整条资讯流失败或静默丢弃。"""
    outcome = _archive_items([_item(hash_="h1", dataset="not_registered_dataset")], data_home)

    assert outcome["datasets"] == {}
    assert outcome["errors"], "被挡的原因要能看见"
    assert "not_registered_dataset" in outcome["errors"][0]
