"""每段受控工具白名单（R2 / QUOT-140 验收第 1 条）。

白名单是**代码内常量**：断言的是「哪段能用哪几个」这件事本身，
以及运行时没有任何办法把它变宽。
"""

from __future__ import annotations

import inspect

import pytest

from ladderstock.ai import tools
from ladderstock.research import checkpoint, pipeline, stages

READ_TOOLS = (tools.LIST_ARTIFACTS, tools.READ_ARTIFACT)


# --------------------------------------------------------------------------- #
# 表的完整性：六段都有、名字都在注册表里、没有第七段
# --------------------------------------------------------------------------- #


def test_table_covers_exactly_the_six_stages() -> None:
    assert set(stages.ALLOWED_TOOLS) == set(checkpoint.STAGES)


def test_every_listed_name_is_registered() -> None:
    for stage, names in stages.ALLOWED_TOOLS.items():
        assert names, stage
        tools.validate_allowed(names)
        assert set(names) <= set(tools.TOOLS), stage


def test_every_stage_can_read_and_write_its_own_stage() -> None:
    for stage in checkpoint.STAGES:
        assert all(name in stages.ALLOWED_TOOLS[stage] for name in READ_TOOLS), stage
    for stage, names in stages.ALLOWED_TOOLS.items():
        assert (tools.WRITE_STAGE in names) is (stage != "report"), stage


def test_unknown_stage_raises() -> None:
    with pytest.raises(ValueError, match="未知研究段"):
        stages.allowed_tools("nope")


# --------------------------------------------------------------------------- #
# 逐段语义：谁不能算什么、谁不能写什么
# --------------------------------------------------------------------------- #


def test_valuation_gets_no_compute() -> None:
    """DEC-025：估值数字的唯一来源是 facts，段内不许再算一遍。"""
    assert tools.COMPUTE not in stages.ALLOWED_TOOLS["valuation"]


def test_report_produces_no_new_numbers_and_no_stage_artifact() -> None:
    table = stages.ALLOWED_TOOLS["report"]
    assert tools.COMPUTE not in table
    assert tools.WRITE_STAGE not in table
    assert table == (tools.LIST_ARTIFACTS, tools.READ_ARTIFACT, tools.WRITE_REPORT)


def test_write_report_only_in_report_stage() -> None:
    for stage, names in stages.ALLOWED_TOOLS.items():
        assert (tools.WRITE_REPORT in names) is (stage == "report"), stage


def test_search_web_only_where_inputs_are_missing() -> None:
    """缺口在行业景气 / 资金流 / 资讯 / 日历四类输入（DEC-018），估值与报告不在内。"""
    for stage in ("company", "financial", "forecast", "risk"):
        assert tools.SEARCH_WEB in stages.ALLOWED_TOOLS[stage], stage
    for stage in ("valuation", "report"):
        assert tools.SEARCH_WEB not in stages.ALLOWED_TOOLS[stage], stage


# --------------------------------------------------------------------------- #
# 只有收窄，没有覆盖
# --------------------------------------------------------------------------- #


def test_web_search_dropped_when_gate_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(tools.WEB_SEARCH_ENV, raising=False)
    for stage in checkpoint.STAGES:
        got = stages.allowed_tools(stage)
        assert tools.SEARCH_WEB not in got, stage
        assert set(got) <= set(stages.ALLOWED_TOOLS[stage]), stage


def test_gate_open_never_widens_beyond_the_table(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(tools.WEB_SEARCH_ENV, "1")
    for stage in checkpoint.STAGES:
        assert set(stages.allowed_tools(stage)) == set(stages.ALLOWED_TOOLS[stage]), stage


@pytest.mark.parametrize(
    "override",
    ["write_report,compute", "read_artifact", "*", ""],
)
def test_no_env_or_runtime_override_can_widen_the_table(
    monkeypatch: pytest.MonkeyPatch, override: str
) -> None:
    """没有任何环境变量 / 配置键能往白名单里加工具；想改只能改这张常量表。"""
    monkeypatch.setenv(tools.WEB_SEARCH_ENV, "1")
    for name in ("LDST_RESEARCH_TOOLS", "LDST_ALLOWED_TOOLS", "LDST_TOOL_WHITELIST"):
        monkeypatch.setenv(name, override)
    for stage in checkpoint.STAGES:
        assert set(stages.allowed_tools(stage)) == set(stages.ALLOWED_TOOLS[stage]), stage


def test_pipeline_takes_no_tool_arguments() -> None:
    """`run()` 的入参里没有工具相关开关：管线不接受「这次多给几个工具」。"""
    params = inspect.signature(pipeline.ResearchPipeline.run).parameters
    assert not [name for name in params if "tool" in name]
    assert not [
        name for name in inspect.signature(pipeline.ResearchPipeline.__init__).parameters
        if "tool" in name
    ]


def test_allowed_tools_returns_a_stable_tuple() -> None:
    first, second = stages.allowed_tools("company"), stages.allowed_tools("company")
    assert isinstance(first, tuple) and first == second
    # 返回的是新对象，改不动常量表
    assert stages.ALLOWED_TOOLS["company"].index(tools.LIST_ARTIFACTS) == 0


# --------------------------------------------------------------------------- #
# 提示词与白名单一致：模型看到的工具 = 本段允许的
# --------------------------------------------------------------------------- #


def test_tool_instruction_lists_exactly_the_allowed_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(tools.WEB_SEARCH_ENV, raising=False)
    for stage in checkpoint.STAGES:
        names = stages.allowed_tools(stage)
        system, _ = stages.build_messages("600000", stage, "2026-10-27", {}, tool_names=names)
        for name in names:
            assert f"- {name}：" in system, (stage, name)
        others = set(tools.TOOLS) - set(names)
        for name in others:
            assert f"- {name}：" not in system, (stage, name)


def test_messages_without_tools_are_unchanged() -> None:
    """不传 tool_names → system 里没有受控工具段：纯文本路径与 R2 之前逐字相同。"""
    plain_system, plain_user = stages.build_messages("600000", "risk", "2026-10-27", {})
    assert "受控工具" not in plain_system
    empty_system, empty_user = stages.build_messages(
        "600000", "risk", "2026-10-27", {}, tool_names=()
    )
    assert (empty_system, empty_user) == (plain_system, plain_user)


def test_plain_messages_cannot_advertise_tools() -> None:
    """第三级收口不带工具：签名里没有 tool_names，system 里也没有受控工具段。"""
    assert "tool_names" not in inspect.signature(stages.plain_messages).parameters
    system, _ = stages.plain_messages("600000", "company", "2026-10-27", {})
    assert "受控工具" not in system
