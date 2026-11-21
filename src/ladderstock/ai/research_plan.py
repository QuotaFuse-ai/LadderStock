"""F2 研究计划：提问后先出一条可改的步骤清单，用户确认后再执行。

为什么值得做：F1 让用户**看见**执行，但看见不等于可控。投研场景里"这一步不用查"
是很常见的判断（已经知道公告没新东西、只想看估值），用户没有机会表达，就只能
拿到一份自己并不想要的八步报告。计划先行 + 可编辑 = 把「查什么」的主动权还给用户。

两条边界：

- **计划只做指引，不做硬闸门**。用户删掉某一步，就用一句「这一步用户取消了，不要查」
  告诉模型；模型仍可能因为答题需要而调用，这是允许的——硬拦会答不出。
  用户改动被静默吞掉才是真问题，所以取消的步骤会同时出现在回答的流水账上。
- **草案不带数字**：标题是描述性的（「拉取个股行情」），不写「拉近 30 日」「查 3 家」。
  用户没确认之前，任何具体范围都不该被当成承诺。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

#: 计划上限：与前端清单高度一致，超过四行就不像"计划"而像"目录"
MAX_STEPS = 6
#: 用户自己加的最多两条——加太多等于把提问重写了一遍，不如新开一轮
MAX_ADDED = 2
#: 单条标题长度上限（前端卡片一行放得下）
MAX_TITLE = 24

#: 判断类问题的信号词：出现即视为"要给结论"，计划里就该有一步"汇总证据 + 翻转条件"
_JUDGE_HINTS = (
    "怎么看",
    "能不能",
    "该不该",
    "值不值",
    "值不值得",
    "买不买",
    "卖不卖",
    "估值",
    "贵不贵",
    "便宜",
    "判断",
    "观点",
    "建议",
    "风险",
)

#: 比较类信号词：多标的对比要单独一步，否则模型容易只查一个
_COMPARE_HINTS = ("对比", "比较", " vs ", "VS", "哪个好", "孰优")

_TITLE_CLEAN = re.compile(r"[\r\n\t]+")


@dataclass(frozen=True)
class Step:
    """计划里的一步。

    `tool` 只是"这一步大概会用到哪个工具"的提示，用于对齐流水账，**不参与执行判断**；
    模型完全可以用别的工具达到同样的目的。
    """

    id: str
    title: str
    tool: str | None = None
    kind: str = "lookup"

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "kind": self.kind}


def _is_judge(question: str) -> bool:
    text = (question or "").lower()
    return any(hint in text for hint in _JUDGE_HINTS)


def _is_compare(question: str) -> bool:
    return any(hint in question for hint in _COMPARE_HINTS)


def draft_steps(
    question: str,
    *,
    symbol: str | None = None,
    kinds: Iterable[str] = (),
) -> list[Step]:
    """按问题生成 3–6 步草案。纯规则，不起模型——计划本身不该再花一次推理。"""
    kinds_set = {str(kind) for kind in kinds}
    steps: list[Step] = []

    def add(title: str, tool: str | None, kind: str) -> None:
        if len(steps) >= MAX_STEPS - 1:  # 最后一步固定留给"汇总"，先占位
            return
        if any(step.title == title for step in steps):
            return
        steps.append(Step(id=f"s{len(steps) + 1}", title=title, tool=tool, kind=kind))

    if symbol:
        add("确认标的与所属行业", "lookup_symbols", "lookup")
        add("拉取个股行情与日线", "lookup_quote", "quote")
    else:
        add("定位问题涉及的标的", "lookup_symbols", "lookup")

    if "financial" in kinds_set:
        add("对比近年财报关键项", "finance_statements", "financial")
    if "announcement" in kinds_set:
        add("检索近期公告与事件", "announcement_search", "announcement")
    if _is_compare(question):
        add("横向对比同业标的", "query_tool", "compare")
    if _is_judge(question):
        add("汇总多空证据并给翻转条件", "risk_history", "judge")

    # 兜底：问题既无财报也无公告信号时，至少给一步"查有没有遗漏的口径"
    if len(steps) < 2:
        add("检查本地数据覆盖情况", "ensure_data", "coverage")

    steps.append(
        Step(id=f"s{len(steps) + 1}", title="汇总结论并标注来源", tool=None, kind="summary")
    )
    return steps


def _clean_title(raw: str) -> str:
    title = _TITLE_CLEAN.sub(" ", str(raw or "")).strip()
    title = re.sub(r"\s{2,}", " ", title)
    return title[:MAX_TITLE]


def normalize_steps(raw: Sequence[Any] | None) -> list[str]:
    """把用户提交的计划洗净：去空、去重、截长、限量。

    返回的是**标题列表**，不是 `Step`——用户加的步没有 `tool`，硬编一个反而会误导。
    空列表视为"用户没有计划"，退回自动生成。
    """
    out: list[str] = []
    for item in raw or ():
        title = _clean_title(getattr(item, "title", item) if not isinstance(item, str) else item)
        if not title:
            continue
        if title in out:
            continue
        out.append(title)
        if len(out) >= MAX_STEPS:
            break
    return out


def validate_edits(*, base: Sequence[Step], edited: Sequence[Any] | None) -> dict[str, Any]:
    """对比草案与用户改动，给出"删了哪些、加了哪些"。

    只用于在回答里说明"这一轮按你的改动执行"，不做执行拦截。
    """
    titles = [step.title for step in base]
    final = normalize_steps(edited)
    if not final:
        final = titles
    removed = [title for title in titles if title not in final]
    added = [title for title in final if title not in titles]
    return {
        "steps": final,
        "removed": removed,
        "added": added[:MAX_ADDED],
        "edited": bool(removed or added),
    }


def render(steps: Sequence[str]) -> str:
    """把计划渲染成 system 段。"""
    if not steps:
        return ""
    lines = "\n".join(f"{index}. {title}" for index, title in enumerate(steps, 1))
    return (
        f"【本轮研究计划（用户已确认，共 {len(steps)} 步）】\n{lines}\n\n"
        "按这个顺序推进：能做的每一步都真的调工具去做，做完在正文里落实对应的一句；"
        "用户没列的步骤不要主动加（除非少了那一步就答不出来，此时先说一句为什么加）。"
    )


def note_for(edits: dict[str, Any]) -> str | None:
    """用户改过计划时，给正文一条可读说明。文案无数字（会被当 notice 用）。"""
    if not edits.get("edited"):
        return None
    parts: list[str] = []
    removed = list(edits.get("removed") or [])
    added = list(edits.get("added") or [])
    if removed:
        parts.append("已按你的调整跳过：" + "、".join(removed))
    if added:
        parts.append("并按你追加的要求补查：" + "、".join(added))
    return "；".join(parts) + "。" if parts else None


__all__ = [
    "MAX_ADDED",
    "MAX_STEPS",
    "MAX_TITLE",
    "Step",
    "draft_steps",
    "normalize_steps",
    "note_for",
    "render",
    "validate_edits",
]
