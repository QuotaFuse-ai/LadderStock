"""事件日历：三个缺口必须显式「暂无数据源」，绝不估算、不编日期（DEC-018 R6）。

这是总经理复核口径点名的一条：**没有源的小节要写「暂无数据源」**，
不能空着、更不能拿别的源顶包。所以这里断言的重点是「没有」的表达方式：

- `status=no_source` + `entries=[]` + `reason` + `ask`（缺什么、怎么补）；
- 有源小节里出现的每一个日期都必须是上游写出来的、且落在本月；
- 抽取事件带证据，独立成块，不冒充日历数据源。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fixtures.news_samples import LOCKUP_DATA, WINDOW_DAY

from ladderstock.errors import EXIT_OK, EXIT_PARTIAL, EXIT_SOURCE, LadderStockError
from ladderstock.news import store as news_store
from ladderstock.news.calendar import (
    NO_SOURCE_SECTIONS,
    SECTION_ORDER,
    SOURCED_SECTIONS,
    STATUS_NO_SOURCE,
    STATUS_SOURCED,
    build_calendar,
    parse_month,
)

#: 顶包候选：名字看着像，但口径不是那件事
IMPOSTORS = ("macro_calendar", "eastmoney_global_news")


def _section(result, key: str):
    return next(section for section in result.sections if section.key == key)


# --------------------------------------------------------------------------- #
# 月份解析
# --------------------------------------------------------------------------- #

def test_parse_month_boundaries() -> None:
    assert parse_month("2026-10") == (date(2026, 10, 1), date(2026, 10, 31))
    assert parse_month("2026-02") == (date(2026, 2, 1), date(2026, 2, 28))
    assert parse_month("2028-02") == (date(2028, 2, 1), date(2028, 2, 29))


def test_parse_month_handles_year_rollover() -> None:
    assert parse_month("2026-12") == (date(2026, 12, 1), date(2026, 12, 31))
    assert parse_month("2027-01") == (date(2027, 1, 1), date(2027, 1, 31))


@pytest.mark.parametrize("junk", [
    "2026",
    "202610",
    "2026-13",
    "2026-00",
    "abc",
    "",
    None,
    "2026/10",
])
def test_parse_month_rejects_loose_formats(junk) -> None:
    """日历的月份会进产物目录名和 JSON 的 `month` 值：只允许一种写法。"""
    with pytest.raises(LadderStockError) as exc:
        parse_month(junk)
    assert exc.value.code == 2


def test_month_window_is_echoed_in_the_payload(news_registry) -> None:
    _transport, registry = news_registry()
    result = build_calendar("2026-11", codes=["600519"], registry=registry)
    assert result.window == ("2026-11-01", "2026-11-30")
    assert result.as_dict()["month"] == "2026-11"


# --------------------------------------------------------------------------- #
# 三个「暂无数据源」的小节
# --------------------------------------------------------------------------- #

def test_three_gaps_are_declared_as_no_source(news_registry) -> None:
    _transport, registry = news_registry()
    result = build_calendar("2026-11", codes=["600519"], registry=registry)
    missing = [section for section in result.sections if section.status == STATUS_NO_SOURCE]
    assert {section.key for section in missing} == {
        "earnings_disclosure",
        "shareholder_meeting",
        "policy_window",
    }
    for section in missing:
        assert section.entries == [], f"{section.key} 没有源就不许有条目"
        assert section.note == "暂无数据源"
        assert section.reason and section.ask, "要说清为什么没有、怎么补上"


def test_no_source_slots_cover_the_three_required_items() -> None:
    """issue 点名的三个缺口一个都不能漏：财报预约披露日 / 股东大会 / 政策发布窗口。"""
    assert {gap["key"] for gap in NO_SOURCE_SECTIONS} == {
        "earnings_disclosure",
        "shareholder_meeting",
        "policy_window",
    }


def test_no_source_sections_each_state_a_distinct_reason() -> None:
    reasons = {gap["reason"] for gap in NO_SOURCE_SECTIONS}
    asks = {gap["ask"] for gap in NO_SOURCE_SECTIONS}
    assert len(reasons) == len(NO_SOURCE_SECTIONS), "三个缺口的理由不能复制粘贴"
    assert len(asks) == len(NO_SOURCE_SECTIONS), "三个缺口各自的补源路径要写清"
    for gap in NO_SOURCE_SECTIONS:
        assert gap["label"]
        assert "th-cn" in gap["ask"] or "Wire" in gap["ask"], "补源要指到具体的上游产品"


def test_no_impostor_is_used_as_a_calendar_source(news_registry) -> None:
    """顶包候选一个都不许出现在有源小节里：口径不同就是不能用。"""
    _transport, registry = news_registry()
    result = build_calendar("2026-11", codes=["600519"], registry=registry)
    sourced_tools = {
        section.tool for section in result.sections if section.status == STATUS_SOURCED
    }
    sourced_data = {
        section.data_source for section in result.sections if section.status == STATUS_SOURCED
    }
    assert sourced_tools <= {"lockup_expiry", "earnings_forecast"}
    assert not sourced_data & set(IMPOSTORS)
    assert SOURCED_SECTIONS == {
        "lockup_expiry": "lockup_calendar",
        "earnings_preview": "earnings_preview",
    }


def test_earnings_preview_is_not_labelled_as_disclosure_date(news_registry) -> None:
    """业绩预告的 notice_date 不是「财报预约披露日」：两节必须分开，各自署名。"""
    _transport, registry = news_registry()
    result = build_calendar("2026-10", codes=["600519"], registry=registry)
    preview = _section(result, "earnings_preview")
    disclosure = _section(result, "earnings_disclosure")
    assert preview.tool == "earnings_forecast"
    assert "预约" not in preview.label
    assert disclosure.status == STATUS_NO_SOURCE
    assert disclosure.entries == []


# --------------------------------------------------------------------------- #
# 有源小节
# --------------------------------------------------------------------------- #

def test_lockup_entries_come_from_upstream_dates_only(news_registry) -> None:
    _transport, registry = news_registry()
    result = build_calendar("2026-11", codes=["600519"], registry=registry)
    section = _section(result, "lockup_expiry")
    assert section.status == STATUS_SOURCED
    assert [entry["date"] for entry in section.entries] == ["2026-11-16"]
    assert section.entries[0]["code"] == "600519"
    assert section.entries[0]["provenance"]["tool"] == "lockup_expiry"
    assert "可流通" in section.entries[0]["detail"]


def test_history_rows_outside_the_month_are_dropped(news_registry) -> None:
    """上游同时给 history 与 upcoming：只留本月的那条，去年的解禁不该出现在下个月。"""
    assert LOCKUP_DATA["history"][0]["date"] == "2025-11-16"
    _transport, registry = news_registry()
    result = build_calendar("2026-11", codes=["600519"], registry=registry)
    dates = {entry["date"] for entry in _section(result, "lockup_expiry").entries}
    assert "2025-11-16" not in dates


def test_every_entry_date_lies_inside_the_month(news_registry) -> None:
    _transport, registry = news_registry()
    for month in ("2026-09", "2026-10", "2026-11", "2026-12"):
        result = build_calendar(month, codes=["600519"], registry=registry)
        start, end = parse_month(month)
        for section in result.sections:
            for entry in section.entries:
                day = date.fromisoformat(entry["date"])
                assert start <= day <= end, f"{month}/{section.key} 排出了窗口外的日期 {day}"


def test_earnings_entries_filter_on_notice_date(news_registry) -> None:
    _transport, registry = news_registry()
    october = _section(
        build_calendar("2026-10", codes=["600519"], registry=registry), "earnings_preview"
    )
    assert [entry["date"] for entry in october.entries] == ["2026-10-14"]
    assert "预增" in october.entries[0]["label"]
    november = _section(
        build_calendar("2026-11", codes=["600519"], registry=registry), "earnings_preview"
    )
    assert november.entries == [], "report_date=2026-09-30 的那条不能按报告期落进 10 月之后"


def test_earnings_section_admits_it_has_no_window_parameter(news_registry) -> None:
    """上游没有时间窗参数：本地过滤出来的空，不能写成「本月没有预告」。"""
    _transport, registry = news_registry()
    section = _section(
        build_calendar("2026-11", codes=["600519"], registry=registry), "earnings_preview"
    )
    assert "不等于当月没有预告" in section.note


def test_lockup_without_code_is_declared_not_fabricated(news_registry) -> None:
    """上游 code 必填：没代码就明说「本轮未指定 --code」，不能给一个看起来完整的空日历。"""
    _transport, registry = news_registry()
    result = build_calendar("2026-11", codes=[], registry=registry)
    section = _section(result, "lockup_expiry")
    assert section.status == STATUS_NO_SOURCE
    assert section.entries == []
    assert "code" in section.reason
    assert "--code" in section.ask


def test_lockup_forwards_month_span_to_the_peer(news_registry) -> None:
    _transport, registry = news_registry()
    build_calendar("2026-11", codes=["600519", "000001"], registry=registry)
    calls = [call for call in _transport.calls if call.tool == "lockup_expiry"]
    assert {call.params["code"] for call in calls} == {"600519", "000001"}
    assert all(call.params["trade_date"] == "2026-11-01" for call in calls)
    assert all(call.params["forward_days"] >= 30 for call in calls)


def test_down_peer_turns_the_section_into_a_declared_gap(news_registry) -> None:
    _transport, registry = news_registry(failing=["lockup_expiry", "earnings_forecast"])
    result = build_calendar("2026-11", codes=["600519"], registry=registry)
    assert result.exit_code == EXIT_SOURCE, "有源小节一个都没成 → 4"
    for key in ("lockup_expiry", "earnings_preview"):
        section = _section(result, key)
        assert section.status == STATUS_NO_SOURCE
        assert section.degraded is True
        assert section.entries == []
        assert "取数失败" in section.reason


def test_partial_failure_is_exit_8_not_0(news_registry) -> None:
    _transport, registry = news_registry(failing=["lockup_expiry"])
    result = build_calendar("2026-11", codes=["600519"], registry=registry)
    assert result.exit_code == EXIT_PARTIAL
    assert _section(result, "lockup_expiry").degraded is True
    assert _section(result, "earnings_preview").status == STATUS_SOURCED


def test_no_fetch_does_not_touch_the_peer(news_registry) -> None:
    transport, registry = news_registry()
    result = build_calendar("2026-11", codes=["600519"], registry=registry, fetch=False)
    assert transport.calls == []
    assert result.exit_code == EXIT_OK
    for key in SOURCED_SECTIONS:
        assert "未取数" in _section(result, key).note


# --------------------------------------------------------------------------- #
# 抽取事件：带证据，独立成块
# --------------------------------------------------------------------------- #

def _seed_store(tmp_path: Path, stream_of) -> None:
    result, _transport = stream_of(persist=True, root=tmp_path)
    assert result.events > 0


def test_extracted_events_appear_only_with_evidence(
    tmp_path: Path, stream_of, news_registry
) -> None:
    _seed_store(tmp_path, stream_of)
    _transport, registry = news_registry()
    result = build_calendar("2026-10", codes=["600519"], registry=registry, root=tmp_path)
    rows = result.extracted_events
    assert rows, "公告正文里写了会议日期的，应该作为抽取事件出现"
    for row in rows:
        assert row["basis"] == "rule_extracted"
        assert row["evidence"]["url"], "无证据的事件不进日历"
        assert row["evidence"]["snippet"]
        assert "非日历数据源" in row["note"]
        assert str(row["date"]).startswith("2026-10")


def test_extracted_events_are_scoped_to_the_month(
    tmp_path: Path, stream_of, news_registry
) -> None:
    _seed_store(tmp_path, stream_of)
    _transport, registry = news_registry()
    november = build_calendar("2026-11", codes=["600519"], registry=registry, root=tmp_path)
    assert all(str(row["date"]).startswith("2026-11") for row in november.extracted_events)
    july = build_calendar("2026-07", codes=["600519"], registry=registry, root=tmp_path)
    assert july.extracted_events == []


def test_calendar_without_a_store_reports_no_extracted_events(
    tmp_path: Path, news_registry
) -> None:
    _transport, registry = news_registry()
    empty_root = tmp_path / "fresh"
    result = build_calendar("2026-11", codes=["600519"], registry=registry, root=empty_root)
    assert result.extracted_events == []
    assert not (empty_root / news_store.DB_FILENAME).exists(), "只读查询不该建空库"


def test_calendar_does_not_read_the_private_watchlist(
    news_registry, tmp_path: Path, monkeypatch
) -> None:
    """日历的代码只能来自显式 --code：悄悄读自选会让公开视图带上隐私面。"""
    from ladderstock.cli import paths
    from ladderstock.core.privacy import private_keys_present

    forbidden = tmp_path / "forbidden"

    def poisoned_data_dir() -> Path:
        raise AssertionError("日历不该走默认数据目录（那会读到隐私库所在的路径）")

    monkeypatch.setattr(paths, "data_dir", poisoned_data_dir)
    _transport, registry = news_registry()
    result = build_calendar("2026-11", codes=["600519"], registry=registry, root=tmp_path)
    assert result.codes == ("600519",)
    assert private_keys_present(result.as_dict()) == []
    assert not forbidden.exists()


# --------------------------------------------------------------------------- #
# 输出契约
# --------------------------------------------------------------------------- #

def test_payload_carries_the_notice_and_summary(news_registry) -> None:
    _transport, registry = news_registry()
    payload = build_calendar("2026-11", codes=["600519"], registry=registry).as_dict()
    assert payload["ok"] is True
    assert payload["domain"] == "news"
    assert payload["topic"] == "event_calendar"
    assert payload["summary"]["no_source"] == [
        "earnings_disclosure",
        "shareholder_meeting",
        "policy_window",
    ]
    assert "不估算、不编日期" in payload["notice"]
    assert [section["key"] for section in payload["sections"]] == list(SECTION_ORDER)


def test_sections_are_emitted_in_fixed_order(news_registry) -> None:
    _transport, registry = news_registry()
    result = build_calendar("2026-11", codes=["600519"], registry=registry)
    assert tuple(section.key for section in result.sections) == SECTION_ORDER


def test_no_source_sections_do_not_advertise_a_data_source(news_registry) -> None:
    """no_source 小节若带上 data_source/tool，页面就会把它画成
    「有源但本月没有」——正是要避免的误读。
    """
    _transport, registry = news_registry()
    for section in build_calendar("2026-11", codes=["600519"], registry=registry).sections:
        payload = section.as_dict()
        if section.status == STATUS_NO_SOURCE:
            assert "data_source" not in payload and "tool" not in payload
            assert payload["reason"] and payload["ask"]
        else:
            assert payload["data_source"] and payload["tool"]


def test_calendar_codes_are_normalised(news_registry) -> None:
    _transport, registry = news_registry()
    result = build_calendar("2026-11", codes=["600519.SH", "000001,sz000002"], registry=registry)
    assert result.codes == ("600519", "000001", "000002")


def test_there_is_no_implicit_today_section(news_registry) -> None:
    """日历只按 --month 说话：`WINDOW_DAY` 这类「今天」的隐式小节不存在。"""
    _transport, registry = news_registry()
    result = build_calendar("2026-10", codes=["600519"], registry=registry)
    assert all(section.key != WINDOW_DAY for section in result.sections)
    assert result.exit_code == EXIT_OK
