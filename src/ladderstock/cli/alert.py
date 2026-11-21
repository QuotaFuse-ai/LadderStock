"""`ldst alert`：提醒规则的命令面（规则 / 渠道 / 手动跑一轮）。

与 Web `/api/alert/*` 同一套 store 与执行体，这里只是命令行形态；
`schedule.yaml` 里 `kind: alert_check` 的任务走同一个 `run_once`。

退出码沿用全仓口径：0 成功 / 2 用法 / 6 对象不存在 / 4 渠道投递失败
（渠道测试的语义与 `notify test` 一致：测试就是真发一条，失败要看得见）。
"""

from __future__ import annotations

import json
from typing import Annotated, NoReturn

import typer

from ladderstock.alert import run as alert_run
from ladderstock.alert import store
from ladderstock.alert.channels import (
    CHANNEL_KINDS,
    DEFAULT_SMTP_PASSWORD_ENV,
    deliver,
    public_config,
)
from ladderstock.alert.rules import KIND_LABELS, RULE_KINDS
from ladderstock.cli.output import OutputOption, emit, error
from ladderstock.errors import (
    EXIT_OK,
    EXIT_SOURCE,
    EXIT_USAGE,
    LadderStockError,
)

rules_app = typer.Typer(no_args_is_help=True, help="提醒规则（价格 / 均线 / 放量 / 浮盈亏）")
channels_app = typer.Typer(
    no_args_is_help=True, help="出站渠道（邮件 / webhook / 钉钉 / 飞书 / 企微）"
)


def _fail(message: str, code: int) -> NoReturn:
    error(message)
    raise typer.Exit(code)


def _catch(exc: LadderStockError) -> NoReturn:
    _fail(exc.message, exc.code)


def kinds_help() -> str:
    return " / ".join(RULE_KINDS)


# --------------------------------------------------------------------------- #
# rules
# --------------------------------------------------------------------------- #


@rules_app.command("add", help="加一条提醒规则（kind：" + " / ".join(RULE_KINDS) + "）")
def rules_add(
    kind: Annotated[str, typer.Option(help="规则类型，见命令说明")],
    symbol: Annotated[str, typer.Option(help="6 位 A 股代码")],
    threshold: Annotated[float, typer.Option(help="阈值：价格(元)/倍数/百分比；均线类忽略")],
    window: Annotated[
        int | None, typer.Option(help="窗口：均线天数(默认20) / 均量天数(默认5)")
    ] = None,
    cooldown_min: Annotated[int, typer.Option(help="冷却分钟：同一触发在该时间内不重报")] = 60,
    channels: Annotated[
        list[str] | None, typer.Option(help="命中后要投递的渠道 id（可重复）")
    ] = None,
    note: Annotated[str, typer.Option(help="备注，随提醒正文带出")] = "",
    output: OutputOption = None,
) -> None:
    try:
        rule = store.add_rule(
            kind,
            symbol,
            threshold,
            window=window,
            cooldown_min=cooldown_min,
            channels=list(channels or []),
            note=note,
        )
    except LadderStockError as exc:
        _catch(exc)
    emit({"ok": True, "rule": rule}, output)


@rules_app.command("list", help="列出全部规则（--enabled 只看启用的）")
def rules_list(
    enabled: Annotated[bool, typer.Option("--enabled", help="只列启用的规则")] = False,
    output: OutputOption = None,
) -> None:
    items = store.list_rules(enabled_only=enabled)
    emit({"ok": True, "total": len(items), "rules": items}, output)


@rules_app.command("show", help="看单条规则 + 当前运行态")
def rules_show(
    rule_id: Annotated[str, typer.Argument(help="规则 id")],
    output: OutputOption = None,
) -> None:
    try:
        rule = store.get_rule(rule_id)
    except LadderStockError as exc:
        _catch(exc)
    state = store.get_state(rule_id)
    emit({"ok": True, "rule": rule, "state": state}, output)


@rules_app.command("rm", help="删除一条规则（连同运行态）")
def rules_rm(
    rule_id: Annotated[str, typer.Argument(help="规则 id")],
    output: OutputOption = None,
) -> None:
    try:
        payload = store.remove_rule(rule_id)
    except LadderStockError as exc:
        _catch(exc)
    emit({"ok": True, **payload}, output)


@rules_app.command("toggle", help="启 / 停一条规则")
def rules_toggle(
    rule_id: Annotated[str, typer.Argument(help="规则 id")],
    off: Annotated[bool, typer.Option("--off", help="停用（缺省为启用）")] = False,
    output: OutputOption = None,
) -> None:
    try:
        rule = store.toggle_rule(rule_id, not off)
    except LadderStockError as exc:
        _catch(exc)
    emit({"ok": True, "rule": rule}, output)


@rules_app.command("update", help="改规则的阈值 / 窗口 / 冷却 / 备注 / 渠道")
def rules_update(
    rule_id: Annotated[str, typer.Argument(help="规则 id")],
    threshold: Annotated[float | None, typer.Option(help="新阈值")] = None,
    window: Annotated[int | None, typer.Option(help="新窗口")] = None,
    cooldown_min: Annotated[int | None, typer.Option(help="新冷却分钟")] = None,
    channels: Annotated[list[str] | None, typer.Option(help="新渠道 id 列表（整体替换）")] = None,
    note: Annotated[str | None, typer.Option(help="新备注")] = None,
    output: OutputOption = None,
) -> None:
    try:
        rule = store.update_rule(
            rule_id,
            threshold=threshold,
            window=window,
            cooldown_min=cooldown_min,
            channels=list(channels) if channels is not None else None,
            note=note,
        )
    except LadderStockError as exc:
        _catch(exc)
    emit({"ok": True, "rule": rule}, output)


# --------------------------------------------------------------------------- #
# channels
# --------------------------------------------------------------------------- #


@channels_app.command("add", help="配一个出站渠道")
def channels_add(
    channel_id: Annotated[str, typer.Argument(help="渠道 id（a-z0-9-_）")],
    kind: Annotated[str, typer.Option(help="类型：" + " / ".join(CHANNEL_KINDS))],
    name: Annotated[str, typer.Option(help="显示名")] = "",
    config: Annotated[
        str,
        typer.Option(
            help='渠道配置 JSON；email 示例 {"smtp_host":"…","from_addr":"…","to_addrs":["…"]}'
        ),
    ] = "{}",
    output: OutputOption = None,
) -> None:
    try:
        parsed = json.loads(config) if str(config).strip() else {}
    except json.JSONDecodeError as exc:
        _fail(f"--config 不是合法 JSON：{exc}", EXIT_USAGE)
    if not isinstance(parsed, dict):
        _fail("--config 需要是 JSON 对象", EXIT_USAGE)
    try:
        channel = store.add_channel(channel_id, kind, name=name, config=parsed)
    except LadderStockError as exc:
        _catch(exc)
    emit(
        {"ok": True, "channel": {**channel, "config": public_config(kind, channel["config"])}},
        output,
    )


@channels_app.command("list", help="列出渠道（webhook URL 一律打码显示）")
def channels_list(output: OutputOption = None) -> None:
    items = [
        {**item, "config": public_config(item["kind"], item["config"])}
        for item in store.list_channels()
    ]
    emit(
        {
            "ok": True,
            "total": len(items),
            "channels": items,
            "smtp_password_env": DEFAULT_SMTP_PASSWORD_ENV,
        },
        output,
    )


@channels_app.command("rm", help="删除一个渠道")
def channels_rm(
    channel_id: Annotated[str, typer.Argument(help="渠道 id")],
    output: OutputOption = None,
) -> None:
    try:
        payload = store.remove_channel(channel_id)
    except LadderStockError as exc:
        _catch(exc)
    emit({"ok": True, **payload}, output)


@channels_app.command("test", help="向渠道真发一条测试消息（失败以 4 退出）")
def channels_test(
    channel_id: Annotated[str, typer.Argument(help="渠道 id")],
    output: OutputOption = None,
) -> None:
    try:
        channel = store.get_channel(channel_id)
    except LadderStockError as exc:
        _catch(exc)
    result = deliver(channel, "LadderStock 渠道测试", "收到即说明该渠道可用。")
    if not result.ok:
        error(f"渠道 {channel_id} 投递失败：{result.detail}")
    emit({"ok": result.ok, "result": result.as_dict()}, output)
    raise typer.Exit(EXIT_OK if result.ok else EXIT_SOURCE)


# --------------------------------------------------------------------------- #
# run / history
# --------------------------------------------------------------------------- #


def alert_run_once(output: OutputOption = None) -> None:
    """手动跑一轮提醒（与调度 `kind: alert_check` 同一个执行体）。"""
    report = alert_run.run_once()
    emit(report, output)
    if report.get("status") == "skipped":
        raise typer.Exit(EXIT_OK)
    status = report.get("status")
    raise typer.Exit(EXIT_OK if status == "ok" else 8)


__all__ = [
    "KIND_LABELS",
    "alert_run_once",
    "channels_app",
    "rules_app",
]


def register(app: typer.Typer) -> None:
    """把 `alert` 挂到 main.py 已建好的 Typer 组上。"""
    app.add_typer(rules_app, name="rules")
    app.add_typer(channels_app, name="channels")
    app.command("run", help="手动跑一轮提醒（CLI / 调度 / Web 同一个执行体）")(alert_run_once)
