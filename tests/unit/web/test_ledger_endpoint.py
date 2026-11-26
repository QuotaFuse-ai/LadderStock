"""F9：`/api/conversation/ledger` 与 `/recall` 的读面/触发面（临时 chat.sqlite）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.data import chat_store
from ladderstock.web.server import create_app

BASE_URL = "http://127.0.0.1:5950"
LOOPBACK = ("127.0.0.1", 54321)
HEADERS = {"X-LDST-Client": "1"}


class _NullControlPlane:
    peers: dict[str, Any] = {}


def _client(tmp_path: Path) -> TestClient:
    app = create_app(
        data_root=tmp_path,
        dist_dir=tmp_path / "no-dist",
        control_plane=_NullControlPlane(),
    )
    return TestClient(app, base_url=BASE_URL, client=LOOPBACK)


def _insert(tmp_path: Path, **overrides: Any) -> None:
    conn = chat_store.connect(tmp_path)
    assert conn is not None
    payload = {
        "record_id": "r1",
        "question": "600519 一个月怎么看",
        "direction": "bullish",
        "horizon_days": 20,
        "symbol": "600519",
        "base_price": 1485.75,
        "base_date": "2026-11-20",
        "falsifier": "跌破 1400 则本条失效",
        "due_date": "2026-12-10",
    }
    payload.update(overrides)
    try:
        chat_store.insert_record(conn, **payload)
    finally:
        chat_store.close(conn)


def test_empty_ledger_is_zero_not_error(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.get("/api/conversation/ledger", headers=HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["records"] == []
    assert body["stats"]["total"] == 0
    # 没判过就没有命中率：给 None，不编一个 0% 或 100%
    assert body["stats"]["hit_rate"] is None


def test_ledger_lists_records(tmp_path: Path) -> None:
    _insert(tmp_path)
    body = _client(tmp_path).get("/api/conversation/ledger", headers=HEADERS).json()
    assert body["stats"]["total"] == 1
    assert body["stats"]["open"] == 1
    record = body["records"][0]
    assert record["record_id"] == "r1"
    assert record["direction"] == "bullish"
    assert record["due_date"] == "2026-12-10"
    assert record["base_price"] == 1485.75


def test_ledger_filters_by_symbol(tmp_path: Path) -> None:
    _insert(tmp_path, record_id="a", symbol="600519")
    _insert(tmp_path, record_id="b", symbol="000001")
    client = _client(tmp_path)
    body = client.get("/api/conversation/ledger?symbol=600519", headers=HEADERS).json()
    assert [item["record_id"] for item in body["records"]] == ["a"]
    # 过滤只影响列表，汇总仍按整张表算
    assert body["stats"]["total"] == 2


def test_recall_on_empty_ledger_is_noop(tmp_path: Path) -> None:
    response = _client(tmp_path).post("/api/conversation/recall", headers=HEADERS)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["due"] == 0
    assert body["hit"] == 0


def test_recall_keeps_future_record_open(tmp_path: Path) -> None:
    """未到期不判：回访只看到期，命中率不会被等待期污染。"""
    _insert(tmp_path, due_date="2099-01-01")
    body = _client(tmp_path).post("/api/conversation/recall", headers=HEADERS).json()
    assert body["due"] == 0
    stats = _client(tmp_path).get("/api/conversation/ledger", headers=HEADERS).json()["stats"]
    assert stats["open"] == 1
    assert stats["judged"] == 0


@pytest.mark.parametrize("status", ["hit", "miss"])
def test_stats_after_manual_close(tmp_path: Path, status: str) -> None:
    _insert(tmp_path)
    conn = chat_store.connect(tmp_path)
    assert conn is not None
    try:
        chat_store.close_record(conn, "r1", status=status, outcome={"reason": "test"})
    finally:
        chat_store.close(conn)
    stats = _client(tmp_path).get("/api/conversation/ledger", headers=HEADERS).json()["stats"]
    assert stats["judged"] == 1
    assert stats[status] == 1
