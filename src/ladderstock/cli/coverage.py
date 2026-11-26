"""`ldst coverage`：F12 数据集看板 + 受控补采的命令行入口（register 模式）。

命令同样分「只读」和「会发请求」两组：

- `board`：62 个数据集逐个列出状态 / 行数 / 最新 / 池内覆盖 / 缺口原因；
- `gaps`：只看需要处理的，按严重度排序；
- `plan DATASET`：这批补采会请求什么（发请求前先看清楚），**不发请求**；
- `backfill DATASET`：默认同样只出计划；`--execute` 才真跑，且单数据集单批 ≤100 只。

`--execute` 会走 `cli/sync.py` 的真实通道（provider_registry 限流 / 水位 / 写锁都在那层），
所以它不是一个「绕过门禁的快捷方式」，而是把现有的增量同步按数据集切了一刀。
"""

from __future__ import annotations

from typing import Annotated, Any

import typer

from ladderstock.cli.output import OutputOption, emit, resolve_format
from ladderstock.data import coverage_board as cb


def _fmt_coverage(row: dict[str, Any]) -> str:
    if not row.get("pool_relevant"):
        return "—"
    if row.get("coverage") is None:
        return "—"
    return f"{row['pool_covered']}/{row['pool_codes']}（{row['coverage'] * 100:.1f}%）"


def _print_row(row: dict[str, Any]) -> None:
    label = cb.STATE_LABELS.get(row["state"], row["state"])
    typer.echo(
        f"  {row['dataset']:<24} {label:<8} 行 {row['rows']:<8}"
        f" 最新 {str(row['latest'] or '-'):<11} 池内 {_fmt_coverage(row)}"
    )
    if row.get("reason"):
        typer.echo(f"      └ {row['reason']}")


def coverage_board(
    dataset: Annotated[
        list[str] | None, typer.Option("--dataset", "-d", help="只查指定数据集（可重复）")
    ] = None,
    only_gaps: Annotated[bool, typer.Option("--gaps", help="只列需要处理的")] = False,
    output: OutputOption = None,
) -> None:
    """62 个数据集各自的落盘情况（只读本地库，不发请求）。"""
    payload = cb.board(datasets=list(dataset) if dataset else None)
    if resolve_format(output) != "table":
        emit(payload, output)
        return
    summary = payload["summary"]
    counts = summary["counts"]
    typer.echo(
        f"代码池 {payload['pool']['size']} 只 · sync.yaml 声明 {payload['declared']} 个"
        f" · 目录 {summary['total']} 个 · 耗时 {payload['elapsed_ms']} ms"
    )
    typer.echo("  ".join(f"{cb.STATE_LABELS[k]} {v}" for k, v in counts.items() if v))
    rows = cb.gaps(payload) if only_gaps else payload["datasets"]
    if not rows:
        typer.echo("没有缺口。")
        return
    for row in rows:
        _print_row(row)


def coverage_gaps(
    output: OutputOption = None,
) -> None:
    """只看需要处理的数据集（按严重度排序）。"""
    payload = cb.board()
    rows = cb.gaps(payload)
    if resolve_format(output) != "table":
        emit({"generated_at": payload["generated_at"], "gaps": rows}, output)
        return
    if not rows:
        typer.echo("没有缺口。")
        return
    typer.echo(f"{len(rows)} 个数据集需要处理（共 {payload['summary']['total']} 个）：")
    for row in rows:
        _print_row(row)


def coverage_plan(
    dataset: Annotated[str, typer.Argument(help="数据集名（DATASET_CATALOG 里的键）")],
    codes: Annotated[
        str | None, typer.Option("--codes", help="逗号分隔的代码，缺省用代码池")
    ] = None,
    max_codes: Annotated[int, typer.Option("--max-codes", help="单批上限")] = cb.MAX_BATCH_CODES,
    output: OutputOption = None,
) -> None:
    """这批补采会请求什么（不发请求）。"""
    code_list = [c.strip() for c in codes.split(",") if c.strip()] if codes else None
    try:
        prepared = cb.plan(dataset, codes=code_list, max_codes=max_codes)
    except ValueError as exc:
        typer.echo(f"补不了：{exc}")
        raise typer.Exit(code=2) from None
    if resolve_format(output) != "table":
        emit(prepared, output)
        return
    typer.echo(f"{prepared['dataset']} · 主源 {prepared['primary']}")
    if prepared["fallback"]:
        typer.echo(f"  备源：{', '.join(prepared['fallback'])}")
    typer.echo(f"  {prepared['requests_hint']}")
    if prepared["per_code"]:
        shown = ", ".join(prepared["codes"][:12])
        more = "" if len(prepared["codes"]) <= 12 else f" …（共 {prepared['code_count']} 只）"
        typer.echo(f"  代码：{shown}{more}")


def coverage_backfill(
    dataset: Annotated[str, typer.Argument(help="数据集名")],
    codes: Annotated[
        str | None, typer.Option("--codes", help="逗号分隔的代码，缺省用代码池")
    ] = None,
    max_codes: Annotated[int, typer.Option("--max-codes", help="单批上限")] = cb.MAX_BATCH_CODES,
    execute: Annotated[bool, typer.Option("--execute", help="真跑（默认只出计划）")] = False,
    output: OutputOption = None,
) -> None:
    """受控补采：默认只出计划；`--execute` 才真跑一次（单数据集单批）。"""
    code_list = [c.strip() for c in codes.split(",") if c.strip()] if codes else None
    try:
        result = cb.backfill(dataset, codes=code_list, execute=execute, max_codes=max_codes)
    except ValueError as exc:
        typer.echo(f"补不了：{exc}")
        raise typer.Exit(code=2) from None
    if resolve_format(output) != "table":
        emit(result, output)
        if result.get("status") in {"failed", "lost"}:
            raise typer.Exit(code=1)
        return
    typer.echo(f"{result['dataset']} · 主源 {result['primary']} · {result['status']}")
    if not execute:
        typer.echo(f"  {result['hint']}")
        typer.echo(f"  {result['requests_hint']}")
        return
    typer.echo(f"  {result.get('detail') or '（无 detail）'}")
    typer.echo(
        f"  行数 {result.get('rows_stored')} · 表内合计 {result.get('total_rows')}"
        f" · 耗时 {result.get('elapsed_ms')} ms"
    )
    if result.get("status") in {"failed", "lost"}:
        raise typer.Exit(code=1)


def register(coverage_app: typer.Typer) -> None:
    """把四个命令挂到 `ldst coverage` 子应用上。"""
    coverage_app.command("board", help="62 个数据集的落盘情况（只读）")(coverage_board)
    coverage_app.command("gaps", help="只看缺口（按严重度排序）")(coverage_gaps)
    coverage_app.command("plan", help="补采计划（不发请求）")(coverage_plan)
    coverage_app.command("backfill", help="受控补采（默认 dry-run）")(coverage_backfill)


__all__ = [
    "coverage_backfill",
    "coverage_board",
    "coverage_gaps",
    "coverage_plan",
    "register",
]
