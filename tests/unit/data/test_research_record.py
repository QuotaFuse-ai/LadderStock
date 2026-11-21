"""F9 账本的表层：落账、到期扫描、判完结、统计口径。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ladderstock.data import chat_store


@pytest.fixture()
def conn(tmp_path: Path):
    connection = chat_store.connect(tmp_path)
    assert connection is not None
    try:
        yield connection
    finally:
        connection.close()


def test_schema_has_research_record(tmp_path: Path) -> None:
    connection = chat_store.connect(tmp_path)
    assert connection is not None
    try:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "research_record" in tables
    finally:
        connection.close()


def test_insert_then_due_then_close(conn) -> None:
    chat_store.insert_record(
        conn,
        record_id="r1",
        question="600519 一个月怎么看",
        direction="bullish",
        horizon_days=20,
        symbol="600519",
        base_price=1485.75,
        base_date="2026-11-20",
        falsifier="跌破 1400 则本条失效",
        due_date="2026-12-10",
    )
    conn.commit()

    assert chat_store.due_records(conn, "2026-12-01") == []
    due = chat_store.due_records(conn, "2026-12-10")
    assert [item["record_id"] for item in due] == ["r1"]
    assert due[0]["direction"] == "bullish"

    chat_store.close_record(conn, "r1", status=chat_store.RECORD_HIT, outcome={"reason": "方向应验"})
    conn.commit()
    assert chat_store.due_records(conn, "2026-12-10") == []


def test_stats_excludes_unjudged(conn) -> None:
    for record_id, status in (("a", chat_store.RECORD_HIT), ("b", chat_store.RECORD_MISS), ("c", chat_store.RECORD_OPEN)):
        chat_store.insert_record(
            conn,
            record_id=record_id,
            question="q",
            direction="bullish",
            horizon_days=5,
            due_date="2026-11-25",
        )
        if status != chat_store.RECORD_OPEN:
            chat_store.close_record(conn, record_id, status=status)
    conn.commit()

    stats = chat_store.record_stats(conn)
    assert stats["total"] == 3
    assert stats["open"] == 1
    assert stats["judged"] == 2
    assert stats["hit_rate"] == 0.5


def test_list_records_filters_by_symbol(conn) -> None:
    chat_store.insert_record(conn, record_id="x", question="q1", direction="bullish", horizon_days=5, symbol="600519")
    chat_store.insert_record(conn, record_id="y", question="q2", direction="bullish", horizon_days=5, symbol="000001")
    conn.commit()
    got = chat_store.list_records(conn, symbol="600519")
    assert [item["record_id"] for item in got] == ["x"]


def test_close_rejects_unknown_status(conn) -> None:
    chat_store.insert_record(conn, record_id="z", question="q", direction="bullish", horizon_days=5)
    with pytest.raises(Exception):
        chat_store.close_record(conn, "z", status="whatever")
