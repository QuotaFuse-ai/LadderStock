"""六段的提示词与消息组装（R1-2 前三段 + R1-3 后三段）。

每段的「提示词 + 产物结构」都在这里定稿：

- 提示词：`prompts/research/<stage>.md`，只讲本段该写什么、不该写什么；
- 产物结构：与五段复盘**同一个** `SECTION_SCHEMA`（`narrative` 必需，
  `highlights` / `risks` / `evidence` 可选）。刻意不给六段各造一套 Schema：
  Schema 里一个指标键都不能有（DEC-006 闸门 5），六段之间的差别是**关注点**，
  不是字段，字段不同会让 R3 的溯源和 R8 的页面各写一套分支；
- 消息：沿用骨架的 `段落标识：<stage>` 行，报告段与假客户端都靠它认段。

`report` 段的快照只有 `upstream`（前五段已落盘的产物），不重新取数。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ladderstock.ai import structured_output
from ladderstock.research import checkpoint

#: R1-2 落地前三段，R1-3 落地后三段；六段现在共用同一条三级降级阶梯
FRONT_STAGES: tuple[str, ...] = checkpoint.STAGES[:3]
BACK_STAGES: tuple[str, ...] = checkpoint.STAGES[3:]

PROMPT_FILES: dict[str, str] = {
    "company": "prompts/research/company.md",
    "financial": "prompts/research/financial.md",
    "forecast": "prompts/research/forecast.md",
    "valuation": "prompts/research/valuation.md",
    "risk": "prompts/research/risk.md",
    "report": "prompts/research/report.md",
}

#: 六段的产物结构：直接复用五段复盘的叙述 Schema（无指标键），不另造
STAGE_SCHEMA: dict[str, Any] = structured_output.SECTION_SCHEMA

JSON_RULES = (
    "\n\n硬约束（输出格式）：\n"
    "1. 只输出一个 JSON 对象，字段仅限 narrative / highlights / risks / evidence；\n"
    "2. narrative 为字符串数组，每个元素一段完整叙述；\n"
    "3. 不得输出任何指标数字键，数字一律由程序计算；\n"
    "4. 引用数字必须与给定快照逐字一致，快照没有的就说没有。"
)


def _project_root() -> Path:
    from ladderstock.cli.paths import project_root

    return project_root()


def prompt_text(stage: str) -> str:
    """读本段提示词；文件缺失时回落到最小可用指令，不让整条链路崩掉。"""
    relative = PROMPT_FILES.get(stage)
    if relative is None:
        raise ValueError(f"未知研究段：{stage}")
    target = _project_root() / relative
    try:
        return target.read_text(encoding="utf-8").strip()
    except OSError:
        title = checkpoint.STAGE_TITLES.get(stage, stage)
        return f"你是 A 股个股研究助手，只写「{title}」这一段的叙述，不写任何指标数字。"


def build_messages(
    code: str, stage: str, day: str, facts: dict[str, Any]
) -> tuple[str, str]:
    """拼 system / user；快照只读，AI 只能追加叙述。"""
    title = checkpoint.STAGE_TITLES.get(stage, stage)
    system = f"{prompt_text(stage)}\n\n通用硬约束：\n{_constraints(title)}{JSON_RULES}"
    user = (
        f"证券代码：{code}\n"
        f"研究日期：{day}\n"
        f"段落标识：{stage}\n"
        f"段落：{title}\n"
        "程序事实快照（只读）：\n"
        f"{json.dumps(facts, ensure_ascii=False, indent=2, default=str)}\n\n"
        "请基于快照写本段叙述，输出 JSON。"
    )
    return system, user


def plain_messages(
    code: str, stage: str, day: str, facts: dict[str, Any]
) -> tuple[str, str]:
    """第三级降级（纯文本）用的消息：同样约束，但明确要求纯文本。"""
    system, user = build_messages(code, stage, day, facts)
    return system.replace(JSON_RULES, _PLAIN_RULES), user.replace("输出 JSON。", "直接写叙述。")


def _constraints(title: str) -> str:
    return (
        f"1. 只写「{title}」这一段的叙述，不写其他段；\n"
        "2. 不得输出或改写任何指标数字——数字由程序计算，你只能引用给定快照；\n"
        "3. 不联网、不编造来源；快照缺什么就明说缺什么；\n"
        "4. 中文输出。"
    )


_PLAIN_RULES = (
    "\n\n硬约束（输出格式）：\n"
    "1. 中文纯文本输出，按段落分行，不要 JSON、不要代码围栏；\n"
    "2. 不得输出或改写任何指标数字，只能引用给定快照里的数字；\n"
    "3. 不联网、不编造来源。"
)


__all__ = [
    "BACK_STAGES",
    "FRONT_STAGES",
    "PROMPT_FILES",
    "STAGE_SCHEMA",
    "build_messages",
    "prompt_text",
]
