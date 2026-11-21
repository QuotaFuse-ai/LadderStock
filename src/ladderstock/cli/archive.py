"""`ldst archive`：F8 研究档案的检索与导出入口。

四个动作都只读本地 `data/research/` 与 `data/research_archive/`，不发外部请求：

- `list`：全量档案表（每份体检成一行：跑了几段 / 缺段 / 降级 / 未核实）；
- `show`：某标的某天（或最近）的档案明细；
- `export`：把六段 md 合并成一份可交付的完整报告；
- `search`：按代码 / 日期 / 关键词找档案，命中回原文片段与所在段。

实现体在 `research/archive.py`；索引文件只是缓存，`list/show/search` 一律现扫现算，
避免"索引写了、产物没写"时读出一张过期的表。
"""

from __future__ import annotations

from typing import Annotated, Any

import typer

from ladderstock.cli.output import OutputOption, emit, resolve_format
from ladderstock.research import archive


def _summary(item: dict[str, Any]) -> str:
    parts = [f"{item['stage_count']}/{item['total_stages']} 段"]
    if item["complete"]:
        parts.append("齐全")
    else:
        parts.append("缺 " + "、".join(item["missing"]))
    if item["degraded"]:
        parts.append("降级 " + str(len(item["degraded"])))
    if item["unverified"]:
        parts.append("未核实 " + str(len(item["unverified"])))
    if item["failed"]:
        parts.append("失败 " + "、".join(item["failed"]))
    return " · ".join(parts)


def archive_list(
    limit: Annotated[int, typer.Option("--limit", "-n", help="最多列几条")] = 30,
    output: OutputOption = None,
) -> None:
    """列出全部研究档案（每份一行体检）。"""
    items = archive.scan()[: max(1, limit)]
    if resolve_format(output) != "table":
        emit({"count": len(items), "items": items}, output)
        return
    if not items:
        typer.echo("还没有研究档案（`ldst research <code>` 跑完一轮才会出现）。")
        return
    total = len(archive.scan())
    typer.echo(f"共 {total} 份档案（显示 {len(items)}）")
    for item in items:
        typer.echo(f"{item['date']}  {item['code']}  {_summary(item)}  {item['updated_at']}")


def archive_show(
    code: Annotated[str, typer.Argument(help="标的代码")],
    day: Annotated[str | None, typer.Option("--date", "-d", help="YYYY-MM-DD，缺省取最近")] = None,
    output: OutputOption = None,
) -> None:
    """看某标的某天（或最近）档案的逐段状态。"""
    item = archive.find(code, day)
    if item is None:
        typer.echo(f"没有 {code} 在 {day or '任意日期'} 的研究档案")
        raise typer.Exit(code=1)
    if resolve_format(output) != "table":
        emit(item, output)
        return
    typer.echo(f"{item['code']} · {item['date']} · {_summary(item)}")
    for row in item["stages"]:
        marks = [
            "json" if row["has_json"] else "无json",
            "md" if row["has_md"] else "无md",
        ]
        if row["degraded"]:
            marks.append("降级")
        if row["unverified"]:
            marks.append("未核实")
        if row["corrupt"]:
            marks.append("损坏")
        if row["failure"]:
            marks.append(f"失败:{row['failure']}")
        typer.echo(
            f"  {row['stage']:<12} {row['title']:<10} 状态={row['status'] or '-'} "
            f"来源={row['sources']}  [{'/'.join(marks)}]  {row['updated_at'] or '-'}"
        )


def archive_export(
    code: Annotated[str, typer.Argument(help="标的代码")],
    day: Annotated[str | None, typer.Option("--date", "-d", help="YYYY-MM-DD，缺省取最近")] = None,
    out: Annotated[
        str | None,
        typer.Option(
            "--out", "-o", help="输出路径（默认 data/research_archive/<day>/<code>.<格式>）"
        ),
    ] = None,
    fmt: Annotated[
        str,
        typer.Option(
            "--format", "-f", help="导出格式：md（可读交付件）/ json（结构化，给程序消费）"
        ),
    ] = "md",
    output: OutputOption = None,
) -> None:
    """导出一份档案：Markdown（封面体检 + 逐段正文 + 依据链）或结构化 JSON。"""
    result = archive.export(code, day, out=out, fmt=fmt)
    structured = resolve_format(output) != "table"
    if structured:
        emit(result, output)
    if not result.get("ok"):
        typer.echo(f"导出失败：{result.get('reason')}")
        raise typer.Exit(code=1)
    if not structured:
        missing = "、".join(result["missing"])
        completeness = "完整" if result["complete"] else f"缺 {missing}"
        typer.echo(
            f"已导出 {result['code']} · {result['date']} → {result['path']}"
            f"（{result['bytes']} 字节，{completeness}）"
        )
    # 索引顺手刷新一次：导出过的档案一定是"已经人读过"的那份
    archive.write_index()


def archive_search(
    keyword: Annotated[str, typer.Argument(help="关键词：代码 / 日期 / 正文片段")],
    limit: Annotated[int, typer.Option("--limit", "-n", help="最多几条")] = 20,
    output: OutputOption = None,
) -> None:
    """在档案里按代码/日期/正文找（返回命中片段与所在段）。"""
    hits = archive.search(keyword, limit=max(1, limit))
    if resolve_format(output) != "table":
        emit({"keyword": keyword, "count": len(hits), "hits": hits}, output)
        return
    if not hits:
        typer.echo(f"没找到含「{keyword}」的档案")
        return
    for hit in hits:
        typer.echo(f"{hit['date']}  {hit['code']}  ({hit['matched_by']})")
        for match in hit.get("matches") or []:
            typer.echo(f"  [{match['stage_title']}] {match['text']}")


def register(archive_app: typer.Typer) -> None:
    archive_app.command("list", help="列出研究档案（每份一行体检）")(archive_list)
    archive_app.command("show", help="看某标的某天的档案明细")(archive_show)
    archive_app.command("export", help="导出档案（md 可读件 / json 结构化）")(archive_export)
    archive_app.command("search", help="按代码/日期/正文检索档案")(archive_search)


__all__ = [
    "archive_export",
    "archive_list",
    "archive_search",
    "archive_show",
    "register",
]
