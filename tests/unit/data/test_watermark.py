"""水位文件：只进不退、原子落盘、运行记录有界（DEC-007 §2.3）。"""

from __future__ import annotations

import json

import pytest

from ladderstock.data import watermark


def test_missing_state_reads_as_empty(tmp_path) -> None:
    store = watermark.WatermarkStore(root=tmp_path)
    assert store.value("daily_k") is None
    assert store.entries() == []
    assert store.runs() == []


def test_advance_persists_and_is_visible_to_new_instance(tmp_path) -> None:
    store = watermark.WatermarkStore(root=tmp_path)
    entry = store.advance("daily_k", "2026-10-24", rows=2, source="tickhub_kline")

    assert entry.value == "2026-10-24"
    assert entry.rows == 2
    assert entry.source == "tickhub_kline"

    reopened = watermark.WatermarkStore(root=tmp_path)
    assert reopened.value("daily_k") == "2026-10-24"
    assert watermark.watermark_path(tmp_path).is_file()


def test_watermark_never_goes_backwards(tmp_path) -> None:
    store = watermark.WatermarkStore(root=tmp_path)
    store.advance("daily_k", "2026-10-24")

    kept = store.advance("daily_k", "2026-10-20", rows=1, source="tickhub_tdx_package")

    assert kept.value == "2026-10-24"
    assert store.value("daily_k") == "2026-10-24"
    # 备源拿到较早一天的数据也不能把断点拉回去，否则重跑会重复拉历史
    assert watermark.WatermarkStore(root=tmp_path).value("daily_k") == "2026-10-24"


def test_advance_rejects_empty_value(tmp_path) -> None:
    store = watermark.WatermarkStore(root=tmp_path)
    with pytest.raises(watermark.WatermarkError):
        store.advance("daily_k", "")


def test_advance_same_value_refreshes_metadata(tmp_path) -> None:
    store = watermark.WatermarkStore(root=tmp_path)
    store.advance("daily_k", "2026-10-24", rows=2, source="tickhub_kline")
    again = store.advance("daily_k", "2026-10-24", rows=7, source="tickhub_kline")

    assert again.value == "2026-10-24"
    assert again.rows == 7


def test_runs_are_bounded_and_ordered(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(watermark, "MAX_RUNS", 3)
    store = watermark.WatermarkStore(root=tmp_path)
    for index in range(5):
        store.record_run(
            "daily_k",
            mode="incremental",
            status="ok",
            rows=index,
            watermark_after="2026-10-24",
        )

    runs = store.runs(limit=0)
    assert len(runs) == 3
    assert [run["rows"] for run in runs] == [2, 3, 4]


def test_status_lists_datasets_without_watermark(tmp_path) -> None:
    store = watermark.WatermarkStore(root=tmp_path)
    store.advance("daily_k", "2026-10-24")
    store.record_run("daily_k", mode="incremental", status="ok")

    payload = store.status(limit=5)

    assert payload["state_file"].endswith("watermark.json")
    assert [row["dataset"] for row in payload["watermarks"]] == ["daily_k"]
    assert "daily_k" not in payload["datasets_without_watermark"]
    assert "sentiment" in payload["datasets_without_watermark"]
    assert len(payload["recent_runs"]) == 1


def test_corrupt_state_file_raises(tmp_path) -> None:
    path = watermark.watermark_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json", encoding="utf-8")

    with pytest.raises(watermark.WatermarkError):
        watermark.WatermarkStore(root=tmp_path)


def test_state_file_is_valid_utf8_json(tmp_path) -> None:
    store = watermark.WatermarkStore(root=tmp_path)
    store.advance("daily_k", "2026-10-24", source="tickhub_kline")

    raw = watermark.watermark_path(tmp_path).read_text(encoding="utf-8")
    data = json.loads(raw)
    assert data["version"] == watermark.STATE_VERSION
    assert data["watermarks"]["daily_k"]["value"] == "2026-10-24"
    # 原子写：tmp 文件不会残留
    assert not list(path for path in watermark.watermark_path(tmp_path).parent.glob("*.tmp"))
