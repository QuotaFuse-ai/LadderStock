"""第④步两个新任务类型：定时研究（agent_research）与盯盘提醒（watch_alert）。

两类任务都不联网：研究走 agent 工具（读本机 DuckDB），盯盘读 `symbol_basic` 报价快照。
测试里把 `ask_agent` 与通知投递换成桩，只验「落盘 + 收件箱 + 结果字段」这三件事实，
不碰真实模型与桌面通知。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ladderstock.cli import schedule as sched
from ladderstock.data import duckdb_store
from ladderstock.journal import inbox, watchlist as watchlist_store
from ladderstock.web import assistant as assistant_module


class _Delivery:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.channel = "console"
        self.detail = "已写入本机终端（stderr）"
        self.degraded = not ok

    def as_dict(self) -> dict[str, Any]:
        return {"channel": self.channel, "ok": self.ok, "detail": self.detail, "degraded": self.degraded}


@pytest.fixture()
def patched(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """把 agent 与通知换成桩，返回可改写的开关。"""
    state: dict[str, Any] = {"reply": "600000 最近在 10.5–10.9 区间震荡。", "used_tools": True, "bar": 0, "delivery_ok": True}

    async def _ask_agent(**kwargs: Any) -> dict[str, Any]:
        state["bar"] += 1
        return {
            "reply": state["reply"],
            "model": "openai:model-a",
            "ai": {"model": "openai:model-a", "source": "global"},
            "fetched_at": "2026-11-16T15:40:00+08:00",
            "metrics": {},
            "sources": [{"name": "本机 daily_k", "fetched_at": "2026-11-16T15:40:00+08:00", "as_of": "2026-11-16"}],
            "used_tools": state["used_tools"],
        }

    monkeypatch.setattr(assistant_module, "ask_agent", _ask_agent)

    def _send(channel: Any, title: str, body: str) -> _Delivery:
        state["sent"] = {"channel": channel, "title": title, "body": body}
        return _Delivery(ok=bool(state["delivery_ok"]))

    monkeypatch.setattr("ladderstock.watch.notifier.send", _send)
    return state


# --------------------------------------------------------------------------- #
# agent_research
# --------------------------------------------------------------------------- #


def test_agent_research_requires_question(tmp_path: Path, patched: dict[str, Any]) -> None:
    outcome = sched.run_job(
        {"id": "agent_research_close", "name": "定时研究", "kind": "agent_research", "question": "  "},
        root=tmp_path,
    )
    assert outcome["status"] == "failed"
    assert "question" in outcome["detail"]
    assert patched["bar"] == 0  # 没问什么就不该跑模型


def test_agent_research_writes_card_inbox_and_notifies(
    tmp_path: Path, patched: dict[str, Any]
) -> None:
    job = {
        "id": "agent_research_close",
        "name": "16:40 定时研究",
        "kind": "agent_research",
        "question": "600000 最近走势怎么看？",
        "symbols": ["600000"],
        "channel": "console",
    }
    outcome = sched.run_job(job, root=tmp_path, now=None)

    assert outcome["status"] == "ok"
    assert outcome["answered"] == 1
    card = Path(outcome["path"])
    assert card.is_file()
    doc = json.loads(card.read_text(encoding="utf-8"))
    assert doc["answers"][0]["symbol"] == "600000"
    assert doc["answers"][0]["reply"] == patched["reply"]

    items = inbox.list_items(root=tmp_path)
    assert items and items[0]["id"] == outcome["inbox_id"]
    assert items[0]["kind"] == "agent_research"
    assert patched["sent"]["title"].startswith("定时研究")


def test_agent_research_partial_failure_degrades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = {"n": 0}

    async def _ask_agent(**kwargs: Any) -> dict[str, Any]:
        calls["n"] += 1
        if kwargs.get("symbol") == "000001":
            raise RuntimeError("模型超时")
        return {
            "reply": "ok",
            "model": "m",
            "fetched_at": "x",
            "metrics": {},
            "sources": [],
            "used_tools": False,
        }

    monkeypatch.setattr(assistant_module, "ask_agent", _ask_agent)
    monkeypatch.setattr("ladderstock.watch.notifier.send", lambda *a, **k: _Delivery())
    job = {
        "id": "agent_research_close",
        "name": "定时研究",
        "kind": "agent_research",
        "question": "两只都看",
        "symbols": ["600000", "000001"],
        "channel": "console",
    }
    outcome = sched.run_job(job, root=tmp_path)
    assert calls["n"] == 2
    assert outcome["status"] == "degraded"
    assert outcome["failed"] and outcome["failed"][0]["symbol"] == "000001"


# --------------------------------------------------------------------------- #
# watch_alert
# --------------------------------------------------------------------------- #


def test_watch_alert_skips_without_watchlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(watchlist_store, "list_watch", lambda root=None: [])
    outcome = sched.run_job({"id": "watch_alert_morning", "name": "盯盘", "kind": "watch_alert"}, root=tmp_path)
    assert outcome["status"] == "skipped"


def test_watch_alert_skips_without_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(watchlist_store, "list_watch", lambda root=None: [{"code": "600000"}])
    outcome = sched.run_job({"id": "watch_alert_morning", "name": "盯盘", "kind": "watch_alert"}, root=tmp_path)
    assert outcome["status"] == "skipped"
    assert "symbol_basic" in outcome["detail"]


def test_watch_alert_hits_threshold_and_labels_snapshot_date(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, patched: dict[str, Any]
) -> None:
    day = date(2026, 11, 16)
    duckdb_store.write_dataset(
        "symbol_basic",
        [
            {"name": "浦发银行", "market": "SH", "price": 10.68, "prev_close": 10.4, "volume": 1, "active": True},
        ],
        source="test:tencent_quote_batch",
        default_trade_date=day,
        root=tmp_path,
    )
    monkeypatch.setattr(watchlist_store, "list_watch", lambda root=None: [{"code": "600000"}])

    # 字典落库走 replace 整日分区，第二次写入带 code 的记录才是可查的行
    duckdb_store.write_dataset(
        "symbol_basic",
        [
            {
                "code": "600000",
                "name": "浦发银行",
                "market": "SH",
                "price": 10.68,
                "prev_close": 10.4,
                "volume": 1,
                "active": True,
            }
        ],
        source="test:tencent_quote_batch",
        default_trade_date=day,
        root=tmp_path,
    )
    monkeypatch.setattr(watchlist_store, "list_watch", lambda root=None: [{"code": "600000"}])

    outcome = sched.run_job(
        {"id": "watch_alert_morning", "name": "盯盘提醒", "kind": "watch_alert", "alert_pct": 1, "channel": "console"},
        root=tmp_path,
    )
    assert outcome["status"] == "ok"
    assert outcome["alerted"] == 1
    items = inbox.list_items(root=tmp_path)
    assert items and items[0]["kind"] == "watch_alert"
    body = "\n".join(items[0]["lines"])
    assert "数据日期 2026-11-16" in body  # 不冒充分时实时
    assert "↑ +2.69%" in body
