"""F8 研究档案：扫描 / 导出 / 检索的口径测试（全部用临时 root，不碰真机数据）。"""

from __future__ import annotations

import json
from pathlib import Path

from ladderstock.research import archive, artifacts


def _document(code: str, day: str, stage: str, *, status: str = "ok", degraded: bool = False) -> dict:
    slot = {"values": {}, "sources": [], "unverified": True}
    return artifacts.build_document(
        code=code,
        day=day,
        stage=stage,
        status=status,
        mode="structured",
        model="fake-model",
        degraded=degraded,
        narrative=["本段叙述第一行。", "本段叙述第二行。"],
        notes=["一条备注。"],
        facts={"pe": 12.3},
        ai={},
        provenance=slot,
    )


def _write(root: Path, code: str, day: str, stage: str, **kwargs) -> dict[str, str]:
    return artifacts.write_stage(_document(code, day, stage, **kwargs), root=root)


def test_scan_empty_root_returns_empty(tmp_path: Path) -> None:
    assert archive.scan(tmp_path) == []
    assert archive.find("600202", root=tmp_path) is None


def test_scan_reports_partial_archive(tmp_path: Path) -> None:
    _write(tmp_path, "600202", "2026-11-18", "company")
    items = archive.scan(tmp_path)
    assert len(items) == 1
    item = items[0]
    assert item["code"] == "600202" and item["date"] == "2026-11-18"
    assert item["stage_count"] == 1 and item["total_stages"] == 6
    assert item["complete"] is False
    assert item["missing"] == ["financial", "forecast", "valuation", "risk", "report"]
    assert item["unverified"] == ["company"]
    rows = {row["stage"]: row for row in item["stages"]}
    assert rows["company"]["has_json"] and rows["company"]["has_md"]
    assert rows["financial"]["has_json"] is False
    assert rows["financial"]["updated_at"] is None


def test_scan_marks_corrupt_json(tmp_path: Path) -> None:
    _write(tmp_path, "600202", "2026-11-18", "company")
    json_path, _md = artifacts.stage_paths("600202", "2026-11-18", "company", tmp_path)
    json_path.write_text("{not json", encoding="utf-8")
    row = archive.scan(tmp_path)[0]["stages"][0]
    assert row["corrupt"] is True
    assert row["status"] is None


def test_export_merges_stages_with_demoted_headings(tmp_path: Path) -> None:
    _write(tmp_path, "600202", "2026-11-18", "company")
    _write(tmp_path, "600202", "2026-11-18", "financial", status="partial", degraded=True)
    result = archive.export("600202", "2026-11-18", root=tmp_path, out=tmp_path / "out.md")
    assert result["ok"] is True
    text = (tmp_path / "out.md").read_text(encoding="utf-8")
    assert text.startswith("# 600202 研究档案 · 2026-11-18")
    # 体检行把缺段与未核实都写出来
    assert "缺 forecast" in text
    assert "未核实 company" in text
    assert "## 1. 公司画像" in text and "## 2. 财务" in text
    # 段内标题降一级，不与档案结构平级
    assert "### 叙述" in text
    assert "\n## 叙述" not in text
    assert "## 依据链总览" in text
    assert "降级" in text
    assert "未产出段落" in text


def test_export_missing_archive_reports_reason(tmp_path: Path) -> None:
    result = archive.export("600202", root=tmp_path)
    assert result["ok"] is False
    assert "没有 600202" in result["reason"]


def test_find_by_date_and_latest(tmp_path: Path) -> None:
    _write(tmp_path, "600202", "2026-11-17", "company")
    _write(tmp_path, "600202", "2026-11-18", "company")
    _write(tmp_path, "600519", "2026-11-18", "company")
    assert archive.find("600202", "2026-11-17", tmp_path)["date"] == "2026-11-17"
    assert archive.find("600202", root=tmp_path)["date"] == "2026-11-18"  # 缺省取最近
    assert archive.find("600202", "2026-11-19", tmp_path) is None
    assert archive.find("000001", root=tmp_path) is None


def test_search_hits_meta_and_content(tmp_path: Path) -> None:
    _write(tmp_path, "600202", "2026-11-18", "company")
    hits = archive.search("600202", root=tmp_path)
    assert hits and hits[0]["matched_by"] == "meta"
    hits = archive.search("本段叙述第二行", root=tmp_path)
    assert hits and hits[0]["matched_by"] == "content"
    match = hits[0]["matches"][0]
    assert match["stage"] == "company" and "本段叙述第二行" in match["text"]
    assert archive.search("不存在的词", root=tmp_path) == []
    assert archive.search("   ", root=tmp_path) == []


def test_write_index_is_atomic_and_reloadable(tmp_path: Path) -> None:
    _write(tmp_path, "600202", "2026-11-18", "company")
    path = archive.write_index(tmp_path)
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["count"] == 1 and data["items"][0]["code"] == "600202"
    assert not list(path.parent.glob("*.tmp"))


def test_export_json_is_structured_with_stage_text(tmp_path: Path) -> None:
    _write(tmp_path, "600202", "2026-11-18", "company")

    result = archive.export("600202", "2026-11-18", root=tmp_path, fmt="json")

    assert result["ok"] is True
    assert result["format"] == "json" and result["path"].endswith(".json")
    payload = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    assert payload["code"] == "600202" and payload["date"] == "2026-11-18"
    stage = next(row for row in payload["stages"] if row["stage"] == "company")
    assert stage["has_json"] and stage["has_md"]
    # 原文直接随 JSON 走，下游不需要再解析 Markdown
    assert "本段叙述第一行" in stage["markdown"]


def test_export_md_is_default_and_unknown_format_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path, "600202", "2026-11-18", "company")

    default = archive.export("600202", root=tmp_path)
    assert default["format"] == "md" and default["path"].endswith(".md")

    bad = archive.export("600202", root=tmp_path, fmt="pdf")
    assert bad["ok"] is False
    assert "只支持 md / json" in bad["reason"]
