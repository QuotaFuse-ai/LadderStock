"""`ldst report`：月报 / 季报 / 年报（B 包，事实包 + AI 叙述 / 模板兑底）。

与 `analytics/period.write_report` 同一个执行体：
- 期间已实现结果 / 按标的归因 / 买卖纪律 / 期间错题 / 市场情绪均值；
- 叙述：配置了模型凭据走 AI（`period_ai.narrate`），失败段落退模板并
  `degraded=True`（退出码 9，与 `ldst weekly` 同口径）；`--no-ai` 或未配置
  凭据时纯模板直出（退出码 0，B 包第一阶段契约不变）；
- 落盘 `data/report/<period>/<tag>.{json,md}`。
"""

from __future__ import annotations

from typing import Annotated, Any

import typer

from ladderstock.analytics import period as report_period
from ladderstock.cli.output import OutputOption, emit, resolve_format
from ladderstock.errors import EXIT_AI_DEGRADED, LadderStockError


def report_command(
    period_type: Annotated[
        str, typer.Option("--period", help="周期：month / quarter / year")
    ] = "month",
    of: Annotated[
        str | None,
        typer.Option("--of", help="目标周期：YYYY-MM / YYYY-Qn / YYYY；缺省取上一个完整周期"),
    ] = None,
    no_ai: Annotated[
        bool, typer.Option("--no-ai", help="跳过 AI 叙述层，纯模板直出（不调模型）")
    ] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="只生成不落盘")] = False,
    output: OutputOption = None,
) -> None:
    """生成周期报告（月/季/年）：事实包 + AI 叙述（模板兑底），落 data/report/。"""
    try:
        if dry_run:
            built = report_period.build_report(period_type, of)
            report = dict(built)
            report["ai"] = {
                "configured": False,
                "degraded": False,
                "failed": False,
                "models": [],
                "ai_failures": [],
                "sections": [],
                "skipped": True,
            }
            payload: dict[str, Any] = {"report": report, "json_path": None, "md_path": None}
        else:
            payload = report_period.write_report(period_type, of, use_ai=not no_ai)
    except LadderStockError as exc:
        typer.echo(f"ldst: {exc.message}", err=True)
        raise typer.Exit(exc.code) from None
    report = payload["report"]
    ai = report.get("ai") or {}
    if resolve_format(output) != "table":
        emit(payload)
        if ai.get("degraded"):
            raise typer.Exit(EXIT_AI_DEGRADED)
        return
    typer.echo(f"{report['label']} 报告已生成（{report['start']} → {report['end']}）")
    if payload["md_path"]:
        typer.echo(f"Markdown：{payload['md_path']}")
        typer.echo(f"JSON：{payload['json_path']}")
    else:
        typer.echo("（dry-run：没有落盘）")
    if ai.get("skipped"):
        typer.echo("叙述：dry-run 不调模型（模板要点见 md 产物；正式生成时走 AI / 模板）")
    elif ai.get("configured"):
        modes = [
            f"{item.get('name')}={item.get('mode')}"
            for item in ai.get("sections") or []
        ]
        typer.echo(f"叙述：AI 已配置（{' · '.join(modes)}）")
        for failure in ai.get("ai_failures") or []:
            name = failure.get("section")
            kind = failure.get("kind")
            model = failure.get("model")
            typer.echo(f"  ! {name}：{kind}（{model}）")
    else:
        typer.echo("叙述：未配置模型凭据，模板直出（配置后重跑可获得 AI 叙述）")
    typer.echo(
        f"已实现 {report['portfolio']['realized_count']} 笔，合计 "
        f"{report['portfolio']['realized_total']:.2f}"
    )
    if ai.get("degraded"):
        raise typer.Exit(EXIT_AI_DEGRADED)


def register(app: typer.Typer) -> None:
    app.command("report", help="月报/季报/年报（事实包 + AI 叙述，模板兑底）")(report_command)


__all__ = ["register", "report_command"]
