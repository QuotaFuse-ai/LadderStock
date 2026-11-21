"""`ldst debate`：F7 多空对照表的命令行入口（register 模式，与 `cli/symbol.py` 同形）。

四个动作刻意分开，因为它们的"能不能做"不一样：

- `run`：真的跑一次 StockSenate 合议（分钟级、烧 token，必须显式触发）；
- `show`：读某标的某天的报告（不跑，只解析 + 门控）；
- `last`：读该标的最近一份报告（等价于 `show` 不带 `--date`）；
- `list` / `status`：只列已保存的报告 / 只看能不能跑（连 157KB 的 state.json 都不读）。

每个读取动作都会把"解析 + 门控 + 块候选"落到 `data/debate/<day>/<run_id>.json`，
页面与档案读同一份，避免两边各算一套口径。
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer

from ladderstock.cli.output import OutputOption, emit, resolve_format
from ladderstock.cli.paths import data_dir
from ladderstock.research import debate


def _analyze(
    report_dir: Path | str,
    *,
    min_quality: float,
    min_points: int,
    persist: bool,
) -> dict[str, Any]:
    """读报告 → 归一化 → 门控 → 块候选 → 落盘，一步不漏。"""
    loaded = debate.load_report(report_dir)
    if not loaded.get("ok"):
        return {"ok": False, "reason": loaded.get("reason"), "report_dir": str(report_dir)}
    normalized = debate.normalize(loaded)
    gate = debate.assess(normalized, min_quality=min_quality, min_points=min_points)
    payload: dict[str, Any] = {
        "ok": True,
        "run_id": normalized.get("run_id"),
        "code": normalized.get("code"),
        "date": normalized.get("date"),
        "report_dir": str(report_dir),
        "normalized": normalized,
        "gate": gate,
    }
    try:
        payload["blocks"] = debate.to_blocks(normalized, gate)
    except Exception as exc:  # noqa: BLE001 - 块契约错误不该吞掉门控结果
        payload["blocks"] = []
        payload["blocks_error"] = f"{type(exc).__name__}: {exc}"
    if persist:
        path = debate.save_result(data_dir(), payload)
        payload["saved"] = str(path) if path else None
    return payload


def _print_human(payload: dict[str, Any]) -> None:
    if not payload.get("ok"):
        typer.echo(f"没读到报告：{payload.get('reason')}")
        raise typer.Exit(code=1)
    gate = payload["gate"]
    nz = payload["normalized"]
    typer.echo(
        f"报告 {payload['run_id']}（{nz.get('code')} / {nz.get('date')}）"
        f" 裁决 {nz.get('rating_label')}（{nz.get('action') or '无动作'}）"
        f" 质量 {gate.get('score')}/{gate.get('threshold')}"
    )
    typer.echo(
        f"多头论点 {gate.get('bull_points')} · 空头论点 {gate.get('bear_points')} · "
        f"失败角色 {len(gate.get('failed_roles') or [])}"
    )
    if gate.get("passed"):
        typer.echo("门控：通过——可以出多空对照表")
        for row in payload.get("blocks") or []:
            block_type = row.get("type")
            body = row.get("payload") or {}
            if block_type == "table":
                for line in body.get("rows") or []:
                    typer.echo("  " + " | ".join(str(cell) for cell in line))
            elif block_type == "conclusion":
                typer.echo(f"  结论：{body.get('text')}")
                if body.get("falsifier"):
                    typer.echo(f"  翻转条件：{body.get('falsifier')}")
    else:
        typer.echo("门控：不通过——本轮不出对照表，原因：")
        for reason in gate.get("reasons") or []:
            typer.echo(f"  - {reason}")
        failed = gate.get("failed_roles") or []
        if failed:
            typer.echo("  失败角色（前 5）：")
            for item in failed[:5]:
                typer.echo(f"    {item.get('role')}：{item.get('reason')}")


def debate_list(
    limit: Annotated[int, typer.Option("--limit", "-n", help="最多列几条")] = 20,
    output: OutputOption = None,
) -> None:
    """列出已保存的合议报告（只读文件名）。"""
    items = debate.list_reports(limit=limit)
    if resolve_format(output) != "table":
        emit({"items": items, "availability": debate.availability()}, output)
        return
    if not items:
        typer.echo(
            "没有已保存的报告。StockSenate 还没跑过，或跑到了别处（见 ldst debate status）。"
        )
        return
    for item in items:
        typer.echo(f"{item['run_id']}  state={'有' if item['has_state'] else '无'}")


def debate_status(
    output: OutputOption = None,
) -> None:
    """能不能跑（CLI / 配置 / 报告数）——回答「为什么这轮没有辩论」。"""
    info = debate.availability()
    if resolve_format(output) != "table":
        emit(info, output)
        return
    typer.echo(f"目录：{info['home']}（{'存在' if info['home_exists'] else '缺失'}）")
    typer.echo(f"CLI：{info['cli'] or '没找到'}")
    typer.echo(f"config/：{'有' if info['config_exists'] else '没有'}")
    typer.echo(f"已保存报告：{info['reports']} 份（最近 {info['latest_run_id'] or '无'}）")


def debate_show(
    code: Annotated[str, typer.Argument(help="标的代码，如 600519")],
    report_date: Annotated[
        str | None, typer.Option("--date", "-d", help="YYYY-MM-DD，缺省取最近一份")
    ] = None,
    root: Annotated[
        str | None, typer.Option("--root", help="Senate 目录（默认 file/modules/senate）")
    ] = None,
    min_quality: Annotated[
        float, typer.Option("--min-quality", help="质量阈值")
    ] = debate.MIN_QUALITY,
    min_points: Annotated[
        int, typer.Option("--min-points", help="多/空各最少论点")
    ] = debate.MIN_POINTS,
    output: OutputOption = None,
) -> None:
    """读某标的某天（或最近）的报告，解析 + 门控。不跑合议。"""
    path = debate.latest_report(code, report_date, Path(root) if root else None)
    if path is None:
        typer.echo(f"没找到 {code} 在 {report_date or '任意日期'} 的报告")
        raise typer.Exit(code=1)
    payload = _analyze(path, min_quality=min_quality, min_points=min_points, persist=True)
    if resolve_format(output) != "table":
        emit(payload, output)
        return
    _print_human(payload)


def debate_last(
    code: Annotated[str, typer.Argument(help="标的代码")],
    root: Annotated[str | None, typer.Option("--root", help="Senate 目录")] = None,
    output: OutputOption = None,
) -> None:
    """读该标的最近一份报告（`show` 不带日期的简写）。"""
    path = debate.latest_report(code, None, Path(root) if root else None)
    if path is None:
        typer.echo(f"没找到 {code} 的报告")
        raise typer.Exit(code=1)
    payload = _analyze(
        path, min_quality=debate.MIN_QUALITY, min_points=debate.MIN_POINTS, persist=True
    )
    if resolve_format(output) != "table":
        emit(payload, output)
        return
    _print_human(payload)


def debate_run(
    code: Annotated[str, typer.Argument(help="标的代码")],
    report_date: Annotated[
        str | None, typer.Option("--date", "-d", help="YYYY-MM-DD，缺省今天")
    ] = None,
    timeout: Annotated[int, typer.Option("--timeout", help="超时秒数")] = debate.DEFAULT_TIMEOUT,
    root: Annotated[str | None, typer.Option("--root", help="Senate 目录")] = None,
    min_quality: Annotated[
        float, typer.Option("--min-quality", help="质量阈值")
    ] = debate.MIN_QUALITY,
    min_points: Annotated[
        int, typer.Option("--min-points", help="多/空各最少论点")
    ] = debate.MIN_POINTS,
    output: OutputOption = None,
) -> None:
    """真跑一次合议（分钟级、烧 token），跑完立刻解析 + 门控。"""
    outcome = debate.run_debate(
        code, report_date, root=Path(root) if root else None, timeout=timeout
    )
    structured = resolve_format(output) != "table"
    if not outcome.get("ok"):
        if structured:
            emit(outcome, output)
        else:
            typer.echo(f"合议没跑成：{outcome.get('reason')}")
        raise typer.Exit(code=1)
    report_dir = outcome.get("report_dir")
    if not report_dir:
        if structured:
            emit({"ok": False, "reason": "跑完了但没定位到报告目录", "run": outcome}, output)
        else:
            typer.echo("跑完了但没定位到报告目录")
        raise typer.Exit(code=1)
    payload = _analyze(report_dir, min_quality=min_quality, min_points=min_points, persist=True)
    payload["run"] = outcome
    if structured:
        emit(payload, output)
        return
    typer.echo(f"合议完成：{outcome.get('elapsed')}s，报告 {report_dir}")
    _print_human(payload)


def register(debate_app: typer.Typer) -> None:
    """把五个命令挂到 `ldst debate` 子应用上（与 `cli/schedule.py:register` 同形）。"""
    debate_app.command("list", help="列出已保存的合议报告")(debate_list)
    debate_app.command("status", help="看能不能跑（CLI/配置/报告数）")(debate_status)
    debate_app.command("show", help="读某标的某天的报告并做质量门控")(debate_show)
    debate_app.command("last", help="读某标的最近一份报告")(debate_last)
    debate_app.command("run", help="真跑一次合议（分钟级、烧 token）")(debate_run)


__all__ = [
    "debate_last",
    "debate_list",
    "debate_run",
    "debate_show",
    "debate_status",
    "register",
]
