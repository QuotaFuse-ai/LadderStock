"""三平台服务后端（QUOT-527）：渲染原生定义、生成注册命令、解析状态。

| 平台 | 机制 | 产物 | 注册/启停 |
|---|---|---|---|
| Windows | 任务计划程序 | Task Scheduler XML | `schtasks /Create /XML`、`/Run`、`/End`、`/Delete` |
| macOS | launchd LaunchAgent | `.plist` | `launchctl bootstrap/bootout gui/<uid>` |
| Linux | systemd --user | `.service` unit | `systemctl --user daemon-reload / enable / disable` |


后端只生成**计划**（文件内容 + argv 列表），不执行；执行、落盘、回滚判定在
`manager.ServiceManager`。三条红线在这里用结构保证：

- 非当前平台的后端不会被 `apply`（见 `ServiceManager`）；
- 退出/卸载只作用于该服务自己的注册单元，不扫描、不改写任何旧计划任务；
- 密钥类环境变量在 `ServiceSpec.validate()` 就被拒，渲染阶段不会再碰它们。
"""

from __future__ import annotations

import getpass
import json
import os
import plistlib
import re
import subprocess
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ladderstock.service.model import (
    LAUNCHD_LABEL,
    SYSTEMD_UNIT,
    WINDOWS_TASK,
    CommandResult,
    Platform,
    ServiceCommand,
    ServiceSpec,
    current_uid,
)

#: Task Scheduler XML 命名空间（Win8+ 用 1.4；1.2 兼容面更宽，保留 1.2 的调度语义即可）
TASK_NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"

#: 服务稳定输出/错误文件（macOS/Linux 由单元重定向；Windows 由进程自身写日志）
STDOUT_LOG = "service.out.log"
STDERR_LOG = "service.err.log"


class PlatformBackend(ABC):
    """单平台后端协议。"""

    platform: Platform
    artifact_kind: str
    artifact_filename: str
    artifact_encoding: str = "utf-8"

    @abstractmethod
    def install_artifact_path(self, home: Path) -> Path:
        """安装态产物落点（apply 时写这里；命令引用同一路径）。"""

    @abstractmethod
    def render(self, spec: ServiceSpec) -> str:
        """渲染原生定义文本（不落盘）。"""

    @abstractmethod
    def install_commands(self, spec: ServiceSpec, artifact: Path) -> tuple[ServiceCommand, ...]:
        """注册 + 立即启动。"""

    @abstractmethod
    def uninstall_commands(self, spec: ServiceSpec) -> tuple[ServiceCommand, ...]:
        """停止 + 反注册（产物文件由 manager 在命令成功后删除）。"""

    @abstractmethod
    def post_uninstall_commands(self, spec: ServiceSpec) -> tuple[ServiceCommand, ...]:
        """删除产物之后的收尾（如 systemd daemon-reload）。"""

    @abstractmethod
    def status_commands(self, spec: ServiceSpec) -> tuple[ServiceCommand, ...]:
        """只读查询；输出由 `parse_status` 解释。"""

    @abstractmethod
    def parse_status(self, results: Sequence[CommandResult]) -> dict[str, object]:
        """把只读查询结果翻成 {installed, running, state, detail}。"""


# --------------------------------------------------------------------------- #
# Windows：任务计划程序
# --------------------------------------------------------------------------- #


@dataclass
class WindowsTaskBackend(PlatformBackend):
    platform: Platform = "windows"
    artifact_kind: str = "task-scheduler-xml"
    artifact_filename: str = "LadderStock-SingleEntry.xml"
    artifact_encoding: str = "utf-16"
    #: 覆盖当前用户名（测试注入；生产留空 = 当前登录用户）
    user: str | None = None

    def install_artifact_path(self, home: Path) -> Path:
        # XML 是注册输入，不是常驻配置：放在 LadderStock 自己的服务目录，
        # 不塞进系统任务目录（任务定义由 Task Scheduler 自己保存）。
        return Path(home) / ".ladderstock-service" / self.artifact_filename

    def _principal_user(self) -> str:
        if self.user:
            return self.user
        name = os.environ.get("USERNAME") or getpass.getuser()
        domain = (os.environ.get("USERDOMAIN") or "").strip()
        return f"{domain}\\{name}" if domain else name

    def render(self, spec: ServiceSpec) -> str:
        spec.validate()
        ET.register_namespace("", TASK_NS)
        task = ET.Element(f"{{{TASK_NS}}}Task", {"version": "1.4"})

        info = ET.SubElement(task, f"{{{TASK_NS}}}RegistrationInfo")
        ET.SubElement(info, f"{{{TASK_NS}}}Description").text = spec.description
        ET.SubElement(info, f"{{{TASK_NS}}}Author").text = "LadderStock"

        triggers = ET.SubElement(task, f"{{{TASK_NS}}}Triggers")
        logon = ET.SubElement(triggers, f"{{{TASK_NS}}}LogonTrigger")
        ET.SubElement(logon, f"{{{TASK_NS}}}Enabled").text = "true"
        ET.SubElement(logon, f"{{{TASK_NS}}}UserId").text = self._principal_user()
        ET.SubElement(logon, f"{{{TASK_NS}}}Delay").text = "PT15S"

        principals = ET.SubElement(task, f"{{{TASK_NS}}}Principals")
        principal = ET.SubElement(principals, f"{{{TASK_NS}}}Principal", {"id": "Author"})
        ET.SubElement(principal, f"{{{TASK_NS}}}UserId").text = self._principal_user()
        ET.SubElement(principal, f"{{{TASK_NS}}}LogonType").text = "InteractiveToken"
        ET.SubElement(principal, f"{{{TASK_NS}}}RunLevel").text = "LeastPrivilege"

        settings = ET.SubElement(task, f"{{{TASK_NS}}}Settings")
        ET.SubElement(settings, f"{{{TASK_NS}}}MultipleInstancesPolicy").text = "IgnoreNew"
        ET.SubElement(settings, f"{{{TASK_NS}}}DisallowStartIfOnBatteries").text = "false"
        ET.SubElement(settings, f"{{{TASK_NS}}}StopIfGoingOnBatteries").text = "false"
        ET.SubElement(settings, f"{{{TASK_NS}}}AllowHardTerminate").text = "true"
        ET.SubElement(settings, f"{{{TASK_NS}}}StartWhenAvailable").text = "true"
        ET.SubElement(settings, f"{{{TASK_NS}}}ExecutionTimeLimit").text = "PT0S"
        ET.SubElement(settings, f"{{{TASK_NS}}}Enabled").text = "true"
        restart = ET.SubElement(settings, f"{{{TASK_NS}}}RestartOnFailure")
        ET.SubElement(restart, f"{{{TASK_NS}}}Interval").text = "PT1M"
        ET.SubElement(restart, f"{{{TASK_NS}}}Count").text = "3"

        actions = ET.SubElement(task, f"{{{TASK_NS}}}Actions", {"Context": "Author"})
        action = ET.SubElement(actions, f"{{{TASK_NS}}}Exec")
        ET.SubElement(action, f"{{{TASK_NS}}}Command").text = spec.argv[0]
        if len(spec.argv) > 1:
            # Windows 命令行引用规则与 CreateProcess 一致；argv 已是列表，
            # 这里只做「列表 → 单字符串」的确定性引用，不经过 shell。
            ET.SubElement(action, f"{{{TASK_NS}}}Arguments").text = subprocess.list2cmdline(
                list(spec.argv[1:])
            )
        ET.SubElement(action, f"{{{TASK_NS}}}WorkingDirectory").text = str(spec.workdir)

        ET.indent(task, space="  ")
        body = ET.tostring(task, encoding="unicode")
        return '<?xml version="1.0" encoding="UTF-16"?>\n' + body + "\n"

    def install_commands(self, spec: ServiceSpec, artifact: Path) -> tuple[ServiceCommand, ...]:
        return (
            ServiceCommand(
                ("schtasks", "/Create", "/TN", WINDOWS_TASK, "/XML", str(artifact), "/F")
            ),
            # 已安装时 /Run 可能因正在运行而返回非 0：这是「已启动」，不是安装失败
            ServiceCommand(("schtasks", "/Run", "/TN", WINDOWS_TASK), optional=True),
        )

    def uninstall_commands(self, spec: ServiceSpec) -> tuple[ServiceCommand, ...]:
        return (
            ServiceCommand(("schtasks", "/End", "/TN", WINDOWS_TASK), optional=True),
            ServiceCommand(("schtasks", "/Delete", "/TN", WINDOWS_TASK, "/F")),
        )

    def post_uninstall_commands(self, spec: ServiceSpec) -> tuple[ServiceCommand, ...]:
        return ()

    def status_commands(self, spec: ServiceSpec) -> tuple[ServiceCommand, ...]:
        script = (
            f"$n = '{WINDOWS_TASK}'; "
            "$t = Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue; "
            "if ($null -eq $t) { Write-Output '{\"installed\": false}' } else { "
            "$i = Get-ScheduledTaskInfo -TaskName $n; "
            "[pscustomobject]@{installed=$true; state=[string]$t.State; "
            "last_run=[string]$i.LastRunTime; last_result=$i.LastTaskResult} "
            "| ConvertTo-Json -Compress }"
        )
        return (
            ServiceCommand(
                ("powershell", "-NoProfile", "-NonInteractive", "-Command", script)
            ),
        )

    def parse_status(self, results: Sequence[CommandResult]) -> dict[str, object]:
        result = results[0] if results else None
        if result is None or result.error:
            return {
                "installed": False,
                "running": False,
                "state": "unknown",
                "detail": (result.error if result else "没有状态查询结果"),
            }
        payload = _first_json_object(result.stdout)
        if payload is None:
            return {
                "installed": False,
                "running": False,
                "state": "unknown",
                "detail": (
                    "Get-ScheduledTask 输出无法解析："
                    + _trim(result.stderr or result.stdout)
                ),
            }
        state = str(payload.get("state") or "")
        return {
            "installed": bool(payload.get("installed")),
            "running": state.lower() == "running",
            "state": state or "unknown",
            "last_run": payload.get("last_run"),
            "last_result": payload.get("last_result"),
            "detail": "任务计划程序",
        }


# --------------------------------------------------------------------------- #
# macOS：launchd LaunchAgent
# --------------------------------------------------------------------------- #


@dataclass
class LaunchdBackend(PlatformBackend):
    platform: Platform = "macos"
    artifact_kind: str = "launchd-plist"
    artifact_filename: str = f"{LAUNCHD_LABEL}.plist"
    uid: int | None = None

    def _uid(self) -> int:
        return self.uid if self.uid is not None else current_uid()

    def install_artifact_path(self, home: Path) -> Path:
        return Path(home) / "Library" / "LaunchAgents" / self.artifact_filename

    def render(self, spec: ServiceSpec) -> str:
        spec.validate()
        payload: dict[str, object] = {
            "Label": LAUNCHD_LABEL,
            "ProgramArguments": list(spec.argv),
            "WorkingDirectory": str(spec.workdir),
            "RunAtLoad": True,
            # 托盘/服务主动退出后不要被 launchd 拉起来（退出 = 全停）
            "KeepAlive": False,
            "ProcessType": "Interactive",
            "LimitLoadToSessionType": "Aqua",
            "ThrottleInterval": 10,
            "StandardOutPath": str(Path(spec.log_dir) / STDOUT_LOG),
            "StandardErrorPath": str(Path(spec.log_dir) / STDERR_LOG),
        }
        if spec.env:
            payload["EnvironmentVariables"] = dict(spec.env)
        return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True).decode("utf-8")

    def install_commands(self, spec: ServiceSpec, artifact: Path) -> tuple[ServiceCommand, ...]:
        target = f"gui/{self._uid()}/{LAUNCHD_LABEL}"
        return (
            # 已加载时先摘掉，保证重复 install 幂等
            ServiceCommand(("launchctl", "bootout", target), optional=True),
            ServiceCommand(("launchctl", "bootstrap", f"gui/{self._uid()}", str(artifact))),
            ServiceCommand(("launchctl", "kickstart", "-k", target), optional=True),
        )

    def uninstall_commands(self, spec: ServiceSpec) -> tuple[ServiceCommand, ...]:
        return (
            ServiceCommand(("launchctl", "bootout", f"gui/{self._uid()}/{LAUNCHD_LABEL}")),
        )

    def post_uninstall_commands(self, spec: ServiceSpec) -> tuple[ServiceCommand, ...]:
        return ()

    def status_commands(self, spec: ServiceSpec) -> tuple[ServiceCommand, ...]:
        return (
            ServiceCommand(("launchctl", "print", f"gui/{self._uid()}/{LAUNCHD_LABEL}")),
        )

    def parse_status(self, results: Sequence[CommandResult]) -> dict[str, object]:
        result = results[0] if results else None
        if result is None or result.error:
            return {
                "installed": False,
                "running": False,
                "state": "unknown",
                "detail": (result.error if result else "没有状态查询结果"),
            }
        if result.returncode != 0:
            return {
                "installed": False,
                "running": False,
                "state": "not-loaded",
                "detail": _trim(result.stderr) or "launchctl 未找到该作业",
            }
        text = result.stdout
        state_match = re.search(r"^\s*state\s*=\s*(\S+)", text, re.MULTILINE)
        pid_match = re.search(r"^\s*pid\s*=\s*(\d+)", text, re.MULTILINE)
        state = state_match.group(1) if state_match else "loaded"
        return {
            "installed": True,
            "running": state == "running" or pid_match is not None,
            "state": state,
            "pid": int(pid_match.group(1)) if pid_match else None,
            "detail": "launchctl print",
        }


# --------------------------------------------------------------------------- #
# Linux：systemd --user
# --------------------------------------------------------------------------- #


def systemd_quote(value: str) -> str:
    """systemd 单元值引用：空格/引号/反斜杠需要引号；`%` 是 specifier 前缀。"""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    if any(ch in escaped for ch in (' ', "\t", '"', "\\")) or escaped == "":
        return f'"{escaped}"'
    return escaped


def _systemd_exec(argv: Sequence[str]) -> str:
    return " ".join(systemd_quote(part) for part in argv)


@dataclass
class SystemdUserBackend(PlatformBackend):
    platform: Platform = "linux"
    artifact_kind: str = "systemd-user-unit"
    artifact_filename: str = SYSTEMD_UNIT
    #: XDG_CONFIG_HOME（测试注入；缺省按 home/.config）
    config_home: Path | None = None

    def _config_home(self, home: Path) -> Path:
        return Path(self.config_home) if self.config_home else Path(home) / ".config"

    def install_artifact_path(self, home: Path) -> Path:
        return self._config_home(home) / "systemd" / "user" / self.artifact_filename

    def render(self, spec: ServiceSpec) -> str:
        spec.validate()
        lines = [
            "[Unit]",
            f"Description={spec.description}",
            "StartLimitIntervalSec=0",
            "After=default.target",
            "",
            "[Service]",
            "Type=simple",
            f"ExecStart={_systemd_exec(spec.argv)}",
            f"WorkingDirectory={systemd_quote(str(spec.workdir))}",
        ]
        for key in sorted(spec.env):
            lines.append(f"Environment={systemd_quote(f'{key}={spec.env[key]}')}")
        lines.extend(
            [
                # 进程异常退出才重启；用户主动 stop / 托盘退出不复活
                "Restart=on-failure",
                "RestartSec=5",
                "TimeoutStopSec=30",
                # 只清理本服务 cgroup 内的子进程；外部 adopted peer 不在这个 cgroup 里
                "KillMode=control-group",
                f"StandardOutput=append:{Path(spec.log_dir) / STDOUT_LOG}",
                f"StandardError=append:{Path(spec.log_dir) / STDERR_LOG}",
                "",
                "[Install]",
                "WantedBy=default.target",
                "",
            ]
        )
        return "\n".join(lines)

    def install_commands(self, spec: ServiceSpec, artifact: Path) -> tuple[ServiceCommand, ...]:
        return (
            ServiceCommand(("systemctl", "--user", "daemon-reload")),
            ServiceCommand(("systemctl", "--user", "enable", "--now", SYSTEMD_UNIT)),
        )

    def uninstall_commands(self, spec: ServiceSpec) -> tuple[ServiceCommand, ...]:
        return (
            # 未安装时 disable 会返回非 0；清理步骤容忍「本来就没有」
            ServiceCommand(
                ("systemctl", "--user", "disable", "--now", SYSTEMD_UNIT),
                optional=True,
            ),
        )

    def post_uninstall_commands(self, spec: ServiceSpec) -> tuple[ServiceCommand, ...]:
        return (ServiceCommand(("systemctl", "--user", "daemon-reload")),)

    def status_commands(self, spec: ServiceSpec) -> tuple[ServiceCommand, ...]:
        return (
            ServiceCommand(
                (
                    "systemctl",
                    "--user",
                    "show",
                    SYSTEMD_UNIT,
                    "--property=LoadState,UnitFileState,ActiveState,SubState",
                    "--no-pager",
                )
            ),
        )

    def parse_status(self, results: Sequence[CommandResult]) -> dict[str, object]:
        result = results[0] if results else None
        if result is None or result.error:
            return {
                "installed": False,
                "running": False,
                "state": "unknown",
                "detail": (result.error if result else "没有状态查询结果"),
            }
        fields: dict[str, str] = {}
        for line in result.stdout.splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                fields[key.strip()] = value.strip()
        load = fields.get("LoadState", "")
        active = fields.get("ActiveState", "")
        unit_file = fields.get("UnitFileState", "")
        installed = bool(load and load != "not-found")
        if result.returncode != 0 and not fields:
            return {
                "installed": False,
                "running": False,
                "state": "unknown",
                "detail": _trim(result.stderr) or "systemctl --user 不可用",
            }
        return {
            "installed": installed,
            "running": active == "active",
            "state": active or load or "unknown",
            "load_state": load or None,
            "unit_file_state": unit_file or None,
            "sub_state": fields.get("SubState") or None,
            "detail": "systemctl --user show",
        }


BACKENDS: Mapping[Platform, type[PlatformBackend]] = {
    "windows": WindowsTaskBackend,
    "macos": LaunchdBackend,
    "linux": SystemdUserBackend,
}


def backend_for(platform: Platform, **kwargs: object) -> PlatformBackend:
    return BACKENDS[platform](**kwargs)


def _first_json_object(text: str) -> dict[str, object] | None:
    for line in reversed(text.splitlines()):
        candidate = line.strip()
        if not candidate.startswith("{"):
            continue
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _trim(text: str, limit: int = 300) -> str:
    flat = " ".join(text.split())
    return flat[-limit:] if len(flat) > limit else flat
