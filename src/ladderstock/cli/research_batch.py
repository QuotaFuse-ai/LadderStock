"""`ldst research-batch <codes...>`：批量研究队列（E 包 / DEC-091）。

为什么独立命令而不是 `research` 的子命令：`research` 是带必填位置参数的
顶层叶子（DEC-003 契约，子命令会撞「Missing argument CODE」）——批量形态
做在同命令上没法表达「多个代码 + 逐票续跑」。

口径（DEC-091 §5）：

- **串行**：一次一票。research 管线里有 DuckDB 读 + AI 调用，
  并发的失败面没人兜；串行 + 逐票 checkpoint 就是队列；
- 逐票**不中断**：单票失败（参数错除外）记入结果，下一票照跑；
  整体退出码 = 最差一票的（9 降级 > 8 部分 > 0）；
- 重复代码去重（保序）；上限 30 票/批（一条命令跑一晚上不是设计目标，
  大池子该用 `--from-pool`）；
- `--from-pool`：从 `sync.yaml` 的 `daily_k.codes` 取池（与采集池同源），
  与显式代码列表二选一。
"""

from __future__ import annotations

from typing import Annotated

import typer

from ladderstock.cli.output import OutputOption, emit, error
from ladderstock.errors import EXIT_OK, EXIT_PARTIAL, EXIT_USAGE
from ladderstock.research import checkpoint
from ladderstock.research.pipeline import ResearchPipeline

#: 单批上限：防止一条命令跑一整晚（大池子走 --from-pool 也受此限）
MAX_BATCH = 30

_WORST_ORDER = {0: 0, EXIT_PARTIAL: 8, 9: 9}


def _pool_codes() -> list[str]:
    """`sync.yaml` 的 `daily_k.codes`（与定时采集池同源；读不到报错不猜）。"""
    import yaml

    from ladderstock.cli.paths import config_dir

    path = config_dir() / "sync.yaml"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        error(f"读 sync.yaml 失败：{exc}")
        raise typer.Exit(EXIT_USAGE) from None
    block = None
    if isinstance(raw, dict):
        sync_block = raw.get("sync")
        if isinstance(sync_block, dict):
            block = sync_block.get("daily_k")
            if block is None:
                datasets = sync_block.get("datasets")
                if isinstance(datasets, dict):
                    block = datasets.get("daily_k")
        if block is None:
            block = raw.get("daily_k")
    codes = block.get("codes") if isinstance(block, dict) else None
    if not isinstance(codes, list):
        error("sync.yaml 里没有 daily_k.codes 池")
        raise typer.Exit(EXIT_USAGE)
    out: list[str] = []
    for code in codes:
        text = str(code).strip().upper()
        if text and text not in out:
            out.append(text)
    return out


def research_batch(
    codes: Annotated[
        list[str] | None,
        typer.Argument(help="证券代码（可多个，6 位数字；与 --from-pool 二选一）"),
    ] = None,
    from_pool: Annotated[
        bool, typer.Option("--from-pool", help="从 sync.yaml 的 daily_k.codes 取池")
    ] = False,
    date: Annotated[str | None, typer.Option("--date", help="研究日期（默认今天）")] = None,
    retry_failed: Annotated[
        bool, typer.Option("--retry-failed", help="每票只补失败 / 未跑过的段")
    ] = False,
    output: OutputOption = None,
) -> None:
    """批量跑个股研究：串行逐票、逐票 checkpoint、单票失败不拖垮队列。"""
    from datetime import UTC, datetime

    day = date or datetime.now(UTC).date().isoformat()
    if from_pool:
        if codes:
            error("代码列表与 --from-pool 二选一")
            raise typer.Exit(EXIT_USAGE)
        wanted = _pool_codes()
    else:
        wanted = []
        for code in codes or []:
            text = str(code).strip().upper()
            if text and text not in wanted:
                wanted.append(text)
    if not wanted:
        error("没有要研究的代码（给代码列表，或 --from-pool）")
        raise typer.Exit(EXIT_USAGE)
    if len(wanted) > MAX_BATCH:
        error(f"单批上限 {MAX_BATCH} 票（当前 {len(wanted)}）：分批跑或用调度")
        raise typer.Exit(EXIT_USAGE)

    from ladderstock.ai import fallback
    from ladderstock.core.boundary import BoundaryViolation

    try:
        config = fallback.load_config()
    except BoundaryViolation as exc:
        error(str(exc))
        raise typer.Exit(3) from None

    results: list[dict[str, object]] = []
    worst = 0
    for position, code in enumerate(wanted, start=1):
        try:
            safe_code = checkpoint.normalize_code(code)
        except ValueError as exc:
            results.append(
                {"code": code, "status": "invalid", "exit_code": EXIT_USAGE, "detail": str(exc)}
            )
            worst = max(worst, EXIT_USAGE)
            continue
        try:
            run = ResearchPipeline(safe_code, config=config).run(day, retry_failed=retry_failed)
            entry = run.as_dict()
            entry["status"] = "done" if run.exit_code == 0 else "degraded"
            entry["exit_code"] = run.exit_code
            worst = max(worst, _WORST_ORDER.get(run.exit_code, run.exit_code))
        except Exception as exc:  # noqa: BLE001 - 单票失败不拖垮队列
            entry = {"code": safe_code, "status": "failed", "exit_code": 1, "detail": str(exc)}
            worst = max(worst, 1)
        entry["position"] = position
        results.append(entry)

    payload = {
        "ok": worst == 0,
        "date": day,
        "total": len(results),
        "failed": sum(1 for row in results if str(row.get("status")) in ("failed", "invalid")),
        "results": results,
    }
    emit(payload, output)
    raise typer.Exit(EXIT_OK if worst == 0 else worst)


def register(app: typer.Typer) -> None:
    app.command("research-batch", help="批量个股研究（串行队列，逐票 checkpoint）")(research_batch)


__all__ = ["register", "research_batch"]
