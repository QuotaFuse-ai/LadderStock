"""R3 渲染与审计（QUOT-141 要求 3 + 4）：`[n]` 角标、〔未核实〕、豁免名单、伪造数字判定。

`citation.py` 是纯函数，这里只喂文本与出处，不碰产物也不碰模型。
对应验收条款：「正常报告 100% 数字被引用或标〔未核实〕」「构造含伪造数字的报告 → 指出位置」。
"""

from __future__ import annotations

from typing import Any

from ladderstock.research import citation
from ladderstock.research.citation import UNVERIFIED_MARK, Source, index_from_slot


def slot(*entries: dict[str, Any]) -> dict[str, Any]:
    """最小可用出处件：`sources[]` 的序号就是角标 `[n]` 的 n。"""
    sources = [
        {**entry, "id": entry.get("id") or f"S{index}"}
        for index, entry in enumerate(entries, start=1)
    ]
    return {
        "values": {},
        "sources": sources,
        "unverified": not sources or any(not item["verified"] for item in sources),
    }


def entry(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "dataset": "tickhub-cn",
        "params": {"metric": "pe"},
        "retrieved_at": "2026-10-31T09:00:00+08:00",
        "value": 18.4,
        "unit": "倍",
        "endpoint": "valuation_pe",
        "as_of": "2026-09-30",
        "url": "http://127.0.0.1:8000/mcp",
        "verified": True,
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# 1. 角标索引
# --------------------------------------------------------------------------- #


def test_index_uses_list_position_as_superscript() -> None:
    index = index_from_slot(slot(entry(value=18.4), entry(value=37.2, dataset="wire")))
    assert [item.index for item in index.sources] == [1, 2]
    assert index.resolve(2) is not None and index.resolve(2).dataset == "wire"
    assert index.resolve(3) is None
    assert index.find_text("18.4") is not None and index.find_text("18.4").index == 1
    assert index.find_text("99.9") is None


def test_same_value_two_sources_picks_lowest_index() -> None:
    """同值多头来源取序号最小的一条，否则同一篇报告两次渲染结果会漂。"""
    index = index_from_slot(slot(entry(value=18.4), entry(value=18.4, dataset="wire")))
    assert index.find_text("18.4") is not None and index.find_text("18.4").index == 1


def test_empty_slot_is_empty_index() -> None:
    assert index_from_slot(None).sources == ()
    assert index_from_slot({}).sources == ()
    assert index_from_slot({"values": {}, "sources": [], "unverified": True}).sources == ()


# --------------------------------------------------------------------------- #
# 2. 渲染：程序补角标
# --------------------------------------------------------------------------- #


def test_annotate_verified_number_gets_superscript_after_unit() -> None:
    index = index_from_slot(slot(entry(value=18.4)))
    assert citation.annotate("市盈率 18.4 倍。", index) == "市盈率 18.4 倍[1]。"


def test_annotate_unbacked_number_gets_mark() -> None:
    """没有出处的数字不许被角标背书：只能如实挂〔未核实〕。"""
    index = index_from_slot(slot(entry(value=18.4)))
    assert citation.annotate("净利润增速 37.2%。", index) == f"净利润增速 37.2%{UNVERIFIED_MARK}。"


def test_annotate_unverified_source_gets_mark_not_superscript() -> None:
    index = index_from_slot(slot(entry(value=18.4, verified=False)))
    text = citation.annotate("市盈率 18.4 倍。", index)
    assert UNVERIFIED_MARK in text and "[1]" not in text


def test_annotate_is_idempotent() -> None:
    """报告段会重渲染上游叙述，渲染两次必须一字不差（`--check` 才有稳定裁判）。"""
    index = index_from_slot(
        slot(entry(value=18.4), entry(value=37.2, dataset="wire", verified=False))
    )
    once = citation.annotate("市盈率 18.4 倍，增速 37.2%，快照 2026-10-27。", index)
    assert citation.annotate(once, index) == once


def test_annotate_skips_exempt_forms() -> None:
    index = index_from_slot(
        slot(entry(value=2026), entry(value=10), entry(value=600000), entry(value=3))
    )
    text = citation.annotate(
        "2026-10-27 收盘，代码：600000，第 3 段，P90 分位，v2.1，R3 定稿。",
        index,
        identifiers=["600000.SH"],
    )
    assert UNVERIFIED_MARK not in text and "[1]" not in text


def test_annotate_leaves_sources_block_untouched() -> None:
    index = index_from_slot(slot(entry(value=18.4)))
    text = "市盈率 18.4 倍。\n\n## 来源\n\n[1] tickhub-cn · valuation_pe · 2026-09-30\n"
    assert citation.annotate(text, index) == text.replace("18.4 倍。", "18.4 倍[1]。")


def test_citation_line_format() -> None:
    assert index_from_slot(slot(entry(value=18.4))).sources[0].citation_line() == (
        "[1] tickhub-cn · valuation_pe · 2026-09-30 · http://127.0.0.1:8000/mcp"
    )
    loose = index_from_slot(
        slot(entry(verified=False, endpoint=None, as_of="", url=None))
    ).sources[0]
    assert loose.citation_line() == f"[1] tickhub-cn · - · - · - · {UNVERIFIED_MARK}"


def test_source_dataclass_roundtrip() -> None:
    source = Source(
        index=1, id="S1", dataset="wire", endpoint=None, as_of="2026-09-30", url=None,
        value=37.2, unit="%", verified=False, metric="revenue_yoy", stage="financial",
    )
    assert source.as_dict()["id"] == "S1"
    assert UNVERIFIED_MARK in source.citation_line()


# --------------------------------------------------------------------------- #
# 3. 审计：每个数字要么引用，要么标未核实
# --------------------------------------------------------------------------- #


def test_audit_accepts_fully_marked_report() -> None:
    index = index_from_slot(slot(entry(value=18.4)))
    result = citation.audit_text(f"市盈率 18.4[1] 倍，增速 37.2%{UNVERIFIED_MARK}。\n", index)
    assert result["ok"] is True
    # exempt=1：角标 `[1]` 里的 1 本身也是数值 token，由「角标本身」这条豁免吃掉
    assert result["counts"] == {
        "numbers": 2,
        "cited": 1,
        "unverified": 1,
        "exempt": 1,
        "sources": 1,
    }
    assert result["findings"] == []


def test_audit_flags_bare_number_with_position() -> None:
    index = index_from_slot(slot(entry(value=18.4)))
    result = citation.audit_text("营收 1,234 亿元，市盈率 18.4[1] 倍。", index)
    assert result["ok"] is False
    finding = result["findings"][0]
    assert finding["number"] == "1,234"
    assert finding["line"] == 1 and finding["column"] == 4
    assert finding["reason"] == "无角标的数字"
    assert "1,234" in finding["excerpt"]


def test_audit_flags_dangling_superscript() -> None:
    index = index_from_slot(slot(entry(value=18.4)))
    result = citation.audit_text("市盈率 18.4[7] 倍。", index)
    assert result["findings"][0]["reason"] == "角标指向不存在的来源"


def test_audit_flags_forged_number_borrowing_a_real_citation() -> None:
    """借真引用报假数：伪造数字最像「有出处」的形态，必须判不合格。"""
    index = index_from_slot(slot(entry(value=18.4)))
    result = citation.audit_text("市盈率 99.9[1] 倍。", index)
    assert result["ok"] is False
    finding = result["findings"][0]
    assert finding["reason"] == "数字与角标记录的来源值不符"
    assert finding["number"] == "99.9" and finding["line"] == 1


def test_audit_flags_citation_to_unverified_source() -> None:
    index = index_from_slot(slot(entry(value=18.4, verified=False)))
    result = citation.audit_text("市盈率 18.4[1] 倍。", index)
    assert result["findings"][0]["reason"] == "角标指向未核实的来源"


def test_audit_counts_exemptions_and_reports_unused_sources() -> None:
    index = index_from_slot(slot(entry(value=18.4), entry(value=2.1)))
    result = citation.audit_text("2026-10-27 第 2 段：市盈率 2.1[2] 倍。", index)
    assert result["ok"] is True
    assert result["counts"]["exempt"] == 5
    assert result["unused_sources"] == [1]


def test_audit_ignores_the_sources_block() -> None:
    index = index_from_slot(slot(entry(value=18.4)))
    text = "市盈率 18.4[1] 倍。\n\n## 来源\n\n[1] tickhub-cn · valuation_pe · 2026-09-30\n"
    result = citation.audit_text(text, index)
    assert result["ok"] is True and result["counts"]["numbers"] == 1


def test_scan_line_reports_exemption_names() -> None:
    tokens = citation.scan_line("- 代码：600000，2026-10-27 封板 093000")
    named = {token.text: token.exempt for token in tokens}
    assert named["600000"] is not None and named["600000"].startswith("证券代码")
    assert named["2026"] == "日期"
    assert named["093000"] is not None and named["093000"].startswith("封板时间")


def test_scan_line_treats_security_code_as_identifier() -> None:
    """产物自己的证券代码是名字不是指标：由调用方显式告知，不靠正则猜。"""
    assert citation.scan_line("600000 开头")[0].exempt is None
    assert citation.scan_line("600000 开头", frozenset({"600000"}))[0].exempt == "标的标识符"
