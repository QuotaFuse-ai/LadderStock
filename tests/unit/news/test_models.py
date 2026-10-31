"""统一 schema 的三条口径：归一化、时间不推测、送模型的最小视图（R6 要求 1 与 4）。

这些函数是资讯流所有判断的地基：去重键、稳定 id、代码识别、时间粒度。
地基错了上面全错，所以逐条钉住，不给「差不多」留余地。
"""

from __future__ import annotations

import json

from ladderstock.news.models import (
    AI_SUMMARY_LIMIT,
    CONTRACT_KEYS,
    SUMMARY_LIMIT,
    build_item,
    canonical_url,
    extract_codes,
    fingerprint,
    make_id,
    normalize_code,
    normalize_title,
    parse_published_at,
    publish_day,
)

# --------------------------------------------------------------------------- #
# 标题归一化
# --------------------------------------------------------------------------- #

def test_title_key_ignores_layout_only_differences() -> None:
    """同一篇稿子在不同来源里的差别只有排版：排版不能把一条变成两条。"""
    assert normalize_title(
        "【平安银行】董事会决议公告",
    ) == normalize_title("平安银行 董事会决议公告")
    assert normalize_title("关于《重组办法》的通知") == normalize_title("关于重组办法的通知")
    assert normalize_title("ABCD级别") == normalize_title("abcd级别")


def test_title_key_normalises_fullwidth_text() -> None:
    assert normalize_title("６００５１９ 业绩") == normalize_title("600519 业绩")
    assert normalize_title(None) == ""
    assert normalize_title("") == ""


# --------------------------------------------------------------------------- #
# URL 规范化
# --------------------------------------------------------------------------- #

def test_canonical_url_drops_tracking_keeps_substance() -> None:
    assert canonical_url("HTTP://Example.COM/a/b/?utm_source=x&sk=1#frag") == (
        "http://example.com/a/b"
    )
    assert canonical_url("https://www.cls.cn/detail/100003") == "https://www.cls.cn/detail/100003"
    assert canonical_url("//tv.cctv.com/x.shtml") == "https://tv.cctv.com/x.shtml"
    assert canonical_url("tv.cctv.com/x") == "https://tv.cctv.com/x"
    assert canonical_url("") == ""
    assert canonical_url(None) == ""


def test_canonical_url_keeps_business_query() -> None:
    """只去跟踪参数，`?id=` 这类真正定位内容的参数必须留着。"""
    assert canonical_url("https://x.com/a?id=7&utm_medium=app") == "https://x.com/a?id=7"


# --------------------------------------------------------------------------- #
# 时间：给到哪天就说哪天，没给时刻就不编时刻
# --------------------------------------------------------------------------- #

def test_parse_published_at_epoch_seconds_are_beijing_time() -> None:
    assert parse_published_at(1_793_323_800) == "2026-10-30T09:30:00+08:00"
    assert parse_published_at(1_793_323_800_000) == "2026-10-30T09:30:00+08:00"  # 毫秒
    assert parse_published_at("1793323800") == "2026-10-30T09:30:00+08:00"


def test_parse_published_at_keeps_date_granularity() -> None:
    """研报 / 新闻联播只有日期：补成 00:00:00 就是替上游编一个它没说过的时刻。"""
    assert parse_published_at("2026-10-30") == "2026-10-30"
    assert parse_published_at("20261030") == "2026-10-30"
    assert parse_published_at("2026-10-30 08:00:00") == "2026-10-30T08:00:00+08:00"


def test_parse_published_at_refuses_junk() -> None:
    for junk in (None, "", "   ", "None", "null", "-", "not-a-date", 0, -1, True, "2026-13-45"):
        assert parse_published_at(junk) is None, junk


def test_publish_day_accepts_both_granularities() -> None:
    assert str(publish_day("2026-10-30T09:30:00+08:00")) == "2026-10-30"
    assert str(publish_day("2026-10-30")) == "2026-10-30"
    assert publish_day(None) is None
    assert publish_day("junk") is None


# --------------------------------------------------------------------------- #
# 代码
# --------------------------------------------------------------------------- #

def test_normalize_code_accepts_the_shapes_upstream_sends() -> None:
    for raw in ("600519", "600519.SH", "sh600519", " 600519 ", "000001.SZ", "830799.BJ"):
        assert normalize_code(raw) in {"600519", "000001", "830799"}, raw
    assert normalize_code("600519.SH") == "600519"


def test_normalize_code_rejects_non_equity_numbers() -> None:
    """金额 / 指数点位不是代码：把「123456 万元」当代码是主要的误抽来源。"""
    for junk in ("", None, "123456", "0755-1234", "HS300", "abc", "6005", "6005199"):
        assert normalize_code(junk) == "", junk


def test_extract_codes_prefers_the_requested_code_then_scans_text() -> None:
    codes = extract_codes("贵州茅台(600519.SH)发布前三季度业绩", preferred="600000")
    assert codes == ("600000", "600519")
    assert extract_codes("单季度净利润重回增长。") == ()
    assert extract_codes(["600519", "000001"], preferred=None) == ("600519", "000001")


def test_extract_codes_does_not_repeat() -> None:
    assert extract_codes("600519 与 600519.SH", preferred="600519") == ("600519",)


# --------------------------------------------------------------------------- #
# 稳定 id 与指纹
# --------------------------------------------------------------------------- #

def test_ids_are_derived_only_from_the_dedup_key() -> None:
    """id 只由去重键决定 → 同一 fixture 重跑必然同 id（幂等的根）。"""
    assert make_id("url:https://a/b") == make_id("url:https://a/b")
    assert make_id("url:https://a/b") != make_id("title:abc")
    assert len(make_id("x")) == 16
    assert fingerprint("abc", "正文") == fingerprint("abc", "正文")
    assert fingerprint("abc", "正文") != fingerprint("abc", "另一段正文")


# --------------------------------------------------------------------------- #
# build_item：契约键集合与丢弃规则
# --------------------------------------------------------------------------- #

def _item(**overrides):
    params = {
        "dataset": "flash_cls",
        "title": "国务院常务会议部署稳经济一揽子措施",
        "url": "https://www.cls.cn/detail/100001",
        "published_at": 1_793_323_800,
        "summary": "会议指出，要抓紧研究细化具体举措。",
    }
    params.update(overrides)
    return build_item(**params)


def test_build_item_exposes_the_eight_contract_keys_plus_audit_keys() -> None:
    payload = _item().as_dict()
    assert set(payload) == set(CONTRACT_KEYS) | {"origins", "provenance"}
    assert payload["source"] == "flash_cls"
    assert payload["url"] == "https://www.cls.cn/detail/100001"
    assert payload["published_at"] == "2026-10-30T09:30:00+08:00"
    assert payload["origins"][0]["dataset"] == "flash_cls"
    assert json.dumps(payload, ensure_ascii=False)  # 页面直接吃这份 JSON


def test_build_item_prefers_url_and_falls_back_to_title_as_dedup_key() -> None:
    with_url = _item()
    without_url = _item(url="")
    assert with_url.dedup_key.startswith("url:")
    assert without_url.dedup_key.startswith("title:")
    assert without_url.url == ""


def test_build_item_drops_rows_with_neither_title_nor_url() -> None:
    assert _item(title="", url="") is None
    assert _item(title=None, url=None) is None


def test_build_item_keeps_url_only_row_without_inventing_a_title() -> None:
    """只有地址没有标题：用地址末段顶上，条目不丢，也不编标题。"""
    item = _item(title="", url="https://x.com/files/900007.PDF")
    assert item is not None
    assert item.title == "900007.PDF"
    assert item.dedup_key == "url:https://x.com/files/900007.PDF"


def test_summary_is_capped_so_flashes_do_not_bloat_the_store() -> None:
    item = _item(summary="长" * (SUMMARY_LIMIT + 500))
    assert len(item.summary) == SUMMARY_LIMIT


# --------------------------------------------------------------------------- #
# 送模型的边界（issue 要求 4）
# --------------------------------------------------------------------------- #

def test_ai_payload_is_title_summary_url_only() -> None:
    item = _item(provenance={"trace_id": "t-1", "fetched_at": "2026-10-30T10:00:00+08:00"})
    payload = item.ai_payload()
    assert set(payload) == {"title", "summary", "url"}
    assert "related_codes" not in payload
    assert "provenance" not in payload
    assert "content" not in payload


def test_ai_payload_truncates_summary_to_200_chars() -> None:
    item = _item(summary="句" * 500)
    assert len(item.ai_payload()["summary"]) == AI_SUMMARY_LIMIT
