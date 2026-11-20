"""`ldst knowledge`：把公告正文投影进 LS 的 qdrant 知识库（形态 2）。

为什么单列这个命令：形态 2 下公告语义检索走 LS 自己的 `ladderstock_knowledge`，
FC 只留 ODS/DWD/DWS 结构化事实。投影这一步必须能被脚本和模块监控调度，
不能只活在手边的临时脚本里。

三条口径：

1. **幂等重跑覆盖**：`sync_symbol` 先按 `source=announcement,+symbol` 删旧点再写，
   point id 由 `(market, symbol, ann_date, section, 序)` 决定，重跑不堆垃圾。
2. **按指纹判要不要跑**：`fingerprint()` 看行数 / 最大 id / 字符总和，三个都没变
   就跳过——稳定态不白跑 embedding、不白写 qdrant。
3. **缺依赖如实报**：FC 的 PG 连不上、qdrant 不在、模型缺失，一律 `AnnouncementsUnavailable`
   非零退出，不假装成功。
"""

from __future__ import annotations

from typing import Annotated, Any

import typer

from ladderstock.cli.output import OutputOption, emit, error
from ladderstock.errors import EXIT_CONFIG, EXIT_OK, EXIT_PARTIAL

knowledge_app = typer.Typer(help="知识库（qdrant 投影与检索）")


def _sync_one(symbol: str, limit: int) -> dict[str, Any]:
    from ladderstock.knowledge import announcements

    try:
        info = announcements.sync_symbol(symbol, limit=limit)
    except announcements.AnnouncementsUnavailable as exc:
        return {"symbol": symbol, "ok": False, "detail": str(exc)}
    return {"symbol": symbol, "ok": True, **info.as_dict()}


@knowledge_app.command("sync-announcements", help="把 dwd 公告正文投影进 qdrant（幂等重跑覆盖）")
def sync_announcements(
    symbol: Annotated[
        list[str] | None,
        typer.Option("--symbol", help="只投这些标的（可重复）；缺省=库里全部带正文的标的"),
    ] = None,
    limit: Annotated[int, typer.Option("--limit", help="单标的取多少条公告")] = 200,
    output: OutputOption = None,
) -> None:
    """投影公告正文到 qdrant；返回每个标的的公告数/切片数/删旧点数。"""
    from ladderstock.knowledge import announcements

    try:
        targets = [str(item).strip() for item in (symbol or []) if str(item).strip()]
        if not targets:
            targets = announcements.symbols()
    except announcements.AnnouncementsUnavailable as exc:
        error(str(exc))
        raise typer.Exit(EXIT_CONFIG) from None
    if not targets:
        emit({"ok": True, "count": 0, "detail": "dwd 里还没有带正文的公告"}, output)
        raise typer.Exit(EXIT_OK)

    results = [_sync_one(target, limit) for target in targets]
    payload: dict[str, Any] = {
        "ok": all(item.get("ok") for item in results),
        "count": len(results),
        "announcements": sum(int(item.get("announcements") or 0) for item in results),
        "chunks": sum(int(item.get("chunks") or 0) for item in results),
    }
    payload["symbols"] = results
    try:
        payload["available"] = announcements.available()
    except announcements.AnnouncementsUnavailable as exc:
        payload["available"] = {"ok": False, "error": str(exc)}
    emit(payload, output)
    raise typer.Exit(EXIT_OK if payload["ok"] else EXIT_PARTIAL)


@knowledge_app.command("status", help="投影现状：qdrant 点数 vs dwd 行数")
def status(output: OutputOption = None) -> None:
    from ladderstock.knowledge import announcements

    try:
        payload = announcements.available()
    except announcements.AnnouncementsUnavailable as exc:
        error(str(exc))
        raise typer.Exit(EXIT_CONFIG) from None
    emit(payload, output)
    raise typer.Exit(EXIT_OK if payload.get("ok") else EXIT_PARTIAL)


def register(app: typer.Typer) -> None:
    app.add_typer(knowledge_app, name="knowledge")


__all__ = ["knowledge_app", "register"]
