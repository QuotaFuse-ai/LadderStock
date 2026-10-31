"""`ldst news` / `ldst events`：资讯雷达的两个叶子命令（R6 / QUOT-144 要求 5）。

命令形态按 issue 原文固定：

- `ldst news <date> --output json`：一天的资讯流；`--until` 拉成区间（上限 31 天）；
- `ldst events --month YYYY-MM`：事件日历（财报披露 / 解禁 / 股东大会 / 政策窗口）。

两个都是**顶层叶子命令**（`<date>` 是位置参数，所以 `news` 下不再挂子命令，
与 `ldst research` 同一条 click 限制，见 `cli/research.py` 模块说明）。

参数口径：

- `--source` 只接受 `core.boundary.NEWS_STREAM_DATASETS` 里的栏目名，未知取值 exit 2；
- `--code` 可重复也可逗号分隔；巨潮 / 深交所 / 东财个股新闻三栏上游要求 code，
  不给就是 `skipped`（在 `sources_status` 里看得见），不是静默少一路；
- `--no-store` 只查不写；默认写本机 `data/news.sqlite`（DEC-006：只落本机）；
- `--ai` **默认关闭**：不加就绝不碰模型；加了也只给规则已抽出的事件补一句叙述。
- 日历不吃持仓 / 自选：要按代码过滤就显式 `--code`（隐私面不读 `journal.sqlite`）。

退出码：全成功 0；部分栏目失败 8；一个都没成 4；参数非法 2；
`--ai` 请求了但一条叙述都没成 9（`EXIT_AI_DEGRADED`）。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated

import typer

from ladderstock.cli.output import OutputOption, emit, error
from ladderstock.errors import (
    EXIT_AI_DEGRADED,
    EXIT_OK,
    EXIT_USAGE,
    LadderStockError,
)
from ladderstock.news import ai_enrich
from ladderstock.news import stream as news_stream
from ladderstock.news.calendar import build_calendar

DATE_HELP = "资讯日期 YYYY-MM-DD（默认今天）"
UNTIL_HELP = "区间右端 YYYY-MM-DD；给了它就从 <date> 连到这一天（≤31 天）"


def _parse_date(value: str, flag: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        error(f"{flag} 需要 YYYY-MM-DD，收到 {value!r}")
        raise typer.Exit(EXIT_USAGE) from None
    if parsed.isoformat() != value:
        # 与 ldst research 同口径：日期会进窗口键与产物名，只允许一种写法
        error(f"{flag} 需要 YYYY-MM-DD，收到 {value!r}")
        raise typer.Exit(EXIT_USAGE)
    return parsed


def news_command(
    date: Annotated[  # noqa: A002 - 位置参数名就是契约里的 <date>
        str | None, typer.Argument(help=DATE_HELP)
    ] = None,
    until: Annotated[str | None, typer.Option("--until", help=UNTIL_HELP)] = None,
    source: Annotated[
        list[str] | None,
        typer.Option("--source", help="只取指定栏目（可重复 / 逗号分隔）"),
    ] = None,
    code: Annotated[
        list[str] | None,
        typer.Option("--code", help="证券代码（可重复 / 逗号分隔）；公告类栏目必填"),
    ] = None,
    limit: Annotated[
        int, typer.Option("--limit", help=f"每栏上限（默认 {news_stream.DEFAULT_LIMIT}）")
    ] = news_stream.DEFAULT_LIMIT,
    store: Annotated[
        bool,
        typer.Option(
            "--store/--no-store",
            help="默认写入本机 data/news.sqlite；--no-store 只查询不写库",
        ),
    ] = True,
    ai: Annotated[
        bool, typer.Option("--ai", help="可选：给抽出的事件补一句模型叙述（默认关闭）")
    ] = False,
    output: OutputOption = None,
) -> None:
    """聚合公告 / 研报 / 快讯 / 政策，规则抽取事件，落本机库。"""
    start = _parse_date(date or datetime.now().astimezone().date().isoformat(), "<date>")
    end = _parse_date(until, "--until") if until else start
    try:
        result = news_stream.collect_stream(
            start=start,
            end=end,
            datasets=list(source or []),
            codes=list(code or []),
            persist=store,
            limit=limit,
        )
    except LadderStockError as exc:
        error(str(exc))
        raise typer.Exit(exc.code) from None

    payload = result.as_dict()
    payload["ai"] = ai_enrich.apply_to_stream(result, enabled=ai)
    if payload["ai"]["requested"] and result.event_objects and not payload["ai"]["enriched"]:
        result.exit_code = EXIT_AI_DEGRADED if result.exit_code == EXIT_OK else result.exit_code

    emit(payload, output)
    raise typer.Exit(result.exit_code)


def events_command(
    month: Annotated[
        str,
        typer.Option(
            "--month",
            help="日历月份 YYYY-MM（必填：日历不默认「当月」，避免误当今日视图）",
        ),
    ],
    code: Annotated[
        list[str] | None,
        typer.Option("--code", help="按代码过滤（可重复 / 逗号分隔）；解禁栏目必需"),
    ] = None,
    limit: Annotated[
        int, typer.Option("--limit", help="每节条数上限（默认 200）")
    ] = 200,
    fetch: Annotated[
        bool,
        typer.Option(
            "--fetch/--no-fetch",
            help="默认向 peer 取数；--no-fetch 只读本机已入库的事件",
        ),
    ] = True,
    output: OutputOption = None,
) -> None:
    """事件日历：财报披露日 / 解禁日 / 股东大会 / 政策发布窗口。"""
    try:
        result = build_calendar(
            month, codes=list(code or []), fetch=fetch, limit=limit
        )
    except LadderStockError as exc:
        error(str(exc))
        raise typer.Exit(exc.code) from None

    emit(result.as_dict(), output)
    raise typer.Exit(result.exit_code)


__all__ = ["events_command", "news_command"]
