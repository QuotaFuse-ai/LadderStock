"""`watch/status.py` 的唯一判据：四种读取结果 + 截断 + 人话摘要。

全部离线：状态文件在 tmp 里现造，不读仓库 `data/`（DEC-001）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ladderstock.watch import status as status_file
from ladderstock.watch.status import (
    STATE_CORRUPT,
    STATE_MISSING,
    STATE_OK,
    STATE_UNSUPPORTED,
    read_status_document,
    recent_events,
    schema_problems,
    status_detail,
    status_path,
)


def document(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "running": True,
        "mode": "offline-single-pass",
        "date": "2026-10-22",
        "threshold": 3.0,
        "started_at": "2026-10-22T09:35:00",
        "last_run_at": "2026-10-22T09:35:00",
        "bars": 5,
        "source": "data/minute_bars/2026-10-22.jsonl",
        "source_detail": "file:2026-10-22.jsonl",
        "event_count": 2,
        "recent_events": [
            {"ts": "2026-10-22T09:33:00", "code": "600000", "kind": "threshold_up", "value": 4.5,
             "source_detail": "file:2026-10-22.jsonl"},
            {"ts": "2026-10-22T09:34:00", "code": "600000", "kind": "seal", "value": 11.0,
             "source_detail": "file:2026-10-22.jsonl"},
        ],
        "events_file": "data/watch/events-2026-10-22.jsonl",
    }
    base.update(overrides)
    return base


def write(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


# --------------------------------------------------------------------------- #
# 路径
# --------------------------------------------------------------------------- #

def test_status_path_is_watch_status_json(tmp_path: Path) -> None:
    assert status_path(tmp_path) == tmp_path / "watch" / "status.json"
    assert status_file.watch_dir(tmp_path) == tmp_path / "watch"


# --------------------------------------------------------------------------- #
# 四态
# --------------------------------------------------------------------------- #

def test_missing_file_is_missing(tmp_path: Path) -> None:
    result = read_status_document(status_path(tmp_path))

    assert result.state == STATE_MISSING
    assert result.ok is False
    assert result.document is None
    assert result.error is None
    assert result.path == status_path(tmp_path)


def test_well_formed_file_is_ok(tmp_path: Path) -> None:
    path = write(status_path(tmp_path), document())

    result = read_status_document(path)

    assert result.state == STATE_OK
    assert result.ok is True
    assert result.document is not None
    assert result.document["event_count"] == 2


@pytest.mark.parametrize(
    "payload",
    ["{not json", "", "[1, 2]", '"text"'],
    ids=["broken-json", "empty", "array", "string"],
)
def test_unreadable_file_is_corrupt(tmp_path: Path, payload: str) -> None:
    path = write(status_path(tmp_path), payload)

    result = read_status_document(path)

    assert result.state == STATE_CORRUPT
    assert result.document is None
    assert result.error  # 必须带错误类型/原因，不能是空串


def test_broken_json_error_names_exception_type(tmp_path: Path) -> None:
    path = write(status_path(tmp_path), "{not json")

    result = read_status_document(path)

    assert "JSONDecodeError" in (result.error or "")


def test_unknown_schema_is_unsupported(tmp_path: Path) -> None:
    path = write(status_path(tmp_path), {"running": "yes", "mode": "", "event_count": -1})

    result = read_status_document(path)

    assert result.state == STATE_UNSUPPORTED
    assert result.document is not None  # 原文仍可给调用方调试
    assert "running" in (result.error or "")
    assert "mode" in (result.error or "")
    assert "event_count" in (result.error or "")


def test_schema_problems_empty_for_real_document() -> None:
    assert schema_problems(document()) == []


def test_bool_is_not_accepted_as_event_count() -> None:
    problems = schema_problems(document(event_count=True))
    assert any("event_count" in item for item in problems)


# --------------------------------------------------------------------------- #
# 截断与摘要
# --------------------------------------------------------------------------- #

def test_recent_events_keeps_last_n() -> None:
    events = [{"n": index} for index in range(status_file.STATUS_RECENT_EVENTS + 10)]

    kept = recent_events(document(recent_events=events))

    assert len(kept) == status_file.STATUS_RECENT_EVENTS
    assert kept == events[-status_file.STATUS_RECENT_EVENTS:]
    assert kept[-1] == {"n": status_file.STATUS_RECENT_EVENTS + 9}


def test_recent_events_tolerates_wrong_type() -> None:
    assert recent_events({"recent_events": "nope"}) == []
    assert recent_events({}) == []


def test_status_detail_summarizes_real_document() -> None:
    detail = status_detail(document())

    assert "运行中" in detail
    assert "offline-single-pass" in detail
    assert "2026-10-22" in detail
    assert "5 根" in detail
    assert "2 条" in detail


def test_status_detail_prefers_explicit_detail() -> None:
    assert status_detail(document(detail="人工写的说明")) == "人工写的说明"


def test_status_detail_marks_stopped() -> None:
    assert "已停止" in status_detail(document(running=False))
