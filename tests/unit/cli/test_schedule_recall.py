"""F9 到期回访定时任务：`kind=recall` 走同一个 `recall.run_recall`，结果折成任务字段。"""

from __future__ import annotations

import pytest

from ladderstock.cli import schedule
from ladderstock.data import chat_store


class _Conn:
    pass


@pytest.fixture()
def fake_store(monkeypatch):
    closed: list[object] = []
    monkeypatch.setattr(chat_store, "connect", lambda root=None: _Conn())
    monkeypatch.setattr(chat_store, "close", lambda conn: closed.append(conn))
    return closed


def test_recall_job_reports_stats(monkeypatch, fake_store):
    from ladderstock.ai import recall

    stats = {
        "as_of": "2026-11-21",
        "due": 3,
        "hit": 1,
        "miss": 1,
        "flipped": 0,
        "undecided": 0,
        "waiting": 1,
        "skipped": 0,
    }
    monkeypatch.setattr(recall, "run_recall", lambda conn, data_root=None: stats)

    out = schedule.run_job(
        {"id": "recall_due", "name": "16:10 观点回访判定", "kind": "recall", "trigger": "16:10"}
    )

    assert out["status"] == "ok"
    assert "到期 3 条" in out["detail"]
    assert out["stats"]["hit"] == 1
    assert fake_store, "连接必须被关掉，否则 sqlite 句柄会漏"


def test_recall_job_no_due_records(monkeypatch, fake_store):
    from ladderstock.ai import recall

    stats = {
        "as_of": "2026-11-21",
        "due": 0,
        "hit": 0,
        "miss": 0,
        "flipped": 0,
        "undecided": 0,
        "waiting": 0,
        "skipped": 0,
    }
    monkeypatch.setattr(recall, "run_recall", lambda conn, data_root=None: stats)

    out = schedule.run_job({"id": "recall_due", "kind": "recall", "trigger": "16:10"})

    assert out["status"] == "ok"
    assert "没有到期记录" in out["detail"]


def test_recall_job_tolerates_unavailable_store(monkeypatch):
    monkeypatch.setattr(chat_store, "connect", lambda root=None: None)

    out = schedule.run_job({"id": "recall_due", "kind": "recall", "trigger": "16:10"})

    assert out["status"] == "failed"
    assert "chat_store" in out["detail"]


def test_recall_kind_registered_and_scheduled():
    assert "recall" in schedule.JOB_KINDS
    jobs = [job for job in schedule.DEFAULT_JOBS if job.get("id") == "recall_due"]
    assert jobs and jobs[0]["kind"] == "recall" and jobs[0]["trigger"] == "16:10"
