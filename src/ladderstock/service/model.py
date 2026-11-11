"""跨平台服务抽象的数据模型（QUOT-527）。

三平台各自的原生机制：Windows 任务计划程序、macOS launchd、Linux systemd --user。
对外只有一份 `ServiceSpec`：同一条启动命令、同一个工作目录、同一份日志落点，
平台差异收在 `backends` 里，调用方不需要知道 XML / plist / unit 长什么样。

安全口径与项目其余部分一致：

- 安装/卸载默认 dry-run，`apply=True` 才动 OS；测试与开发宿主永远只走 dry-run
  或注入假执行器；
- `ServiceSpec` 不接受密钥类环境变量（密钥只进当前进程环境/凭据存储，不进
  unit/plist/XML，也不进日志）；
- 命令一律 argv 列表、`shell=False`，不做字符串拼接。
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Platform = Literal["windows", "macos", "linux"]

#: 三个后端都实现了的完整平台集合（`render --platform all` 按这个顺序渲染）
ALL_PLATFORMS: tuple[Platform, ...] = ("windows", "macos", "linux")

#: 用户可见的服务名（Windows 任务名 / 日志标题）
SERVICE_NAME = "LadderStock"

#: macOS LaunchAgent 的 label（反向 DNS；同时也是 launchctl 的作业标识）
LAUNCHD_LABEL = "com.quotafuse.ladderstock"

#: Linux systemd --user 的 unit 名
SYSTEMD_UNIT = "ladderstock.service"

#: Windows 任务计划程序里的任务名（与旧散落任务区分，交接时一眼能认出）
WINDOWS_TASK = "LadderStock Single Entry"

#: 密钥类环境变量名的特征词。命中即拒：unit/plist/XML 是可被备份/分享的配置文件，
#: 不是密钥存储。
SECRET_NAME_MARKERS: tuple[str, ...] = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "APIKEY",
    "API_KEY",
    "PRIVATE_KEY",
    "CREDENTIAL",
    "CONNECTION_STRING",
    "DSN",
)

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class ServiceError(RuntimeError):
    """服务定义/安装错误；消息直接给人看（CLI 落 EXIT_CONFIG）。"""


def detect_platform(value: str | None = None) -> Platform | None:
    """把 `sys.platform` / 显式平台名归一成三种后端之一；未知平台返回 None。"""
    raw = (value or sys.platform).strip().lower()
    if raw in ("windows", "win32", "cygwin", "msys"):
        return "windows"
    if raw in ("darwin", "macos", "mac"):
        return "macos"
    if raw.startswith("linux"):
        return "linux"
    return None


def platform_label(platform: Platform | None) -> str:
    return {
        "windows": "Windows 任务计划程序",
        "macos": "macOS launchd (LaunchAgent)",
        "linux": "Linux systemd --user",
        None: "未知平台",
    }[platform]


def assert_no_secrets(env: Mapping[str, str]) -> None:
    """服务定义只允许非密钥环境变量；命中密钥特征词直接拒绝（失败关闭）。"""
    hits = sorted(
        name
        for name in env
        if any(marker in name.upper() for marker in SECRET_NAME_MARKERS)
    )
    if hits:
        raise ServiceError(
            "服务定义不允许携带密钥类环境变量："
            + ", ".join(hits)
            + "；密钥/连接串只进当前进程环境或系统凭据存储，不写进 unit/plist/XML"
        )


def _check_env_value(name: str, value: str) -> None:
    if "\x00" in value or "\r" in value or "\n" in value:
        raise ServiceError(f"环境变量 {name} 的值不能包含换行或 NUL（服务定义按行解析）")


@dataclass(frozen=True)
class ServiceSpec:
    """一条平台无关的服务定义。

    `argv[0]` 必须是绝对路径：launchd/systemd 不经过 shell、也不解析 PATH，
    任务计划程序同样建议绝对路径；相对路径在安装态会随 cwd 漂移。
    """

    name: str
    argv: tuple[str, ...]
    workdir: Path
    log_dir: Path
    description: str = "LadderStock 单入口：看板 + 自愈 + 自有子进程"
    env: Mapping[str, str] = field(default_factory=dict)

    def validate(self) -> None:
        if not _NAME_RE.match(self.name):
            raise ServiceError(f"服务名 {self.name!r} 不符合 [A-Za-z0-9._-] 规则")
        if not self.argv:
            raise ServiceError("服务定义缺少启动命令（argv 为空）")
        exe = Path(self.argv[0])
        if not exe.is_absolute():
            raise ServiceError(f"服务启动程序必须是绝对路径：{self.argv[0]!r}")
        if not Path(self.workdir).is_absolute():
            raise ServiceError(f"工作目录必须是绝对路径：{self.workdir}")
        if not Path(self.log_dir).is_absolute():
            raise ServiceError(f"日志目录必须是绝对路径：{self.log_dir}")
        assert_no_secrets(self.env)
        for key, value in self.env.items():
            if not _NAME_RE.match(key):
                raise ServiceError(f"环境变量名 {key!r} 不合法")
            _check_env_value(key, value)

    def to_dict(self) -> dict[str, object]:
        # 这里只输出结构，不展开 content；env 已过密钥守卫
        return {
            "name": self.name,
            "argv": list(self.argv),
            "workdir": str(self.workdir),
            "log_dir": str(self.log_dir),
            "description": self.description,
            "env": dict(self.env),
        }


@dataclass(frozen=True)
class ServiceCommand:
    """一条原生注册命令；`optional=True` 表示「本来可能没有」的清理步骤。"""

    argv: tuple[str, ...]
    optional: bool = False

    def to_dict(self) -> dict[str, object]:
        return {"argv": list(self.argv), "optional": self.optional}


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    #: 起不来/超时（没有返回码）时的原因；有它就不看 returncode 语义
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.returncode == 0

    def to_dict(self, *, limit: int = 4000) -> dict[str, object]:
        return {
            "argv": list(self.argv),
            "returncode": self.returncode,
            "ok": self.ok,
            "error": self.error,
            "stdout": self.stdout[-limit:],
            "stderr": self.stderr[-limit:],
        }


@dataclass(frozen=True)
class ServicePlan:
    """安装/卸载计划：artifact 先落盘，再执行 commands。"""

    platform: Platform
    artifact_kind: str
    artifact_path: Path
    content: str
    commands: tuple[ServiceCommand, ...]
    post_commands: tuple[ServiceCommand, ...] = ()
    #: Windows 任务 XML 用 UTF-16（Task Scheduler 导出格式），其余平台 UTF-8
    artifact_encoding: str = "utf-8"

    def content_bytes(self) -> bytes:
        return self.content.encode(self.artifact_encoding)

    def to_dict(self, *, include_content: bool = False) -> dict[str, object]:
        payload: dict[str, object] = {
            "platform": self.platform,
            "mechanism": platform_label(self.platform),
            "artifact_kind": self.artifact_kind,
            "artifact_path": str(self.artifact_path),
            "artifact_encoding": self.artifact_encoding,
            "artifact_sha256": hashlib.sha256(self.content_bytes()).hexdigest(),
            "artifact_bytes": len(self.content_bytes()),
            "commands": [item.to_dict() for item in self.commands],
            "post_commands": [item.to_dict() for item in self.post_commands],
        }
        if include_content:
            payload["content"] = self.content
        return payload


def current_uid() -> int:
    getuid = getattr(os, "getuid", None)
    return int(getuid()) if callable(getuid) else 501
