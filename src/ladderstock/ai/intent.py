"""QUOT-615：问答意图判定（纯规则，无 LLM 往返）。

五类：`chitchat` / `capability` / `concept` / `data_query` / `comparison`。
判定不确定时一律回退 `data_query`，保持既有装配行为（现有调用方与测试不回归）。
"""

from __future__ import annotations

import re
import unicodedata

INTENT_CHITCHAT = "chitchat"
INTENT_CAPABILITY = "capability"
INTENT_CONCEPT = "concept"
INTENT_DATA_QUERY = "data_query"
INTENT_COMPARISON = "comparison"

INTENTS: tuple[str, ...] = (
    INTENT_CHITCHAT,
    INTENT_CAPABILITY,
    INTENT_CONCEPT,
    INTENT_DATA_QUERY,
    INTENT_COMPARISON,
)

# A 股 6 位代码（可选 SH/SZ/BJ 前缀、可选 . / 空格分隔）
_CODE_RE = re.compile(r"\b(?:SH|SZ|BJ)?[:. ]?\d{6}\b", re.IGNORECASE)

_COMPARISON_WORDS = ("对比", "比较", "相比", "哪个好", "谁更强", " vs ", "vs.", "差别", "区别")

_CAPABILITY_WORDS = (
    "你能做什么",
    "你能干什么",
    "你会什么",
    "你会做什么",
    "有什么功能",
    "都有什么功能",
    "怎么用",
    "怎么使用",
    "如何使用",
    "使用说明",
    "功能介绍",
    "能帮我做什么",
)

_CHITCHAT_WORDS = (
    "你好",
    "您好",
    "嗨",
    "哈喽",
    "哈罗",
    "hello",
    "hi",
    "在吗",
    "在不在",
    "早上好",
    "上午好",
    "中午好",
    "下午好",
    "晚上好",
    "晚安",
    "谢谢",
    "多谢",
    "感谢",
    "辛苦了",
    "麻烦你了",
    "再见",
    "拜拜",
    " bye",
    "bye ",
    "你是谁",
    "介绍下自己",
    "介绍一下自己",
    "自我介绍",
    "很高兴认识",
    "请多指教",
)

_CONCEPT_WORDS = (
    "什么是",
    "啥是",
    "什么叫",
    "是什么意思",
    "为什么",
    "为何",
    "原理",
    "机制",
    "含义",
    "是什么概念",
    "解释一下",
    "解释下",
    "怎么理解",
    "如何理解",
    "术语",
    "介绍一下",
    "讲讲",
    "是什么意思呢",
)

MAX_INTENT_INPUT_LEN = 4000


def _normalize(question: str) -> str:
    text = unicodedata.normalize("NFKC", question or "")
    return text.strip().lower()


def _symbols(text: str) -> list[str]:
    """提取 A 股代码，去重保序。"""
    seen: list[str] = []
    for match in _CODE_RE.findall(text):
        code = re.sub(r"[^0-9]", "", match)
        if code not in seen:
            seen.append(code)
    return seen


def classify_intent(question: str, context: str = "", page_key: str | None = None) -> str:
    """规则式意图判定。纯函数：不读写文件、不发请求。

    空输入 / 超长输入 / 判定不出时一律回退 `data_query`。
    """
    text = _normalize(question)
    if not text or len(text) > MAX_INTENT_INPUT_LEN:
        return INTENT_DATA_QUERY
    # 纯符号 / 无任何字母数字汉字
    if not re.search(r"[\w\u4e00-\u9fff]", text):
        return INTENT_DATA_QUERY

    symbols = _symbols(text)
    if len(symbols) >= 2:
        return INTENT_COMPARISON
    if len(symbols) == 1:
        # 混合句「你好，600000 现在多少钱」：有标的就要数，一律 data_query
        return INTENT_DATA_QUERY

    if any(word in text for word in _COMPARISON_WORDS):
        return INTENT_COMPARISON

    if any(word in text for word in _CAPABILITY_WORDS):
        return INTENT_CAPABILITY

    if any(word in text for word in _CHITCHAT_WORDS):
        return INTENT_CHITCHAT

    if any(word in text for word in _CONCEPT_WORDS):
        return INTENT_CONCEPT

    return INTENT_DATA_QUERY


__all__ = [
    "INTENTS",
    "INTENT_CHITCHAT",
    "INTENT_CAPABILITY",
    "INTENT_CONCEPT",
    "INTENT_DATA_QUERY",
    "INTENT_COMPARISON",
    "classify_intent",
]
