"""`ldst research <code>`：个股六段研究管线（R1-1 骨架）。

命令形态固定为「代码 + 开关」（DEC-003 命名风格），参数语义与 `ldst ai retry` 一致：

- `--date`：研究日期 YYYY-MM-DD，默认今天；
- `--only <stage>`：只跑这一段，其余段状态一律不动（已完成的段不重跑，与
  `ldst ai retry --section` 同语义；要强制重跑加 `--force`）；
- `--retry-failed`：只补失败 / 未跑过的段，已完成段不重跑（不重复消耗额度）；
- `--output json`：稳定契约，键集合见 `research/pipeline.ResearchRunResult.as_dict`。

退出码：六段全成功 0；有段降级或失败 9（`EXIT_AI_DEGRADED`）；参数非法 2；
缺少 `config/ai.yaml` 3。

**契约提醒（给 R3 / R8）**：本命令是顶层叶子命令，`<code>` 是位置参数，
所以 `research` 不能再挂子命令——click 的组回调带必填位置参数时，
子命令路径会先撞上「Missing argument CODE」。报告审计请做成同一命令上的开关
（如 `ldst research <code> --only report --check`），不要写成 `ldst research report <code>`。
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated

import typer

from ladderstock.ai import fallback
from ladderstock.cli.output import OutputOption, emit, error
from ladderstock.core.boundary import BoundaryViolation
from ladderstock.errors import EXIT_CONFIG, EXIT_USAGE
from ladderstock.research import checkpoint
from ladderstock.research.pipeline import ResearchPipeline

DATE_HELP = "研究日期 YYYY-MM-DD（默认今天）"


def _parse_date(value: str | None) -> str:
    if value is None:
        return datetime.now(UTC).date().isoformat()
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        error(f"--date 需要 YYYY-MM-DD，收到 {value!r}")
        raise typer.Exit(EXIT_USAGE) from None
    # 回写成 ISO 长格式：`20261027` 这类紧凑写法能被 fromisoformat 接受，
    # 但日期是产物目录名与 `--output json` 的 `date` 值，必须只有一种写法
    return parsed.isoformat()


def research(
    code: Annotated[str, typer.Argument(help="证券代码（6 位数字，可带 .SH/.SZ/.BJ）")],
    date: Annotated[str | None, typer.Option("--date", help=DATE_HELP)] = None,
    only: Annotated[
        str | None,
        typer.Option("--only", help=f"只跑一段：{', '.join(checkpoint.STAGES)}"),
    ] = None,
    retry_failed: Annotated[
        bool, typer.Option("--retry-failed", help="只补失败 / 未跑过的段（成功段不重跑）")
    ] = False,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="重跑已选中的段（含已完成段）；配 --only 用即强制重跑那一段",
        ),
    ] = False,
    output: OutputOption = None,
) -> None:
    """按 company→financial→forecast→valuation→risk→report 顺序跑一次个股研究。"""
    day = _parse_date(date)
    try:
        safe_code = checkpoint.normalize_code(code)
    except ValueError as exc:
        error(str(exc))
        raise typer.Exit(EXIT_USAGE) from None

    try:
        config = fallback.load_config()
    except BoundaryViolation as exc:
        error(str(exc))
        raise typer.Exit(EXIT_CONFIG) from None

    try:
        run = ResearchPipeline(safe_code, config=config).run(
            day, only=only, retry_failed=retry_failed, force=force
        )
    except ValueError as exc:
        # `--only` 传了未知阶段（与 `ldst ai retry --section` 同一类用法错误）
        error(str(exc))
        raise typer.Exit(EXIT_USAGE) from None

    emit(run.as_dict(), output)
    raise typer.Exit(run.exit_code)


__all__ = ["research"]
