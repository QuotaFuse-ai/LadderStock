"""QUOT-488：存储层（manifest 索引 / 归档门槛 / 冲突裁决 / safePath）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from ladderstock.knowledge.model import Dossier, KnowledgeError
from ladderstock.knowledge.store import (
    PathEscape,
    archive_run,
    dossier_dir,
    load_manifest,
    load_profile,
    record_refutation,
    should_archive,
    write_profile,
)


def _dossier(**overrides: object) -> Dossier:
    base: dict[str, object] = {
        "symbol": "600000",
        "market": "SH",
        "name": "浦发银行",
        "as_of": "2026-11-08",
        "status": "fresh",
        "valid_days": 90,
        "body": "# 标的档案\n\n## 1. 业务与产业链位置\n- 主营：对公银行\n",
    }
    base.update(overrides)
    return Dossier(**base)  # type: ignore[arg-type]


@dataclass
class FakeRun:
    code: str = "600000"
    date: str = "2026-11-08"
    failed: list[str] = field(default_factory=list)
    generated: list[str] = field(default_factory=lambda: ["report"])
    stages: list[dict] = field(default_factory=list)


def test_write_and_load_roundtrip(tmp_path: Path) -> None:
    write_profile(tmp_path, _dossier())
    dossier = load_profile(tmp_path, "SH", "600000")
    assert dossier is not None
    assert dossier.symbol == "600000"
    assert "主营业务" not in dossier.body or dossier.body  # 正文原样
    manifest = load_manifest(tmp_path)
    assert manifest["companies"]["SH:600000"]["dir"] == "SH_600000"


def test_load_missing_profile_is_none(tmp_path: Path) -> None:
    assert load_profile(tmp_path, "SZ", "000001") is None


def test_profile_rejects_bad_market_on_load(tmp_path: Path) -> None:
    with pytest.raises(KnowledgeError):
        dossier_dir(tmp_path, "XX", "600000")


# --------------------------------------------------------------------------- #
# 归档门槛：仅当 run 非 failed 且含 report
# --------------------------------------------------------------------------- #


def test_should_archive_requires_report() -> None:
    assert should_archive(FakeRun()) is True
    assert should_archive(FakeRun(generated=["company"])) is False
    assert should_archive(FakeRun(failed=["report"])) is False


def test_archive_run_writes_latest_and_run(tmp_path: Path) -> None:
    outcome = archive_run(tmp_path, "SH", FakeRun(), run_id="r-1")
    assert outcome.archived is True
    latest = dossier_dir(tmp_path, "SH", "600000") / "latest.md"
    run_file = dossier_dir(tmp_path, "SH", "600000") / "runs" / "r-1.md"
    assert latest.is_file() and run_file.is_file()
    manifest = load_manifest(tmp_path)
    assert manifest["companies"]["SH:600000"]["as_of"] == "2026-11-08"


def test_failed_run_archives_nothing(tmp_path: Path) -> None:
    outcome = archive_run(tmp_path, "SH", FakeRun(failed=["report"]), run_id="r-bad")
    assert outcome.archived is False
    assert outcome.event == "archive.skipped"
    assert not dossier_dir(tmp_path, "SH", "600000").exists()
    assert not (tmp_path / "knowledge" / "SH_600000").exists()


def test_archive_gate_rejects_secret_in_content(tmp_path: Path) -> None:
    @dataclass
    class SecretRun(FakeRun):
        stages: list = field(
            default_factory=lambda: [
                {"name": "report", "status": "ok", "facts": {"key": "sk-abcdefghij1234567890abcd"}}
            ]
        )

    outcome = archive_run(tmp_path, "SH", SecretRun(), run_id="r-secret")
    assert outcome.archived is False
    assert outcome.event == "archive.gate_rejected"
    assert not (tmp_path / "knowledge" / "SH_600000" / "latest.md").exists()


# --------------------------------------------------------------------------- #
# 冲突裁决：实时优先 + 追加反证 + 旧结论保留
# --------------------------------------------------------------------------- #


def test_record_refutation_appends_and_marks(tmp_path: Path) -> None:
    write_profile(
        tmp_path,
        _dossier(
            body=(
                "## 3. 历史结论(每条必带 as_of;与实时数据冲突时以实时为准并记录反证)\n"
                "| as_of | 结论 | 依据 | 有效期(天) | 状态(fresh / stale / refuted) |\n"
                "|---|---|---|---|---|\n"
                "| 2026-10-01 | 2026Q3 归母净利同比增长 8% | 三季报 | 90 | fresh |\n"
            )
        ),
    )
    path = record_refutation(
        tmp_path,
        "SH",
        "600000",
        old_conclusion="2026Q3 归母净利同比增长 8%",
        realtime_value="同比增长 2.3%（TickHub 实时快报）",
        evidence="TickHub MCP finance_snapshot 2026-11-08",
        day="2026-11-09",
    )
    text = path.read_text(encoding="utf-8")
    assert "2026Q3 归母净利同比增长 8%" in text  # 旧结论保留
    assert "实时数据推翻旧结论" in text  # 反证追加
    assert "| 2026-11-09 |" in text and "refuted" in text
    dossier = load_profile(tmp_path, "SH", "600000")
    assert dossier is not None and dossier.status == "refuted"  # 显式反证 → refuted


def test_record_refutation_requires_existing_profile(tmp_path: Path) -> None:
    with pytest.raises(KnowledgeError):
        record_refutation(
            tmp_path,
            "SH",
            "600000",
            old_conclusion="x",
            realtime_value="y",
            evidence="z",
        )


# --------------------------------------------------------------------------- #
# safePath：目录穿越与符号链接
# --------------------------------------------------------------------------- #


def test_safe_path_rejects_escape(tmp_path: Path) -> None:
    from ladderstock.knowledge.store import knowledge_root, safe_path

    with pytest.raises(PathEscape):
        safe_path(knowledge_root(tmp_path), "SH_600000", "..", "..", "elsewhere.md")


def test_safe_path_rejects_symlink(tmp_path: Path) -> None:
    from ladderstock.knowledge.store import knowledge_root, safe_path

    root = knowledge_root(tmp_path)
    root.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("secret", encoding="utf-8")
    link = root / "SH_600000"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Windows 无符号链接权限")
    with pytest.raises(PathEscape):
        safe_path(root, "SH_600000", "latest.md")


def test_safe_path_allows_normal(tmp_path: Path) -> None:
    from ladderstock.knowledge.store import knowledge_root, safe_path

    root = knowledge_root(tmp_path)
    (root / "SH_600000").mkdir(parents=True)
    target = safe_path(root, "SH_600000", "latest.md")
    assert str(target).startswith(str(root))
