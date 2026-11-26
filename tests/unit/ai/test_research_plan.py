"""F2 研究计划：草案规则、用户改动对齐、注入文案。"""

from __future__ import annotations

from ladderstock.ai import research_plan


def test_draft_with_symbol_and_financial() -> None:
    steps = research_plan.draft_steps(
        "600519 近三年营收怎么样", symbol="600519", kinds=["financial"]
    )
    titles = [s.title for s in steps]
    assert titles[0].startswith("确认标的")
    assert any("财报" in t for t in titles)
    assert titles[-1] == "汇总结论并标注来源"
    assert len(steps) <= research_plan.MAX_STEPS


def test_draft_without_symbol_locates_first() -> None:
    steps = research_plan.draft_steps("茅台这家公司值不值得买")
    assert steps[0].title == "定位问题涉及的标的"
    assert any("翻转条件" in s.title for s in steps)


def test_draft_caps_steps() -> None:
    steps = research_plan.draft_steps(
        "600519 对比 000001 哪个好，财报和公告都要，给个判断",
        symbol="600519",
        kinds=["financial", "announcement", "quote", "kline"],
    )
    assert len(steps) <= research_plan.MAX_STEPS


def test_steps_have_stable_ids() -> None:
    steps = research_plan.draft_steps("600519 怎么样", symbol="600519")
    ids = [s.id for s in steps]
    assert ids == [f"s{i + 1}" for i in range(len(ids))]


def test_normalize_strips_and_dedupes() -> None:
    got = research_plan.normalize_steps(["  查公告 ", "", "查公告", "看财报\n第二行"])
    assert got == ["查公告", "看财报 第二行"]


def test_normalize_truncates_long_title() -> None:
    got = research_plan.normalize_steps(["查" * 40])
    assert len(got[0]) == research_plan.MAX_TITLE


def test_validate_edits_reports_removed_and_added() -> None:
    base = research_plan.draft_steps("600519 怎么样", symbol="600519")
    titles = [s.title for s in base]
    edited = [t for t in titles if "日线" not in t] + ["查一下股东户数"]
    edits = research_plan.validate_edits(base=base, edited=edited)
    assert edits["edited"] is True
    assert "拉取个股行情与日线" in edits["removed"]
    assert edits["added"] == ["查一下股东户数"]


def test_validate_edits_without_change() -> None:
    base = research_plan.draft_steps("600519 怎么样", symbol="600519")
    edits = research_plan.validate_edits(base=base, edited=[s.title for s in base])
    assert edits["edited"] is False
    assert research_plan.note_for(edits) is None


def test_render_lists_numbered_steps() -> None:
    text = research_plan.render(["确认标的", "拉取行情"])
    assert "1. 确认标的" in text
    assert "2. 拉取行情" in text
    assert "共 2 步" in text


def test_render_empty_is_blank() -> None:
    assert research_plan.render([]) == ""
