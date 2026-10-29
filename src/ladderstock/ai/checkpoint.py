"""五段 AI 编排的断点续跑（M3 §6.3）。

状态机顺序固定：情绪 → 资金 → 题材 → 龙虎榜 → 龙头。**每段完成即落盘**
（`<data_dir>/state/ai_checkpoint/<date>.json`，原子写），因此某一段失败
不会吞掉前序成果：

- 成功/降级段落保留叙述，`ai retry --section` 只重跑失败段；
- 进程中途退出后再次运行，已完成段落直接复用，不重复消耗额度；
- 文件损坏/不存在时返回全新状态，不把坏 JSON 当成功缓存。

本模块只负责状态读写，不调模型（见 `ai/orchestrator.py`）。
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SECTIONS: tuple[str, ...] = ("sentiment", "capital", "theme", "dragon_tiger", "leader")

# pending 尚未跑；ok 结构化/补偿成功；degraded 纯文本降级；failed 全链路失败
STATUSES: tuple[str, ...] = ("pending", "ok", "degraded", "failed")
DONE_STATUSES: frozenset[str] = frozenset({"ok", "degraded"})

CHECKPOINT_SUBDIR = ("state", "ai_checkpoint")


def checkpoint_path(day: Any, root: Any = None) -> Path:
    return checkpoint_dir(root) / f"{day}.json"


def checkpoint_dir(root: Any = None) -> Path:
    base = Path(root) if root is not None else _data_dir()
    return base.joinpath(*CHECKPOINT_SUBDIR)


def _data_dir() -> Path:
    from ladderstock.cli.paths import data_dir

    return data_dir()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def fresh(day: Any) -> dict[str, Any]:
    return {
        "date": str(day),
        "version": 1,
        "updated_at": _now(),
        "sections": {name: {"status": "pending"} for name in SECTIONS},
    }


def load(day: Any, root: Any = None) -> dict[str, Any]:
    """读 checkpoint；缺失或损坏时返回全新状态（并保留损坏文件的备份线索）。"""
    path = checkpoint_path(day, root)
    if not path.is_file():
        return fresh(day)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fresh(day)
    if not isinstance(data, dict) or not isinstance(data.get("sections"), dict):
        return fresh(day)
    document = fresh(day)
    document["updated_at"] = str(data.get("updated_at") or document["updated_at"])
    for name in SECTIONS:
        state = data["sections"].get(name)
        if isinstance(state, dict) and state.get("status") in STATUSES:
            document["sections"][name] = state
    return document


def save(document: dict[str, Any], root: Any = None) -> Path:
    """原子写入：临时文件 + `os.replace`，避免读到半截状态。"""
    document["updated_at"] = _now()
    path = checkpoint_path(document.get("date"), root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    os.replace(temp, path)
    return path


def section_state(document: dict[str, Any], name: str) -> dict[str, Any]:
    sections = document.get("sections") or {}
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
) -> dict[str, Any]:
    """写入一段的执行结果；调用方随后应立即 `save()`。"""
    if status not in STATUSES:
        raise ValueError(f"未知段落状态：{status}")
    document.setdefault("sections", {})[name] = {
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


def failed_sections(document: dict[str, Any]) -> list[str]:
    return [
        name
        for name in SECTIONS
        if section_state(document, name).get("status") == "failed"
    ]


def done_sections(document: dict[str, Any]) -> list[str]:
    return [
        name
        for name in SECTIONS
        if section_state(document, name).get("status") in DONE_STATUSES
    ]


def pending_sections(document: dict[str, Any]) -> list[str]:
    return [
        name
        for name in SECTIONS
        if section_state(document, name).get("status") == "pending"
    ]


def is_complete(document: dict[str, Any]) -> bool:
    return all(
        section_state(document, name).get("status") in DONE_STATUSES for name in SECTIONS
    )


__all__ = [
    "DONE_STATUSES",
    "SECTIONS",
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
]
