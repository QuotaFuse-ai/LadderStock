"""`ldst weekly` 命令体：周度事实包 + 五段 AI 叙述（R11，QUOT-149）。

实现与注册分离：`cli/main.py` 只 import + 一行 `register_weekly(app)`。

契约（docs/specs/SPEC-WEEKLY.md、docs/CLI.md §A′）：

| 形态 | 退出码 |
|---|---|
| `weekly --week 2026-W44`（有数据、AI 正常） | 0 |
| `weekly`（缺省本周） | 0 / 9 |
| `weekly --week 2026-w00` 等非法周标签 | 2（参数错误） |
| 本周没有任何可用单日指标（事实包 `available=false`） | 6 |
| 事实包有数但 AI 段落降级 / 失败 | 9（`EXIT_AI_DEGRADED`，与 `ldst review` 同口径） |

「6 优先于 9」：整周没数据时 AI 降级只是附带现象，脚本该看的是前者；两种情况
**产物都已落盘**（要求 4：降级不得整体失败，事实包 + 模板照写）。

只读本机缓存与本机产物，不发起任何网络请求（DEC-006）。
"""

from __future__ import annotations

from typing import Annotated

import typer

from ladderstock.cli.output import OutputOption, emit, error
from ladderstock.errors import EXIT_NOT_FOUND, EXIT_OK, EXIT_USAGE
from ladderstock.weekly import pipeline as weekly_pipeline
from ladderstock.weekly import week as week_module


def parse_week(value: str | None) -> str:
    """`--week` 接受 `YYYY-Www` / `YYYY-wWw` / 某个日期；非法直接 2。"""
    try:
        return week_module.normalize_week(value) if value else week_module.current_week()
    except ValueError as exc:
        error(str(exc))
        raise typer.Exit(EXIT_USAGE) from None


def weekly(
    week: Annotated[
        str | None, typer.Option("--week", help="ISO 周标签 YYYY-Www（缺省本周）")
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="已完成的段落也重新生成")
    ] = False,
    output: OutputOption = None,
) -> None:
    """生成本周（或指定周）的周度复盘：程序聚合事实包 + 五段 AI 叙述。"""
    tag = parse_week(week)

    report, run, pack = weekly_pipeline.generate(tag, force=force)
    exit_code = EXIT_NOT_FOUND if not pack.get("available") else run.exit_code
    emit(
        {
            "week": tag,
            "range": report["range"],
            "ok": bool(pack.get("available")) and not run.failed,
            "available": bool(pack.get("available")),
            "reason": pack.get("reason"),
            "configured": run.configured,
            "degraded": run.degraded,
            "failed": run.failed,
            "models": run.models,
            "session_days": pack.get("session_days") or [],
            "missing_days": [item["date"] for item in pack.get("missing_days") or []],
            "report_path": str(weekly_pipeline.weekly_path(tag)),
            "markdown_path": str(weekly_pipeline.markdown_path(tag)),
            "checkpoint": run.checkpoint_path,
            "sections": [
                {
                    "name": item.get("name"),
                    "status": item.get("status"),
                    "mode": item.get("mode"),
                    "model": item.get("model"),
                    "degraded": bool(item.get("degraded")),
                }
                for item in report.get("sections") or []
            ],
            "ai_failures": run.ai_failures,
            "headline": report.get("headline") or [],
            "exit_code": exit_code,
        },
        output,
    )
    if exit_code != EXIT_OK:
        if not pack.get("available"):
            error(f"{tag} 没有可用的周度指标：{pack.get('reason')}")
        raise typer.Exit(exit_code)


def register(app: typer.Typer) -> None:
    """把 `weekly` 挂到根命令上（main.py 只加一行注册）。"""
    app.command("weekly", help="周度复盘（事实包聚合 + 五段 AI 叙述）")(weekly)


__all__ = ["parse_week", "register", "weekly"]
