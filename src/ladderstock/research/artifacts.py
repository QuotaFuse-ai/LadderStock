"""研究阶段产物：`<data_dir>/research/<code>/<date>/<stage>.json` + `<stage>.md`。

每段跑完立即落盘两件：结构化 JSON（后续段与页面消费）与 Markdown 叙述（人读）。
写盘沿用 checkpoint 的原子写纪律（同目录临时文件 + `os.replace`），
所以「某段失败不吞前序成果」在文件层面也成立：已写完的产物不会被半截覆盖。

`provenance` 是三键固定形状（`values` / `sources` / `unverified`，见
`docs/specs/SPEC-RESEARCH-PROVENANCE.md`）：R1 只留字段位，**R3 起由
`provenance_slot()` 填值**——程序写入，模型写不进去（`provenance` 不在
`AI_WRITABLE` 白名单里）。Markdown 侧把每个数字渲染成 `[n]` 角标或〔未核实〕，
文末 `## 来源` 按 `sources[]` 序号列出落点。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ladderstock.ai.checkpoint import atomic_write
from ladderstock.core.boundary import Provenance, ProvenanceAudit
from ladderstock.research import checkpoint, citation

ARTIFACT_SUBDIR = "research"

#: provenance 的字段形状（值可以空，键不能少——R1 定稿至今未变）
PROVENANCE_KEYS: tuple[str, ...] = ("values", "sources", "unverified")


def empty_slot() -> dict[str, Any]:
    """空出处：没有任何引用来源，一律未核实。"""
    return {"values": {}, "sources": [], "unverified": True}


def provenance_slot(audit: ProvenanceAudit | None, *, stage: str | None = None) -> dict[str, Any]:
    """`assert_provenance()` 的结果 → `<stage>.json` 的 provenance 字段。

    `sources[]` 按指标键排序生成，序号即角标 `[n]`（同一份输入渲染两次必须完全一致，
    否则 `--check` 与产物 diff 都不可信）；`values[<指标键>]` 只回指来源 id，
    数值本身仍只有一份，落在 `facts` 里。
    """
    values: dict[str, Any] = {}
    sources: list[dict[str, Any]] = []
    records = dict(audit.records) if audit else {}
    for key in sorted(records):
        record: Provenance = records[key]
        entry = _source_entry(record, position=len(sources) + 1, stage=stage)
        sources.append(entry)
        values[key] = {"sources": [entry["id"]], "unverified": not record.verified}
    # 「整件已核实」的条件比 values 更严：audit.unverified 还包含「压根没出处」的那批指标键
    unverified = audit is None or not sources or bool(audit.unverified)
    return {"values": values, "sources": sources, "unverified": unverified}


def _source_entry(record: Provenance, *, position: int, stage: str | None) -> dict[str, Any]:
    """一条来源记录：SPEC v0.1 的四个键在位，R3 的引用要素补在后面。"""
    params: dict[str, Any] = {"metric": record.metric or None}
    if stage:
        params["stage"] = stage
    return {
        "id": f"S{position}",
        "dataset": record.source,
        "params": params,
        "retrieved_at": record.fetched_at,
        "value": record.value,
        "unit": record.unit,
        "endpoint": record.endpoint,
        "as_of": record.as_of,
        "url": record.url,
        "verified": record.verified,
    }


def merge_slots(slots: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """报告段的出处 = 前五段来源的并集（`params.stage` + `params.upstream_id` 指回原段）。

    报告段自己不产出 `values`：数值只有一份，落在各段 `facts` 里（DEC-024），
    这里合并的是**引用元数据**，不是数字。
    """
    sources: list[dict[str, Any]] = []
    unverified_any = False
    for stage in checkpoint.STAGES:
        slot = slots.get(stage) or {}
        entries = list(slot.get("sources") or [])
        for entry in entries:
            params = dict(entry.get("params") or {})
            params.setdefault("stage", stage)
            params["upstream_id"] = entry.get("id")
            merged = {**entry, "params": params}
            sources.append(_renumber(merged, len(sources) + 1))
        # 只真的产出过数字的段参与判定：空段（本段没指标）不该把整份报告拖成未核实，
        # 但「有指标却缺出处」的段（unverified=true 且 values 非空）必须拖累报告
        if entries or slot.get("values"):
            unverified_any = unverified_any or bool(slot.get("unverified"))
    if not sources:
        unverified_any = True
    return {"values": {}, "sources": sources, "unverified": unverified_any}


def _renumber(entry: dict[str, Any], position: int) -> dict[str, Any]:
    return {**entry, "id": f"S{position}"}


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
    provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """组装阶段产物（结构化件）。字段顺序即落盘顺序，保持稳定有利 diff。

    `provenance` 由程序算（`provenance_slot(audit)`）；不传就是空出处——
    「没有引用来源就不许当已核实」这条从 R1 保持到现在。
    """
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
        "provenance": dict(provenance) if provenance else empty_slot(),
        "failure": failure,
        "attempts": list(attempts or []),
    }


def build_markdown(document: dict[str, Any]) -> str:
    """渲染人读件：每个数字后面跟 `[n]` 角标或〔未核实〕，文末 `## 来源` 列落点。

    角标由程序补，不让模型自己写引用——模型写 `[2]` 等于自证清白，审计就失去裁判。
    """
    code = str(document.get("code") or "")
    identifiers = frozenset({code}) if code else frozenset()
    slot = document.get("provenance") or {}
    index = citation.index_from_slot(slot)
    lines = [
        f"# {document.get('title') or document.get('stage') or ''}",
        "",
        f"- 代码：{document.get('code')}",
        f"- 日期：{document.get('date')}",
        f"- 状态：{document.get('status')} · mode={document.get('mode')}"
        f" · 模型={document.get('model') or '-'}",
    ]
    narrative = [str(item) for item in document.get("narrative") or []]
    lines += ["", "## 叙述", ""]
    if narrative:
        lines += [
            citation.annotate(item, index, identifiers=identifiers) for item in narrative
        ]
    else:
        lines += ["（本段无叙述）", ""]
    metrics = _metric_lines(document, index, identifiers)
    if metrics:
        lines += ["", "## 指标", ""]
        lines += metrics
    notes = [str(item) for item in document.get("notes") or []]
    if notes:
        lines += ["", "## 备注", ""]
        lines += [
            f"- {citation.annotate(note, index, identifiers=identifiers)}" for note in notes
        ]
    if document.get("degraded"):
        lines += ["", "> 本段为降级产物（占位/纯文本），未走结构化输出。"]
    lines += ["", citation.SOURCES_HEADING, ""]
    lines += _source_lines(index)
    return "\n".join(lines) + "\n"


def _metric_lines(
    document: dict[str, Any], index: citation.CitationIndex, identifiers: frozenset[str]
) -> list[str]:
    """`facts` 里的程序指标逐条列出并带角标：叙述引用的数与指标对不上时，这里就是对照面。

    `ai` / `upstream` 是结构与叙述，不是指标，不进这一节（DEC-006：数字只有一份）。
    """
    lines: list[str] = []
    facts = document.get("facts") or {}
    for key, value in facts.items():
        if key in {"ai", "upstream", "provenance"}:
            continue
        if isinstance(value, (dict, list, tuple)) or value is None:
            continue
        text = citation.annotate(f"{value}", index, identifiers=identifiers)
        lines.append(f"- {key}：{text}")
    return lines


def _source_lines(index: citation.CitationIndex) -> list[str]:
    if not index.sources:
        return ["（本段无引用来源；正文中的数字一律标〔未核实〕。）"]
    return [source.citation_line() for source in index.sources]


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
    "empty_slot",
    "merge_slots",
    "provenance_slot",
    "read_stage",
    "stage_paths",
    "write_stage",
]
