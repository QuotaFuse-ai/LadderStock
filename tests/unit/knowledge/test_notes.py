"""笔记存储单测（E 包 / DEC-091）：CRUD + 标签归一 + 过滤。"""

from __future__ import annotations

from pathlib import Path

import pytest

from ladderstock.errors import EXIT_NOT_FOUND, EXIT_USAGE
from ladderstock.knowledge import notes


def test_add_list_update_remove_roundtrip(tmp_path: Path) -> None:
    created = notes.add_note(
        "等三季报再决定加仓",
        target="SH:600000",
        title="浦发观察",
        tags=["银行", "观察"],
        root=tmp_path,
    )
    assert created["market"] == "SH" and created["symbol"] == "600000"
    # 标签按存储层的稳定序（tag 字典序）返回，与传入顺序无关
    assert sorted(created["tags"]) == ["观察", "银行"]

    listed = notes.list_notes(root=tmp_path, target="600000")
    assert len(listed) == 1
    assert listed[0]["target"] == "SH:600000"

    updated = notes.update_note(
        created["id"], body="看完三季报：不动", tags=["银行"], root=tmp_path
    )
    assert updated["body"] == "看完三季报：不动"
    assert updated["tags"] == ["银行"]  # tags 传了就是整体替换
    assert updated["title"] == "浦发观察"  # 未传的字段不动

    removed = notes.remove_note(created["id"], root=tmp_path)
    assert removed["removed"] is True
    with pytest.raises(Exception) as err:
        notes.get_note(created["id"], root=tmp_path)
    assert err.value.code == EXIT_NOT_FOUND


def test_clean_tags_normalizes() -> None:
    assert notes.clean_tags([" 银行 ", "", "银行", "Obs", "obS"]) == ["银行", "Obs"]
    assert notes.clean_tags(None) == []
    # 数量截断
    many = [f"t{index}" for index in range(40)]
    assert len(notes.clean_tags(many)) == notes.MAX_TAGS


def test_empty_body_rejected(tmp_path: Path) -> None:
    with pytest.raises(Exception) as err:
        notes.add_note("   ", root=tmp_path)
    assert err.value.code == EXIT_USAGE


def test_oversized_body_rejected(tmp_path: Path) -> None:
    with pytest.raises(Exception) as err:
        notes.add_note("x" * (notes.MAX_BODY_CHARS + 1), root=tmp_path)
    assert err.value.code == EXIT_USAGE


def test_tag_filter_and_empty_store(tmp_path: Path) -> None:
    notes.add_note("a", target="600000", tags=["银行"], root=tmp_path)
    notes.add_note("b", target="600519", tags=["白酒"], root=tmp_path)
    only_bank = notes.list_notes(root=tmp_path, tag="银行")
    assert [row["body"] for row in only_bank] == ["a"]

    # 空库目录（不存在库文件）返回空列表，不建库
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    assert notes.list_notes(root=fresh) == []
    assert notes.list_tags(root=fresh) == []


def test_list_tags_with_counts(tmp_path: Path) -> None:
    notes.add_note("a", tags=["银行", "观察"], root=tmp_path)
    notes.add_note("b", tags=["银行"], root=tmp_path)
    tags = {row["tag"]: row["notes"] for row in notes.list_tags(root=tmp_path)}
    assert tags == {"银行": 2, "观察": 1}