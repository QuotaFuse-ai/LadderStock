"""`ldst report`：月报 / 季报 / 年报（B 包后半，模板叙述 + 事实包）。

与 `analytics/period.build_report` 同一个执行体：
- 期间已实现结果 / 按标的归因 / 买卖纪律 / 期间错题 / 市场情绪均值；
- 落盘 `data/report/<period>/<tag>.{json,md}`；
- 不调 AI（叙述是程序模板），AI 版月报留给后续单。
"""

from __future__ import annotations

from typing import Annotated, Any

import typer

from ladderstock.analytics import period as report_period
from ladderstock.cli.output import OutputOption, emit, resolve_format
from ladderstock.errors import LadderStockError


def report_command(
    period_type: Annotated[
        str, typer.Option("--period", help="周期：month / quarter / year")
    ] = "month",
    of: Annotated[
        str | None,
        typer.Option("--of", help="目标周期：YYYY-MM / YYYY-Qn / YYYY；缺省取上一个完整周期"),
    ] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="只生成不落盘")] = False,
    output: OutputOption = None,
) -> None:
    """生成周期报告（月/季/年）：事实包 + 模板叙述，落 data/report/。"""
    try:
        if dry_run:
            built = report_period.build_report(period_type, of)
            payload: dict[str, Any] = {"report": built, "json_path": None, "md_path": None}
        else:
            payload = report_period.write_report(period_type, of)
    except LadderStockError as exc:
        typer.echo(f"ldst: {exc.message}", err=True)
        raise typer.Exit(exc.code) from None
    report = payload["report"]
    if resolve_format(output) != "table":
        emit(payload, output)
        return
    typer.echo(f"{report['label']} 报告已生成（{report['start']} → {report['end']}）")
    if payload["md_path"]:
        typer.echo(f"Markdown：{payload['md_path']}")
        typer.echo(f"JSON：{payload['json_path']}")
    else:
        typer.echo("（dry-run：没有落盘）")
    typer.echo(
        f"已实现 {report['portfolio']['realized_count']} 笔，合计 "
        f"{report['portfolio']['realized_total']:.2f}"
    )


def register(app: typer.Typer) -> None:
    app.command("report", help="月报/季报/年报（事实包 + 模板叙述）")(report_command)


__all__ = ["register", "report_command"]
