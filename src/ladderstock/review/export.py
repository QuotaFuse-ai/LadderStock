"""复盘报告导出：PDF（CJK 字体）+ Markdown 自动降级。

文档明确要求（M1）：
- 弃用 helvetica（不含中文字形），按平台探测可用的 CJK 字体并注册到 PDF 引擎；
- 字体缺失或 PDF 渲染失败时**自动降级 Markdown**，stderr 说明原因，退出码仍为 0；
- `--format md` 与 PDF 降级产物走**同一个渲染函数**，保证内容一致。

边界（DEC-006）：本模块只消费已生成的复盘 JSON，不取数、不计算指标。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

ExportFormat = Literal["pdf", "md"]

PDF_SUFFIX = ".pdf"
MD_SUFFIX = ".md"

# 注册到 reportlab 的字体名（内部使用，不对外暴露）
_REGULAR_NAME = "LadderCJK"
_BOLD_NAME = "LadderCJK-Bold"

# 显式覆盖：设置后只认这个字体文件，不再探测平台默认路径。
# 测试与离线环境用它强制触发降级分支；值为空字符串等价于未设置。
FONT_ENV = "LDST_CJK_FONT"


def _candidate_fonts() -> list[Path]:
    """按平台探测可用的 CJK TrueType 字体（ttf / ttc）。

    `LDST_CJK_FONT` 显式指定时只认该路径：这是唯一能稳定复现「字体缺失 →
    降级 Markdown」的方式，也便于用户把字体放到自定义目录。
    """
    override = os.environ.get(FONT_ENV, "").strip()
    if override:
        return [Path(override)]

    candidates: list[Path] = []
    home = Path.home()

    if os.name == "nt":
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        candidates += [
            fonts / "msyh.ttc",  # 微软雅黑
            fonts / "msyhbd.ttc",
            fonts / "simsun.ttc",  # 宋体
            fonts / "simhei.ttf",  # 黑体
            fonts / "Deng.ttf",  # 等线
        ]
    candidates += [
        # macOS
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/System/Library/Fonts/STHeiti Medium.ttc"),
        Path("/Library/Fonts/Arial Unicode.ttf"),
        # Linux：Noto / 思源 / 文泉驿
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
        Path("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"),
        Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
        Path("/usr/share/fonts/truetype/arphic/uming.ttc"),
        # 用户目录兜底（便携安装）
        home / ".local/share/fonts/NotoSansCJK-Regular.ttc",
        home / "Library/Fonts/NotoSansCJK-Regular.ttc",
    ]
    return candidates


@dataclass(frozen=True)
class FontSpec:
    """已注册到 PDF 引擎的字体。`path` 即实际渲染用的字体文件。"""

    path: Path
    bold_path: Path | None
    name: str = _REGULAR_NAME

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": str(self.path),
            "bold_path": str(self.bold_path) if self.bold_path else None,
        }


@dataclass(frozen=True)
class ExportOutcome:
    """一次导出的结果（CLI 直接序列化到 stdout）。"""

    format: ExportFormat
    path: Path
    degraded: bool
    reason: str | None = None
    font: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "path": str(self.path),
            "degraded": self.degraded,
            "reason": self.reason,
            "font": self.font,
        }


# --------------------------------------------------------------------------- #
# 字体注册
# --------------------------------------------------------------------------- #

def register_cjk_font() -> tuple[FontSpec | None, str]:
    """探测并注册 CJK 字体。返回 `(spec, reason)`；失败时 spec 为 None。

    `reason` 是给用户看的降级原因（stderr / stdout 都会用）。
    """
    try:
        from reportlab.pdfbase import pdfmetrics  # type: ignore[import-untyped]
        from reportlab.pdfbase.ttfonts import TTFont  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - 依赖缺失分支由测试替换
        return None, f"PDF 引擎不可用（reportlab 未安装：{exc}）；请 pip install reportlab"

    attempted: list[str] = []
    regular: FontSpec | None = None
    bold_path: Path | None = None

    for path in _candidate_fonts():
        attempted.append(str(path))
        if not path.is_file():
            continue
        try:
            pdfmetrics.registerFont(TTFont(_REGULAR_NAME, str(path), subfontIndex=0))
        except Exception:  # noqa: BLE001 - 字体损坏/格式不符时继续找下一个
            continue
        regular = FontSpec(path=path, bold_path=None)
        # 同目录的粗体：找不到就用同一字体注册家族，避免 <b> 回退到不含中文字形的字体
        for sibling in _bold_candidates(path):
            if not sibling.is_file():
                continue
            try:
                pdfmetrics.registerFont(TTFont(_BOLD_NAME, str(sibling), subfontIndex=0))
            except Exception:  # noqa: BLE001
                continue
            bold_path = sibling
            break
        pdfmetrics.registerFontFamily(
            _REGULAR_NAME, normal=_REGULAR_NAME, bold=_BOLD_NAME if bold_path else _REGULAR_NAME
        )
        regular = FontSpec(path=path, bold_path=bold_path)
        break

    if regular is None:
        tried = "、".join(attempted[:6]) or "（无候选）"
        return None, f"未找到可用的中文字体（已尝试：{tried}）"
    return regular, ""


def _bold_candidates(regular: Path) -> list[Path]:
    name = regular.name.lower()
    if name.startswith("msyh") and regular.suffix.lower() == ".ttc":
        return [regular.with_name("msyhbd.ttc")]
    if name.startswith("simsun"):
        return [regular.with_name("simsunb.ttf")]
    if name.startswith("deng"):
        return [regular.with_name("Dengb.ttf")]
    if name.startswith("pingfang"):
        return [regular.with_name("PingFang.ttc")]  # 家族内含粗体，注册同一文件
    if name.startswith("notosanscjk"):
        return [regular.with_name("NotoSansCJK-Bold.ttc")]
    return []


# --------------------------------------------------------------------------- #
# Markdown 渲染（--format md 与 PDF 降级共用）
# --------------------------------------------------------------------------- #

def render_markdown(
    report: dict[str, Any],
    *,
    degraded: bool = False,
    degrade_reason: str | None = None,
) -> str:
    """把复盘 JSON 渲染成 Markdown。

    这是唯一的 Markdown 渲染入口：`--format md` 与 PDF 降级产物都调用它，
    因此两者内容完全一致，只有顶部的导出徽标不同。
    """
    lines: list[str] = []

    if degraded:
        reason = degrade_reason or "PDF 渲染不可用"
        lines.append(f"> **导出格式：Markdown（已降级）** —— PDF 不可用：{reason}")
    else:
        lines.append("> **导出格式：Markdown**（用户指定 `--format md`）")

    lines.append("")

    title = str(report.get("title") or _default_title(report))
    lines += [f"# {title}", ""]

    meta = _meta_line(report)
    if meta:
        lines += [meta, ""]

    source_detail = str(report.get("source_detail") or "").strip()
    if source_detail:
        lines += [f"> 数据源说明：{source_detail}", ""]

    headline = _string_list(report.get("headline"))
    if headline:
        lines += ["## 今日要点", ""]
        lines += [f"- {item}" for item in headline]
        lines.append("")

    for section in _sections(report):
        lines += [f"## {section['title']}", ""]
        for paragraph in section["narrative"]:
            lines += [paragraph, ""]
        if section["metrics"]:
            lines += _metrics_table(section["metrics"]), ""
        if section["notes"]:
            lines += [f"- {note}" for note in section["notes"]]
            lines.append("")

    failures = _failures(report)
    if failures:
        lines += ["## AI 失败段落", "", "| 段落 | 错误分类 | 提示 | 可重试 |", "|---|---|---|---|"]
        for failure in failures:
            lines.append(
                "| {section} | {kind} | {action} | {retryable} |".format(
                    section=_cell(failure.get("section") or "-"),
                    kind=_cell(failure.get("kind") or "-"),
                    action=_cell(failure.get("action") or failure.get("message") or "-"),
                    retryable="是" if failure.get("retryable") else "否",
                )
            )
        lines.append("")

    validation = report.get("validation") or {}
    conditions = _string_list(validation.get("conditions"))
    if conditions:
        date = validation.get("date")
        suffix = f"（{date}）" if date else ""
        lines += [f"## 明日验证条件{suffix}", ""]
        lines += [f"- {item}" for item in conditions]
        lines.append("")

    footer = str(report.get("footer") or "").strip()
    if footer:
        lines += ["---", "", footer, ""]

    return "\n".join(lines).rstrip() + "\n"


def _default_title(report: dict[str, Any]) -> str:
    date = report.get("date")
    return f"{date} 收盘复盘" if date else "收盘复盘"


def _meta_line(report: dict[str, Any]) -> str:
    parts: list[str] = []
    if report.get("date"):
        parts.append(f"数据时点：{report['date']}")
    if report.get("freshness"):
        parts.append(f"新鲜度：{report['freshness']}")
    if report.get("source_status"):
        parts.append(f"数据源状态：{report['source_status']}")
    ai = report.get("ai") or {}
    if isinstance(ai, dict) and ai.get("model"):
        mode = "（降级）" if ai.get("degraded") else ""
        parts.append(f"AI 模型：{ai['model']}{mode}")
    generated = report.get("generated_at") or ""
    if generated:
        parts.append(f"生成时间：{generated}")
    else:
        parts.append(f"导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    return "> " + " · ".join(parts) if parts else ""


def _sections(report: dict[str, Any]) -> list[dict[str, Any]]:
    """归一化段落结构，容忍叙事为字符串或列表、指标为 dict 或空。"""
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(report.get("sections") or [], start=1):
        if not isinstance(raw, dict):
            continue
        narrative = raw.get("narrative")
        if isinstance(narrative, str):
            paragraphs = [narrative.strip()] if narrative.strip() else []
        else:
            paragraphs = _string_list(narrative)
        metrics = raw.get("metrics")
        normalized = {str(k): v for k, v in metrics.items()} if isinstance(metrics, dict) else {}
        result.append(
            {
                "title": str(raw.get("title") or f"第 {index} 段"),
                "narrative": paragraphs,
                "metrics": normalized,
                "notes": _string_list(raw.get("notes")),
            }
        )
    return result


def _failures(report: dict[str, Any]) -> list[dict[str, Any]]:
    raw = report.get("ai_failures") or []
    return [item for item in raw if isinstance(item, dict)]


def _metrics_table(metrics: dict[str, Any]) -> str:
    lines = ["| 指标 | 数值 |", "|---|---|"]
    for key, value in metrics.items():
        lines.append(f"| {_cell(key)} | {_cell(_scalar(value))} |")
    return "\n".join(lines)


def _scalar(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def _cell(value: Any) -> str:
    return _scalar(value).replace("|", "\\|").replace("\n", " ")


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)]


# --------------------------------------------------------------------------- #
# PDF 渲染
# --------------------------------------------------------------------------- #

def _build_pdf(report: dict[str, Any], out_path: Path, font: FontSpec) -> None:
    """用 Platypus 渲染 PDF；中文字体已在 `register_cjk_font` 中注册。"""
    from reportlab.lib import colors  # type: ignore[import-untyped]
    from reportlab.lib.enums import TA_LEFT  # type: ignore[import-untyped]
    from reportlab.lib.pagesizes import A4  # type: ignore[import-untyped]
    from reportlab.lib.styles import ParagraphStyle  # type: ignore[import-untyped]
    from reportlab.lib.units import mm  # type: ignore[import-untyped]
    from reportlab.platypus import (  # type: ignore[import-untyped]
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    bold_name = _BOLD_NAME if font.bold_path else _REGULAR_NAME
    h1 = ParagraphStyle(
        "LadderH1", fontName=bold_name, fontSize=20, leading=28, spaceAfter=6
    )
    h2 = ParagraphStyle(
        "LadderH2", fontName=bold_name, fontSize=14, leading=20, spaceBefore=12, spaceAfter=6
    )
    body = ParagraphStyle(
        "LadderBody",
        fontName=_REGULAR_NAME,
        fontSize=10.5,
        leading=17,
        alignment=TA_LEFT,
        spaceAfter=4,
        wordWrap="CJK",
    )
    meta = ParagraphStyle(
        "LadderMeta", parent=body, fontSize=9, textColor=colors.HexColor("#555555")
    )
    bullet = ParagraphStyle(
        "LadderBullet",
        parent=body,
        leftIndent=12,
        bulletIndent=2,
        # 项目符号也必须用 CJK 字体，否则 reportlab 会回退到 Helvetica
        bulletFontName=bold_name,
        bulletFontSize=10.5,
    )
    badge = ParagraphStyle(
        "LadderBadge", parent=body, fontSize=9, textColor=colors.HexColor("#8a4b00")
    )

    story: list[Any] = []
    title = str(report.get("title") or _default_title(report))
    story.append(Paragraph(_escape(title), h1))
    story.append(
        Paragraph(_escape(f"导出格式：PDF（中文字体：{font.path.name}）"), badge)
    )
    meta_text = _meta_line(report).lstrip("> ").strip()
    if meta_text:
        story.append(Paragraph(_escape(meta_text), meta))
    source_detail = str(report.get("source_detail") or "").strip()
    if source_detail:
        story.append(Paragraph(_escape(f"数据源说明：{source_detail}"), meta))
    story.append(Spacer(1, 4 * mm))

    headline = _string_list(report.get("headline"))
    if headline:
        story.append(Paragraph("今日要点", h2))
        for item in headline:
            story.append(Paragraph(_escape(item), bullet, bulletText="•"))

    for section in _sections(report):
        story.append(Paragraph(_escape(section["title"]), h2))
        for paragraph in section["narrative"]:
            story.append(Paragraph(_escape(paragraph), body))
        if section["metrics"]:
            data = [["指标", "数值"]] + [
                [_escape(str(key)), _escape(_scalar(value))]
                for key, value in section["metrics"].items()
            ]
            table = Table(data, hAlign="LEFT", repeatRows=1)
            table.setStyle(
                TableStyle(
                    [
                        ("FONTNAME", (0, 0), (-1, -1), _REGULAR_NAME),
                        ("FONTNAME", (0, 0), (-1, 0), bold_name),
                        ("FONTSIZE", (0, 0), (-1, -1), 9.5),
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
                        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#999999")),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 6),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ]
                )
            )
            story.append(Spacer(1, 2 * mm))
            story.append(table)
            story.append(Spacer(1, 2 * mm))
        for note in section["notes"]:
            story.append(Paragraph(_escape(note), bullet, bulletText="•"))

    failures = _failures(report)
    if failures:
        story.append(Paragraph("AI 失败段落", h2))
        data = [["段落", "错误分类", "可行动提示", "可重试"]]
        for failure in failures:
            data.append(
                [
                    _escape(str(failure.get("section") or "-")),
                    _escape(str(failure.get("kind") or "-")),
                    _escape(str(failure.get("action") or failure.get("message") or "-")),
                    "是" if failure.get("retryable") else "否",
                ]
            )
        table = Table(data, hAlign="LEFT", repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), _REGULAR_NAME),
                    ("FONTNAME", (0, 0), (-1, 0), bold_name),
                    ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#999999")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        story.append(table)

    validation = report.get("validation") or {}
    conditions = _string_list(validation.get("conditions"))
    if conditions:
        suffix = f"（{validation['date']}）" if validation.get("date") else ""
        story.append(Paragraph(_escape(f"明日验证条件{suffix}"), h2))
        for item in conditions:
            story.append(Paragraph(_escape(item), bullet, bulletText="•"))

    footer = str(report.get("footer") or "").strip()
    if footer:
        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph(_escape(footer), meta))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title=title,
        author="LadderStock",
    )
    doc.build(story)


def _escape(text: str) -> str:
    """Paragraph 只认 XML 转义；不给它喂原始 markdown。"""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br/>")
    )


# --------------------------------------------------------------------------- #
# 导出入口
# --------------------------------------------------------------------------- #

def export_review(
    report: dict[str, Any],
    out_path: Path,
    fmt: ExportFormat = "pdf",
) -> ExportOutcome:
    """导出复盘：PDF 失败自动降级到同名 Markdown（退出码由调用方保持 0）。"""
    out_path = _with_suffix(out_path, fmt)

    if fmt == "md":
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(render_markdown(report), encoding="utf-8")
        return ExportOutcome(format="md", path=out_path, degraded=False)

    font, reason = register_cjk_font()
    if font is None:
        return _degrade(report, out_path, reason)

    try:
        _build_pdf(report, out_path, font)
    except Exception as exc:  # noqa: BLE001 - 任何渲染失败都降级，不能崩溃
        return _degrade(report, out_path, f"PDF 渲染失败（{type(exc).__name__}）")

    return ExportOutcome(format="pdf", path=out_path, degraded=False, font=str(font.path))


def _degrade(report: dict[str, Any], pdf_path: Path, reason: str) -> ExportOutcome:
    md_path = pdf_path.with_suffix(MD_SUFFIX)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    text = render_markdown(report, degraded=True, degrade_reason=reason)
    md_path.write_text(text, encoding="utf-8")
    return ExportOutcome(format="md", path=md_path, degraded=True, reason=reason)


def _with_suffix(path: Path, fmt: ExportFormat) -> Path:
    suffix = PDF_SUFFIX if fmt == "pdf" else MD_SUFFIX
    if path.suffix.lower() != suffix:
        path = path.with_suffix(suffix)
    return path
