"""跨平台服务抽象（QUOT-527）。

三平台原生机制（Windows 任务计划程序 / macOS launchd / Linux systemd --user）
统一成 `ServiceManager` 的 install / uninstall / status / render 四个动作，
外加托盘退出契约 `TrayController`（退出只停 supervisor 自有子进程）。
"""

from __future__ import annotations

from ladderstock.service.manager import CommandRunner, ServiceManager, SubprocessRunner
from ladderstock.service.model import (
    ALL_PLATFORMS,
    LAUNCHD_LABEL,
    SERVICE_NAME,
    SYSTEMD_UNIT,
    WINDOWS_TASK,
    CommandResult,
    Platform,
    ServiceCommand,
    ServiceError,
    ServicePlan,
    ServiceSpec,
    detect_platform,
    platform_label,
)
from ladderstock.service.spec import SERVICE_SUBCOMMAND, default_spec, entry_argv
from ladderstock.service.tray import (
    TrayBackend,
    TrayController,
    TrayUnavailable,
    build_tray_backend,
    tray_available,
)

__all__ = [
    "ALL_PLATFORMS",
    "CommandResult",
    "CommandRunner",
    "LAUNCHD_LABEL",
    "Platform",
    "SERVICE_NAME",
    "SERVICE_SUBCOMMAND",
    "SYSTEMD_UNIT",
    "ServiceCommand",
    "ServiceError",
    "ServiceManager",
    "ServicePlan",
    "ServiceSpec",
    "SubprocessRunner",
    "TrayBackend",
    "TrayController",
    "TrayUnavailable",
    "WINDOWS_TASK",
    "build_tray_backend",
    "default_spec",
    "detect_platform",
    "entry_argv",
    "platform_label",
    "tray_available",
]
