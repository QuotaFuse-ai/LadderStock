"""本机提醒收件箱 `journal/inbox.py` 的契约（QUOT-432 / DEC-058）。

钉住四条：

- 同一 `dedupe_key` 重跑**只留一条**，且 `read_at` 不被清空（补跑不把已读改回未读）；
- 未读数 = `read_at IS NULL` 的条数；`mark_read` / `mark_all_read` 幂等；
- `list_items` 按 `created_at DESC`，`limit` 有界；
- 只读函数在库不存在时返回空结果且**不建库**。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from ladderstock.data import sqlite_store
from ladderstock.errors import EXIT_NOT_FOUND, EXIT_USAGE, LadderStockError
from ladderstock.journal import inbox


def _add(root: Path, **overrides):
    payload = {
        "job": "premarket_digest",
        "job_label": "09:05 盘前摘要",
        "title": "盘前摘要 2026-11-05",
        "lines": ["持仓 1 只 · 自选 1 只", "600000 现价 10.50 元"],
        "link": "/positions",
        "dedupe_key": "premarket_digest:2026-11-05",
        "kind": "digest",
        "root": root,
    }
    payload.update(overrides)
    return inbox.add_item(**payload)


def test_add_item_roundtrip_marks_unread(tmp_path: Path) -> None:
    item = _add(tmp_path)

    assert item["job"] == "premarket_digest"
    assert item["title"] == "盘前摘要 2026-11-05"
    assert item["lines"] == ["持仓 1 只 · 自选 1 只", "600000 现价 10.50 元"]
    assert item["link"] == "/positions"
    assert item["is_read"] is False
    assert item["read_at"] is None
    assert inbox.unread_count(root=tmp_path) == 1


def test_same_dedupe_key_updates_one_row_and_preserves_read_at(tmp_path: Path) -> None:
    first = _add(tmp_path, now=datetime(2026, 11, 5, 9, 5))
    read = inbox.mark_read(first["id"], root=tmp_path, now=datetime(2026, 11, 5, 10, 0))
    assert read["is_read"] is True
    assert inbox.unread_count(root=tmp_path) == 0

    second = _add(
        tmp_path,
        title="盘前摘要 2026-11-05（补跑）",
        lines=["刷新后的正文"],
        now=datetime(2026, 11, 5, 9, 6),
    )

    items = inbox.list_items(root=tmp_path)
    assert len(items) == 1
    assert second["id"] == first["id"]
    assert items[0]["title"] == "盘前摘要 2026-11-05（补跑）"
    assert items[0]["lines"] == ["刷新后的正文"]
    # 已读状态保留：补跑不把 read_at 清空
    assert items[0]["read_at"] == read["read_at"]
    assert items[0]["is_read"] is True
    assert inbox.unread_count(root=tmp_path) == 0


def test_items_without_dedupe_key_are_not_deduplicated(tmp_path: Path) -> None:
    _add(tmp_path, dedupe_key=None, title="第一条")
    _add(tmp_path, dedupe_key=None, title="第二条")

    assert len(inbox.list_items(root=tmp_path)) == 2


def test_unread_count_and_mark_all_read_are_idempotent(tmp_path: Path) -> None:
    first = _add(tmp_path, dedupe_key=None)
    _add(tmp_path, dedupe_key=None, title="另一条")

    inbox.mark_read(first["id"], root=tmp_path)
    assert inbox.unread_count(root=tmp_path) == 1

    outcome = inbox.mark_all_read(root=tmp_path, now=datetime(2026, 11, 5, 16, 0))
    assert outcome["updated"] == 1
    assert inbox.unread_count(root=tmp_path) == 0
    assert inbox.mark_all_read(root=tmp_path)["updated"] == 0


def test_mark_read_is_idempotent_and_keeps_first_timestamp(tmp_path: Path) -> None:
    item = _add(tmp_path)
    first = inbox.mark_read(item["id"], root=tmp_path, now=datetime(2026, 11, 5, 10, 0))
    second = inbox.mark_read(item["id"], root=tmp_path, now=datetime(2026, 11, 5, 11, 0))

    assert second["read_at"] == first["read_at"]


def test_mark_read_unknown_id_is_not_found(tmp_path: Path) -> None:
    _add(tmp_path)

    with pytest.raises(LadderStockError) as excinfo:
        inbox.mark_read("nope", root=tmp_path)
    assert excinfo.value.code == EXIT_NOT_FOUND


def test_list_items_is_newest_first_and_respects_limit(tmp_path: Path) -> None:
    _add(tmp_path, dedupe_key=None, title="旧", now=datetime(2026, 11, 5, 9, 0))
    _add(tmp_path, dedupe_key=None, title="新", now=datetime(2026, 11, 5, 16, 0))

    titles = [item["title"] for item in inbox.list_items(root=tmp_path)]
    assert titles == ["新", "旧"]
    assert [item["title"] for item in inbox.list_items(limit=1, root=tmp_path)] == ["新"]


def test_list_items_rejects_out_of_range_limit(tmp_path: Path) -> None:
    with pytest.raises(LadderStockError) as excinfo:
        inbox.list_items(0, root=tmp_path)
    assert excinfo.value.code == EXIT_USAGE

    with pytest.raises(LadderStockError):
        inbox.list_items(inbox.MAX_LIMIT + 1, root=tmp_path)


def test_lines_are_trimmed_and_bounded(tmp_path: Path) -> None:
    item = _add(
        tmp_path,
        lines=["  第一行  ", "", "   ", "第二行", *[f"第 {i} 行" for i in range(60)]],
    )

    assert item["lines"][0] == "第一行"
    assert item["lines"][1] == "第二行"
    assert len(item["lines"]) == inbox.MAX_LINES


def test_non_text_line_is_a_usage_error(tmp_path: Path) -> None:
    with pytest.raises(LadderStockError) as excinfo:
        _add(tmp_path, lines=["ok", 42])  # type: ignore[list-item]
    assert excinfo.value.code == EXIT_USAGE


def test_read_functions_do_not_create_the_database(tmp_path: Path) -> None:
    assert inbox.list_items(root=tmp_path) == []
    assert inbox.unread_count(root=tmp_path) == 0
    assert inbox.mark_all_read(root=tmp_path)["updated"] == 0

    with pytest.raises(LadderStockError) as excinfo:
        inbox.mark_read("nope", root=tmp_path)
    assert excinfo.value.code == EXIT_NOT_FOUND
    assert (tmp_path / sqlite_store.DB_FILENAME).exists() is False
