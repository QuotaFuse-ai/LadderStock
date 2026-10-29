"""`ldst export` 命令体：读取已生成的复盘 JSON，导出 PDF / Markdown。

实现与注册分离（`cli/main.py` 只 import + 注册），便于测试直接调用。
复盘数据默认在 `data/review/<date>.json`，导出默认为 `data/exports/<date>-review.pdf`；
两者都跟随 `LDST_DATA_DIR`（见 `cli/paths.py`），便于测试与便携安装。
"""

from __future__ import annotations

import json
import sys
from datetime import date as date_type
from pathlib import Path
from typing import Annotated, Any

import typer

from ladderstock.cli.output import OutputOption, emit, error
from ladderstock.cli.paths import data_dir
from ladderstock.core.privacy import private_keys_present, strip_private
from ladderstock.errors import EXIT_CONFIG, EXIT_NOT_FOUND, EXIT_USAGE
from ladderstock.journal.stats import compute_stats
from ladderstock.review.export import ExportFormat, export_review

REVIEW_SUBDIR = "review"
EXPORT_SUBDIR = "exports"


def review_path(day: str) -> Path:
    """复盘 JSON 的默认位置（M3 复盘流水线写入同一路径）。"""
    return data_dir() / REVIEW_SUBDIR / f"{day}.json"


def _validate_date(value: str) -> str:
    try:
        date_type.fromisoformat(value)
    except ValueError:
        error(f"--date 需要 YYYY-MM-DD，收到 {value!r}")
        raise typer.Exit(EXIT_USAGE) from None
    return value


def _resolve_source(day: str | None) -> tuple[str, Path]:
    """确定要导出的复盘：显式日期，或 data/review 下最新的一份。"""
    if day:
        day = _validate_date(day)
        return day, review_path(day)

    base = data_dir() / REVIEW_SUBDIR
    files = sorted(base.glob("*.json")) if base.is_dir() else []
    if not files:
        error(f"没有可导出的复盘：{base} 下没有 *.json（先运行 ldst review --date YYYY-MM-DD）")
        raise typer.Exit(EXIT_NOT_FOUND)
    return files[-1].stem, files[-1]


def load_review(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        error(f"复盘文件不是合法 JSON：{path}（{exc.msg}，第 {exc.lineno} 行）")
        raise typer.Exit(EXIT_CONFIG) from None
    except OSError as exc:
        error(f"读取复盘文件失败：{path}（{exc}）")
        raise typer.Exit(EXIT_CONFIG) from None
    if not isinstance(data, dict):
        error(f"复盘文件顶层必须是对象：{path}")
        raise typer.Exit(EXIT_CONFIG)
    return data


def export(
    format: Annotated[str, typer.Option("--format", help="pdf / md")] = "pdf",
    date: Annotated[str | None, typer.Option(help="交易日 YYYY-MM-DD，缺省取最新复盘")] = None,
    out: Annotated[
        str | None, typer.Option(help="输出路径（默认 data/exports/<date>-review.*）")
    ] = None,
    with_journal: Annotated[
        bool,
        typer.Option(
            "--with-journal",
            help="附加本机交易日志纪律统计（隐私数据，会写进导出文件；默认不导出）",
        ),
    ] = False,
    output: OutputOption = None,
) -> None:
    """导出复盘报告；PDF 不可用时自动降级 Markdown（退出码仍为 0）。"""
    if format not in ("pdf", "md"):
        error("--format 只接受 pdf 或 md")
        raise typer.Exit(EXIT_USAGE)

    day, source = _resolve_source(date)
    if not source.is_file():
        error(f"未找到 {day} 的复盘：{source}（先运行 ldst review --date {day}）")
        raise typer.Exit(EXIT_NOT_FOUND)

    report = load_review(source)
    report.setdefault("date", day)

    # 隐私（DEC-006 / audit R-2）：复盘 JSON 里即使**任意层级**混进 journal /
    # trades / positions / holdings / watchlist（含中文写法）也不外泄。原先只
    # `pop` 顶层 `journal`，嵌套同名字段照样漏；现在与 web / MCP 共用同一份
    # `strip_private()`。剥掉的字段名写 stderr 明示，不静默改内容。
    stripped = private_keys_present(report)
    report = strip_private(report)
    if stripped:
        sys.stderr.write(f"ldst: 导出已剔除隐私字段：{'、'.join(stripped)}\n")
        sys.stderr.flush()

    # 隐私（DEC-006）：默认导出绝不包含交易日志；只有显式 --with-journal 才附加
    # 「纪律统计摘要」（不含明细/备注），并在 stderr 提醒用户这是本机隐私数据。
    journal_included = False
    if with_journal:
        stats = compute_stats(root=data_dir())
        if stats["total_trades"]:
            sections = report.get("sections")
            if not isinstance(sections, list):
                sections = []
                report["sections"] = sections
            sections.append(_journal_section(stats))
            journal_included = True
            sys.stderr.write(
                "ldst: 导出包含本机交易日志纪律统计（隐私数据，请自行保管，勿上传）\n"
            )
            sys.stderr.flush()
        else:
            sys.stderr.write("ldst: --with-journal 已指定，但本机没有交易日志，未附加\n")
            sys.stderr.flush()

    target = Path(out) if out else data_dir() / EXPORT_SUBDIR / f"{day}-review"
    outcome = export_review(report, target, _as_format(format))

    if outcome.degraded:
        # stderr 说明原因；stdout 仍输出结构化结果，退出码 0，不把降级伪装成失败
        sys.stderr.write(f"ldst: PDF 导出降级为 Markdown：{outcome.reason}\n")
        sys.stderr.flush()

    emit(
        {
            "ok": True,
            "date": day,
            "source": str(source),
            "journal_included": journal_included,
            **outcome.as_dict(),
        },
        output,
    )


def _journal_section(stats: dict[str, Any]) -> dict[str, Any]:
    """把纪律统计摘要转成复盘渲染器认识的段落（只有聚合值，没有明细）。"""
    win_rate = stats.get("win_rate")
    ratio = stats.get("profit_loss_ratio")
    return {
        "title": "交易日志（隐私摘要）",
        "narrative": [
            "本段由 --with-journal 显式导出，仅含本机纪律统计的聚合值，"
            "不含证券代码、备注或逐笔明细。",
        ],
        "metrics": {
            "成交笔数": stats.get("total_trades"),
            "闭环次数": stats.get("closed_rounds"),
            "胜率（%）": win_rate if win_rate is not None else "样本不足",
            "盈亏比": ratio if ratio is not None else "样本不足",
            "已实现盈亏": stats.get("realized_pnl"),
            "偏离计划次数": (stats.get("deviation_review") or {}).get("count"),
        },
        "notes": [],
    }


def _as_format(value: str) -> ExportFormat:
    return "pdf" if value == "pdf" else "md"
