"""五段 checkpoint：落盘、恢复、损坏回退（M3 §6.3）。

断点续跑的核心承诺：某段失败不吞掉前序成果，坏文件不被当成成功缓存。
"""

from __future__ import annotations

import json

from ladderstock.ai import checkpoint

DAY = "2026-10-27"


def test_fresh_has_all_sections_pending(tmp_path) -> None:
    document = checkpoint.fresh(DAY)
    assert list(document["sections"]) == list(checkpoint.SECTIONS)
    assert checkpoint.pending_sections(document) == list(checkpoint.SECTIONS)
    assert checkpoint.done_sections(document) == []
    assert checkpoint.is_complete(document) is False


def test_record_and_save_roundtrip(tmp_path) -> None:
    document = checkpoint.load(DAY, tmp_path)
    checkpoint.record(
        document,
        "sentiment",
        status="ok",
        mode="structured",
        narrative=["情绪回暖"],
        model="m1",
    )
    checkpoint.record(
        document,
        "capital",
        status="failed",
        mode="failed",
        narrative=[],
        failure={"kind": "network", "message": "连接超时"},
    )
    path = checkpoint.save(document, tmp_path)
    assert path == checkpoint.checkpoint_path(DAY, tmp_path)
    assert path.is_file()

    loaded = checkpoint.load(DAY, tmp_path)
    assert checkpoint.done_sections(loaded) == ["sentiment"]
    assert checkpoint.failed_sections(loaded) == ["capital"]
    assert checkpoint.section_state(loaded, "sentiment")["narrative"] == ["情绪回暖"]
    assert checkpoint.section_state(loaded, "capital")["status"] == "failed"
    # 其余段落仍是 pending，不会被伪造成成功
    assert checkpoint.pending_sections(loaded) == ["theme", "dragon_tiger", "leader"]


def test_load_missing_returns_fresh(tmp_path) -> None:
    document = checkpoint.load(DAY, tmp_path)
    assert checkpoint.pending_sections(document) == list(checkpoint.SECTIONS)


def test_corrupt_checkpoint_is_ignored(tmp_path) -> None:
    path = checkpoint.checkpoint_path(DAY, tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"sections": [1, 2, 3]', encoding="utf-8")

    document = checkpoint.load(DAY, tmp_path)
    assert checkpoint.pending_sections(document) == list(checkpoint.SECTIONS)


def test_unknown_status_is_dropped(tmp_path) -> None:
    path = checkpoint.checkpoint_path(DAY, tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "date": DAY,
                "sections": {
                    "sentiment": {"status": "ok", "narrative": ["x"]},
                    "capital": {"status": "finished"},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    document = checkpoint.load(DAY, tmp_path)
    assert checkpoint.done_sections(document) == ["sentiment"]
    assert checkpoint.section_state(document, "capital")["status"] == "pending"


def test_record_rejects_unknown_status() -> None:
    document = checkpoint.fresh(DAY)
    try:
        checkpoint.record(document, "sentiment", status="nope", mode="x", narrative=[])
    except ValueError as exc:
        assert "未知段落状态" in str(exc)
    else:  # pragma: no cover - 没有抛异常就是契约被破坏
        raise AssertionError("record 应拒绝未知状态")


def test_degraded_counts_as_done(tmp_path) -> None:
    document = checkpoint.fresh(DAY)
    for name in checkpoint.SECTIONS:
        checkpoint.record(
            document, name, status="degraded", mode="text", narrative=["降级叙述"], degraded=True
        )
    assert checkpoint.is_complete(document) is True
