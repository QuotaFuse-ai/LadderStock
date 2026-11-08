"""自愈失败通知（QUOT-491 / 482-C）。

D5 定稿：失败要有系统通知——Windows Toast，内容含**模块名 + 失败原因摘要 +
面板链接/端口**；通知失败不得影响主流程（本模块全部异常就地吞掉，只落一行
事件日志）；**不得借通知引入托盘常驻**（D6），Toast 弹完即走、无任何驻留组件。

通道两条：
- `ToastSink`：Windows 通知栏（PowerShell + WinRT，shell=False，超时保护）；
- `EventLogSink`：`data/supervisor/notifications.log`（走有上限的滚动追加），
  面板/验收取证都从这读，也是 Toast 不可用环境下的兜底通道。
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as _xml_escape

from ladderstock.supervisor.logroll import append_capped, utc_line

_log = logging.getLogger(__name__)

TOAST_TIMEOUT_SECONDS = 10.0
NOTIFY_LOG_MAX_LINES = 200
TOAST_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

RunSubprocess = Callable[..., Any]


def _toast_text(text: str) -> str:
    """Toast 文本进 XML 的转义（属性内的引号一并处理）。"""
    return _xml_escape(str(text), {'"': "&quot;"})


def failure_title(module: str, attempt: int, max_attempts: int) -> str:
    return f"LadderStock 自愈：{module} 第 {attempt}/{max_attempts} 次重拉失败"


def gave_up_title(module: str, attempts: int) -> str:
    return f"LadderStock 自愈：{module} 已停止重拉，需人工处理"


def failure_body(module: str, ports: Iterable[int], reason: str, panel_url: str) -> str:
    port_text = "/".join(str(port) for port in ports) or "无端口"
    return f"模块 {module}（端口 {port_text}）失败：{reason}。面板：{panel_url}"


class EventLogSink:
    """事件日志通道：滚动文件，永不抛异常。"""

    def __init__(self, path: str | Path, *, max_lines: int = NOTIFY_LOG_MAX_LINES) -> None:
        self.path = Path(path)
        self.max_lines = max_lines

    def failure(
        self, *, module: str, ports: tuple[int, ...], attempt: int, max_attempts: int, reason: str
    ) -> None:
        self._write(f"FAILURE module={module} attempt={attempt}/{max_attempts} reason={reason}")

    def gave_up(self, *, module: str, ports: tuple[int, ...], attempts: int, reason: str) -> None:
        self._write(f"GAVE_UP module={module} attempts={attempts} reason={reason}")

    def recovered(self, *, module: str, pid: int | None) -> None:
        self._write(f"RECOVERED module={module} pid={pid}")

    def _write(self, message: str) -> None:
        try:
            append_capped(self.path, utc_line(message), max_lines=self.max_lines)
        except OSError:
            _log.warning("自愈事件日志写入失败（已吞）")


class ToastSink:
    """Windows Toast 通道：失败/超时一律吞掉并记一行日志，绝不影响自愈主流程。"""

    def __init__(
        self,
        *,
        panel_url: str = "http://127.0.0.1:5950/",
        timeout: float = TOAST_TIMEOUT_SECONDS,
        runner: RunSubprocess | None = None,
        enabled: bool = True,
    ) -> None:
        self.panel_url = panel_url
        self.timeout = timeout
        self._runner: RunSubprocess = runner or subprocess.run
        self.enabled = enabled

    def failure(
        self, *, module: str, ports: tuple[int, ...], attempt: int, max_attempts: int, reason: str
    ) -> None:
        self._show(failure_title(module, attempt, max_attempts),
                   failure_body(module, ports, reason, self.panel_url))

    def gave_up(self, *, module: str, ports: tuple[int, ...], attempts: int, reason: str) -> None:
        self._show(gave_up_title(module, attempts),
                   failure_body(module, ports, reason, self.panel_url))

    def recovered(self, *, module: str, pid: int | None) -> None:
        # D5：只有失败才弹通知；恢复不弹 Toast，事件日志通道（EventLogSink）留痕即可。
        return

    def _show(self, title: str, body: str) -> None:
        if not self.enabled:
            return
        script = _toast_script(title, body)
        try:
            completed = self._runner(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    script,
                ],
                capture_output=True,
                timeout=self.timeout,
                shell=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            _log.warning("Toast 通知发送失败（已吞，不影响主流程）：%s", exc)
            return
        if completed.returncode != 0:
            _log.warning(
                "Toast 通知非零退出（已吞，不影响主流程）：rc=%s",
                getattr(completed, "returncode", "?"),
            )


def _toast_script(title: str, body: str) -> str:
    safe_title = _toast_text(title)
    safe_body = _toast_text(body)
    return (
        "[Windows.UI.Notifications.ToastNotificationManager,"
        " Windows.UI.Notifications, ContentType=WindowsRuntime] > $null; "
        "$xml = New-Object Windows.Data.Xml.Dom.XmlDocument; "
        "$xml.LoadXml('<toast><visual><binding template=\"ToastGeneric\">"
        f"<text>{safe_title}</text><text>{safe_body}</text>"
        "</binding></visual></toast>'); "
        "$toast = New-Object Windows.UI.Notifications.ToastNotification $xml; "
        f"[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{TOAST_APP_ID}')"
        ".Show($toast)"
    )


class MultiNotifier:
    """聚合多条通知通道；每条独立 try/except——单通道炸掉不拖累其它通道。"""

    def __init__(self, sinks: Iterable[Any]) -> None:
        self.sinks = list(sinks)

    def failure(
        self, *, module: str, ports: tuple[int, ...], attempt: int, max_attempts: int, reason: str
    ) -> None:
        for sink in self.sinks:
            try:
                sink.failure(
                    module=module,
                    ports=tuple(ports),
                    attempt=attempt,
                    max_attempts=max_attempts,
                    reason=reason,
                )
            except Exception:  # noqa: BLE001 — 通知失败必须被吞（D5）
                _log.warning("自愈通知通道异常（已吞，不影响主流程）", exc_info=True)

    def gave_up(self, *, module: str, ports: tuple[int, ...], attempts: int, reason: str) -> None:
        for sink in self.sinks:
            try:
                sink.gave_up(module=module, ports=tuple(ports), attempts=attempts, reason=reason)
            except Exception:  # noqa: BLE001 — 通知失败必须被吞（D5）
                _log.warning("自愈通知通道异常（已吞，不影响主流程）", exc_info=True)

    def recovered(self, *, module: str, pid: int | None) -> None:
        for sink in self.sinks:
            try:
                sink.recovered(module=module, pid=pid)
            except Exception:  # noqa: BLE001
                _log.warning("自愈通知通道异常（已吞，不影响主流程）", exc_info=True)


def build_default_notifier(
    log_path: str | Path, *, panel_url: str = "http://127.0.0.1:5950/", toast_enabled: bool = True
) -> MultiNotifier:
    """默认通道：事件日志（总是可用）+ Windows Toast（失败即吞）。"""
    return MultiNotifier(
        [
            EventLogSink(log_path),
            ToastSink(panel_url=panel_url, enabled=toast_enabled),
        ]
    )


__all__ = [
    "EventLogSink",
    "MultiNotifier",
    "ToastSink",
    "build_default_notifier",
    "failure_body",
    "failure_title",
    "gave_up_title",
]
