"""个股研究六段的 checkpoint：复用 `ai/checkpoint.py` 的状态机，只换落盘布局。

- 段落常量与顺序：`STAGES`（公司画像 → 财务 → 盈利预期 → 估值 → 风险 → 报告）；
- 落盘键：`<data_dir>/state/research_checkpoint/<code>/<date>.json`，每段一条，原子写；
- 状态语义与五段复盘**完全一致**（`pending / ok / degraded / failed`），
  `--only` / `--retry-failed` 走同一个 `checkpoint.select_targets`。

本模块不调模型，也不写阶段产物（产物见 `research/artifacts.py`）。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ladderstock.ai import checkpoint as base

STAGES: tuple[str, ...] = (
    "company",
    "financial",
    "forecast",
    "valuation",
    "risk",
    "report",
)

#: 前五段是报告的输入；报告段只消费它们的产物，不重新取数
REPORT_INPUTS: tuple[str, ...] = STAGES[:5]
REPORT_STAGE = "report"

STAGE_TITLES: dict[str, str] = {
    "company": "公司画像",
    "financial": "财务",
    "forecast": "盈利预期",
    "valuation": "估值",
    "risk": "风险",
    "report": "报告合成",
}

CHECKPOINT_SUBDIR = ("state", "research_checkpoint")

LAYOUT = base.CheckpointLayout(subdir=CHECKPOINT_SUBDIR, names=STAGES, field="stages")

#: 6 位数字代码，可带交易所后缀；`code` 会进目录名，所以必须先过这道白名单
CODE_PATTERN = re.compile(r"^\d{6}(?:\.(?:SH|SZ|BJ))?$", re.IGNORECASE)


def normalize_code(code: Any) -> str:
    """校验并规范化证券代码；非法代码抛 `ValueError`（防目录穿越）。"""
    text = str(code or "").strip().upper()
    if not CODE_PATTERN.fullmatch(text):
        raise ValueError(f"非法证券代码：{code!r}（需要 6 位数字，可选 .SH/.SZ/.BJ 后缀）")
    return text


def _parts(code: Any) -> tuple[str, ...]:
    return (normalize_code(code),)


def checkpoint_dir(code: Any, root: Any = None) -> Path:
    return base.checkpoint_dir(root, layout=LAYOUT, parts=_parts(code))


def checkpoint_path(code: Any, day: Any, root: Any = None) -> Path:
    return base.checkpoint_path(day, root, layout=LAYOUT, parts=_parts(code))


def fresh(code: Any, day: Any) -> dict[str, Any]:
    return base.fresh(day, layout=LAYOUT, extra={"code": normalize_code(code)})


def load(code: Any, day: Any, root: Any = None) -> dict[str, Any]:
    return base.load(
        day,
        root,
        layout=LAYOUT,
        parts=_parts(code),
        extra={"code": normalize_code(code)},
    )


def save(document: dict[str, Any], root: Any = None) -> Path:
    return base.save(document, root, layout=LAYOUT, parts=_parts(document.get("code")))


def stage_state(document: dict[str, Any], name: str) -> dict[str, Any]:
    return base.section_state(document, name, layout=LAYOUT)


def record(document: dict[str, Any], name: str, **result: Any) -> dict[str, Any]:
    return base.record(document, name, layout=LAYOUT, **result)


def targets(
    document: dict[str, Any],
    *,
    force: bool = False,
    retry_failed: bool = False,
    only: str | None = None,
) -> list[str]:
    """本次要跑的段（语义与五段 `ldst ai retry` 一致）。"""
    return base.select_targets(
        document,
        layout=LAYOUT,
        force=force,
        retry_failed=retry_failed,
        only=only,
        kind="阶段",
    )


def done_stages(document: dict[str, Any]) -> list[str]:
    return base.done_sections(document, layout=LAYOUT)


def failed_stages(document: dict[str, Any]) -> list[str]:
    return base.failed_sections(document, layout=LAYOUT)


def pending_stages(document: dict[str, Any]) -> list[str]:
    return base.pending_sections(document, layout=LAYOUT)


def is_complete(document: dict[str, Any]) -> bool:
    return base.is_complete(document, layout=LAYOUT)


def latest_day(code: Any, root: Any = None) -> str | None:
    """该代码最近一次有 checkpoint 的日期（`research_status` 缺省 date 时用）。"""
    directory = checkpoint_dir(code, root)
    if not directory.is_dir():
        return None
    days = sorted(path.stem for path in directory.glob("*.json"))
    return days[-1] if days else None


def status_summary(code: Any, day: Any, root: Any = None) -> dict[str, Any]:
    """只读进度视图（`research_status` MCP 工具与 CLI 共用）。"""
    document = load(code, day, root)
    path = checkpoint_path(code, day, root)
    return {
        "code": normalize_code(code),
        "date": str(day),
        "checkpoint": str(path),
        "exists": path.is_file(),
        "updated_at": document.get("updated_at"),
        "complete": is_complete(document),
        "stages": [
            {
                "name": name,
                "title": STAGE_TITLES.get(name, name),
                "status": str(stage_state(document, name).get("status") or "pending"),
                "mode": stage_state(document, name).get("mode"),
                "degraded": bool(stage_state(document, name).get("degraded")),
            }
            for name in STAGES
        ],
        "done": done_stages(document),
        "failed": failed_stages(document),
        "pending": pending_stages(document),
    }


__all__ = [
    "CHECKPOINT_SUBDIR",
    "CODE_PATTERN",
    "LAYOUT",
    "REPORT_INPUTS",
    "REPORT_STAGE",
    "STAGES",
    "STAGE_TITLES",
    "checkpoint_dir",
    "checkpoint_path",
    "done_stages",
    "failed_stages",
    "fresh",
    "is_complete",
    "latest_day",
    "load",
    "normalize_code",
    "pending_stages",
    "record",
    "save",
    "stage_state",
    "status_summary",
    "targets",
]
