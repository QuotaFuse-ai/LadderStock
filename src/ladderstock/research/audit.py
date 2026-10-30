"""报告审计：只读已落盘的产物，判「每个数字都能点回出处」这条能不能成立。

`--check` 走这条路，**不调模型、不碰数据层**（DEC-024：报告段 0 次取数；
`ldst research <code> --check` 同样一次外部调用都不发）。审计两件事：

1. 结构化件自洽：`provenance.values` 回指的来源必须存在，声称已核实的必须真有人给出处，
   `facts` 里没有出处的数值字段只能落在「未核实」这一侧；
2. 人读件合格：`<stage>.md` 里每个数字带 `[n]` 或〔未核实〕，`[n]` 指得回去、
   且角的值与数字对得上（借真引用报假数也判不合格）。

`report` 段之外也能审（`stage=<stage>`），因为六段共用同一份形状与同一个渲染器。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ladderstock.core.boundary import numeric_value
from ladderstock.research import artifacts, checkpoint, citation

#: 审计的小节里不算指标的键（叙述与上游结构，数字只有一份，落在各段 facts）
_NON_METRIC_KEYS = frozenset({"ai", "upstream", "provenance", "failure", "attempts"})

MISSING_ARTIFACT = "missing"


def audit_document(document: Mapping[str, Any]) -> list[str]:
    """结构化件自洽检查：返回问题列表，空列表即合格。"""
    slot = document.get("provenance") or {}
    values: Mapping[str, Any] = slot.get("values") or {}
    entries = list(slot.get("sources") or [])
    sources = {str(item.get("id")): item for item in entries if isinstance(item, Mapping)}
    problems: list[str] = []

    for key in sorted(values):
        entry = values[key]
        refs = list((entry or {}).get("sources") or [])
        if not refs:
            problems.append(f"provenance.values.{key} 列出了指标却没有引用来源")
            continue
        for ref in refs:
            if str(ref) not in sources:
                problems.append(f"provenance.values.{key} 指向不存在的来源 {ref}")
        claimed = not bool((entry or {}).get("unverified"))
        backed = all(sources[str(ref)].get("verified") for ref in refs if str(ref) in sources)
        if claimed and not backed:
            problems.append(f"provenance.values.{key} 声称已核实，但引用来源 verified=false")

    if not entries and slot.get("unverified") is not True:
        problems.append("provenance.sources 为空但 unverified=false（没有来源不许当已核实）")

    # 没有出处的数值字段：必须让整件保持「未核实」
    metrics = document.get("facts") or {}
    unbacked = sorted(
        str(key)
        for key, value in metrics.items()
        if key not in _NON_METRIC_KEYS and numeric_value(value) is not None and key not in values
    )
    if unbacked and slot.get("unverified") is not True:
        problems.append(
            "以下数值字段没有 provenance，却把整件标成已核实：" + "、".join(unbacked)
        )
    return problems


def audit_stage(
    code: Any,
    day: Any,
    *,
    stage: str = checkpoint.REPORT_STAGE,
    root: Any = None,
) -> dict[str, Any]:
    """审计一段产物的两件文件；产物缺失返回 `status=missing`（CLI 据此走退出码 6）。"""
    safe_code = checkpoint.normalize_code(code)
    json_path, md_path = artifacts.stage_paths(safe_code, day, stage, root)
    document = artifacts.read_stage(safe_code, day, stage, root)
    if document is None or not md_path.is_file():
        # 键集合与已审计时一致（`--output json` 契约稳定，消费方不必为缺产物分支写特判）
        return {
            "status": MISSING_ARTIFACT,
            "code": safe_code,
            "date": str(day),
            "stage": stage,
            "json": str(json_path),
            "md": str(md_path),
            "ok": False,
            "reason": "产物不存在或损坏：结构化件或人读件缺失",
            "counts": {"numbers": 0, "cited": 0, "unverified": 0, "exempt": 0, "sources": 0},
            "exempt_hits": {},
            "unused_sources": [],
            "findings": [],
            "problems": [],
        }

    markdown = md_path.read_text(encoding="utf-8")
    index = citation.index_from_slot(document.get("provenance") or {})
    result = citation.audit_text(markdown, index, identifiers=[safe_code])
    problems = audit_document(document)

    ok = result["ok"] and not problems
    payload: dict[str, Any] = {
        "status": "audited",
        "code": safe_code,
        "date": str(document.get("date") or day),
        "stage": stage,
        "json": str(json_path),
        "md": str(md_path),
        "ok": ok,
        "reason": None,
        "counts": result["counts"],
        "exempt_hits": result["exempt_hits"],
        "unused_sources": result["unused_sources"],
        "findings": result["findings"],
        "problems": problems,
    }
    if not ok:
        payload["reason"] = (
            "报告审计不通过：" + f"{len(result['findings'])} 处数字引用不合格"
            + ("" if not problems else f"，{len(problems)} 处产物自洽问题")
        )
    return payload


__all__ = ["MISSING_ARTIFACT", "audit_document", "audit_stage"]
