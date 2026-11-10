"""`ldst supervisor`（QUOT-523 单入口）：不进看板也能启停整套服务。

同一台机器上可能同时跑着两类东西：看板（5950，本入口/看门狗拉起）与七个
顺序模块（5950 之外，supervisor 账本托管）。这个命令只操作**账本能证明是
我们拉起的进程**——归属判定见 `SupervisorEngine.state()`：外部进程一律不碰
（DEC-014），所以它同样不会误伤生产上不是它拉起的进程。

子命令：

- `start [--only 模块 ...]`：按注册表顺序全起（单模块失败不挡后面的）；
- `stop  [--force] [--only 模块 ...]`：反向全停；不确认时只打印计划（预演）；
- `restart --only 模块`：单模块重启，其它模块 pid 必须不变；
- `status`：每模块 state / 归属 / 监听 + 账本 PID。

退出码：全部成功 0；有模块失败 `EXIT_PARTIAL`；`start` 前有外部进程占着
端口时照常尝试，失败信息逐模块给出。
"""

from __future__ import annotations

from typing import Annotated, Any

import typer

from ladderstock.cli.output import OutputOption, emit, error
from ladderstock.cli.paths import data_dir
from ladderstock.errors import EXIT_CONFIG, EXIT_OK, EXIT_PARTIAL


def _engine(data_root: str | None) -> Any:
    from ladderstock.web.supervisor_api import build_supervisor_engines

    root = data_root or str(data_dir())
    return build_supervisor_engines(root, panel_url="http://127.0.0.1:5950/").processes


def start(
    only: Annotated[
        list[str] | None, typer.Option("--only", help="只起这些模块（可重复）；缺省按顺序全起")
    ] = None,
    data_root: Annotated[str | None, typer.Option("--data-root", help="数据目录")] = None,
    output: OutputOption = None,
) -> None:
    """按顺序全起（或只起 --only 指定的模块）。"""
    from ladderstock.supervisor.engine import SupervisorError

    engine = _engine(data_root)
    if only:
        results: dict[str, Any] = {}
        for name in only:
            try:
                engine.start_module(name)
            except SupervisorError as exc:
                results[name] = {"ok": False, "error": str(exc)}
            else:
                results[name] = {"ok": True, "error": None}
        payload: dict[str, Any] = {"ok": all(item["ok"] for item in results.values())}
        payload["modules"] = results
    else:
        payload = engine.start_all()
    emit(payload, output)
    raise typer.Exit(EXIT_OK if payload.get("ok") else EXIT_PARTIAL)


def stop(
    force: Annotated[
        bool, typer.Option("--force", help="确认执行（缺省只打印将停的模块）")
    ] = False,
    only: Annotated[list[str] | None, typer.Option("--only", help="只停这些模块（可重复）")] = None,
    data_root: Annotated[str | None, typer.Option("--data-root", help="数据目录")] = None,
    output: OutputOption = None,
) -> None:
    """反向全停；不带 --force 时预演（只报告账本视角，不动任何进程）。"""
    from ladderstock.supervisor.engine import SupervisorError

    engine = _engine(data_root)
    if not force:
        state = engine.state()
        managed = [
            name
            for name, item in (state.get("modules") or {}).items()
            if item.get("ownership") == "managed" and item.get("listening")
        ]
        emit(
            {
                "ok": True,
                "dry_run": True,
                "would_stop": managed,
                "order": list(reversed(state.get("order") or [])),
                "hint": "确认后加 --force 执行；外部进程不在提示范围内",
            },
            output,
        )
        raise typer.Exit(EXIT_OK)
    if only:
        results: dict[str, Any] = {}
        for name in only:
            try:
                engine.stop_module(name)
            except SupervisorError as exc:
                results[name] = {"ok": False, "error": str(exc)}
            else:
                results[name] = {"ok": True, "error": None}
        payload: dict[str, Any] = {"ok": all(item["ok"] for item in results.values())}
        payload["modules"] = results
    else:
        payload = engine.stop_all()
    emit(payload, output)
    raise typer.Exit(EXIT_OK if payload.get("ok") else EXIT_PARTIAL)


def restart(
    only: Annotated[
        list[str] | None, typer.Option("--only", help="要重启的模块（可重复，至少一个）")
    ] = None,
    data_root: Annotated[str | None, typer.Option("--data-root", help="数据目录")] = None,
    output: OutputOption = None,
) -> None:
    """单模块重启：只动指定模块，其它模块 pid 必须不变。"""
    from ladderstock.supervisor.engine import SupervisorError

    if not only:
        error("restart 需要至少一个 --only 模块（全量重启请先 stop --force 再 start）")
        raise typer.Exit(EXIT_CONFIG)
    engine = _engine(data_root)
    results: dict[str, Any] = {}
    for name in only:
        try:
            engine.restart_module(name)
        except SupervisorError as exc:
            results[name] = {"ok": False, "error": str(exc)}
        else:
            results[name] = {"ok": True, "error": None}
    payload: dict[str, Any] = {"ok": all(item["ok"] for item in results.values())}
    payload["modules"] = results
    emit(payload, output)
    raise typer.Exit(EXIT_OK if payload.get("ok") else EXIT_PARTIAL)


def status(
    data_root: Annotated[str | None, typer.Option("--data-root", help="数据目录")] = None,
    output: OutputOption = None,
) -> None:
    """每模块 state / 归属 / 监听 / 账本 PID。"""
    emit(_engine(data_root).state(), output)
    raise typer.Exit(EXIT_OK)


def register(app: typer.Typer) -> None:
    group = typer.Typer(no_args_is_help=True, help="单入口 supervisor：全起 / 全停 / 单模块重启")
    group.command("start", help="按注册表顺序全起（--only 只起指定模块）")(start)
    group.command("stop", help="反向全停（缺省预演；--force 执行）")(stop)
    group.command("restart", help="单模块重启（--only 必填）")(restart)
    group.command("status", help="每模块 state / 归属 / 监听")(status)
    app.add_typer(group, name="supervisor")


__all__ = ["register", "restart", "start", "status", "stop"]
