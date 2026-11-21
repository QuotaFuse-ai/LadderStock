"""公告候选前置筛：分母 = 池 ∪ 池外异动，且池外票不进池。

这一组用例锁住三条口径：
1. 分母是并集（池内异动不算增项，池外异动才把分母撑大）；
2. 阈值判断与 `watch_alert` 同口径（±5%，边界含等于）；
3. 没有行情快照就 `fallback`，采集侧据此回退全池——不能把采集变成 0 只。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ladderstock.news import candidates as news_candidates


@pytest.fixture()
def focus(monkeypatch):
    """关注域 = 池 ∪ 自选 ∪ 持仓；这里用自选注入"池外票"。

    完全不在关注域的票（哪怕全市场涨停）不进候选——池外异动只能来自
    你自己盯着的东西，否则前置筛会退化成全市场扫描。
    """
    watch: dict[str, list[str]] = {"codes": []}
    monkeypatch.setattr(
        "ladderstock.journal.watchlist.list_watch",
        lambda root=None: [{"code": code} for code in watch["codes"]],
    )
    monkeypatch.setattr(
        "ladderstock.journal.position.list_positions", lambda root=None: []
    )
    return watch


def _market(monkeypatch, rows, day="2026-11-20"):
    import datetime as dt

    monkeypatch.setattr(
        "ladderstock.data.symbol_universe.latest_trade_date",
        lambda root=None: dt.date.fromisoformat(day),
    )
    monkeypatch.setattr(
        "ladderstock.data.duckdb_store.query_dataset",
        lambda dataset, trade_date, root=None: list(rows),
    )


def _row(code: str, price: float, prev: float, name: str = "") -> dict:
    return {"code": code, "name": name or code, "price": price, "prev_close": prev}


def test_union_denominator_counts_only_outside_pool(focus, monkeypatch) -> None:
    pool = [f"6{index:05d}" for index in range(5)]
    focus["codes"] = ["300750", "688981", "000001"]
    rows = [
        _row(code, 10.0, 10.0) for code in pool
    ] + [
        # 池内异动：只算进 movers，不撑大分母
        _row(pool[0], 10.6, 10.0),
        # 池外异动两只（在自选里）：分母 5 → 7
        _row("300750", 100.0, 92.0, "宁德时代"),
        _row("688981", 50.0, 52.9, "中芯国际"),
        # 池外但没异动：不进名单
        _row("000001", 12.0, 11.9),
        # 完全不在关注域、却大涨：不采（前置筛不是全市场扫描）
        _row("601999", 10.0, 13.0),
    ]
    _market(monkeypatch, rows)

    result = news_candidates.announcement_candidates(pool, threshold=5.0)

    assert result["pool_size"] == 5
    assert result["fallback"] is False
    assert sorted(result["extra"]) == ["300750", "688981"]
    assert len(result["union"]) == 7
    assert {item["code"] for item in result["movers"]} == {pool[0], "300750", "688981"}
    assert "池 5 只" in result["note"] and "池外异动 2 只" in result["note"] and "= 7 只" in result["note"]
    # 池外票只进当天分母，不进池：pool_size 仍是 5
    assert result["union"].count("300750") == 1


def test_threshold_boundary_is_inclusive(focus, monkeypatch) -> None:
    pool = ["600000"]
    focus["codes"] = ["600009", "600018"]
    rows = [
        _row("600000", 10.0, 10.0),
        _row("600009", 10.0, 9.53),  # +4.93%
        _row("600018", 10.0, 9.5),  # +5.26%
    ]
    _market(monkeypatch, rows)

    result = news_candidates.announcement_candidates(pool, threshold=5.0)

    assert [item["code"] for item in result["movers"]] == ["600018"]


def test_missing_snapshot_falls_back_to_full_pool(focus, monkeypatch) -> None:
    pool = ["600000", "600009"]
    monkeypatch.setattr(
        "ladderstock.data.symbol_universe.latest_trade_date", lambda root=None: None
    )

    result = news_candidates.announcement_candidates(pool, threshold=5.0)

    assert result["fallback"] is True
    assert result["union"] == sorted(pool)
    assert "回退全池" in result["note"]
    assert "本机没有报价快照" in result["fallback_reason"]


def test_stale_snapshot_falls_back_instead_of_pretending(focus, monkeypatch) -> None:
    """快照太旧：不拿上次的价格假装「当日异动」，直接回退全池。"""
    import datetime as dt

    pool = ["600000", "600009"]
    focus["codes"] = ["300750"]
    rows = [_row("600000", 10.0, 10.0), _row("600009", 10.0, 10.6), _row("300750", 100.0, 92.0)]
    _market(monkeypatch, rows, day="2026-11-01")

    result = news_candidates.announcement_candidates(
        pool, threshold=5.0, today=dt.date(2026, 11, 21)
    )

    assert result["fallback"] is True
    assert result["union"] == sorted(pool)
    assert "过期" in result["fallback_reason"]
    assert result["snapshot_day"] == "2026-11-01"


def test_sidecar_roundtrip(tmp_path: Path) -> None:
    payload = {
        "day": "2026-11-20",
        "pool_size": 85,
        "union_size": 87,
        "extra": ["300750"],
        "covered": 12,
        "collected": {"600000": 3},
    }
    path = news_candidates.write_candidates(tmp_path, payload)
    assert path is not None and path.is_file()

    doc = news_candidates.read_candidates(tmp_path, "2026-11-20")
    assert doc["union_size"] == 87 and doc["covered"] == 12
    assert news_candidates.read_candidates(tmp_path, "2026-11-21") is None
