"""`_run_watch_live` 的采集接线：现取实时价 → 顺手落成当日分钟样本。

盯盘链路的缺口就在这一格：扫描器只读 `data/minute_bars/<date>.jsonl`，而整仓此前
没有写入者，所以 `/watch` 永远「还没有取过数」。这两条用例钉住接线本身——
不联网（假 provider）、不碰真实自选（假清单），只验证「取到的价变成了当天的样本」。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from ladderstock.cli.schedule import _run_watch_live

CST = ZoneInfo("Asia/Shanghai")
JOB = {
    "id": "test-watch-live",
    "name": "测试盯盘",
    "alert_pct": 3.0,
    "move_pct": 1.0,
    "min_gap_min": 30.0,
    "channel": "console",
    "auto_research": False,
}


@pytest.fixture()
def fake_quote(monkeypatch: pytest.MonkeyPatch) -> None:
    """1 只自选、报价平昨收——不触发提醒，只走采集与落盘这一段。"""

    class _Outcome:
        envelope = SimpleNamespace(
            payload={
                "records": [
                    {"code": "600000", "name": "浦发银行", "price": 10.0, "prev_close": 10.0}
                ]
            }
        )

    class _Registry:
        def __init__(self, root: Any = None) -> None:
            self.root = root

        def fetch_live(self, *_args: Any, **_kwargs: Any) -> _Outcome:
            return _Outcome()

    monkeypatch.setattr("ladderstock.data.provider_registry.ProviderRegistry", _Registry)
    monkeypatch.setattr(
        "ladderstock.journal.watchlist.list_watch",
        lambda root=None: [{"code": "600000"}],
    )
    monkeypatch.setattr(
        "ladderstock.journal.position.list_positions",
        lambda **_: [],
    )


def test_in_session_run_writes_minute_bars(tmp_path: Path, fake_quote: None) -> None:
    result = _run_watch_live(
        JOB, now=datetime(2026, 11, 17, 10, 0, tzinfo=CST), timezone="Asia/Shanghai", root=tmp_path
    )

    assert result["status"] == "ok"
    assert result["alerted"] == 0
    assert "分钟样本 1 条" in result["detail"]

    bars_file = tmp_path / "minute_bars" / "2026-11-17.jsonl"
    assert bars_file.is_file()
    rows = [json.loads(line) for line in bars_file.read_text(encoding="utf-8").splitlines() if line]
    assert len(rows) == 1
    assert rows[0]["code"] == "600000"
    assert rows[0]["ts"].startswith("2026-11-17T10:00")
    assert rows[0]["price"] == pytest.approx(10.0)

    assert (tmp_path / "watch_live_state.json").is_file()


def test_out_of_session_run_writes_nothing(tmp_path: Path, fake_quote: None) -> None:
    """盘后 spot_quote 是收盘价：不采集，也就不会生产假盘中样本。"""
    result = _run_watch_live(
        JOB, now=datetime(2026, 11, 17, 21, 30, tzinfo=CST), timezone="Asia/Shanghai", root=tmp_path
    )

    assert result["status"] == "skipped"
    assert "非交易时段" in result["detail"]
    assert not (tmp_path / "minute_bars" / "2026-11-17.jsonl").exists()


def test_limit_price_from_today_limit_pool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """涨停池只提供「涨停价」：样本里补上 limit_price 供封板判定，不额外产行。"""
    from types import SimpleNamespace

    class _Outcome:
        envelope = SimpleNamespace(
            payload={"records": [{"code": "600000", "price": 10.0, "prev_close": 10.0}]}
        )

    class _Registry:
        def __init__(self, root: Any = None) -> None:
            self.root = root

        def fetch_live(self, *_args: Any, **_kwargs: Any) -> _Outcome:
            return _Outcome()

    monkeypatch.setattr("ladderstock.data.provider_registry.ProviderRegistry", _Registry)
    monkeypatch.setattr(
        "ladderstock.journal.watchlist.list_watch", lambda root=None: [{"code": "600000"}]
    )
    monkeypatch.setattr("ladderstock.journal.position.list_positions", lambda **_: [])
    # 当日涨停池：600000 一字涨停价 11.00；600999 是跌到 4% 的脏行，必须被丢掉
    monkeypatch.setattr(
        "ladderstock.data.archive.snapshot",
        lambda *_a, **_k: SimpleNamespace(
            records=[
                {"code": "600000", "price": 11.0, "change_pct": 10.01},
                {"code": "600999", "price": 9.4, "change_pct": 4.0},
            ]
        ),
    )

    result = _run_watch_live(
        JOB, now=datetime(2026, 11, 17, 10, 0, tzinfo=CST), timezone="Asia/Shanghai", root=tmp_path
    )

    assert result["status"] == "ok"
    rows = [
        json.loads(line)
        for line in (tmp_path / "minute_bars" / "2026-11-17.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    assert len(rows) == 1  # 涨停池不产生额外样本行
    assert rows[0]["limit_price"] == pytest.approx(11.0)


def test_no_limit_price_when_stock_not_in_limit_pool(
    tmp_path: Path, fake_quote: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """不在当日涨停池里就不写涨停价：封板判不了就如实留空，不按 10% 猜。"""
    from types import SimpleNamespace

    monkeypatch.setattr(
        "ladderstock.data.archive.snapshot",
        lambda *_a, **_k: SimpleNamespace(records=[]),
    )

    result = _run_watch_live(
        JOB, now=datetime(2026, 11, 17, 10, 0, tzinfo=CST), timezone="Asia/Shanghai", root=tmp_path
    )

    assert result["status"] == "ok"
    rows = [
        json.loads(line)
        for line in (tmp_path / "minute_bars" / "2026-11-17.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    assert rows and "limit_price" not in rows[0]


def test_positions_join_the_bar_universe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """持仓票与自选一起取数、一起落样本：持仓的异动更需要被看到。"""
    seen: dict[str, Any] = {}

    class _Outcome:
        envelope = SimpleNamespace(
            payload={
                "records": [
                    {"code": "600000", "price": 10.0, "prev_close": 10.0},
                    {"code": "000001", "price": 8.0, "prev_close": 8.0},
                ]
            }
        )

    class _Registry:
        def __init__(self, root: Any = None) -> None:
            self.root = root

        def fetch_live(self, tool: str, **_kwargs: Any) -> _Outcome:
            seen["codes"] = (_kwargs.get("params") or {}).get("codes")
            return _Outcome()

    monkeypatch.setattr("ladderstock.data.provider_registry.ProviderRegistry", _Registry)
    monkeypatch.setattr(
        "ladderstock.journal.watchlist.list_watch", lambda root=None: [{"code": "600000"}]
    )
    monkeypatch.setattr(
        "ladderstock.journal.position.list_positions", lambda **_: [{"code": "000001"}]
    )

    result = _run_watch_live(
        JOB, now=datetime(2026, 11, 17, 10, 0, tzinfo=CST), timezone="Asia/Shanghai", root=tmp_path
    )

    assert result["status"] == "ok"
    assert set(seen["codes"].split(",")) == {"sh600000", "sz000001"}
    rows = [
        json.loads(line)
        for line in (tmp_path / "minute_bars" / "2026-11-17.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    assert {row["code"] for row in rows} == {"600000", "000001"}


def test_empty_watchlist_falls_back_to_limit_pool_samples(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """新用户没有自选时，用最近交易日涨停池兜底采样本——否则 /watch 永远等不到数据。

    两条硬约束：兜底票**只落样本、不逐票提醒**；涨停价在当日池未同步时按昨收推算，
    创业板的 20% 幅度必须按板块算，不能一律 10%。
    """
    from types import SimpleNamespace

    class _Outcome:
        envelope = SimpleNamespace(
            payload={
                "records": [
                    # 600000 涨 5%（越过 3% 阈值）：兜底票也不该产生提醒
                    {"code": "600000", "name": "浦发银行", "price": 10.5, "prev_close": 10.0},
                    {"code": "300001", "name": "特锐德", "price": 12.0, "prev_close": 10.0},
                ]
            }
        )

    class _Registry:
        def __init__(self, root: Any = None) -> None:
            self.root = root

        def fetch_live(self, *_args: Any, **_kwargs: Any) -> _Outcome:
            return _Outcome()

    monkeypatch.setattr("ladderstock.data.provider_registry.ProviderRegistry", _Registry)
    monkeypatch.setattr("ladderstock.journal.watchlist.list_watch", lambda root=None: [])
    monkeypatch.setattr("ladderstock.journal.position.list_positions", lambda **_: [])
    # 当日涨停池没有，最近一个有缓存的是 11-16
    monkeypatch.setattr(
        "ladderstock.data.archive.snapshot",
        lambda dataset, day, root=None: (
            SimpleNamespace(
                records=[
                    {"code": "600000", "name": "浦发银行"},
                    {"code": "300001", "name": "特锐德"},
                ]
            )
            if dataset == "limit_pool" and day == "2026-11-16"
            else (_ for _ in ()).throw(RuntimeError("该日没有缓存"))
        ),
    )

    result = _run_watch_live(
        JOB, now=datetime(2026, 11, 17, 10, 0, tzinfo=CST), timezone="Asia/Shanghai", root=tmp_path
    )

    assert result["status"] == "ok"
    assert result["alerted"] == 0, "兜底票只落样本，不能逐票推提醒"
    assert result["sample_pool_day"] == "2026-11-16"
    assert result["sample_only"] == 2
    assert "兜底采集" in result["detail"]

    rows = {
        row["code"]: row
        for row in (
            json.loads(line)
            for line in (tmp_path / "minute_bars" / "2026-11-17.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line
        )
    }
    assert set(rows) == {"600000", "300001"}
    # 主板 10%：10.0 → 11.00；创业板 20%：10.0 → 12.00
    assert rows["600000"]["limit_price"] == pytest.approx(11.0)
    assert rows["300001"]["limit_price"] == pytest.approx(12.0)


def test_no_watchlist_and_no_pool_stays_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """连涨停池都没有时明确 skip，并说清为什么——不写空文件冒充采集。"""
    monkeypatch.setattr("ladderstock.journal.watchlist.list_watch", lambda root=None: [])
    monkeypatch.setattr("ladderstock.journal.position.list_positions", lambda **_: [])
    monkeypatch.setattr(
        "ladderstock.data.archive.snapshot",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("没有缓存")),
    )

    result = _run_watch_live(
        JOB, now=datetime(2026, 11, 17, 10, 0, tzinfo=CST), timezone="Asia/Shanghai", root=tmp_path
    )

    assert result["status"] == "skipped"
    assert "最近的涨停池也读不到" in result["detail"]
    assert not (tmp_path / "minute_bars" / "2026-11-17.jsonl").exists()


@pytest.mark.parametrize(
    ("code", "name", "expected"),
    [
        ("600000", "浦发银行", 10.0),
        ("300001", "特锐德", 20.0),
        ("688001", "华兴源创", 20.0),
        ("830799", "艾融软件", 30.0),
        ("600001", "ST某某", 5.0),
    ],
)
def test_board_limit_pct_matches_board(code: str, name: str, expected: float) -> None:
    from ladderstock.cli.schedule import _board_limit_pct

    assert _board_limit_pct(code, name) == expected
