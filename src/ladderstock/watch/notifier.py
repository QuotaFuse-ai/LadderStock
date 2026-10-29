"""通知渠道抽象：`console` / `desktop`，发送失败只告警、绝不阻塞主流程。

边界（DEC-006）：通知只落本机终端或本机桌面通知，不接第三方推送服务、不写任何
凭据、不发网络请求。需要别的投递方式时通过 `LDST_DESKTOP_NOTIFY_CMD` 指定一条
**本机命令**，标题与正文经环境变量传入（不拼进 shell 命令行，避免引号与注入问题）。

失败语义（CLI.md §B `notify test`、`docs/RELEASE_CHECKLIST.md`）：

- `send()` **从不因投递失败抛异常**：返回 `DeliveryResult(ok=False, degraded=True)`，
  由调用方决定是告警继续（定时任务、复盘）还是报错退出（`notify test`）；
- 渠道名非法属于用法问题，`normalize_channel()` 直接抛 `UnknownNotifierChannel`；
- 后端缺失也不猜、不假装成功：`detail` 写明缺什么、怎么补。
"""

from __future__ import annotations

import importlib
import os
import shlex
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import IO, Any

CHANNEL_CONSOLE = "console"
CHANNEL_DESKTOP = "desktop"

CHANNELS: tuple[str, ...] = (CHANNEL_CONSOLE, CHANNEL_DESKTOP)
DEFAULT_CHANNEL = CHANNEL_CONSOLE

# 桌面通知壳命令：为空的表示「用内置后端（winotify / plyer）」
DESKTOP_CMD_ENV = "LDST_DESKTOP_NOTIFY_CMD"
DESKTOP_TIMEOUT_SECONDS = 10.0

CONSOLE_PREFIX = "ldst 通知"


class UnknownNotifierChannel(ValueError):
    """渠道名不在 `CHANNELS` 里（用法错误，退出码 2）。"""


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    """一次投递的结果；`degraded=True` 表示「失败了但按降级继续」。"""

    channel: str
    ok: bool
    detail: str
    degraded: bool = False
    sent_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "ok": self.ok,
            "detail": self.detail,
            "degraded": self.degraded,
            "sent_at": self.sent_at,
        }


def normalize_channel(channel: str | None) -> str:
    """把 `--channel` 归一：缺省用 `console`；未知渠道抛用法错误。"""
    text = (channel or "").strip().lower() or DEFAULT_CHANNEL
    if text not in CHANNELS:
        raise UnknownNotifierChannel(
            f"未知通知渠道 {channel!r}；可用：{', '.join(CHANNELS)}"
        )
    return text


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# console
# --------------------------------------------------------------------------- #

def _send_console(
    title: str, body: str, stream: IO[str] | None = None
) -> DeliveryResult:
    """写本机终端：走 stderr，保证 stdout 只留结构化结果（CLI.md §F）。"""
    target = stream if stream is not None else sys.stderr
    try:
        target.write(f"{CONSOLE_PREFIX}｜{title}\n")
        if body:
            target.write(f"{body}\n")
        target.flush()
    except Exception as exc:  # noqa: BLE001 - 终端写不出去也只能告警
        return DeliveryResult(
            channel=CHANNEL_CONSOLE,
            ok=False,
            detail=f"终端写入失败：{exc}",
            degraded=True,
            sent_at=_now_iso(),
        )
    return DeliveryResult(
        channel=CHANNEL_CONSOLE,
        ok=True,
        detail="已写入本机终端（stderr）",
        sent_at=_now_iso(),
    )


# --------------------------------------------------------------------------- #
# desktop
# --------------------------------------------------------------------------- #

def _desktop_backend() -> Callable[[str, str], None] | None:
    """找本机桌面通知后端：Windows 优先 winotify，其余平台退回 plyer。"""
    if sys.platform.startswith("win"):
        try:
            winotify = importlib.import_module("winotify")
        except Exception:  # noqa: BLE001 - 缺包就是没有后端
            winotify = None
        if winotify is not None:

            def _winotify(title: str, body: str) -> None:
                toast = winotify.Notification(
                    app_id="LadderStock", title=title, msg=body or " "
                )
                toast.show()

            return _winotify
    try:
        plyer = importlib.import_module("plyer.notification")
    except Exception:  # noqa: BLE001
        return None

    def _plyer(title: str, body: str) -> None:
        plyer.notify(
            title=title, message=body or " ", app_name="LadderStock", timeout=8
        )

    return _plyer


def _split_command(command: str) -> list[str]:
    """把壳命令切成 argv。

    Windows 上 `posix=False` 会把引号原样留在 token 里（`subprocess` 再传就会带上
    字面引号），所以这里统一剥掉成对的包裹引号；POSIX 模式本来就会去掉引号，剥
    不到也只是原样返回。
    """
    argv = shlex.split(command, posix=os.name != "nt")
    return [
        token[1:-1]
        if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'"
        else token
        for token in argv
    ]


def _send_desktop(title: str, body: str) -> DeliveryResult:
    """桌面通知：壳命令优先，其次内置后端；任何失败都只降级不抛。"""
    stamp = _now_iso()
    command = os.environ.get(DESKTOP_CMD_ENV, "").strip()
    if command:
        try:
            argv = _split_command(command)
        except ValueError as exc:
            return DeliveryResult(
                channel=CHANNEL_DESKTOP,
                ok=False,
                detail=f"{DESKTOP_CMD_ENV} 无法解析：{exc}",
                degraded=True,
                sent_at=stamp,
            )
        if not argv:
            return DeliveryResult(
                channel=CHANNEL_DESKTOP,
                ok=False,
                detail=f"{DESKTOP_CMD_ENV} 为空命令",
                degraded=True,
                sent_at=stamp,
            )
        env = {**os.environ, "LDST_NOTIFY_TITLE": title, "LDST_NOTIFY_BODY": body}
        try:
            proc = subprocess.run(  # noqa: S603 - 只跑用户显式配置的本机命令
                argv,
                env=env,
                timeout=DESKTOP_TIMEOUT_SECONDS,
                capture_output=True,
                text=True,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return DeliveryResult(
                channel=CHANNEL_DESKTOP,
                ok=False,
                detail=f"桌面通知命令执行失败：{exc}",
                degraded=True,
                sent_at=stamp,
            )
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()
            hint = f"：{tail[-1][:120]}" if tail else ""
            return DeliveryResult(
                channel=CHANNEL_DESKTOP,
                ok=False,
                detail=f"桌面通知命令返回 {proc.returncode}{hint}",
                degraded=True,
                sent_at=stamp,
            )
        return DeliveryResult(
            channel=CHANNEL_DESKTOP,
            ok=True,
            detail=f"已通过 {DESKTOP_CMD_ENV} 投递",
            sent_at=stamp,
        )

    backend = _desktop_backend()
    if backend is None:
        return DeliveryResult(
            channel=CHANNEL_DESKTOP,
            ok=False,
            detail=(
                "未找到桌面通知后端（可安装 winotify，"
                f"或设置 {DESKTOP_CMD_ENV} 指向本机通知命令）"
            ),
            degraded=True,
            sent_at=stamp,
        )
    try:
        backend(title, body)
    except Exception as exc:  # noqa: BLE001 - 后端异常一律降级
        return DeliveryResult(
            channel=CHANNEL_DESKTOP,
            ok=False,
            detail=f"桌面通知后端异常：{exc}",
            degraded=True,
            sent_at=stamp,
        )
    return DeliveryResult(
        channel=CHANNEL_DESKTOP, ok=True, detail="已弹出本机桌面通知", sent_at=stamp
    )


# --------------------------------------------------------------------------- #
# 对外入口
# --------------------------------------------------------------------------- #

def send(
    channel: str | None,
    title: str,
    body: str = "",
    *,
    stream: IO[str] | None = None,
) -> DeliveryResult:
    """向单个渠道投递；投递失败只返回 `ok=False`，**不抛异常**。"""
    name = normalize_channel(channel)
    if name == CHANNEL_CONSOLE:
        return _send_console(title, body, stream)
    return _send_desktop(title, body)


def send_many(
    channels: Iterable[str | None],
    title: str,
    body: str = "",
) -> list[DeliveryResult]:
    """按顺序投递多个渠道；单个渠道失败不影响其余渠道。"""
    return [send(channel, title, body) for channel in channels]


def all_delivered(results: Iterable[DeliveryResult]) -> bool:
    return all(result.ok for result in results)


__all__ = [
    "CHANNELS",
    "CHANNEL_CONSOLE",
    "CHANNEL_DESKTOP",
    "DEFAULT_CHANNEL",
    "DESKTOP_CMD_ENV",
    "DeliveryResult",
    "UnknownNotifierChannel",
    "all_delivered",
    "normalize_channel",
    "send",
    "send_many",
]
