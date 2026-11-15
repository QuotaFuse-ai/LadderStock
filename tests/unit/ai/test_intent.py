"""QUOT-615：意图判定 `classify_intent` 的单测。"""

from __future__ import annotations

import pytest

from ladderstock.ai.intent import (
    INTENT_CAPABILITY,
    INTENT_CHITCHAT,
    INTENT_COMPARISON,
    INTENT_CONCEPT,
    INTENT_DATA_QUERY,
    classify_intent,
)


class TestChitchat:
    @pytest.mark.parametrize(
        "question",
        [
            "你好",
            "早上好呀",
            "谢谢你的帮助",
        ],
    )
    def test_greetings_and_thanks(self, question: str) -> None:
        assert classify_intent(question) == INTENT_CHITCHAT

    @pytest.mark.parametrize(
        "question",
        [
            "你是谁",
            "介绍一下自己",
            "再见",
        ],
    )
    def test_identity_and_farewell(self, question: str) -> None:
        assert classify_intent(question) == INTENT_CHITCHAT


class TestCapability:
    @pytest.mark.parametrize(
        "question",
        [
            "你能做什么",
            "这个工具有什么功能",
            "怎么用",
        ],
    )
    def test_capability_questions(self, question: str) -> None:
        assert classify_intent(question) == INTENT_CAPABILITY


class TestConcept:
    @pytest.mark.parametrize(
        "question",
        [
            "什么是市盈率",
            "市净率是什么意思",
            "解释一下换手率",
        ],
    )
    def test_concept_questions(self, question: str) -> None:
        assert classify_intent(question) == INTENT_CONCEPT


class TestDataQuery:
    @pytest.mark.parametrize(
        "question",
        [
            "600000 最近一个交易日收盘价是多少",
            "贵州茅台今天涨了多少",
            "看下 SH000001 的成交额",
        ],
    )
    def test_symbol_queries(self, question: str) -> None:
        assert classify_intent(question) == INTENT_DATA_QUERY


class TestComparison:
    @pytest.mark.parametrize(
        "question",
        [
            "600000 和 600036 对比一下",
            "贵州茅台和五粮液哪个好",
            "SZ000001 vs SZ000002 谁更强",
        ],
    )
    def test_comparison_questions(self, question: str) -> None:
        assert classify_intent(question) == INTENT_COMPARISON


class TestMixedAndFallback:
    def test_mixed_greeting_with_symbol_is_data_query(self) -> None:
        assert classify_intent("你好，600000 现在多少钱") == INTENT_DATA_QUERY

    def test_empty_input_falls_back(self) -> None:
        assert classify_intent("") == INTENT_DATA_QUERY

    def test_symbol_only_input_falls_back_to_data_query(self) -> None:
        assert classify_intent("!!!@@@###") == INTENT_DATA_QUERY

    def test_overlong_input_falls_back(self) -> None:
        assert classify_intent("你好" * 3000) == INTENT_DATA_QUERY

    def test_none_like_input_no_exception(self) -> None:
        assert classify_intent("   ") == INTENT_DATA_QUERY

    def test_unknown_question_falls_back(self) -> None:
        assert classify_intent("帮我看看盘面") == INTENT_DATA_QUERY
