"""六段的提示词与消息组装（R1-2 前三段 + R1-3 后三段）。

每段的「提示词 + 产物结构」都在这里定稿：

- 提示词：`prompts/research/<stage>.md`，只讲本段该写什么、不该写什么；
- 产物结构：与五段复盘**同一个** `SECTION_SCHEMA`（`narrative` 必需，
  `highlights` / `risks` / `evidence` 可选）。刻意不给六段各造一套 Schema：
  Schema 里一个指标键都不能有（DEC-006 闸门 5），六段之间的差别是**关注点**，
  不是字段，字段不同会让 R3 的溯源和 R8 的页面各写一套分支；
- 消息：沿用骨架的 `段落标识：<stage>` 行，报告段与假客户端都靠它认段。

`report` 段的快照只有 `upstream`（前五段已落盘的产物），不重新取数。

R2（QUOT-140）在这里再加一张**代码内常量**表 `ALLOWED_TOOLS`：每段能用哪几个
受控工具。运行时不接受任何覆盖参数，也不读 `config/ai.yaml`——与上面
`PROMPT_FILES` 同一个口径（DEC-021 §4），因为「模型能碰什么」是边界，不是配置。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ladderstock.ai import structured_output, tools
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

#: 每段的受控工具白名单（R2 验收：白名单外的调用第 1 次即判该段失败）。
#: 逐条理由（详表与权衡见 DEC-026）：
#:
#: - 读类（`list_artifacts` / `read_artifact`）六段都给：只能看本次研究目录内的
#:   落盘产物，越不出 `<code>/<date>`，也不触网；
#: - `compute` 不给 `valuation`（DEC-025 §2「估值数字的唯一来源是 facts，段内与
#:   提示词都不许算」），也不给 `report`（报告段只汇总前五段已落盘的叙述，
#:   不产生新数字）；给了 `compute` 的段，数字也只能是快照里的或工具算出来的，
#:   模型仍不许心算（DEC-006 闸门 5）；
#: - `write_stage` 只给前五段：每段只能写自己的草稿文件，报告段写的是报告；
#: - `write_report` 只给 `report`；
#: - `search_web` 按 DEC-018 进表（缺口在行业景气 / 资金流 / 资讯 / 日历四类输入，
#:   对应 company / financial / forecast / risk），但 `allowed_tools()` 在
#:   `LDST_WEB_SEARCH` 没开时把它剔掉——默认零外呼，取数实现仍属 R4。
ALLOWED_TOOLS: dict[str, tuple[str, ...]] = {
    "company": (
        tools.LIST_ARTIFACTS,
        tools.READ_ARTIFACT,
        tools.COMPUTE,
        tools.WRITE_STAGE,
        tools.SEARCH_WEB,
    ),
    "financial": (
        tools.LIST_ARTIFACTS,
        tools.READ_ARTIFACT,
        tools.COMPUTE,
        tools.WRITE_STAGE,
        tools.SEARCH_WEB,
    ),
    "forecast": (
        tools.LIST_ARTIFACTS,
        tools.READ_ARTIFACT,
        tools.COMPUTE,
        tools.WRITE_STAGE,
        tools.SEARCH_WEB,
    ),
    "valuation": (tools.LIST_ARTIFACTS, tools.READ_ARTIFACT, tools.WRITE_STAGE),
    "risk": (
        tools.LIST_ARTIFACTS,
        tools.READ_ARTIFACT,
        tools.COMPUTE,
        tools.WRITE_STAGE,
        tools.SEARCH_WEB,
    ),
    "report": (tools.LIST_ARTIFACTS, tools.READ_ARTIFACT, tools.WRITE_REPORT),
}

#: 六段的产物结构：直接复用五段复盘的叙述 Schema（无指标键），不另造
STAGE_SCHEMA: dict[str, Any] = structured_output.SECTION_SCHEMA


def _self_check_tables() -> None:
    """导入期自检：白名单表必须覆盖六段，且每个名字都在工具注册表里。

    宁可模块导入就报错，也不要等到某段跑起来才发现少了一段。
    """
    for stage in checkpoint.STAGES:
        names = ALLOWED_TOOLS[stage]
        tools.validate_allowed(names)
        if not names:
            raise ValueError(f"{stage} 段的工具白名单为空，R2 之后每段至少要有读产物的能力")


_self_check_tables()

JSON_RULES = (
    "\n\n硬约束（输出格式）：\n"
    "1. 只输出一个 JSON 对象，字段仅限 narrative / highlights / risks / evidence；\n"
    "2. narrative 为字符串数组，每个元素一段完整叙述；\n"
    "3. 不得输出任何指标数字键，数字一律由程序计算；\n"
    "4. 引用数字必须与给定快照逐字一致，快照没有的就说没有；\n"
    "5. 不要自己写引用角标 `[n]` 或〔未核实〕，来源标记一律由程序渲染。"
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


def allowed_tools(stage: str) -> tuple[str, ...]:
    """本段实际可用的工具。

    只可能比 `ALLOWED_TOOLS` **更窄**：受控检索闸门没开时把 network 工具剔掉，
    任何情况下都不会因为运行时参数多出几个名字来（R2「不接受运行时覆盖」）。
    """
    names = ALLOWED_TOOLS.get(stage)
    if names is None:
        raise ValueError(f"未知研究段：{stage}")
    if tools.env_enabled(tools.WEB_SEARCH_ENV):
        return names
    return tuple(name for name in names if not tools.TOOLS[name].network)


def build_messages(
    code: str,
    stage: str,
    day: str,
    facts: dict[str, Any],
    *,
    provenance: Mapping[str, Any] | None = None,
    tool_names: Sequence[str] | None = None,
) -> tuple[str, str]:
    """拼 system / user；快照只读，AI 只能追加叙述。

    R2：`tool_names` 只在真要发工具请求时传（客户端支持 tool-calling）：不传时
    system 与 R2 之前逐字相同，纯文本路径不受影响。
    R3：指标进上下文时**同时**带出处（`provenance` 块），模型才有得可引；
    没有出处的数字它照样只能照抄，渲染阶段会被标成〔未核实〕。
    """
    title = checkpoint.STAGE_TITLES.get(stage, stage)
    instruction = tools.tool_instruction(tool_names or ())
    system = f"{prompt_text(stage)}\n\n通用硬约束：\n{_constraints(title)}{JSON_RULES}{instruction}"
    user = (
        f"证券代码：{code}\n"
        f"研究日期：{day}\n"
        f"段落标识：{stage}\n"
        f"段落：{title}\n"
        "程序事实快照（只读）：\n"
        f"{json.dumps(facts, ensure_ascii=False, indent=2, default=str)}\n"
        f"{_provenance_block(provenance)}\n"
        "请基于快照写本段叙述，输出 JSON。"
    )
    return system, user


def plain_messages(
    code: str,
    stage: str,
    day: str,
    facts: dict[str, Any],
    *,
    provenance: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    """第三级降级（纯文本）用的消息：同样约束，但明确要求纯文本。

    刻意不接 `tool_names`：第三级是「模型连结构化都没做到」的收口，
    再挂工具只会多烧一轮额度，改不了格式问题。
    """
    system, user = build_messages(code, stage, day, facts, provenance=provenance)
    return system.replace(JSON_RULES, _PLAIN_RULES), user.replace("输出 JSON。", "直接写叙述。")


def _provenance_block(provenance: Mapping[str, Any] | None) -> str:
    """出处块：没有来源就明说「本段数值全部未核实」，而不是留个空对象让模型猜。"""
    if not provenance or not (provenance.get("sources") or provenance.get("values")):
        return "数字出处（只读）：本段没有任何引用来源，出现的数字一律按未核实处理。\n"
    return (
        "数字出处（只读，程序写入）：\n"
        f"{json.dumps(provenance, ensure_ascii=False, indent=2, default=str)}\n"
    )


def _constraints(title: str) -> str:
    return (
        f"1. 只写「{title}」这一段的叙述，不写其他段；\n"
        "2. 不得输出或改写任何指标数字——数字由程序计算，你只能引用给定快照；\n"
        "3. 不联网、不编造来源；快照缺什么就明说缺什么；\n"
        "4. 不自行标注引用角标或「未核实」，来源标记由程序渲染；\n"
        "5. 中文输出。"
    )


_PLAIN_RULES = (
    "\n\n硬约束（输出格式）：\n"
    "1. 中文纯文本输出，按段落分行，不要 JSON、不要代码围栏；\n"
    "2. 不得输出或改写任何指标数字，只能引用给定快照里的数字；\n"
    "3. 不联网、不编造来源，也不自己标引用角标。"
)


__all__ = [
    "ALLOWED_TOOLS",
    "BACK_STAGES",
    "FRONT_STAGES",
    "PROMPT_FILES",
    "STAGE_SCHEMA",
    "allowed_tools",
    "build_messages",
    "plain_messages",
    "prompt_text",
]
