"""``ldst backtest`` 命令体：A 股日频策略回测 M1（QUOT-500）。

实现与注册分离：``cli/main.py`` 只 import + 一行 ``register_backtest(backtest_app)``。

命令：

- ``backtest run``：跑一次回测，落盘「运行卡」（JSON / Markdown / 自包含 HTML 三份），
  stdout 默认给摘要（run_id、数据指纹、校验门结论、产物路径），``--output json`` 给全卡；
- ``backtest list``：列出本机已落盘的运行卡摘要（只读目录，不重算任何指标）；
- ``backtest show``：打印一张已落盘运行卡（``--output json`` 给全卡，table 给摘要；
  人读版 Markdown 见产物文件 ``<data_dir>/backtests/<run_id>/<run_id>.md``）。

退出码：

- ``0``：跑完且校验门**通过**；
- ``8``（``EXIT_PARTIAL``）：跑完但校验门**未通过**——产物照样落盘、卡片照样标红，
  脚本侧据此分流（这是「结果已出、结论不合格」，不是错误）；
- ``2``：命令参数非法（策略名未知、日期格式错、标的超出 M1 标的域）；
- ``3``：配置非法（负数、滑点/佣金/印花税为 0、切分比例越界、档位无序、yaml 缺字段）；
- ``4``：本机数据缺失（没有历史库 / 该区间没行 / 复权口径不支持）；
- ``6``：``list`` / ``show`` 找不到运行卡。
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

import typer

from ladderstock.backtest.config import BacktestConfigError
from ladderstock.backtest.feed import FeedError
from ladderstock.backtest.runner import (
    DEFAULT_INITIAL_CASH,
    BacktestError,
    artifacts_root,
    list_runs,
    load_run_card,
    run_backtest,
)
from ladderstock.backtest.strategy import BUILTIN_NAMES, StrategyError
from ladderstock.backtest.universe import UniverseError
from ladderstock.cli.output import OutputOption, emit, error, resolve_format
from ladderstock.cli.paths import config_dir
from ladderstock.errors import (
    EXIT_CONFIG,
    EXIT_NOT_FOUND,
    EXIT_OK,
    EXIT_PARTIAL,
    EXIT_SOURCE,
    EXIT_USAGE,
)

_EXIT_BY_ERROR: tuple[tuple[type[BaseException], int], ...] = (
    (BacktestConfigError, EXIT_CONFIG),
    (FeedError, EXIT_SOURCE),
    (UniverseError, EXIT_USAGE),
    (StrategyError, EXIT_USAGE),
    (BacktestError, EXIT_USAGE),
)


def _exit_code_for(exc: BaseException) -> int:
    for cls, code in _EXIT_BY_ERROR:
        if isinstance(exc, cls):
            return code
    return EXIT_USAGE


def _parse_tiers(raw: str) -> tuple[float, ...]:
    parts = [chunk.strip() for chunk in raw.split(",") if chunk.strip()]
    if len(parts) < 2:
        raise BacktestError(f"--cost-tiers 至少 2 档、逗号分隔，收到 {raw!r}")
    try:
        return tuple(float(chunk) for chunk in parts)
    except ValueError as exc:
        raise BacktestError(f"--cost-tiers 无法解析为数值列表：{raw!r}") from exc


def _resolve_config_path(explicit: str | None) -> Path | None:
    """``--config`` 显式给了就当用户配置（读不到 = 错误）；没给则找配置目录。

    配置目录里没有 ``backtest.yaml`` 时返回 ``None``，交给 ``load_config`` 走兜底并把
    ``sources`` 标 ``fallback``（运行卡里看得到）；配置目录里**有**文件却写坏了，
    由 ``load_config`` 报错，不静默回落。
    """
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise BacktestConfigError(f"--config 指定的文件不存在：{path}")
        return path
    candidate = config_dir() / "backtest.yaml"
    return candidate if candidate.is_file() else None


def backtest_run(
    strategy: Annotated[
        str, typer.Option("--strategy", help=f"内置策略：{' / '.join(BUILTIN_NAMES)}")
    ] = "ma_cross",
    symbol: Annotated[
        str, typer.Option("--symbol", help="6 位 A 股代码（M1 只支持主板/创业板普通股）")
    ] = "600000",
    date_from: Annotated[
        str, typer.Option("--from", help="起始日 YYYY-MM-DD（含）")
    ] = "2024-01-01",
    date_to: Annotated[
        str | None, typer.Option("--to", help="结束日 YYYY-MM-DD（含）；缺省取本机最新一根")
    ] = None,
    commission: Annotated[
        float | None, typer.Option("--commission", help="佣金率（万 2.5 = 0.00025，双边）")
    ] = None,
    slippage: Annotated[
        float | None, typer.Option("--slippage", help="滑点率（千 1 = 0.001，不得为 0）")
    ] = None,
    stamp_duty: Annotated[
        float | None, typer.Option("--stamp-duty", help="卖出印花税（千 1 = 0.001，不得为 0）")
    ] = None,
    min_trades: Annotated[
        int | None, typer.Option("--min-trades", help="校验门最小完整交易次数")
    ] = None,
    in_sample_ratio: Annotated[
        float | None, typer.Option("--in-sample-ratio", help="样本内占比（0~1 开区间，按时间顺序）")
    ] = None,
    cost_tiers: Annotated[
        str | None,
        typer.Option(
            "--cost-tiers", help="成本敏感性佣金率档位，逗号分隔且升序（如 0.0002,0.0005,0.001）"
        ),
    ] = None,
    initial_cash: Annotated[
        float, typer.Option("--initial-cash", help="初始资金（元）")
    ] = DEFAULT_INITIAL_CASH,
    config: Annotated[
        str | None,
        typer.Option("--config", help="配置文件路径（缺省用配置目录里的 backtest.yaml）"),
    ] = None,
    short_window: Annotated[
        int | None, typer.Option("--short-window", help="ma_cross 短均线窗口")
    ] = None,
    long_window: Annotated[
        int | None, typer.Option("--long-window", help="ma_cross 长均线窗口")
    ] = None,
    lookback: Annotated[
        int | None, typer.Option("--lookback", help="momentum / breakout 回看窗口")
    ] = None,
    threshold: Annotated[
        float | None, typer.Option("--threshold", help="momentum 触发阈值（小数）")
    ] = None,
    output: OutputOption = None,
) -> None:
    """跑一次回测：撮合 → 计分 → 校验门 → 运行卡（JSON/MD/HTML 落盘）。"""
    strategy_kwargs: dict[str, Any] = {}
    for key, value in (
        ("short_window", short_window),
        ("long_window", long_window),
        ("lookback", lookback),
        ("threshold", threshold),
    ):
        if value is not None:
            strategy_kwargs[key] = value
    overrides: dict[str, Any] = {}
    for key, value in (
        ("commission", commission),
        ("slippage", slippage),
        ("stamp_duty", stamp_duty),
        ("min_trades", min_trades),
        ("in_sample_ratio", in_sample_ratio),
    ):
        if value is not None:
            overrides[key] = value

    try:
        if cost_tiers is not None:
            overrides["cost_tiers"] = _parse_tiers(cost_tiers)
        config_path = _resolve_config_path(config)
        result = run_backtest(
            strategy=strategy,
            symbol=symbol,
            start=date_from,
            # 没给 --to 时交给 feed 取到最新一根；日期上界用一个足够远的哨兵
            end=date_to or "2999-12-31",
            strategy_kwargs=strategy_kwargs,
            config_path=config_path,
            overrides=overrides,
            initial_cash=initial_cash,
        )
    except (BacktestError, BacktestConfigError, FeedError, UniverseError, StrategyError) as exc:
        error(str(exc))
        raise typer.Exit(_exit_code_for(exc)) from exc

    fmt = resolve_format(output)
    if fmt == "table":
        gate = result.card["gate"]
        emit(
            {
                "run_id": result.run_id,
                "symbol": result.card["symbol"],
                "strategy": result.card["strategy"],
                "period": result.card["period"],
                "data_fingerprint": result.card["data"]["fingerprint"],
                "gate_passed": gate["passed"],
                "gate_status": gate["status"],
                "red_reasons": gate["red_reasons"],
                "warnings": gate["warnings"],
                "artifacts_dir": str(result.artifacts_dir),
                "json": str(result.json_path),
                "markdown": str(result.markdown_path),
                "html": str(result.html_path),
            },
            fmt,
        )
    else:
        emit(result.card, output)

    if not result.card["gate"]["passed"]:
        error("校验门未通过（运行卡已标红）：" + "；".join(result.card["gate"]["red_reasons"]))
        raise typer.Exit(EXIT_PARTIAL)
    raise typer.Exit(EXIT_OK)


def backtest_list(
    limit: Annotated[int, typer.Option("--limit", help="最多列多少条（新的在前）")] = 20,
    output: OutputOption = None,
) -> None:
    """列出本机已落盘的运行卡摘要（只读目录）。"""
    runs = list_runs(limit=limit)
    emit({"root": str(artifacts_root()), "count": len(runs), "runs": runs}, output)
    raise typer.Exit(EXIT_OK)


def backtest_show(
    run_id: Annotated[str, typer.Argument(help="运行卡 run_id（见 `ldst backtest list`）")],
    output: OutputOption = None,
) -> None:
    """打印一张已落盘的运行卡（JSON/table；Markdown 产物文件另存）。"""
    try:
        card = load_run_card(run_id)
    except FileNotFoundError as exc:
        error(str(exc))
        raise typer.Exit(EXIT_NOT_FOUND) from exc
    except BacktestError as exc:
        error(str(exc))
        raise typer.Exit(EXIT_USAGE) from exc
    # `--output markdown` 不在 OutputOption 的三档里：Markdown 版运行卡直接读产物
    # `<data_dir>/backtests/<run_id>/<run_id>.md`，CLI 这里只做 JSON/table/csv。
    emit(card, output)
    raise typer.Exit(EXIT_OK)


def register(app: typer.Typer) -> None:
    """把 ``backtest run|list|show`` 挂到给定的 typer 组上。"""
    app.command("run", help="跑一次回测并落盘运行卡（JSON / Markdown / HTML）")(backtest_run)
    app.command("list", help="列出已落盘的运行卡摘要")(backtest_list)
    app.command("show", help="打印一张运行卡（Markdown 见产物文件）")(backtest_show)


__all__ = [
    "backtest_list",
    "backtest_run",
    "backtest_show",
    "register",
]
