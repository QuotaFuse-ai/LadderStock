"""`ldst brief`：F11 always-on 简报的命令行入口（register 模式）。

命令刻意分成"只读"和"会落库"两组：

- `morning` / `close` / `alerts`：现场生成一份简报，**只打印，不写提醒中心**；
- `today`：三类各生成一份，一屏看完今天有什么；
- `history`：最近几天状态文件里"推了什么、跳过了什么"；
- `run KIND`：生成 → 指纹去重 → 写收件箱 →（`--notify` 或配置打开时）投递；
- `config`：看 / 改开关（`notify` / `channel` / `threshold`）。

`run` 与 `build` 分开是因为到点跑（调度器）和无副作用地"看一眼"是两种需求：
前者会写库，后者绝不能。

约定：任何一条取不到数都写"未取到（原因）"，不补 0、不猜、不发空简报。
"""

from __future__ import annotations

from typing import Annotated, Any

import typer

from ladderstock.cli.output import OutputOption, emit, resolve_format
from ladderstock.watch import brief


def _print_brief(result: brief.Brief) -> None:
    typer.echo(f"【{result.title}】{result.status} · {result.detail}")
    if not result.items:
        typer.echo("  （没有条目）")
        return
    for index, item in enumerate(result.items, start=1):
        typer.echo(f"  {index}. {item.title}  <{item.source}>")
        for line in item.lines:
            typer.echo(f"     {line}")


def _build_and_print(
    kind: str,
    *,
    threshold: float,
    output: Any,
) -> None:
    result = brief.build(kind, threshold=threshold)
    if resolve_format(output) != "table":
        emit(result.as_dict(), output)
    else:
        _print_brief(result)


def brief_morning(
    threshold: Annotated[
        float, typer.Option("--threshold", help="异动阈值（%）")
    ] = brief.ALERT_THRESHOLD_PCT,
    output: OutputOption = None,
) -> None:
    """生成晨报（只打印，不写收件箱）。"""
    _build_and_print(brief.BRIEF_MORNING, threshold=threshold, output=output)


def brief_close(
    threshold: Annotated[
        float, typer.Option("--threshold", help="异动阈值（%）")
    ] = brief.ALERT_THRESHOLD_PCT,
    output: OutputOption = None,
) -> None:
    """生成收盘复盘（只打印，不写收件箱）。"""
    _build_and_print(brief.BRIEF_CLOSE, threshold=threshold, output=output)


def brief_alerts(
    threshold: Annotated[
        float, typer.Option("--threshold", help="异动阈值（%）")
    ] = brief.ALERT_THRESHOLD_PCT,
    output: OutputOption = None,
) -> None:
    """生成持仓异动（只打印，不写收件箱）。"""
    _build_and_print(brief.BRIEF_ALERTS, threshold=threshold, output=output)


def brief_today(
    threshold: Annotated[
        float, typer.Option("--threshold", help="异动阈值（%）")
    ] = brief.ALERT_THRESHOLD_PCT,
    output: OutputOption = None,
) -> None:
    """三类各生成一份，一屏看完今天。"""
    payload = {kind: brief.build(kind, threshold=threshold).as_dict() for kind in brief.BRIEFS}
    if resolve_format(output) != "table":
        emit(payload, output)
        return
    for row in payload.values():
        typer.echo(f"【{row['title']}】{row['status']} · {row['detail']}")
        for index, item in enumerate(row["items"], start=1):
            typer.echo(f"  {index}. {item['title']}  <{item['source']}>")
            for line in item["lines"]:
                typer.echo(f"     {line}")
        typer.echo("")


def brief_run(
    kind: Annotated[str, typer.Argument(help="morning / close_review / position_alerts")],
    notify: Annotated[
        bool, typer.Option("--notify/--no-notify", help="是否走通知渠道（默认跟随配置）")
    ] = False,
    channel: Annotated[
        str | None, typer.Option("--channel", help="console / desktop（缺省用配置）")
    ] = None,
    threshold: Annotated[float | None, typer.Option("--threshold", help="异动阈值（%）")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="只生成，不写收件箱、不投递")] = False,
    output: OutputOption = None,
) -> None:
    """生成并发布一份简报：去重 → 收件箱 →（可选）投递。"""
    name = str(kind or "").strip().lower()
    if name not in brief.BRIEFS:
        typer.echo(f"没有这种简报 {kind!r}；可用：{' / '.join(brief.BRIEFS)}")
        raise typer.Exit(code=2)
    settings = brief.load_settings()
    value = settings["threshold"] if threshold is None else threshold
    result = brief.build(name, threshold=value)
    structured = resolve_format(output) != "table"
    if dry_run:
        if structured:
            emit({**result.as_dict(), "dry_run": True}, output)
        else:
            _print_brief(result)
            typer.echo("（dry-run：没有写收件箱，也没有投递）")
        return
    outcome = brief.publish(
        result,
        notify=notify,
        channel=channel or (settings["channel"] if notify else None),
    )
    if structured:
        emit({**outcome, "brief": result.as_dict()}, output)
        return
    _print_brief(result)
    typer.echo(
        f"推送 {len(outcome['pushed'])} 条 · 跳过（同日已推）{len(outcome['skipped'])} 条"
        f" · 状态文件 {outcome['state_path']}"
    )
    for row in outcome["delivered"]:
        typer.echo(f"  投递 {row['channel']}：{'成功' if row['ok'] else '失败'} · {row['detail']}")


def brief_history(
    limit: Annotated[int, typer.Option("--limit", "-n", help="最多几天")] = 10,
    output: OutputOption = None,
) -> None:
    """最近几天的简报状态（只读状态文件）。"""
    days = brief.history(limit=limit)
    if resolve_format(output) != "table":
        emit({"days": days}, output)
        return
    if not days:
        typer.echo("还没有跑过简报（data/brief/ 为空）。")
        return
    for day in days:
        typer.echo(f"{day['trade_day']}  已推送 {day['published']} 条指纹")
        for run in day["briefs"]:
            typer.echo(
                f"  {run.get('label')} · {run.get('status')} · 条目 {run.get('items')}"
                f" · 推送 {run.get('pushed')} · {run.get('generated_at')}"
            )


def brief_config(
    notify: Annotated[
        bool | None, typer.Option("--notify/--no-notify", help="是否投递到渠道")
    ] = None,
    channel: Annotated[str | None, typer.Option("--channel", help="console / desktop")] = None,
    threshold: Annotated[float | None, typer.Option("--threshold", help="异动阈值（%）")] = None,
    output: OutputOption = None,
) -> None:
    """查看或修改简报开关（写 config/brief.yaml）。"""
    structured = resolve_format(output) != "table"
    if notify is None and channel is None and threshold is None:
        settings = brief.load_settings()
        payload = {**settings, "path": str(brief.settings_path())}
        if structured:
            emit(payload, output)
            return
        typer.echo(
            f"notify={settings['notify']} channel={settings['channel']}"
            f" threshold={settings['threshold']}"
        )
        typer.echo(f"配置文件：{payload['path']}（不存在时用默认值）")
        return
    try:
        saved = brief.save_settings(notify=notify, channel=channel, threshold=threshold)
    except ValueError as exc:
        typer.echo(f"改不了：{exc}")
        raise typer.Exit(code=2) from None
    if structured:
        emit(saved, output)
        return
    typer.echo(
        f"已写 {saved['path']}：notify={saved['notify']} channel={saved['channel']}"
        f" threshold={saved['threshold']}"
    )


def register(brief_app: typer.Typer) -> None:
    """把七个命令挂到 `ldst brief` 子应用上。"""
    brief_app.command("morning", help="生成晨报（只打印）")(brief_morning)
    brief_app.command("close", help="生成收盘复盘（只打印）")(brief_close)
    brief_app.command("alerts", help="生成持仓异动（只打印）")(brief_alerts)
    brief_app.command("today", help="三类各生成一份")(brief_today)
    brief_app.command("run", help="生成并发布（写收件箱 + 可选投递）")(brief_run)
    brief_app.command("history", help="最近几天的推送状态")(brief_history)
    brief_app.command("config", help="查看/修改通知开关")(brief_config)


__all__ = [
    "brief_alerts",
    "brief_close",
    "brief_config",
    "brief_history",
    "brief_morning",
    "brief_run",
    "brief_today",
    "register",
]
