"""AI 编排的断点续跑状态机（M3 §6.3 五段复盘 / R1-1 个股六段研究共用）。

本模块只负责**状态读写**，不调模型（编排在 `ai/orchestrator.py` 与
`research/pipeline.py`）。两套编排的差别只有一个：落盘布局 `CheckpointLayout`。

- 五段复盘：`<data_dir>/state/ai_checkpoint/<date>.json`，字段 `sections`；
- 个股研究：`<data_dir>/state/research_checkpoint/<code>/<date>.json`，字段 `stages`。

顺序固定、**每一段完成即落盘**，因此某一段失败不会吞掉前序成果：

- 成功/降级段落保留叙述，`retry_failed` 只重跑失败或未跑过的段落；
- 进程中途退出后再次运行，已完成段落直接复用，不重复消耗额度；
- 文件损坏/不存在时返回全新状态，不把坏 JSON 当成功缓存。
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SECTIONS: tuple[str, ...] = ("sentiment", "capital", "theme", "dragon_tiger", "leader")

# pending 尚未跑；ok 结构化/补偿成功；degraded 纯文本降级；failed 全链路失败
STATUSES: tuple[str, ...] = ("pending", "ok", "degraded", "failed")
DONE_STATUSES: frozenset[str] = frozenset({"ok", "degraded"})

CHECKPOINT_SUBDIR = ("state", "ai_checkpoint")


@dataclass(frozen=True)
class CheckpointLayout:
    """一套编排的落盘布局：目录、段落顺序、文档里的段落字段名。

    `parts` 是日期文件名之前的额外层级（个股研究按 `<code>` 分层）。
    """

    subdir: tuple[str, ...] = CHECKPOINT_SUBDIR
    names: tuple[str, ...] = SECTIONS
    field: str = "sections"


REVIEW_LAYOUT = CheckpointLayout()


def checkpoint_dir(
    root: Any = None,
    *,
    layout: CheckpointLayout = REVIEW_LAYOUT,
    parts: tuple[str, ...] = (),
) -> Path:
    base = Path(root) if root is not None else _data_dir()
    return base.joinpath(*layout.subdir, *parts)


def checkpoint_path(
    day: Any,
    root: Any = None,
    *,
    layout: CheckpointLayout = REVIEW_LAYOUT,
    parts: tuple[str, ...] = (),
) -> Path:
    return checkpoint_dir(root, layout=layout, parts=parts) / f"{day}.json"


def atomic_write(path: Path, text: str) -> Path:
    """原子写入：同目录临时文件 + `os.replace`，避免读到半截内容。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)
    return path


def _data_dir() -> Path:
    from ladderstock.cli.paths import data_dir

    return data_dir()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def fresh(
    day: Any,
    *,
    layout: CheckpointLayout = REVIEW_LAYOUT,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    document: dict[str, Any] = dict(extra or {})
    document.update(
        {
            "date": str(day),
            "version": 1,
            "updated_at": _now(),
            layout.field: {name: {"status": "pending"} for name in layout.names},
        }
    )
    return document


def load(
    day: Any,
    root: Any = None,
    *,
    layout: CheckpointLayout = REVIEW_LAYOUT,
    parts: tuple[str, ...] = (),
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """读 checkpoint；缺失或损坏时返回全新状态（不把坏 JSON 当成功缓存）。"""
    path = checkpoint_path(day, root, layout=layout, parts=parts)
    if not path.is_file():
        return fresh(day, layout=layout, extra=extra)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fresh(day, layout=layout, extra=extra)
    if not isinstance(data, dict) or not isinstance(data.get(layout.field), dict):
        return fresh(day, layout=layout, extra=extra)
    document = fresh(day, layout=layout, extra=extra)
    document["updated_at"] = str(data.get("updated_at") or document["updated_at"])
    for name in layout.names:
        state = data[layout.field].get(name)
        if isinstance(state, dict) and state.get("status") in STATUSES:
            document[layout.field][name] = state
    return document


def save(
    document: dict[str, Any],
    root: Any = None,
    *,
    layout: CheckpointLayout = REVIEW_LAYOUT,
    parts: tuple[str, ...] = (),
) -> Path:
    document["updated_at"] = _now()
    path = checkpoint_path(document.get("date"), root, layout=layout, parts=parts)
    return atomic_write(
        path, json.dumps(document, ensure_ascii=False, indent=2, default=str) + "\n"
    )


def section_state(
    document: dict[str, Any], name: str, *, layout: CheckpointLayout = REVIEW_LAYOUT
) -> dict[str, Any]:
    sections = document.get(layout.field) or {}
    state = sections.get(name)
    return state if isinstance(state, dict) else {"status": "pending"}


def record(
    document: dict[str, Any],
    name: str,
    *,
    status: str,
    mode: str,
    narrative: list[str],
    model: str | None = None,
    degraded: bool = False,
    notes: list[str] | None = None,
    failure: dict[str, Any] | None = None,
    attempts: list[dict[str, Any]] | None = None,
    layout: CheckpointLayout = REVIEW_LAYOUT,
) -> dict[str, Any]:
    """写入一段的执行结果；调用方随后应立即 `save()`。"""
    if status not in STATUSES:
        raise ValueError(f"未知段落状态：{status}")
    document.setdefault(layout.field, {})[name] = {
        "status": status,
        "mode": mode,
        "model": model,
        "degraded": bool(degraded),
        "narrative": list(narrative),
        "notes": list(notes or []),
        "failure": failure,
        "attempts": list(attempts or []),
        "completed_at": _now() if status != "pending" else None,
    }
    return document


def failed_sections(
    document: dict[str, Any], *, layout: CheckpointLayout = REVIEW_LAYOUT
) -> list[str]:
    return [
        name
        for name in layout.names
        if section_state(document, name, layout=layout).get("status") == "failed"
    ]


def done_sections(
    document: dict[str, Any], *, layout: CheckpointLayout = REVIEW_LAYOUT
) -> list[str]:
    return [
        name
        for name in layout.names
        if section_state(document, name, layout=layout).get("status") in DONE_STATUSES
    ]


def pending_sections(
    document: dict[str, Any], *, layout: CheckpointLayout = REVIEW_LAYOUT
) -> list[str]:
    return [
        name
        for name in layout.names
        if section_state(document, name, layout=layout).get("status") == "pending"
    ]


def is_complete(document: dict[str, Any], *, layout: CheckpointLayout = REVIEW_LAYOUT) -> bool:
    return all(
        section_state(document, name, layout=layout).get("status") in DONE_STATUSES
        for name in layout.names
    )


def select_targets(
    document: dict[str, Any],
    *,
    layout: CheckpointLayout = REVIEW_LAYOUT,
    force: bool = False,
    retry_failed: bool = False,
    only: str | None = None,
    kind: str = "段落",
) -> list[str]:
    """按 `force` / `retry_failed` / `only` 选出本次要跑的段（两套编排共用同一语义）。

    - `only`：只跑这一段，其余一律不动（不改其他段状态）；
    - `retry_failed`：只补失败 / 未跑过的段，成功段一律不重跑；
    - 默认：只补未完成的段（断点续跑）。
    """
    if only is not None and only not in layout.names:
        raise ValueError(f"未知{kind}：{only}（可选：{', '.join(layout.names)}）")
    done = set(done_sections(document, layout=layout))
    targets: list[str] = []
    for name in layout.names:
        if only is not None and name != only:
            continue
        if force:
            targets.append(name)
        elif name not in done:
            targets.append(name)
    return targets


__all__ = [
    "CHECKPOINT_SUBDIR",
    "DONE_STATUSES",
    "REVIEW_LAYOUT",
    "SECTIONS",
    "STATUSES",
    "CheckpointLayout",
    "atomic_write",
    "checkpoint_dir",
    "checkpoint_path",
    "done_sections",
    "failed_sections",
    "fresh",
    "is_complete",
    "load",
    "pending_sections",
    "record",
    "save",
    "section_state",
    "select_targets",
]
