"""行数口径（QUOT-354 BUG-F）：数据集本机没有产物时，`0` 不是一条读数。

`data/archive.py:snapshot()` 对「读到了源但当天 0 行」和「根本没有这个数据集当天的分区」
都返回空列表，只有 `backend` 能区分。复盘指标必须把这个区分带到前端——
`review/SectionMetrics.tsx` 对 `null` 显示 `—`、对 `0` 显示 0，两者不能混。
"""

from __future__ import annotations

from typing import Any

import pytest

from ladderstock.review import pipeline


@pytest.fixture()
def fake_records(monkeypatch: pytest.MonkeyPatch):
    """按数据集喂 `(records, backend)`；未登记的数据集一律按「没有产物」返回。"""

    def apply(**by_dataset: tuple[list[dict[str, Any]], str]) -> None:
        def _records(dataset: str, day: Any, root: Any) -> tuple[list[dict[str, Any]], str]:
            return by_dataset.get(dataset, ([], "none"))

        monkeypatch.setattr(pipeline, "_records", _records)

    return apply


def test_theme_rows_keep_real_zero_but_drop_unread_dataset(fake_records) -> None:
    fake_records(
        industry_board=([{"name": "AI 算力"}] * 3, "parquet"),
        concept_board=([], "duckdb"),
        theme_news=([], "none"),
    )

    metrics = pipeline._theme_metrics("2026-11-03", None)

    assert metrics["available"] is True
    assert metrics["industry_rows"] == 3
    # 读到了数据源、当天就是 0 行 —— 这是真实读数，保留 0
    assert metrics["concept_rows"] == 0
    # 本机没有 theme_news 当天的产物 —— 不是「今天 0 条资讯」
    assert metrics["news_rows"] is None
    # 原因仍可查：后端逐源写进 source_detail，前端「数据来源」行原样出示
    assert "theme_news: none" in metrics["source_detail"]


def test_dragon_tiger_rows_follow_the_same_caliber(fake_records) -> None:
    fake_records(dragon_tiger=([], "none"), dragon_tiger_seats=([], "none"))

    metrics = pipeline._dragon_tiger_metrics("2026-11-03", None)

    assert metrics["available"] is False
    assert metrics["board_rows"] is None
    assert metrics["seat_rows"] is None
