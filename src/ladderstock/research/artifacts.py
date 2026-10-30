"""研究阶段产物：`<data_dir>/research/<code>/<date>/<stage>.json` + `<stage>.md`。

每段跑完立即落盘两件：结构化 JSON（后续段与页面消费）与 Markdown 叙述（人读）。
写盘沿用 checkpoint 的原子写纪律（同目录临时文件 + `os.replace`），
所以「某段失败不吞前序成果」在文件层面也成立：已写完的产物不会被半截覆盖。

`provenance` 是为 R3（数字级溯源 +〔未核实〕）**预留的字段位**：
`values` 按指标键登记 `sources[]` 与 `unverified`，本任务只保证字段存在、
骨架阶段一律 `unverified=true`（没有引用来源就不许当已核实）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ladderstock.ai.checkpoint import atomic_write
from ladderstock.research import checkpoint

ARTIFACT_SUBDIR = "research"

#: R3 定稿前，provenance 的字段形状就固定下来（值可以空，键不能少）
PROVENANCE_KEYS: tuple[str, ...] = ("values", "sources", "unverified")


def artifact_dir(code: Any, day: Any, root: Any = None) -> Path:
    base_dir = Path(root) if root is not None else _data_dir()
    return base_dir / ARTIFACT_SUBDIR / checkpoint.normalize_code(code) / str(day)


def stage_paths(code: Any, day: Any, stage: str, root: Any = None) -> tuple[Path, Path]:
    directory = artifact_dir(code, day, root)
    return directory / f"{stage}.json", directory / f"{stage}.md"


def artifact_paths(code: Any, day: Any, root: Any = None) -> dict[str, dict[str, Any]]:
    """六段产物路径（`--output json` 与 MCP 共用，路径只有一处定义）。"""
    paths: dict[str, dict[str, Any]] = {}
    for stage in checkpoint.STAGES:
        json_path, md_path = stage_paths(code, day, stage, root)
        paths[stage] = {"json": str(json_path), "md": str(md_path), "exists": json_path.is_file()}
    return paths


def build_document(
    *,
    code: str,
    day: str,
    stage: str,
    status: str,
    mode: str,
    model: str | None,
    degraded: bool,
    narrative: list[str],
    notes: list[str],
    facts: dict[str, Any],
    ai: dict[str, Any],
    failure: dict[str, Any] | None = None,
    attempts: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """组装阶段产物（结构化件）。字段顺序即落盘顺序，保持稳定有利 diff。"""
    return {
        "code": code,
        "date": day,
        "stage": stage,
        "title": checkpoint.STAGE_TITLES.get(stage, stage),
        "status": status,
        "mode": mode,
        "model": model,
        "degraded": degraded,
        "narrative": list(narrative),
        "notes": list(notes),
        "facts": dict(facts),
        "ai": dict(ai),
        "provenance": {
            "values": {},
            "sources": [],
            # 骨架阶段没有任何取数与引用，数值一律未核实（R3 逐条补 sources）
            "unverified": True,
        },
        "failure": failure,
        "attempts": list(attempts or []),
    }


def build_markdown(document: dict[str, Any]) -> str:
    title = str(document.get("title") or document.get("stage") or "")
    lines = [
        f"# {title}",
        "",
        f"- 代码：{document.get('code')}",
        f"- 日期：{document.get('date')}",
        f"- 状态：{document.get('status')} · mode={document.get('mode')}"
        f" · 模型={document.get('model') or '-'}",
    ]
    narrative = [str(item) for item in document.get("narrative") or []]
    lines += ["", "## 叙述", ""]
    lines += narrative if narrative else ["（本段无叙述）", ""]
    notes = [str(item) for item in document.get("notes") or []]
    if notes:
        lines += ["", "## 备注", ""]
        lines += [f"- {note}" for note in notes]
    if document.get("degraded"):
        lines += ["", "> 本段为降级产物（占位/纯文本），未走结构化输出。"]
    lines += ["", "> 引用与核实标记（sources / 未核实）由 R3 渲染，本件只保留字段位。"]
    return "\n".join(lines) + "\n"


def write_stage(document: dict[str, Any], root: Any = None) -> dict[str, str]:
    """原子写出 `<stage>.json` + `<stage>.md`，返回两件路径。"""
    code = document["code"]
    day = document["date"]
    stage = str(document["stage"])
    json_path, md_path = stage_paths(code, day, stage, root)
    atomic_write(
        json_path, json.dumps(document, ensure_ascii=False, indent=2, default=str) + "\n"
    )
    atomic_write(md_path, build_markdown(document))
    return {"json": str(json_path), "md": str(md_path)}


def read_stage(code: Any, day: Any, stage: str, root: Any = None) -> dict[str, Any] | None:
    """读某段结构化产物；不存在或损坏返回 None（报告段据此判定「缺输入」）。"""
    json_path, _md_path = stage_paths(code, day, stage, root)
    if not json_path.is_file():
        return None
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _data_dir() -> Path:
    from ladderstock.cli.paths import data_dir

    return data_dir()


__all__ = [
    "ARTIFACT_SUBDIR",
    "PROVENANCE_KEYS",
    "artifact_dir",
    "artifact_paths",
    "build_document",
    "build_markdown",
    "read_stage",
    "stage_paths",
    "write_stage",
]
