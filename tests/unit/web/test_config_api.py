"""配置只读视图的单测：根目录纠正、分区扫描、缺省不崩。

这几个断言守的都是一次真实踩坑：`server.build_app` 传进来的是 **data 目录**，
早期实现直接拿它去读 `data/config/sync.yaml`，接口 200 但返回空表，
页面显示"配置里数据集 0"。所以这里第一条就是根目录纠正。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from ladderstock.web import config_api


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_project_root_recovers_from_data_dir(tmp_path: Path) -> None:
    project = tmp_path / "LadderStock"
    _write(project / "config" / "sync.yaml", "sync:\n  datasets: {}\n")
    data = project / "data"
    data.mkdir()
    assert config_api._project_root(data) == project
    assert config_api._project_root(project) == project


def test_data_root_stays_on_data_dir(tmp_path: Path) -> None:
    project = tmp_path / "LadderStock"
    _write(project / "config" / "sync.yaml", "sync:\n  datasets: {}\n")
    data = project / "data"
    (data / "archive").mkdir(parents=True)
    assert config_api._data_root(data, project) == data
    assert config_api._data_root(None, project) == project / "data"


def test_sync_view_reads_pool_and_finds_partitions(tmp_path: Path) -> None:
    project = tmp_path / "LadderStock"
    _write(
        project / "config" / "sync.yaml",
        "sync:\n"
        "  datasets:\n"
        "    daily_k:\n"
        "      primary: tickhub_kline\n"
        "      fallback: [tickhub_tdx_package]\n"
        "      codes: ['600000', '600519', '000001']\n"
        "    research_report:\n"
        "      primary: tickhub_research_report\n"
        "      codes_from: symbol_basic\n",
    )
    data = project / "data"
    day = data / "archive" / "2026" / "11" / "20"
    day.mkdir(parents=True)
    (day / "daily_k.parquet").write_bytes(b"")

    view = config_api._sync_view(project, data, dt.date(2026, 11, 20))

    assert view["count"] == 2
    assert view["covered"] == 1
    assert view["base_pool"] == 3
    by_name = {row["name"]: row for row in view["datasets"]}
    assert by_name["daily_k"]["latest_day"] == "2026-11-20"
    assert by_name["daily_k"]["today_ready"] is True
    assert by_name["daily_k"]["fallback"] == ["tickhub_tdx_package"]
    assert by_name["research_report"]["latest_day"] is None
    assert by_name["research_report"]["stale_days"] is None
    assert by_name["research_report"]["codes_from"] == "symbol_basic"


def test_backfill_view_survives_missing_config(tmp_path: Path) -> None:
    project = tmp_path / "LadderStock"
    (project / "data").mkdir(parents=True)
    view = config_api._backfill_view(project, project / "data")
    assert view["ok"] is True
    assert view["universe"] == 0
    assert view["latest_report"] is None
