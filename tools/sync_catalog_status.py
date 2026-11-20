"""按工具审计结果同步 `content/skills/catalog.json` 的状态。

口径（只认实测）：
- 某个方向用到的工具全部 `ok`（或动作类 / 页面自身接口）→ `ready`；
- 部分可用 → `partial`，note 里点名哪个工具、什么原因；
- 全部失败 → `unconnected`，note 写真实原因。
- `existing_page` / `not_supported` 不动：它们表示「已在别的页面」与「本阶段不做」，
  不是连通性问题。
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(r"D:\workplace\LadderStock")
CATALOG = ROOT / "content" / "skills" / "catalog.json"
AUDIT = ROOT / "state" / "skill_tools_audit.json"

OK_STATES = {"ok", "action", "local_api"}
#: 这些方向不按连通性改状态
FROZEN = {"existing_page", "not_supported"}


def load_tools(package: str, steps: list[int]) -> list[str]:
    path = ROOT / "content" / "skills" / package / "steps.json"
    if not path.is_file():
        return []
    doc = json.loads(path.read_text(encoding="utf-8"))
    out: list[str] = []
    for pos, step in enumerate(doc.get("steps", []), start=1):
        # steps.json 的步骤没有 id 字段，位置序号（1 起）才是它的编号
        index = step.get("index", step.get("no", pos))
        if steps and index not in steps:
            continue
        for tool in step.get("tools") or []:
            if tool not in out:
                out.append(tool)
    return out


def main() -> int:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    audit = json.loads(AUDIT.read_text(encoding="utf-8"))

    changed: list[str] = []
    for item in catalog["items"]:
        status = item["status"]
        if status in FROZEN:
            continue
        tools = load_tools(item["package"], item.get("steps") or [])
        if not tools:
            continue
        rows = [(tool, audit.get(tool, {})) for tool in tools]
        ok = [tool for tool, row in rows if row.get("status") in OK_STATES]
        bad = [(tool, row) for tool, row in rows if row.get("status") not in OK_STATES]
        if not bad:
            new_status, note = "ready", "工具实测可达（2026-11-18 审计）"
        elif ok:
            reasons = "；".join(
                f"{tool}（{row.get('status')}: {row.get('detail', '')[:60]}）" for tool, row in bad
            )
            new_status, note = "partial", f"部分工具未通：{reasons}"
        else:
            reasons = "；".join(
                f"{tool}（{row.get('status')}: {row.get('detail', '')[:60]}）" for tool, row in bad
            )
            new_status, note = "unconnected", f"工具均未通：{reasons}"
        if item["status"] != new_status:
            changed.append(f"#{item['id']} {item['label']}: {item['status']} → {new_status}")
            item["status"] = new_status
            item["note"] = note
        elif item.get("note") and new_status != "ready":
            item["note"] = note

    tally: dict[str, int] = {}
    for item in catalog["items"]:
        tally[item["status"]] = tally.get(item["status"], 0) + 1
    catalog["status_count"] = tally
    catalog["updated"] = "2026-11-18"
    CATALOG.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"改动 {len(changed)} 条：")
    for line in changed:
        print("  " + line)
    print("新状态分布:", json.dumps(tally, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
