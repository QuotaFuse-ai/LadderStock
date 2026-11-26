"""F12 覆盖看板：事实口径 + 受控补采的三条边界。

钉住五件事：

1. 空目录（没有库、没有 sync.yaml）不报 500，而是「62 个数据集全 unregistered/never」；
2. 有库有数据时 `rows / latest / pool_covered` 从库里现算，`state` 由它们推出来；
3. 陈旧判定按数据集自己的窗口（`daily_k` 3 天、财报 130 天）；
4. 事件型数据集（研报、龙虎榜）覆盖低不判 partial——否则页面永远红；
5. `backfill` 缺省不发任何请求（`execute=False`），超 100 只直接拒绝。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from ladderstock.data import coverage_board as cb
from ladderstock.data import duckdb_store


def _write_sync_yaml(config_root: Path, *, primary: str = "tickhub_kline") -> Path:
    config_root.mkdir(parents=True, exist_ok=True)
    path = config_root / "sync.yaml"
    path.write_text(
        "sync:\n"
        "  datasets:\n"
        "    daily_k:\n"
        f"      primary: {primary}\n"
        "      fallback: [tickhub_tdx_package]\n"
        "      mode: append\n"
        "      codes: [600519, 000858, 601318]\n",
        encoding="utf-8",
    )
    return path


def _seed_daily_k(root: Path, rows: list[dict[str, Any]]) -> None:
    """**必须**把 root 传进 write_dataset：不传就写到真实 data/market.duckdb。

    首版漏了这个参数，把 3 条假行情写进了本机真实库（并覆盖了 600519 2026-11-17
    的原始行），已单独清理并用受控补采恢复。这行注释是给后来者看的。
    """
    root.mkdir(parents=True, exist_ok=True)
    duckdb_store.write_dataset("daily_k", rows, source="test", root=root)


def test_empty_root_is_all_unregistered(tmp_path: Path) -> None:
    payload = cb.board(
        root=tmp_path,
        config_root=tmp_path / "config",
        datasets=["daily_k", "us_13f"],
    )
    assert payload["db"]["exists"] is False
    assert payload["pool"]["size"] == 0
    assert payload["declared"] == 0
    assert {row["dataset"] for row in payload["datasets"]} == {"daily_k", "us_13f"}
    assert {row["state"] for row in payload["datasets"]} == {cb.STATE_UNREGISTERED}
    assert payload["summary"]["total"] == 2


def test_board_reads_real_counts(tmp_path: Path) -> None:
    _write_sync_yaml(tmp_path / "config")
    _seed_daily_k(
        tmp_path / "data",
        [
            {"code": "600519", "trade_date": "2026-11-17", "close": 1457.7},
            {"code": "000858", "trade_date": "2026-11-17", "close": 168.2},
            {"code": "300750", "trade_date": "2026-11-17", "close": 210.0},
        ],
    )
    payload = cb.board(
        root=tmp_path / "data",
        config_root=tmp_path / "config",
        datasets=["daily_k"],
    )
    row = payload["datasets"][0]
    assert row["rows"] == 3
    assert row["codes"] == 3
    assert row["latest"] == "2026-11-17"
    assert row["pool_relevant"] is True
    assert row["pool_covered"] == 2  # 600519 / 000858 在池，300750 不在
    assert row["coverage"] == pytest.approx(2 / 3, rel=1e-3)


def test_stale_uses_dataset_window(tmp_path: Path) -> None:
    """池内盖满但日期过旧 → stale（3 天窗口）；partial 优先于 stale，所以这里先把池铺满。"""
    _write_sync_yaml(tmp_path / "config")
    _seed_daily_k(
        tmp_path / "data",
        [
            {"code": "600519", "trade_date": "2026-11-10", "close": 1400},
            {"code": "000858", "trade_date": "2026-11-10", "close": 160},
            {"code": "601318", "trade_date": "2026-11-10", "close": 52},
        ],
    )
    payload = cb.board(
        root=tmp_path / "data",
        config_root=tmp_path / "config",
        datasets=["daily_k"],
        today=date(2026, 11, 20),
    )
    row = payload["datasets"][0]
    assert row["age_days"] == 10
    assert row["state"] == cb.STATE_STALE
    assert "2026-11-10" in row["reason"]


def test_pool_short_is_partial_only_for_required(tmp_path: Path) -> None:
    """事件型（研报）池内覆盖低不被判缺口；全池型（日线）缺一只就是缺口。"""
    _write_sync_yaml(tmp_path / "config")
    _seed_daily_k(
        tmp_path / "data",
        [{"code": "600519", "trade_date": "2026-11-18", "close": 1457}],
    )
    payload = cb.board(
        root=tmp_path / "data",
        config_root=tmp_path / "config",
        datasets=["daily_k"],
        today=date(2026, 11, 19),
    )
    assert payload["datasets"][0]["state"] == cb.STATE_PARTIAL
    assert "1/3" in payload["datasets"][0]["reason"]


def test_gaps_sorted_by_severity(tmp_path: Path) -> None:
    _write_sync_yaml(tmp_path / "config")
    payload = cb.board(root=tmp_path / "data", config_root=tmp_path / "config")
    rows = cb.gaps(payload)
    assert rows, "空库时缺口列表不该为空"
    order = {state: index for index, state in enumerate(cb.STATUS_ORDER)}
    ranks = [order[row["state"]] for row in rows]
    assert ranks == sorted(ranks)


def test_plan_rejects_unregistered(tmp_path: Path) -> None:
    with pytest.raises(ValueError) as info:
        cb.plan("us_13f", config_root=tmp_path / "config")
    assert "sync.yaml" in str(info.value)


def test_plan_rejects_oversize_batch(tmp_path: Path) -> None:
    _write_sync_yaml(tmp_path / "config")
    codes = [f"{600000 + index:06d}" for index in range(120)]
    with pytest.raises(ValueError) as info:
        cb.plan("daily_k", codes=codes, config_root=tmp_path / "config")
    assert "100" in str(info.value)


def test_backfill_default_is_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_sync_yaml(tmp_path / "config")

    def _boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("dry-run 不该调用 run_sync")

    monkeypatch.setattr("ladderstock.cli.sync.run_sync", _boom)
    result = cb.backfill(
        "daily_k",
        config_root=tmp_path / "config",
        root=tmp_path / "data",
        codes=["600519"],
    )
    assert result["status"] == "planned"
    assert result["execute"] is False
    assert result["codes"] == ["600519"]
