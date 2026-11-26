"""wire 降级明细：把「哪几个源不可用」从 Wire 自己的归档里读出来。

背景：`qfw run --output json` 的摘要只给 `sources_unavailable` 计数，页面只能显示
「28 个不可用」，没法回答「是哪 28 个」。Wire 每天把逐源状态写在
`data/archive/<day>/sources.json`，LadderStock 只读它，不改 Wire、不打网络请求。
"""

from __future__ import annotations

import json
from pathlib import Path

from ladderstock.cli.schedule import _wire_unavailable_sources


def _archive(wire_dir: Path, day: str, sources: list[dict]) -> None:
    target = wire_dir / "data" / "archive" / day
    target.mkdir(parents=True, exist_ok=True)
    (target / "sources.json").write_text(
        json.dumps({"date": day, "sources": sources}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_only_failed_sources_are_returned(tmp_path: Path) -> None:
    wire = tmp_path / "wire"
    _archive(
        wire,
        "2026-11-20",
        [
            {"source_id": "fed_watch", "status": "ok", "error": None},
            {"source_id": "google_research", "status": "unavailable", "error": "超时: https://research.google/blog/rss/"},
            {"source_id": "electrek", "status": "degraded", "error": ""},
        ],
    )

    rows = _wire_unavailable_sources(wire, "2026-11-20")

    assert [row["id"] for row in rows] == ["google_research", "electrek"]
    assert rows[0]["status"] == "unavailable"
    assert "超时" in rows[0]["error"]
    assert rows[1]["status"] == "degraded"


def test_missing_or_broken_archive_is_empty_not_an_error(tmp_path: Path) -> None:
    wire = tmp_path / "wire"
    assert _wire_unavailable_sources(wire, "2026-11-20") == []
    _archive(wire, "2026-11-20", [])
    (wire / "data" / "archive" / "2026-11-20" / "sources.json").write_text("{", encoding="utf-8")
    assert _wire_unavailable_sources(wire, "2026-11-20") == []
    assert _wire_unavailable_sources(wire, None) == []


def test_limit_caps_the_list(tmp_path: Path) -> None:
    wire = tmp_path / "wire"
    rows = [{"source_id": f"s{index}", "status": "unavailable", "error": "403"} for index in range(10)]
    _archive(wire, "2026-11-20", rows)

    capped = _wire_unavailable_sources(wire, "2026-11-20", limit=3)

    assert len(capped) == 3
    assert [row["id"] for row in capped] == ["s0", "s1", "s2"]
