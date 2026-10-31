"""规则抽取的抽查表：正确抽取 / 误抽防护 / 已知漏抽，逐行钉住（R6 要求 2）。

这张表是 issue 验收项「抽取抽查表（准确率/漏抽/误抽样本）」的代码形态：
`fixtures/news_samples.SPOT_CHECK` 每加一行，这里就多发一个用例，
`docs/tasks/QUOT-144/` 的表格与测试同源，不允许文档说一套、代码跑另一套。

漏抽行**也是断言**：它们不是「还没做」，是「按口径就该不抽」——
定期报告不是预告、订单充足不是中标、业绩新闻不是业绩预告。
把它们写成用例，是为了让以后有人放宽关键词时必须先解释这些行为怎么变。
"""

from __future__ import annotations

import pytest
from fixtures.news_samples import SPOT_CHECK, corpus_items

from ladderstock.news.events import (
    EVENT_EXTRACTION_EXCLUDED_DATASETS,
    EVENT_RULES,
    ExtractionReport,
    NewsEvent,
    extract_events,
    rule_for,
)


@pytest.fixture(scope="module")
def corpus():
    items = corpus_items()
    return items, extract_events(items)


def _types_for(corpus, fragment: str) -> set[str]:
    items, report = corpus
    matched = {item.id for item in items if fragment in item.title}
    assert matched, f"抽查表里的片段 {fragment!r} 在语料里找不到，表和语料已经脱节"
    return {event.type for event in report.events if event.item_id in matched}


# --------------------------------------------------------------------------- #
# 逐行抽查
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("tool", "fragment", "expected", "_note"),
    SPOT_CHECK,
    ids=[f"{row[1][:18]}" for row in SPOT_CHECK],
)
def test_spot_check_rows(corpus, tool, fragment, expected, _note) -> None:
    """`tool` 只用来提醒表格的列序，判定按标题片段——同一个片段可能多源都有。"""
    assert tool  # 表格第一列是样本来源，保留它让失败信息能对上文档
    found = _types_for(corpus, fragment)
    if expected is None:
        assert found == set(), f"{fragment} 不该出事件，实际出了 {found}"
    else:
        assert expected in found, f"{fragment} 期望 {expected}，实际 {found or '没抽到'}"


def test_spot_check_table_is_self_consistent(corpus) -> None:
    """表格本身也要被检查：期望类型必须是登记过的规则键，且不能有两行互相矛盾。"""
    rule_keys = {rule.key for rule in EVENT_RULES}
    seen: dict[str, set[str | None]] = {}
    for _tool, fragment, expected, note in SPOT_CHECK:
        assert expected is None or expected in rule_keys, fragment
        assert note, fragment
        seen.setdefault(fragment, set()).add(expected)
        assert len(seen[fragment]) == 1, f"同一片段有两种期望：{fragment}"


def test_extraction_covers_the_seven_required_types() -> None:
    """issue 点名的七类必须有规则：业绩预告/减持/增持/解禁/并购/中标/政策。"""
    required = {
        "earnings_preview",
        "shareholder_reduction",
        "shareholder_increase",
        "lockup_expiry",
        "ma_restructuring",
        "contract_award",
        "policy",
    }
    assert required <= {rule.key for rule in EVENT_RULES}
    assert rule_for("shareholder_meeting") is not None  # 日历需要的第八类


def test_negative_rows_are_recorded_as_suppressed(corpus) -> None:
    """误抽防护不是「悄悄没抽」：否定语境要在账本里能看到是谁被谁压掉的。"""
    _items, report = corpus
    suppressed = {(row["item_id"], row["rule"], row["term"]) for row in report.skipped_negation}
    assert any(term == "未减持" for _id, _rule, term in suppressed), suppressed
    assert any(term == "终止重大资产重组" for _id, _rule, term in suppressed), suppressed


def test_items_without_url_are_skipped_not_invented(corpus) -> None:
    _items, report = corpus
    no_url_ids = {item.id for item in _items if not item.url}
    assert no_url_ids, "语料里必须有「上游不给 url」的样本（东财全球快讯）"
    assert no_url_ids == set(report.skipped_no_url)
    assert not (no_url_ids & {event.item_id for event in report.events})


def test_research_reports_never_produce_events(corpus) -> None:
    """研报是二手解读：实测「评级：增持」会被股东增持规则命中两次，所以整栏排除。"""
    items, report = corpus
    report_ids = {item.id for item in items if item.source in EVENT_EXTRACTION_EXCLUDED_DATASETS}
    assert report_ids, "语料里必须有研报样本"
    assert report_ids == set(report.skipped_excluded_source)
    assert not (report_ids & {event.item_id for event in report.events})


def test_excluded_dataset_has_a_reason() -> None:
    assert EVENT_EXTRACTION_EXCLUDED_DATASETS == frozenset({"research_report"})


# --------------------------------------------------------------------------- #
# 事件的硬约束
# --------------------------------------------------------------------------- #

def test_every_event_carries_evidence(corpus) -> None:
    _items, report = corpus
    assert report.events, "语料应该至少抽出几条事件"
    for event in report.events:
        assert event.evidence["url"], event
        assert event.evidence["snippet"], event
        assert event.evidence["matched"], event
        assert event.evidence["source"], event
        assert event.extractor == "rule", "规则是唯一的事件生产者"
        assert event.item_id, "事件必须能回溯到资讯条目"


def test_event_ids_and_order_are_deterministic(corpus) -> None:
    items, report = corpus
    again = extract_events(items)
    assert [event.id for event in again.events] == [event.id for event in report.events]
    assert [event.type for event in again.events] == [event.type for event in report.events]


def test_extraction_is_independent_of_input_order(corpus) -> None:
    items, report = corpus
    shuffled = extract_events(list(reversed(items)))
    assert {event.id for event in shuffled.events} == {event.id for event in report.events}
    assert [event.id for event in shuffled.events] == [event.id for event in report.events]


def test_by_type_counts_match_the_events(corpus) -> None:
    _items, report = corpus
    assert sum(report.by_type.values()) == len(report.events)


def test_date_taken_from_evidence_when_the_text_states_it(corpus) -> None:
    """解禁提示公告标题里写了 2026-11-16：日期取自原文，并标明 basis=evidence_text。"""
    _items, report = corpus
    lockup = next(event for event in report.events if event.type == "lockup_expiry")
    assert lockup.event_date == "2026-11-16"
    assert lockup.date_basis == "evidence_text"


def test_date_falls_back_to_publish_day_otherwise(corpus) -> None:
    _items, report = corpus
    policy = next(event for event in report.events if event.type == "policy")
    assert policy.date_basis == "published_at"
    assert policy.event_date == "2026-10-30"


def test_missing_evidence_is_rejected_at_construction() -> None:
    """`NewsEvent` 自己拦：无 url / 无片段的事件根本构造不出来。"""
    with pytest.raises(ValueError, match="evidence.url"):
        NewsEvent(
            id="x", type="policy", label="政策", rule_id="policy", event_date=None,
            title="t", codes=(), evidence={"snippet": "片段"},
        )
    with pytest.raises(ValueError, match="evidence.snippet"):
        NewsEvent(
            id="x", type="policy", label="政策", rule_id="policy", event_date=None,
            title="t", codes=(), evidence={"url": "https://x"},
        )


def test_empty_corpus_extracts_nothing() -> None:
    report = ExtractionReport()
    assert report.as_dict()["events"] == 0
    assert extract_events([]).events == []


# --------------------------------------------------------------------------- #
# 规则的复合口径
# --------------------------------------------------------------------------- #

def test_policy_needs_subject_and_action_together() -> None:
    """只出现「通知」的普通公告不算政策事件：这是 policy 规则拆成两组的原因。"""
    from ladderstock.news.models import build_item

    alone = build_item(
        dataset="flash_cls",
        title="某公司关于召开董事会会议的通知",
        url="https://x/1",
        published_at=1_793_323_800,
    )
    assert not [event for event in extract_events([alone]).events if event.type == "policy"]

    together = build_item(
        dataset="flash_cls",
        title="证监会发布新规 完善退市制度",
        url="https://x/2",
        published_at=1_793_323_800,
    )
    assert [event.type for event in extract_events([together]).events] == ["policy"]


def test_negation_window_is_local_not_whole_document() -> None:
    """否定词只在证据窗口内生效：离关键词很远的「不」不该把真事件压掉。"""
    from ladderstock.news.models import build_item

    far = build_item(
        dataset="announcement_szse",
        title="关于股东减持股份计划的公告",
        url="https://x/3",
        published_at=1_793_323_800,
        summary="前情回顾：" + "无关文字" * 60 + "，此前并无减持安排。",
    )
    events = extract_events([far]).events
    assert [event.type for event in events] == ["shareholder_reduction"]
