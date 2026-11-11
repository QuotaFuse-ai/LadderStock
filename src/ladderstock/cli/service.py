"""`ldst service`（QUOT-527）：三平台安装/开机自启/托盘。

- `render`：生成三平台原生定义（Task Scheduler XML / launchd plist / systemd
  unit），任何平台都能跑，是可以直接验真的产物；
- `install` / `uninstall`：**默认 dry-run**，`--apply` 才写文件并调用原生命令；
  只允许在计划目标平台执行；
- `status`：只读查询当前平台是否注册、是否在跑；
- `tray`：托盘（可选 pystray）或 headless 退出契约；退出只停 supervisor 自有
  子进程，adopted peer 原样保留。

红线：开发/测试只在临时目录与注入执行器上验证；不安装/修改/卸载生产宿主的
任何 OS 服务或计划任务。
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from ladderstock.cli.output import OutputOption, emit, error
from ladderstock.cli.paths import data_dir
from ladderstock.errors import EXIT_CONFIG, EXIT_OK, EXIT_PARTIAL
from ladderstock.service import (
    ALL_PLATFORMS,
    Platform,
    ServiceError,
    ServiceManager,
    ServiceSpec,
    TrayController,
    TrayUnavailable,
    build_tray_backend,
    default_spec,
    tray_available,
)


def _manager(platform: str | None = None) -> ServiceManager:
    return ServiceManager(platform=_platform(platform), data_root=data_dir())


def _platform(value: str | None) -> Platform | None:
    if not value:
        return None
    from ladderstock.service.model import detect_platform

    normalized = detect_platform(value)
    if normalized is None:
        raise ServiceError(f"不认识的平台 {value!r}；可选 windows / macos / linux")
    return normalized


def _spec() -> ServiceSpec:
    return default_spec(data_root=data_dir())


def _platforms(values: list[str] | None) -> tuple[Platform, ...]:
    if not values:
        return ALL_PLATFORMS
    selected: list[Platform] = []
    for value in values:
        platform = _platform(value)
        assert platform is not None
        if platform not in selected:
            selected.append(platform)
    return tuple(selected)


def render(
    platform: Annotated[
        list[str] | None,
        typer.Option("--platform", help="只渲染指定平台（可重复）；缺省三平台全渲染"),
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option("--out", help="产物目录；缺省 <数据目录>/service/units"),
    ] = None,
    output: OutputOption = None,
) -> None:
    """生成三平台原生服务定义（不写 OS 注册表，任何平台都能验证）。"""
    try:
        platforms = _platforms(platform)
        spec = _spec()
        target = Path(out) if out is not None else data_dir() / "service" / "units"
        payload = _manager().render_all(spec, target, platforms=platforms)
    except ServiceError as exc:
        error(str(exc))
        raise typer.Exit(EXIT_CONFIG) from None
    emit(payload, output)
    raise typer.Exit(EXIT_OK)


def install(
    apply: Annotated[
        bool, typer.Option("--apply", help="确认执行：写产物并调用原生注册命令（缺省 dry-run）")
    ] = False,
    platform: Annotated[
        str | None, typer.Option("--platform", help="目标平台；缺省当前平台（必须等于当前平台）")
    ] = None,
    output: OutputOption = None,
) -> None:
    """安装为服务/开机自启（默认只打印计划，--apply 才执行）。"""
    try:
        payload = _manager(platform).install(_spec(), apply=apply)
    except ServiceError as exc:
        error(str(exc))
        raise typer.Exit(EXIT_CONFIG) from None
    emit(payload, output)
    raise typer.Exit(EXIT_OK if payload.get("ok") else EXIT_PARTIAL)


def uninstall(
    apply: Annotated[
        bool, typer.Option("--apply", help="确认执行：反注册并删除产物（缺省 dry-run）")
    ] = False,
    platform: Annotated[
        str | None, typer.Option("--platform", help="目标平台；缺省当前平台（必须等于当前平台）")
    ] = None,
    output: OutputOption = None,
) -> None:
    """卸载服务/开机自启（默认只打印计划，--apply 才执行）。"""
    try:
        payload = _manager(platform).uninstall(_spec(), apply=apply)
    except ServiceError as exc:
        error(str(exc))
        raise typer.Exit(EXIT_CONFIG) from None
    emit(payload, output)
    raise typer.Exit(EXIT_OK if payload.get("ok") else EXIT_PARTIAL)


def status(
    output: OutputOption = None,
) -> None:
    """当前平台的服务注册/运行状态（只读）。"""
    try:
        payload = _manager().status(_spec())
    except ServiceError as exc:
        error(str(exc))
        raise typer.Exit(EXIT_CONFIG) from None
    emit(payload, output)
    raise typer.Exit(EXIT_OK)


def tray(
    check: Annotated[
        bool, typer.Option("--check", help="只报告托盘后端是否可用，不启动图标")
    ] = False,
    headless: Annotated[
        bool, typer.Option("--headless", help="无图标模式：按 --quit-after 触发退出契约")
    ] = False,
    quit_after: Annotated[
        float, typer.Option("--quit-after", help="headless 模式等待秒数后执行退出")
    ] = 0.0,
    panel_url: Annotated[
        str, typer.Option("--panel-url", help="托盘「打开看板」目标")
    ] = "http://127.0.0.1:5950/",
    output: OutputOption = None,
) -> None:
    """托盘：退出只停 supervisor 自有子进程，adopted peer 原样保留。"""
    available, detail = tray_available()
    if check:
        emit(
            {
                "ok": True,
                "available": available,
                "backend": "pystray" if available else "unavailable",
                "detail": detail,
                "hint": "无 GUI 时用 `service tray --headless --quit-after 0` 验证退出契约",
            },
            output,
        )
        raise typer.Exit(EXIT_OK)
    try:
        backend = build_tray_backend(headless=headless, quit_after=quit_after)
    except TrayUnavailable as exc:
        error(str(exc))
        raise typer.Exit(EXIT_CONFIG) from None

    from ladderstock.web.supervisor_api import build_supervisor_engines

    engine = build_supervisor_engines(str(data_dir()), panel_url=panel_url).processes
    controller = TrayController(engine.stop_owned, panel_url=panel_url)
    try:
        backend.run(controller)
    except TrayUnavailable as exc:
        error(str(exc))
        raise typer.Exit(EXIT_CONFIG) from None
    result = controller.result or {"ok": True, "quit": False, "kept_adopted": []}
    result.setdefault("backend", backend.name)
    emit(result, output)
    raise typer.Exit(EXIT_OK if result.get("ok") else EXIT_PARTIAL)


def register(app: typer.Typer) -> None:
    group = typer.Typer(
        no_args_is_help=True,
        help="跨平台服务/开机自启：install / uninstall / status / render / tray",
    )
    group.command("render", help="生成三平台服务定义（不注册，任何平台可验证）")(render)
    group.command("install", help="安装为服务/开机自启（缺省 dry-run；--apply 执行）")(install)
    group.command("uninstall", help="卸载服务/开机自启（缺省 dry-run；--apply 执行）")(uninstall)
    group.command("status", help="当前平台注册/运行状态（只读）")(status)
    group.command("tray", help="托盘退出契约（pystray 或 --headless）")(tray)
    app.add_typer(group, name="service")


__all__ = [
    "install",
    "register",
    "render",
    "status",
    "tray",
    "uninstall",
]
