"""`ldst export`：PDF 中文字体与 Markdown 自动降级（M1 验收）。

覆盖：
- 中文 PDF 文本可提取（pypdf 可用时）；字体注册参数可断言（pypdf 缺失时）；
- 字体缺失 → 自动降级 Markdown，stderr 说明原因，退出码仍为 0；
- `--format md` 与 PDF 降级产物共用同一渲染函数；
- Markdown 渲染包含标题 / 表格 / 列表 / 降级徽标。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ladderstock.cli.main import app
from ladderstock.review import export as export_mod
from ladderstock.review.export import (
    _candidate_fonts,
    export_review,
    register_cjk_font,
    render_markdown,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
SAMPLE = FIXTURES / "review_sample.json"
DATE = "2026-10-27"

runner = CliRunner()


def _load_sample() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


@pytest.fixture()
def report() -> dict:
    return _load_sample()


@pytest.fixture()
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把复盘 fixture 摆进临时数据目录，模拟 `ldst review` 已经产出过报告。"""
    (tmp_path / "review").mkdir()
    (tmp_path / "review" / f"{DATE}.json").write_text(
        SAMPLE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    return tmp_path


# --------------------------------------------------------------------------- #
# Markdown 渲染（--format md 与降级共用）
# --------------------------------------------------------------------------- #

def test_markdown_has_headings_tables_and_lists(report: dict) -> None:
    text = render_markdown(report)
    assert "# 2026-10-27 收盘复盘" in text
    assert "## 今日要点" in text
    assert "- 情绪温度 62" in text
    assert "| 指标 | 数值 |" in text
    assert "| temperature | 62 |" in text
    assert "| 指标 | 数值 |\n|---|---|\n| temperature | 62 |" in text
    assert "## 明日验证条件（2026-10-28）" in text
    assert "- 情绪温度不低于 55" in text
    # AI 失败段落按四分类结果展示，而不是"AI 环节没跑通"
    assert "## AI 失败段落" in text
    assert "| truncated |" in text
    assert "| quota |" in text
    # 正常导出不带降级徽标
    assert "已降级" not in text


def test_markdown_degraded_badge_states_reason(report: dict) -> None:
    text = render_markdown(report, degraded=True, degrade_reason="未找到可用的中文字体")
    assert text.startswith("> **导出格式：Markdown（已降级）**")
    assert "未找到可用的中文字体" in text


def test_markdown_accepts_string_narrative_and_empty_metrics(report: dict) -> None:
    text = render_markdown(report)
    # fixture 里第二个段落 narrative 是字符串而不是列表
    assert "最高板为 5 板" in text
    assert "## 题材与消息面" in text


# --------------------------------------------------------------------------- #
# 字体探测
# --------------------------------------------------------------------------- #

def test_candidate_fonts_are_platform_specific() -> None:
    candidates = _candidate_fonts()
    assert candidates, "必须至少尝试一个平台字体路径"
    if os.name == "nt":
        assert any(str(path).lower().endswith(("msyh.ttc", "simsun.ttc")) for path in candidates)
    else:
        assert any("PingFang" in str(path) or "NotoSansCJK" in str(path) for path in candidates)


def test_explicit_font_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(export_mod.FONT_ENV, str(SAMPLE))
    assert _candidate_fonts() == [SAMPLE]


def test_register_font_reports_reason_when_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "no-such-font.ttf"
    monkeypatch.setenv(export_mod.FONT_ENV, str(missing))
    spec, reason = register_cjk_font()
    assert spec is None
    assert "未找到可用的中文字体" in reason


# --------------------------------------------------------------------------- #
# PDF：中文可提取
# --------------------------------------------------------------------------- #

_SYSTEM_FONT, _SYSTEM_FONT_REASON = register_cjk_font()
requires_cjk_font = pytest.mark.skipif(
    _SYSTEM_FONT is None, reason=f"本机无 CJK 字体（{_SYSTEM_FONT_REASON}）；降级分支另行覆盖"
)


@requires_cjk_font
def test_pdf_registers_cjk_font_not_helvetica(report: dict, tmp_path: Path) -> None:
    outcome = export_review(report, tmp_path / "review", "pdf")
    assert outcome.format == "pdf"
    assert outcome.degraded is False
    assert outcome.font and Path(outcome.font).is_file()
    assert Path(outcome.path).stat().st_size > 1000

    # 弃用 helvetica：所有真正写字（Tj / TJ）的文本块都必须用注册的 CJK 字体。
    # reportlab 会在页首写一个空的 `/F1 12 Tf … ET` 初始化块，那里不出字形，不算。
    pypdf = pytest.importorskip("pypdf")
    reader = pypdf.PdfReader(str(outcome.path))
    used: set[str] = set()
    names: dict[str, str] = {}
    for page in reader.pages:
        names = {
            str(key).lstrip("/"): str(font.get_object().get("/BaseFont"))
            for key, font in page["/Resources"]["/Font"].items()
        }
        content = page.get_contents().get_data().decode("latin-1")
        for block in re.findall(r"BT(.*?)ET", content, re.S):
            if "Tj" in block or "TJ" in block:
                used.update(re.findall(r"/(F[\w+.\-]+)\s+[\d.]+\s+Tf", block))
    assert used, "PDF 里没有找到任何文本绘制"
    for key in used:
        assert "Helvetica" not in names[key], f"{key} 仍是 helvetica，中文会渲染成空白"
        assert "ABCDEF" in names[key] or "YaHei" in names[key] or "Song" in names[key]


@requires_cjk_font
def test_pdf_text_is_extractable_and_chinese(report: dict, tmp_path: Path) -> None:
    outcome = export_review(report, tmp_path / "review", "pdf")
    pypdf = pytest.importorskip("pypdf", reason="提取库不可用时由字体注册用例覆盖")
    reader = pypdf.PdfReader(str(outcome.path))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert len(reader.pages) >= 1
    for expected in ("收盘复盘", "情绪温度", "晋级率", "连板天梯", "明日验证条件"):
        assert expected in text, f"PDF 文本缺少中文内容：{expected}"


# --------------------------------------------------------------------------- #
# 降级：字体缺失 → Markdown，退出码 0
# --------------------------------------------------------------------------- #

def test_missing_font_degrades_to_markdown(
    report: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(export_mod.FONT_ENV, str(tmp_path / "absent.ttf"))
    outcome = export_review(report, tmp_path / "review", "pdf")
    assert outcome.degraded is True
    assert outcome.format == "md"
    assert outcome.path.suffix == ".md"
    assert not (tmp_path / "review.pdf").exists()
    assert outcome.reason and "中文字体" in outcome.reason
    text = outcome.path.read_text(encoding="utf-8")
    assert "已降级" in text
    assert "情绪温度" in text


def test_md_and_degraded_share_one_renderer(
    report: dict, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    md = export_review(report, tmp_path / "a", "md")
    monkeypatch.setenv(export_mod.FONT_ENV, str(tmp_path / "absent.ttf"))
    degraded = export_review(report, tmp_path / "b", "pdf")

    md_body = md.path.read_text(encoding="utf-8").split("\n", 1)[1]
    degraded_body = degraded.path.read_text(encoding="utf-8").split("\n", 1)[1]
    assert md_body == degraded_body


# --------------------------------------------------------------------------- #
# CLI 端到端
# --------------------------------------------------------------------------- #

def test_cli_export_md_writes_report(data_root: Path) -> None:
    result = runner.invoke(app, ["export", "--format", "md", "--date", DATE])
    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["format"] == "md"
    assert payload["degraded"] is False
    target = Path(payload["path"])
    assert target.is_file()
    assert "情绪温度" in target.read_text(encoding="utf-8")


def test_cli_export_pdf_degrades_with_exit_zero(
    data_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(export_mod.FONT_ENV, str(data_root / "absent.ttf"))
    result = runner.invoke(app, ["export", "--format", "pdf", "--date", DATE])
    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["format"] == "md"
    assert payload["degraded"] is True
    assert "降级为 Markdown" in result.stderr


def test_cli_export_missing_review_exits_6(data_root: Path) -> None:
    result = runner.invoke(app, ["export", "--format", "pdf", "--date", "2026-01-05"])
    assert result.exit_code == 6
    assert "未找到" in result.stderr


def test_cli_export_rejects_unknown_format(data_root: Path) -> None:
    result = runner.invoke(app, ["export", "--format", "docx", "--date", DATE])
    assert result.exit_code == 2
    assert "--format 只接受 pdf 或 md" in result.stderr


def test_cli_export_uses_latest_review_when_date_omitted(data_root: Path) -> None:
    result = runner.invoke(app, ["export", "--format", "md"])
    assert result.exit_code == 0, result.stderr
    assert json.loads(result.stdout)["date"] == DATE
