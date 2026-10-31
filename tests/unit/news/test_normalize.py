"""形状翻译层：每个栏目吃哪一种上游形状，都有样本压着（DEC-006 闸门 2 的资讯版）。

`normalize` 只做翻译，不补救：缺字段就是缺。这里断言的重点不是「有条目」，
而是**该空的地方必须空**（东财全球快讯没有 url）、**该保留粒度的地方不能补时刻**
（新闻联播只有日期）——这两条一破，就等于替上游编了它没说过的话。
"""

from __future__ import annotations

import pytest
from fixtures.news_samples import (
    CCTV_DATA,
    CLS_DATA,
    CNINFO_DATA,
    CROSS_SOURCE_TITLE,
    EM_GLOBAL_DATA,
    EM_STOCK_DATA,
    REPORTS_DATA,
    SINA_REPORTS_DATA,
    SZSE_DATA,
    WINDOW_DAY,
)

from ladderstock.news import normalize


def _decode(dataset: str, data, tool: str = "t"):
    return normalize.decode(dataset, data, provenance={"dataset": dataset, "tool": tool})


# --------------------------------------------------------------------------- #
# 登记表
# --------------------------------------------------------------------------- #

def test_every_registered_dataset_has_a_decoder() -> None:
    """翻译层覆盖的栏目 = 边界登记里的资讯栏目，两边不允许各说各话。"""
    from ladderstock.core.boundary import NEWS_STREAM_DATASETS

    assert set(normalize.supported_datasets()) == set(NEWS_STREAM_DATASETS)


def test_unregistered_dataset_is_refused_not_guessed() -> None:
    with pytest.raises(KeyError, match="没有资讯解码器"):
        normalize.decode("rumor_weibo", [{"title": "x"}])


# --------------------------------------------------------------------------- #
# 公告：巨潮 / 深交所
# --------------------------------------------------------------------------- #

def test_cninfo_announcements_translation() -> None:
    items = _decode("announcement_cninfo", CNINFO_DATA)
    assert len(items) == 6
    head = next(item for item in items if "股东会" in item.title)
    assert head.url.endswith("900001.PDF")
    assert head.published_at == f"{WINDOW_DAY}T00:00:00+08:00"
    assert head.source == "announcement_cninfo"
    assert head.provenance["dataset"] == "announcement_cninfo"
    # 栏目自带的 code + 标题里的 code 都要在，且不重复
    assert "600519" in head.related_codes
    assert len(head.related_codes) == len(set(head.related_codes))


def test_cninfo_falls_back_to_request_context_for_code() -> None:
    """条目自己不写 code 时，用取数上下文顶上——公告类必须有代码才谈得上事件归属。"""
    payload = {
        "code": "000002",
        "announcements": [{"title": "无代码公告", "url": "https://x/1.PDF"}],
    }
    items = _decode("announcement_cninfo", payload)
    assert items[0].related_codes == ("000002",)


def test_szse_announcements_use_format_as_summary() -> None:
    items = _decode("announcement_szse", SZSE_DATA)
    assert len(items) == 4
    assert all(item.summary == "PDF" for item in items)
    assert {item.related_codes[0] for item in items} == {"000001"}


def test_rows_tolerate_bare_strings_and_missing_keys() -> None:
    """上游偶尔给字符串数组：原文留着，不静默丢，也不假装是结构化标题。"""
    items = _decode("flash_cls", [{"title": "甲"}, "乙是一条纯文本快讯", {"nope": 1}])
    assert [item.title for item in items] == ["甲", "乙是一条纯文本快讯"]
    assert all(item.url == "" for item in items)


# --------------------------------------------------------------------------- #
# 快讯：cls / 见闻 / 东财全球
# --------------------------------------------------------------------------- #

def test_cls_flash_uses_epoch_seconds_and_content_as_summary() -> None:
    items = _decode("flash_cls", CLS_DATA)
    assert len(items) == 4
    first = items[0]
    assert first.published_at == "2026-10-30T09:30:00+08:00"
    assert "抓紧研究细化" in first.summary
    assert first.source == "flash_cls"


def test_wallstreetcn_uses_uri_as_url() -> None:
    items = _decode(
        "flash_wallstreetcn",
        [{"title": "x", "uri": "https://wallstreetcn.com/livenews/1"}],
    )
    assert items[0].url == "https://wallstreetcn.com/livenews/1"


def test_eastmoney_global_items_have_no_url_and_are_not_filled() -> None:
    """实测上游不给 url：留空。事件抽取因此不认它们（无证据即不作为资料）。"""
    items = _decode("flash_eastmoney", EM_GLOBAL_DATA)
    assert len(items) == 2
    assert all(item.url == "" for item in items)
    assert all(item.dedup_key.startswith("title:") for item in items)
    assert items[0].published_at == f"{WINDOW_DAY}T08:00:00+08:00"


# --------------------------------------------------------------------------- #
# 个股新闻 / 新闻联播 / 研报
# --------------------------------------------------------------------------- #

def test_eastmoney_stock_news_uses_date_field_and_code_context() -> None:
    items = _decode("news_eastmoney_stock", EM_STOCK_DATA)
    assert len(items) == 2
    assert items[0].published_at == f"{WINDOW_DAY}T23:04:44+08:00"
    assert all("600519" in item.related_codes for item in items)


def test_cctv_stays_at_day_granularity() -> None:
    """新闻联播只给到「哪天」：保持日期粒度，不补 00:00。"""
    items = _decode("news_cctv", CCTV_DATA)
    assert len(items) == 2
    assert all(item.published_at == WINDOW_DAY for item in items)
    assert all(item.summary == "" for item in items)
    assert CROSS_SOURCE_TITLE in {item.title for item in items}


def test_cctv_without_per_item_time_falls_back_to_payload_date() -> None:
    items = _decode(
        "news_cctv",
        {"date": "2026-10-30", "items": [{"title": "甲", "url": "https://x"}]},
    )
    assert items[0].published_at == "2026-10-30"


def test_research_reports_carry_org_analyst_rating_as_summary() -> None:
    """主源（东财）字段名与备源（新浪）不同，但都归到同一个数据集 + 同一种摘要写法。"""
    items = _decode("research_report", REPORTS_DATA)
    assert len(items) == 2
    assert "机构：太平洋" in items[0].summary
    assert "评级：增持" in items[0].summary
    assert items[0].published_at == "2026-08-18"  # 只有日期 → 保持日期粒度
    assert items[0].url.endswith(".pdf")

    backup = _decode("research_report", SINA_REPORTS_DATA)
    assert len(backup) == 1
    assert backup[0].summary == "机构：某券商；分析师：张三；类别：公司研究"
    assert backup[0].source == "research_report"


def test_report_without_rating_only_lists_what_exists() -> None:
    items = _decode("research_report", {"reports": [{"title": "无评级", "org": "某券商"}]})
    assert items[0].summary == "机构：某券商"


# --------------------------------------------------------------------------- #
# provenance 随条目走（DEC-018 R4：任何内容都要带来源标签）
# --------------------------------------------------------------------------- #

def test_provenance_is_attached_to_every_item() -> None:
    items = _decode("flash_cls", CLS_DATA, tool="cls_telegraph")
    assert all(item.provenance["tool"] == "cls_telegraph" for item in items)
    assert all(item.provenance["dataset"] == "flash_cls" for item in items)
