"""`ldst notify test`：通知链路冒烟（与 09:05 盘前提醒同一条链路）。

只调本机渠道（`watch/notifier.py` 的 console / desktop），不发网络请求、不写凭据。
失败语义：投递失败按 CLI.md 以退出码 4 结束（渠道不可用），但在调度与复盘里同一
个 notifier 只告警、不阻塞主流程。
"""

from __future__ import annotations

from typing import Annotated, NoReturn

import typer

from ladderstock.cli.output import OutputOption, emit, error
from ladderstock.errors import EXIT_OK, EXIT_SOURCE, EXIT_USAGE
from ladderstock.watch.notifier import (
    CHANNELS,
    UnknownNotifierChannel,
    normalize_channel,
    send,
)

DEFAULT_TITLE = "LadderStock 通知链路冒烟"
DEFAULT_MESSAGE = "这是一条测试通知：收到即说明该渠道可用。"


def _fail(message: str, code: int) -> NoReturn:
    error(message)
    raise typer.Exit(code)


def notify_test(
    channel: Annotated[
        str | None, typer.Option(help="通知渠道：console / desktop（缺省 console）")
    ] = None,
    title: Annotated[str, typer.Option(help="通知标题")] = DEFAULT_TITLE,
    message: Annotated[str, typer.Option("--message", help="通知正文")] = DEFAULT_MESSAGE,
    output: OutputOption = None,
) -> None:
    """向指定渠道投递一条测试通知，并报告投递结果。"""
    try:
        name = normalize_channel(channel)
    except UnknownNotifierChannel as exc:
        _fail(str(exc), EXIT_USAGE)

    result = send(name, title, message)
    if not result.ok:
        error(f"通知渠道 {name} 投递失败：{result.detail}")
        error("按降级处理：调度与复盘不会因此中断，换渠道或补后端后重试")
    emit(
        {
            "ok": result.ok,
            "channel": name,
            "available_channels": list(CHANNELS),
            "detail": result.detail,
            "degraded": result.degraded,
            "sent_at": result.sent_at,
            "title": title,
            "local_only": True,
        },
        output,
    )
    raise typer.Exit(EXIT_OK if result.ok else EXIT_SOURCE)


def register(notify_app: typer.Typer) -> None:
    """把 `notify` 子命令挂到 main.py 已建好的 Typer 组上。"""
    notify_app.command("test", help="通知链路冒烟（与 09:05 盘前提醒同链路）")(
        notify_test
    )


__all__ = ["DEFAULT_MESSAGE", "DEFAULT_TITLE", "notify_test", "register"]
