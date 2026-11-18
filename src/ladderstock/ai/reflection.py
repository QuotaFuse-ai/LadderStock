"""自我反思：这一轮没查数却给了数字性结论时，补查一轮再交付。

它不是「让模型再想一遍」——那种反思只会把同一份猜测换个说法说出来。真正值得
反思的是**证据缺口**：模型没有调用任何工具，却在正文里给了价格、涨跌幅、资金流
这类必须是取数得出的结论。这时唯一正确的动作是回去补取。

三条设计约束：

1. **判据确定性**：全部由本轮事实（是否调用工具、metrics 是否为空）+ 正文/问题的
   正则决定，不额外调用模型判断「要不要反思」——否则每轮都多一次模型往返。
2. **只反思一次**：补查那一轮不再反思，避免递归。
3. **可关**：`LDST_SELF_REFLECT=0` 一关就退回原行为，出问题能立刻止损。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

#: 开关：默认开。设为 "0" / "off" / "false" 关闭
ENV_SWITCH = "LDST_SELF_REFLECT"

_OFF_VALUES = {"0", "off", "false", "no"}

#: 价格形态：10.73 / 3.05（两位小数，前后都不许贴数字，避免误吃日期与序号）
_PRICE_RE = re.compile(r"(?<![\d.])\d{1,4}\.\d{2}(?![\d])")

#: 百分比：+3.2% / -0.91 % / 12%
_PCT_RE = re.compile(r"[+\-－±]?\d+(?:\.\d+)?\s*%")

#: 明确的数据诉求词：问到这些，答案里必须有工具取来的数
_DATA_TERMS = re.compile(
    r"(多少钱|什么价|几块|现价|收盘|开盘|涨了|跌了|涨跌|涨跌幅|涨幅|跌幅|"
    r"成交额|成交量|换手|资金流|净流入|流出|龙虎榜|涨停|跌停|连板|炸板|"
    r"市盈率|市净率|市值|估值|财报|营收|净利润|公告|快讯|板块|指数)"
)

#: 结果里「像结论但不是数」的词：有它们也说明这是数据型回答
_REPLY_DATA_HINTS = re.compile(
    r"(上涨|下跌|收于|报收|成交|净流|换手|涨停|跌停|较昨|同比|环比|估值|市盈)"
)

#: 概念解释类问句：问的是「是什么/怎么算」，本来就不需要取数
_CONCEPT_RE = re.compile(
    r"(什么是|是什么意思|是什么|怎么算|怎么理解|什么意思|"
    r"什么区别|的区别|原理|为什么|如何理解|科普|解释一下)"
)


@dataclass(frozen=True)
class Decision:
    """要不要补查、为什么。`reason` 会写进日志与 status 帧，便于事后复盘。"""

    needed: bool
    reason: str = ""


def enabled() -> bool:
    raw = os.environ.get(ENV_SWITCH, "").strip().lower()
    return raw not in _OFF_VALUES


def _has_number_claim(reply: str) -> bool:
    return bool(_PRICE_RE.search(reply) or _PCT_RE.search(reply))


def decide(
    *,
    question: str,
    reply: str,
    used_tools: bool,
    metrics: dict | None,
    enabled_override: bool | None = None,
) -> Decision:
    """判断这一轮是否需要补查。只读事实，不做猜测。"""
    if enabled_override is False or not enabled():
        return Decision(False, "反思已关闭")

    text = (reply or "").strip()
    if used_tools or metrics:
        # 已经查过：数字有出处，没什么可反思的（查错是工具的事，不是反思能修的）
        return Decision(False, "本轮已调用工具")
    if not text:
        return Decision(False, "没有正文，交给失败态处理")

    if _has_number_claim(text):
        return Decision(True, "没调用工具却给了具体价格/涨跌幅")
    if _CONCEPT_RE.search(question or ""):
        # 「什么是市盈率」这类问题答案是知识，不是数据；即便正文里出现「估值」字样
        # 也不该触发一轮取数
        return Decision(False, "概念解释类问题，本来不需要取数")
    if _DATA_TERMS.search(question or "") and _REPLY_DATA_HINTS.search(text):
        # 用户问的是数据，回答里也是数据口吻，但没有一个数来自工具
        return Decision(True, "问题要数据、回答是数据口吻，但本轮没取数")
    return Decision(False, "不像数据型回答，无需补查")


def followup(question: str, *, reason: str) -> str:
    """补查那一轮的提问：原问题 + 明确的取证要求。

    指令里必须写死两件事——先查再答、查不到就认——否则第二轮很可能又编一遍。
    """
    base = (question or "").strip()
    return (
        f"{base}\n\n"
        "【补查要求】上一轮回答（" + (reason or "缺少取数证据") + "）没有调用任何工具。"
        "这一轮必须先用工具取到真实数据再回答；取不到就直接说「没取到」，"
        "不要给估计值，也不要用记忆里的数字。"
    )


def should_replace(first: str, second: str) -> bool:
    """第二轮要不要顶掉第一轮：非空、且不短于原来的一半（防止把有效回答换成残句）。"""
    first_text = (first or "").strip()
    second_text = (second or "").strip()
    if not second_text:
        return False
    if _has_number_claim(second_text) and not _has_number_claim(first_text):
        return True
    return len(second_text) * 2 >= len(first_text)
