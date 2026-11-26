"""周期报告单测：周期解析（月/季/年 + 缺省上期 + 非法格式）与空库事实包。"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ladderstock.analytics import period
from ladderstock.errors import EXIT_USAGE, LadderStockError


def test_resolve_month_and_quarter_and_year() -> None:
    start, end, label, tag = period.resolve_period("month", "2026-11")
    assert (start, end, tag) == (date(2026, 11, 1), date(2026, 11, 30), "2026-11")
    assert label == "2026-11"

    start, end, _label, tag = period.resolve_period("month", "2026-02")
    assert (start, end) == (date(2026, 2, 1), date(2026, 2, 28))
    assert tag == "2026-02"

    start, end, _label, tag = period.resolve_period("quarter", "2026-Q4")
    assert (start, end) == (date(2026, 10, 1), date(2026, 12, 31))
    assert tag == "2026-Q4"

    start, end, _label, tag = period.resolve_period("quarter", "2026-Q1")
    assert (start, end) == (date(2026, 1, 1), date(2026, 3, 31))

    start, end, _label, tag = period.resolve_period("year", "2025")
    assert (start, end) == (date(2025, 1, 1), date(2025, 12, 31))
    assert tag == "2025"


def test_resolve_defaults_to_previous_complete_period() -> None:
    today = date.today()
    start, end, _label, _tag = period.resolve_period("month", None)
    # 上一个月：end 是本月 1 日的前一天，start 是上月 1 日
    assert end == today.replace(day=1) - period.timedelta(days=1)
    assert start == end.replace(day=1)

    start, end, _label, _tag = period.resolve_period("year", None)
    assert end == date(today.year - 1, 12, 31)
    assert start == date(today.year - 1, 1, 1)


def test_resolve_rejects_illegal_input() -> None:
    cases = (
        ("week", "2026-11"),
        ("month", "2026-13"),
        ("month", "2026/11"),
        ("quarter", "2026-Q5"),
        ("year", "26"),
    )
    for kind, of in cases:
        with pytest.raises(LadderStockError) as err:
            period.resolve_period(kind, of)
        assert err.value.code == EXIT_USAGE


def test_build_report_on_empty_data_is_honest(tmp_path: Path) -> None:
    report = period.build_report("month", "2026-11", root=tmp_path)
    assert report["ok"] is True
    assert report["trades_available"] is False
    assert report["portfolio"]["realized_count"] == 0
    assert report["portfolio"]["win_rate"] is None
    assert "没有已实现交易" in report["portfolio"]["reason"]
    assert report["attribution"] == []
    # 市场情绪缺复盘产物：available=False + 原因，不补数
    assert report["market"]["available"] is False
    assert report["market"]["reason"]

    text = period.render_markdown(report)
    assert "2026-11 月报" in text
    assert "本期没有已实现交易" in text
    assert "不可用" in text  # 市场情绪一节如实说明


def test_write_report_lands_json_and_md(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 叙述层会读模型配置：钉到空的临时配置目录（未配置 → 模板直出，绝不拨真模型）
    monkeypatch.setenv("LDST_CONFIG_DIR", str(tmp_path / "config"))
    written = period.write_report("quarter", "2026-Q3", root=tmp_path)
    json_path = Path(written["json_path"])
    md_path = Path(written["md_path"])
    assert json_path.is_file() and md_path.is_file()
    assert json_path.parent.name == "quarter"
    assert json_path.name == "2026-Q3.json"
    assert "2026-Q3 季报" in md_path.read_text(encoding="utf-8")


def _stub_config(monkeypatch: pytest.MonkeyPatch, models: tuple[str, ...]) -> None:
    """把叙述层的配置面钉成「已配置 + 给定模型链」，凭据与网络都不碰。"""
    from types import SimpleNamespace

    from ladderstock.analytics import period_ai

    stub = SimpleNamespace(
        chain_for=lambda module, role: [SimpleNamespace(label=name) for name in models],
    )
    monkeypatch.setattr(period_ai.fallback, "load_config", lambda root=None: stub)
    monkeypatch.setattr(period_ai.fallback, "is_configured", lambda cfg: True)


def test_write_report_unconfigured_ai_stays_template(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LDST_CONFIG_DIR", str(tmp_path / "config"))
    written = period.write_report("month", "2026-11", root=tmp_path)
    ai = written["report"]["ai"]
    assert ai["configured"] is False
    assert ai["degraded"] is False
    assert all(item["mode"] == "template" for item in ai["sections"])
    assert "未配置 AI 或未启用" in Path(written["md_path"]).read_text(encoding="utf-8")


def test_narrate_uses_model_then_falls_back_per_section(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """四类段级结果一次钉全：AI 正常 / JSON 坏→纯文本 / 换模型成功 / 全败退模板。"""
    from ladderstock.analytics import period_ai

    _stub_config(monkeypatch, ("m1", "m2"))
    report = period.build_report("month", "2026-11", root=tmp_path)

    class FakeClient:
        def complete(self, model: str, system: str, user: str, *, json_mode: bool) -> str:
            if "期间一句话" in user:
                assert model == "m1"
                return '{"narrative": ["本期没有已实现交易（AI 叙述）。"]}'
            if "组合结果" in user:
                # 坏 JSON：应退回纯文本分行，仍算 AI 段
                return "第一行叙述\n第二行叙述"
            if "按标的归因" in user:
                if model == "m1":
                    raise RuntimeError("m1 挂了")
                assert model == "m2"
                return '{"narrative": ["本期没有已实现归因（AI 叙述）。"]}'
            raise RuntimeError("m1/m2 都挂了")

    narrated = period_ai.narrate(report, client=FakeClient(), config_root=tmp_path)
    by_name = {item["name"]: item for item in narrated["sections"]}
    assert by_name["overview"]["mode"] == "ai"
    assert by_name["portfolio"]["mode"] == "ai"  # 纯文本降级仍是 AI 段
    assert by_name["attribution"]["mode"] == "ai"
    assert by_name["attribution"]["model"] == "m2"
    assert by_name["discipline"]["mode"] == "template"
    assert by_name["discipline"]["degraded"] is True
    assert by_name["market"]["mode"] == "template"
    assert narrated["degraded"] is True and narrated["failed"] is True
    # 模板段确实复述事实包，不空段
    assert by_name["discipline"]["narrative"]


def test_render_markdown_prefers_ai_sections_and_marks_degraded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ladderstock.analytics import period_ai

    _stub_config(monkeypatch, ("m1",))
    report = period.build_report("month", "2026-11", root=tmp_path)
    narrated = period_ai.narrate(report, client=_AllTemplateClient(), config_root=tmp_path)
    report["ai"] = {
        "configured": True,
        "degraded": narrated["degraded"],
        "sections": narrated["sections"],
    }
    text = period.render_markdown(report)
    assert "AI 叙述" in text
    assert "程序模板要点" in text  # 降级段有标记


class _AllTemplateClient:
    def complete(self, model: str, system: str, user: str, *, json_mode: bool) -> str:
        raise RuntimeError("全部失败")