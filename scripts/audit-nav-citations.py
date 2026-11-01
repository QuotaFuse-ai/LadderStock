#!/usr/bin/env python3
"""QUOT-304：`docs/UI-SPEC.md` 的侧栏声明 vs `frontend/src/components/Layout.tsx` 的真实侧栏。

改导航最容易留静默例外：文档写「侧栏『市场』分组『事件日历』」，代码里那条已经搬走了；
或者 `Layout.tsx:80` 这种行号还指着上一次提交的布局。QUOT-300 的 vitest 兜住了
「代码 ↔ 路由表」这一半，本脚本兜另一半——「文档 ↔ 代码」。

三条判定：
  R1 §0 表「侧栏入口」列：`组 · 条目` 必须能在真实侧栏里按路由找到；
     写「无侧栏入口」的行必须真的没有侧栏项，写「底部固定项」的必须在 `PINNED_NAV`。
  R2 §3 各屏「入口」行：`侧栏「G」分组「L」` / `侧栏首项「L」` 与真实分组条目一致；
     行内引用的 `Layout.tsx:N` / `N-M` 若落进侧栏声明区，就必须命中该行说的那条目——
     落在侧栏声明区之外（侧栏宽、`main#main` 这类外壳行号）不算错。
  R3 反向：真实侧栏每一条路由都必须在 §0 表里登记（防止加了入口忘了写文档）。

用法：仓库根目录 `python scripts/audit-nav-citations.py`（CI 里加 `--quiet`）。退出码非 0 = 不一致。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

NAV_START = re.compile(r"^export const NAV_SECTIONS")
PINNED_START = re.compile(r"^export const PINNED_NAV")
SECTION_KEY = re.compile(r"^    key: '([a-z-]+)',")
SECTION_LABEL = re.compile(r"^    label: '([^']+)',")
PAGE = re.compile(r"\{ to: '([^']+)', label: '([^']+)'")


def parse_nav(layout_text: str):
    """返回 (sections, pinned, nav_span)。sections = [{key,label,entries:[(line,to,label)]}]。"""
    lines = layout_text.splitlines()
    sections: list[dict] = []
    pinned: list[tuple[int, str, str]] = []
    nav_lo = nav_hi = None
    cur = None
    in_nav = False
    for i, line in enumerate(lines, start=1):
        if NAV_START.match(line):
            in_nav, nav_lo = True, i
            continue
        if PINNED_START.match(line):
            in_nav, nav_hi = False, i - 1
            m = PAGE.search(line)
            if m:
                pinned.append((i, m.group(1), m.group(2)))
            continue
        if not in_nav:
            continue
        m = SECTION_KEY.match(line)
        if m:
            cur = {"key": m.group(1), "label": None, "entries": []}
            sections.append(cur)
            continue
        m = SECTION_LABEL.match(line)
        if m and cur is not None and cur["label"] is None:
            cur["label"] = m.group(1)
            continue
        m = PAGE.search(line)
        if m and cur is not None:
            cur["entries"].append((i, m.group(1), m.group(2)))
    return sections, pinned, (nav_lo, nav_hi)


def all_entries(sections):
    """摊平成 (组名, 条目名, 行号, to)。"""
    return [(s["label"], lab, line, to) for s in sections for line, to, lab in s["entries"]]


def find_entry(sections, path, label=None):
    """按路由（忽略 `?tab=`）与可选标签查侧栏条目 → [(组名, 条目名, 行号)]。"""
    base = path.split("?")[0]
    return [
        (group, lab, line)
        for group, lab, line, to in all_entries(sections)
        if to.split("?")[0] == base and (label is None or lab == label)
    ]


def cited_spans(cite: str):
    """`Layout.tsx:90-92,197-203` → [(90,92),(197,203)]。"""
    spans = []
    for chunk in re.findall(r"Layout\.tsx:([\d,\- ]+)", cite):
        for part in [p.strip() for p in chunk.split(",")]:
            m = re.match(r"^(\d+)(?:-(\d+))?$", part)
            if m:
                spans.append((int(m.group(1)), int(m.group(2) or m.group(1))))
    return spans


def audit_section0(spec_lines, sections, pinned):
    """R1：§0 逐屏表的「侧栏入口」列。返回 (problems, 已登记路由集合)。"""
    problems = []
    documented = set()
    for ln, text in enumerate(spec_lines, start=1):
        if not re.match(r"^\|\s*\d+\s*\|", text):
            continue
        escaped = text.replace(r"\|", "\x00")
        cells = [c.strip() for c in escaped.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        path_cell, cell = [c.replace("\x00", "|") for c in (cells[2], cells[3])]
        pm = re.search(r"`(/[^`]*)`", path_cell.replace(r"\|", "~"))
        if not pm:
            continue
        path = pm.group(1).split("~")[0].split("（")[0]
        documented.add(path.split("?")[0])
        if cell.startswith("无侧栏入口"):
            if find_entry(sections, path) or any(to.split("?")[0] == path for _, to, _ in pinned):
                problems.append(f"UI-SPEC.md:{ln} 写「无侧栏入口」，但 `{path}` 在侧栏里有入口")
            continue
        if "底部固定" in cell:
            if not any(to.split("?")[0] == path for _, to, _ in pinned):
                problems.append(f"UI-SPEC.md:{ln} 写「侧栏底部固定项」，但 `{path}` 不在 `PINNED_NAV`")
            continue
        gm = re.match(r"^(.+?) · (.+?)$", cell)
        if not gm:
            problems.append(f"UI-SPEC.md:{ln} 「侧栏入口」列不是 `组 · 条目` 格式：{cell}")
            continue
        group = gm.group(1).strip()
        label = re.sub(r"（.*?）$", "", gm.group(2).strip()).strip()
        hits = find_entry(sections, path)
        if not hits:
            problems.append(f"UI-SPEC.md:{ln} 写「{group} · {label}」，侧栏里没有指向 `{path}` 的条目")
        else:
            real_group, real_label, _ = hits[0]
            if real_group != group:
                problems.append(f"UI-SPEC.md:{ln} 写组「{group}」，`{path}` 现在挂在「{real_group}」")
            if " / " not in label and real_label != label:
                problems.append(
                    f"UI-SPEC.md:{ln} 写条目「{label}」，`{path}` 在侧栏里的标签是「{real_label}」"
                )
    return problems, documented


def audit_screen_rows(spec_lines, sections, nav_span):
    """R2：§3 各屏「入口」行的分组/条目声明与 Layout 行号。"""
    problems = []
    entry_row = re.compile(r"^\| 入口 \|(.+?)\|(.+?)\|")
    nav_lo, nav_hi = nav_span
    entries = all_entries(sections)
    for ln, text in enumerate(spec_lines, start=1):
        m = entry_row.match(text)
        if not m:
            continue
        claim, cite = m.group(1).strip(), m.group(2).strip()
        gm = re.search(r"侧栏「([^」]+)」分组「([^」]+)」", claim)
        hm = re.search(r"侧栏首项「([^」]+)」", claim)
        plain_group = re.search(r"侧栏「([^」]+)」", claim)
        want_label = gm.group(2) if gm else (hm.group(1) if hm else None)
        want_group = gm.group(1) if gm else (plain_group.group(1) if plain_group and not hm else None)

        if want_label:
            hit = [e for e in entries if e[1] == want_label]
            if not hit:
                problems.append(f"UI-SPEC.md:{ln} 侧栏里没有条目「{want_label}」")
            elif want_group and hit[0][0] != want_group:
                problems.append(
                    f"UI-SPEC.md:{ln} 条目「{want_label}」现在挂在「{hit[0][0]}」，文档写「{want_group}」"
                )

        for a, b in cited_spans(cite):
            if nav_lo is None or b < nav_lo or a > (nav_hi or a):
                continue  # 指外壳行号，不是侧栏条目引用
            inside = {lab for _, lab, line, _ in entries if a <= line <= b}
            if not inside:
                problems.append(
                    f"UI-SPEC.md:{ln} 引用 `Layout.tsx:{a}-{b}` 落进侧栏声明区却没有条目（过期行号）"
                )
            elif want_label and want_label not in inside:
                problems.append(
                    f"UI-SPEC.md:{ln} `Layout.tsx:{a}-{b}` 指的是 {sorted(inside)}，正文说的是「{want_label}」"
                )
    return problems


def audit_reverse(sections, pinned, documented):
    """R3：真实侧栏每条路由都要在 §0 登记过。"""
    problems = [
        f"Layout.tsx:{line} 侧栏条目「{group} · {label}」(`{to}`) 在 §0 表里没有登记"
        for group, label, line, to in all_entries(sections)
        if to.split("?")[0] not in documented
    ]
    problems += [
        f"底部固定项「{lab}」(`{to}`) 在 §0 表里没有登记"
        for _, to, lab in pinned
        if to not in documented
    ]
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", default="docs/UI-SPEC.md")
    ap.add_argument("--layout", default="frontend/src/components/Layout.tsx")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    layout_text = Path(args.layout).read_text(encoding="utf-8")
    spec_lines = Path(args.spec).read_text(encoding="utf-8").splitlines()
    sections, pinned, nav_span = parse_nav(layout_text)

    p1, documented = audit_section0(spec_lines, sections, pinned)
    p2 = audit_screen_rows(spec_lines, sections, nav_span)
    p3 = audit_reverse(sections, pinned, documented)
    problems = p1 + p2 + p3

    if not args.quiet:
        print("真实侧栏：")
        for s in sections:
            print(f"  [{s['key']}] {s['label']}：", " / ".join(f"{lab}({to})" for _, to, lab in s["entries"]))
        print("  [pinned] 底部固定：", " / ".join(f"{lab}({to})" for _, to, lab in pinned))
    print(f"§0 登记 {len(documented)} 条路由，侧栏 {len(all_entries(sections)) + len(pinned)} 条入口")
    if problems:
        print(f"\n发现 {len(problems)} 处文档 ↔ 侧栏不一致：")
        for p in problems:
            print("  - " + p)
        return 1
    print("结论：UI-SPEC 的侧栏声明与 Layout.tsx 一致（分组、条目、行号三项都对得上）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
