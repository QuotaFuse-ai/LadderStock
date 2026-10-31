"""周度五段编排的 checkpoint：复用 `ai/checkpoint.py` 状态机，只换落盘布局。

- 段落顺序与状态语义与每日复盘**完全一致**（`SECTIONS` / `pending·ok·degraded·failed`），
  所以 `--force` / 断点续跑走同一个 `checkpoint.select_targets`；
- 落盘 `<data_dir>/state/weekly_checkpoint/<week>.json`：**不与每日 checkpoint 混目录**——
  `ldst ai status` 取「最近一份」是按文件名倒序扫 `state/ai_checkpoint/`，
  `2026-W44` 字典序大于 `2026-10-30`，混进去会把每日复盘的状态视图永久顶掉；
- 本模块不调模型、不写周报产物（产物见 `weekly/pipeline.py`）。
"""

from __future__ import annotations

from typing import Any

from ladderstock.ai import checkpoint as base
from ladderstock.weekly import week as week_module

CHECKPOINT_SUBDIR = ("state", "weekly_checkpoint")

#: 段落名与字段名沿用每日复盘（`field="sections"`），只有目录不同：
#: 编排层的 `_result()` / `record()` 只认 `names` 与 `field`，因此可直接复用。
LAYOUT = base.CheckpointLayout(subdir=CHECKPOINT_SUBDIR, names=base.SECTIONS, field="sections")

SECTION_TITLES = {
    "sentiment": "一周情绪温度与晋级率",
    "capital": "一周资金流向",
    "theme": "一周题材轮动与主线持续",
    "dragon_tiger": "一周龙虎榜与席位",
    "leader": "一周龙头与梯队",
}


def checkpoint_path(week: Any, root: Any = None) -> Any:
    return base.checkpoint_path(week_module.normalize_week(week), root, layout=LAYOUT)


def load(week: Any, root: Any = None) -> dict[str, Any]:
    return base.load(week_module.normalize_week(week), root, layout=LAYOUT)


def save(document: dict[str, Any], root: Any = None) -> Any:
    return base.save(document, root, layout=LAYOUT)


def targets(
    document: dict[str, Any],
    *,
    force: bool = False,
    retry_failed: bool = False,
    only: str | None = None,
) -> list[str]:
    """本次要跑的段（语义与每日复盘 / `ldst research` 一致）。"""
    return base.select_targets(
        document, layout=LAYOUT, force=force, retry_failed=retry_failed, only=only
    )


def section_state(document: dict[str, Any], name: str) -> dict[str, Any]:
    return base.section_state(document, name, layout=LAYOUT)


def record(document: dict[str, Any], name: str, **result: Any) -> dict[str, Any]:
    return base.record(document, name, layout=LAYOUT, **result)


def status_summary(week: Any, root: Any = None) -> dict[str, Any]:
    """只读进度视图（CLI `--output json` 与周报页共用）。"""
    tag = week_module.normalize_week(week)
    document = load(tag, root)
    path = checkpoint_path(tag, root)
    return {
        "week": tag,
        "checkpoint": str(path),
        "exists": path.is_file(),
        "updated_at": document.get("updated_at"),
        "complete": base.is_complete(document, layout=LAYOUT),
        "sections": [
            {
                "name": name,
                "title": SECTION_TITLES.get(name, name),
                "status": str(section_state(document, name).get("status") or "pending"),
                "mode": section_state(document, name).get("mode"),
                "degraded": bool(section_state(document, name).get("degraded")),
            }
            for name in base.SECTIONS
        ],
        "done": base.done_sections(document, layout=LAYOUT),
        "failed": base.failed_sections(document, layout=LAYOUT),
        "pending": base.pending_sections(document, layout=LAYOUT),
    }


__all__ = [
    "CHECKPOINT_SUBDIR",
    "LAYOUT",
    "SECTION_TITLES",
    "checkpoint_path",
    "load",
    "record",
    "save",
    "section_state",
    "status_summary",
    "targets",
]
