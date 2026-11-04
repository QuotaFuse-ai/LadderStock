"""QUOT-373 / DEC-054：盘中批量槽存储的写、读、清理与 archive 读侧接线。"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

from ladderstock.core import intraday
from ladderstock.data import archive

DATASET = "fund_flow_realtime"  # boundary.py:439 已登记，owner_of 可过


def _records(day: str, code: str = "600000") -> list[dict[str, object]]:
    return [{"code": code, "name": "浦发银行", "trade_date": day, "main_net": 12345.0}]


def test_write_batch_slot_creates_and_refuses_same_minute_overwrite(tmp_path: Path) -> None:
    now = datetime(2026, 11, 4, 10, 30, 0)
    first = intraday.write_batch_slot(
        DATASET, _records("2026-11-04"), root=tmp_path, now=now
    )
    assert first["written"] is True
    assert first["slot"] == "10:30"
    assert first["path"] == tmp_path / "intraday" / "2026-11-04" / DATASET / "1030.json"

    same_minute = intraday.write_batch_slot(
        DATASET, _records("2026-11-04", "600519"), root=tmp_path, now=now
    )
    assert same_minute["written"] is False
    payload = json.loads(first["path"].read_text(encoding="utf-8"))
    assert payload["records"][0]["code"] == "600000"  # 先到先得，未被第二轮覆盖

    next_minute = intraday.write_batch_slot(
        DATASET, _records("2026-11-04"), root=tmp_path, now=now + timedelta(minutes=1)
    )
    assert next_minute["written"] is True
    assert next_minute["slot"] == "10:31"


def test_write_batch_slot_skips_empty_records(tmp_path: Path) -> None:
    result = intraday.write_batch_slot(
        DATASET, [], root=tmp_path, now=datetime(2026, 11, 4, 10, 30, 0)
    )
    assert result["written"] is False
    assert result["rows"] == 0
    assert not result["path"].exists()


def test_latest_batch_slot_returns_newest(tmp_path: Path) -> None:
    base = datetime(2026, 11, 4, 14, 0, 0)
    for minute in (0, 1, 2):
        intraday.write_batch_slot(
            DATASET, _records("2026-11-04"), root=tmp_path, now=base + timedelta(minutes=minute)
        )
    slot = intraday.latest_batch_slot(DATASET, date(2026, 11, 4), root=tmp_path)
    assert slot is not None
    assert slot["slot"] == "14:02"
    assert slot["records"][0]["code"] == "600000"


def test_latest_batch_slot_none_when_missing(tmp_path: Path) -> None:
    assert intraday.latest_batch_slot(DATASET, date(2026, 11, 4), root=tmp_path) is None


def test_snapshot_reads_latest_slot_without_archive(tmp_path: Path) -> None:
    """读侧接线：有槽走 intraday-slot，且全程不产生 parquet / duckdb 文件。"""
    day = date(2026, 11, 4)
    intraday.write_batch_slot(
        DATASET, _records("2026-11-04"), root=tmp_path, now=datetime(2026, 11, 4, 14, 0, 0)
    )
    intraday.write_batch_slot(
        DATASET, _records("2026-11-04"), root=tmp_path, now=datetime(2026, 11, 4, 14, 1, 0)
    )
    snap = archive.snapshot(DATASET, day, root=tmp_path)
    assert snap.backend == "intraday-slot"
    assert len(snap.records) == 1
    assert snap.records[0]["code"] == "600000"
    assert snap.as_of == day
    assert snap.cached_at is not None
    # 关键验收：archive 侧文件数 == 0（盘中的刷新不触发 parquet 重写）
    archive_dir = tmp_path / "archive" / "2026" / "11" / "04"
    assert not archive_dir.exists() or not any(archive_dir.iterdir())


def test_snapshot_falls_back_when_only_slot_dir_empty(tmp_path: Path) -> None:
    """槽目录存在但没有可读槽 → 走原有链路（none backend），不崩。"""
    day = date(2026, 11, 4)
    intraday.batch_slot_dir(DATASET, day, root=tmp_path).mkdir(parents=True)
    snap = archive.snapshot(DATASET, day, root=tmp_path)
    assert snap.backend == "none"
    assert snap.records == []


def test_purge_batch_slots_keeps_recent_days(tmp_path: Path) -> None:
    today = date(2026, 11, 4)
    for offset in (0, 1, 2, 5):
        day = today - timedelta(days=offset)
        intraday.write_batch_slot(
            DATASET,
            _records(day.isoformat()),
            root=tmp_path,
            now=datetime(day.year, day.month, day.day, 10, 0, 0),
        )
    removed = intraday.purge_batch_slots(root=tmp_path, today=today, keep_days=3)
    # offset 0/1/2 保留，offset 5 被清
    assert any("2026-10-30" in p for p in removed)
    assert (tmp_path / "intraday" / "2026-11-04" / DATASET).is_dir()
    assert (tmp_path / "intraday" / "2026-11-03" / DATASET).is_dir()
    assert (tmp_path / "intraday" / "2026-11-02" / DATASET).is_dir()
    assert not (tmp_path / "intraday" / "2026-10-30").exists()


def test_purge_spares_spot_quote_snapshot_files(tmp_path: Path) -> None:
    """spot_quote 的 `<HHMM>.json` 与 status.json 不在 <dataset>/ 子目录，不受清理影响。"""
    today = date(2026, 11, 4)
    old_day = today - timedelta(days=9)
    spot_dir = tmp_path / "intraday" / old_day.isoformat()
    spot_dir.mkdir(parents=True)
    spot_file = spot_dir / "0925.json"
    spot_file.write_text("{}", encoding="utf-8")

    removed = intraday.purge_batch_slots(root=tmp_path, today=today, keep_days=3)
    assert removed == []  # 没有 <dataset>/ 子目录可删
    assert spot_file.exists()  # spot_quote 快照原样保留
