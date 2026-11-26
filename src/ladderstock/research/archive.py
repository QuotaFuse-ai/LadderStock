"""F8 研究档案：把六段研究产物变成"可检索、可导出、带依据链"的档案。

已有 `research/artifacts.py` 负责**单段**落盘（`research/<code>/<day>/<stage>.json|.md`），
这里补三件它不管的事：

1. **档案表**：扫全量 `<data>/research/<code>/<day>/`，汇总成一张可检索的表
   （每段跑没跑、状态、是否降级、是否未核实、最后写入时间），落到
   `research_archive/index.json`；读盘现算，不依赖增量事件（少一次写就少一次漂移）；
2. **导出**：把六段 md 合并成一份可交付的完整报告
   `research_archive/<day>/<code>.md`（封面 + 逐段叙述 + 每段来源 + 依据链总览），
   导出前做一次"段齐全/未核实"体检，缺什么就写在封面，不假装完整；
3. **检索**：按代码 / 日期 / 关键词查档案，关键词命中回原文片段（带所在段名），
   让"上次那份研究里说过什么"能被找到，而不是靠人记得路径。

口径：
- 数字只有一份——合并报告不重算任何指标，只搬运各段已落的文本与 `## 来源`；
- 未核实是**事实字段**，不是评价：某段 `provenance.unverified=true`，封面就写它未核实；
- 导出是纯本地写盘（`research_archive/`），不发外部请求，重复导出覆盖同名文件是幂等的。
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from ladderstock.ai.checkpoint import atomic_write
from ladderstock.research import artifacts, checkpoint

ARCHIVE_SUBDIR = "research_archive"
INDEX_NAME = "index.json"
#: 索引里最多保留多少条（研究是低频动作，一年也到不了这个量级；纯防失控增长）
MAX_INDEX_ITEMS = 5000
#: 检索时最多读多少段 md（避免一次 grep 整个语料库）
SEARCH_READ_LIMIT = 200


# --------------------------------------------------------------------------- #
# 扫描 / 档案表
# --------------------------------------------------------------------------- #


def _data_dir() -> Path:
    from ladderstock.cli.paths import data_dir

    return data_dir()


def archive_root(root: Any = None) -> Path:
    base = Path(root) if root is not None else _data_dir()
    return base / ARCHIVE_SUBDIR


def research_root(root: Any = None) -> Path:
    base = Path(root) if root is not None else _data_dir()
    return base / artifacts.ARTIFACT_SUBDIR


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(ts))


def _stage_entry(code: str, day: str, stage: str, root: Any) -> dict[str, Any]:
    json_path, md_path = artifacts.stage_paths(code, day, stage, root)
    document: dict[str, Any] | None = None
    if json_path.is_file():
        document = artifacts.read_stage(code, day, stage, root)
    slot = (document or {}).get("provenance") or {}
    return {
        "stage": stage,
        "title": checkpoint.STAGE_TITLES.get(stage, stage),
        "has_json": json_path.is_file(),
        "has_md": md_path.is_file(),
        # 结构化件读不出来（半截写/损坏）与"没跑过"是两件事，分开报
        "corrupt": json_path.is_file() and document is None,
        "status": (document or {}).get("status"),
        "mode": (document or {}).get("mode"),
        "degraded": bool((document or {}).get("degraded")),
        "unverified": bool(slot.get("unverified")) if slot else None,
        "sources": len(slot.get("sources") or []) if slot else 0,
        "failure": ((document or {}).get("failure") or {}).get("reason"),
        "updated_at": _iso(_mtime(json_path) or _mtime(md_path)),
    }


def scan(root: Any = None) -> list[dict[str, Any]]:
    """扫 `<data>/research/<code>/<day>/`，每个 (code, day) 出一条档案。

    不读整份 JSON 的深层字段以外的东西（`read_stage` 已经是全量读，单文件几十 KB，
    量级可控）；目录没有就返回空表，不报错——"还没研究过"是正常状态。
    """
    base = research_root(root)
    items: list[dict[str, Any]] = []
    if not base.is_dir():
        return items
    for code_dir in sorted(base.iterdir()):
        if not code_dir.is_dir():
            continue
        code = code_dir.name
        for day_dir in sorted(code_dir.iterdir()):
            if not day_dir.is_dir():
                continue
            day = day_dir.name
            stages = [_stage_entry(code, day, stage, root) for stage in checkpoint.STAGES]
            ran = [row for row in stages if row["has_json"] or row["has_md"]]
            if not ran:
                continue
            missing = [row["stage"] for row in stages if not row["has_json"]]
            items.append(
                {
                    "code": code,
                    "date": day,
                    "dir": str(day_dir),
                    "stages": stages,
                    "stage_count": len(ran),
                    "total_stages": len(checkpoint.STAGES),
                    "complete": not missing,
                    "missing": missing,
                    "degraded": [row["stage"] for row in stages if row["degraded"]],
                    "unverified": [row["stage"] for row in stages if row["unverified"]],
                    "failed": [row["stage"] for row in stages if row["failure"]],
                    "updated_at": max(
                        (row["updated_at"] or "" for row in stages), default=""
                    ),
                }
            )
    items.sort(key=lambda item: (item["date"], item["code"]), reverse=True)
    return items[:MAX_INDEX_ITEMS]


def build_index(root: Any = None) -> dict[str, Any]:
    items = scan(root)
    return {
        "generated_at": _iso(time.time()),
        "root": str(research_root(root)),
        "count": len(items),
        "complete": sum(1 for item in items if item["complete"]),
        "items": items,
    }


def write_index(root: Any = None) -> Path:
    path = archive_root(root) / INDEX_NAME
    atomic_write(path, json.dumps(build_index(root), ensure_ascii=False, indent=1) + "\n")
    return path


def find(code: str, day: str | None = None, root: Any = None) -> dict[str, Any] | None:
    """找某标的某天（或最近一天）的档案。"""
    code = checkpoint.normalize_code(code)
    items = [item for item in scan(root) if item["code"] == code]
    if day:
        for item in items:
            if item["date"] == str(day):
                return item
        return None
    return items[0] if items else None


# --------------------------------------------------------------------------- #
# 导出
# --------------------------------------------------------------------------- #

_STAGE_HEADING = re.compile(r"^#\s+.*$", re.MULTILINE)
_HEADING = re.compile(r"^(#{2,5})\s", re.MULTILINE)


def _strip_leading_title(markdown: str) -> str:
    """去掉段 md 的第一行 `# 标题`，并把段内标题**降一级**。

    段 md 自带 `## 叙述` / `## 指标` / `## 来源`，与档案自己的 `## N. 段名` 同级会
    读成平级结构；降一级（`##`→`###`）后，目录层级才是「档案 → 段落 → 段内小节」。
    """
    stripped = markdown.lstrip("\ufeff")
    match = _STAGE_HEADING.match(stripped)
    if match:
        stripped = stripped[match.end() :].lstrip("\n")
    stripped = _HEADING.sub(lambda m: "#" * (len(m.group(1)) + 1) + " ", stripped)
    return stripped.rstrip() + "\n"


def _summary_line(item: dict[str, Any]) -> str:
    parts = [f"{item['stage_count']}/{item['total_stages']} 段已跑"]
    if item["missing"]:
        parts.append("缺 " + "、".join(item["missing"]))
    if item["degraded"]:
        parts.append("降级 " + "、".join(item["degraded"]))
    if item["unverified"]:
        parts.append("未核实 " + "、".join(item["unverified"]))
    if item["failed"]:
        parts.append("失败 " + "、".join(item["failed"]))
    return " · ".join(parts)


def build_markdown(item: dict[str, Any], root: Any = None) -> str:
    """把一份档案渲染成交付件：封面体检 → 逐段正文 → 依据链总览。"""
    code, day = item["code"], item["date"]
    title_lines = [
        f"# {code} 研究档案 · {day}",
        "",
        f"- 代码：{code}",
        f"- 日期：{day}",
        f"- 体检：{_summary_line(item)}",
        f"- 最后更新：{item['updated_at'] or '-'}",
        "",
        "> 本件由程序从六段落盘产物合并而成，未重算任何数字；每段文末的「来源」是该段自己的引用清单。",
        "",
        "## 目录",
        "",
    ]
    ran_stages = [row for row in item["stages"] if row["has_md"]]
    for position, row in enumerate(ran_stages, start=1):
        title_lines.append(f"{position}. {row['title']}（{row['status'] or '状态未知'}）")
    body: list[str] = []
    evidence: list[str] = []
    for position, row in enumerate(ran_stages, start=1):
        md_path = artifacts.stage_paths(code, day, row["stage"], root)[1]
        try:
            text = md_path.read_text(encoding="utf-8")
        except OSError as exc:
            text = f"（本段 Markdown 读不出来：{type(exc).__name__}）\n"
        body += ["", f"## {position}. {row['title']}", "", _strip_leading_title(text)]
        evidence.append(
            f"- {row['title']}：状态 {row['status'] or '-'} · 来源 {row['sources']} 条"
            f"{' · 未核实' if row['unverified'] else ''}"
            f"{' · 降级' if row['degraded'] else ''}"
            + (f" · 失败：{row['failure']}" if row["failure"] else "")
        )
    if not ran_stages:
        body += ["", "（本档案没有任何已写出的段落文件。）"]
    tail = ["", "## 依据链总览", ""] + evidence
    missing = item["missing"]
    if missing:
        tail += ["", f"> 未产出段落：{'、'.join(missing)}。这份档案不完整，引用前先补跑。"]
    return "\n".join(title_lines + body + tail).rstrip() + "\n"


def build_payload(item: dict[str, Any], root: Any = None) -> dict[str, Any]:
    """结构化导出（JSON）：段级体检 + 每段 Markdown 原文。

    和 MD 导出同源：数字与状态都直接取自已落盘产物，不再重算；下游程序要引用
    哪一段，拿 `stages[].markdown` 就是那一份原文，不需要再解析 Markdown。
    """
    stages: list[dict[str, Any]] = []
    for row in item["stages"]:
        markdown: str | None = None
        if row["has_md"]:
            md_path = artifacts.stage_paths(item["code"], item["date"], row["stage"], root)[1]
            try:
                markdown = md_path.read_text(encoding="utf-8")
            except OSError:
                markdown = None
        stages.append(
            {
                "stage": row["stage"],
                "title": row["title"],
                "status": row["status"],
                "has_json": row["has_json"],
                "has_md": row["has_md"],
                "sources": row["sources"],
                "degraded": row["degraded"],
                "unverified": row["unverified"],
                "corrupt": row["corrupt"],
                "failure": row["failure"],
                "updated_at": row["updated_at"],
                "markdown": markdown,
            }
        )
    return {
        "code": item["code"],
        "date": item["date"],
        "updated_at": item["updated_at"],
        "complete": item["complete"],
        "missing": item["missing"],
        "degraded": item["degraded"],
        "unverified": item["unverified"],
        "failed": item["failed"],
        "stages": stages,
    }


def export(
    code: str,
    day: str | None = None,
    root: Any = None,
    out: Any = None,
    fmt: str = "md",
) -> dict[str, Any]:
    """导出一份完整档案：`md`（可读交付件）或 `json`（结构化，给程序消费）。"""
    item = find(code, day, root)
    if item is None:
        return {"ok": False, "reason": f"没有 {code} 在 {day or '任意日期'} 的研究档案"}
    if fmt == "json":
        text = json.dumps(build_payload(item, root), ensure_ascii=False, indent=2, default=str) + "\n"
        suffix = "json"
    elif fmt == "md":
        text = build_markdown(item, root)
        suffix = "md"
    else:
        return {"ok": False, "reason": f"不支持的导出格式 {fmt!r}（只支持 md / json）"}
    target = (
        Path(out)
        if out is not None
        else archive_root(root) / item["date"] / f"{item['code']}.{suffix}"
    )
    atomic_write(target, text)
    return {
        "ok": True,
        "code": item["code"],
        "date": item["date"],
        "format": suffix,
        "path": str(target),
        "bytes": len(text.encode("utf-8")),
        "complete": item["complete"],
        "missing": item["missing"],
        "unverified": item["unverified"],
    }


# --------------------------------------------------------------------------- #
# 检索
# --------------------------------------------------------------------------- #


def search(keyword: str, root: Any = None, limit: int = 20) -> list[dict[str, Any]]:
    """按关键词查档案：先匹配代码/日期，再在各段 md 里找原文，回命中片段与段名。"""
    needle = keyword.strip()
    if not needle:
        return []
    hits: list[dict[str, Any]] = []
    items = scan(root)
    read_count = 0
    for item in items:
        meta_hit = needle in item["code"] or needle in item["date"]
        if meta_hit:
            hits.append({**item, "matches": [], "matched_by": "meta"})
            if len(hits) >= limit:
                break
            continue
        if read_count >= SEARCH_READ_LIMIT or len(hits) >= limit:
            continue
        matches: list[dict[str, str]] = []
        for row in item["stages"]:
            md_path = artifacts.stage_paths(item["code"], item["date"], row["stage"], root)[1]
            if not md_path.is_file():
                continue
            read_count += 1
            try:
                text = md_path.read_text(encoding="utf-8")
            except OSError:
                continue
            for line in text.splitlines():
                if needle in line:
                    matches.append(
                        {"stage": row["stage"], "stage_title": row["title"], "text": line.strip()[:200]}
                    )
                    if len(matches) >= 5:
                        break
            if len(matches) >= 5:
                break
        if matches:
            hits.append({**item, "matches": matches, "matched_by": "content"})
    return hits[:limit]


__all__ = [
    "ARCHIVE_SUBDIR",
    "INDEX_NAME",
    "archive_root",
    "build_index",
    "build_markdown",
    "export",
    "find",
    "research_root",
    "scan",
    "search",
    "write_index",
]
