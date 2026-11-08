"""通知层的「绝不影响主流程」契约（QUOT-491 / 482-C，验收证据 ④）。

钉住：

| 场景 | 期望 |
|---|---|
| Toast 进程起不来 / 超时 / 非零退出 | 吞掉，只记一行日志，不抛 |
| MultiNotifier 某条通道炸掉 | 其它通道照发 |
| 事件日志 | 滚动上限内写入 |
| Toast 脚本 | 模块名 / 原因里的引号被转义，不破 XML |
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from ladderstock.supervisor.notify import (
    EventLogSink,
    MultiNotifier,
    ToastSink,
    _toast_script,
    _toast_text,
    build_default_notifier,
    failure_body,
    failure_title,
    gave_up_title,
)


class BoomRunner:
    def __init__(self, mode: str = "raise") -> None:
        self.mode = mode
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        self.calls.append(argv)
        if self.mode == "raise":
            raise OSError("powershell 不存在")
        if self.mode == "timeout":
            raise subprocess.TimeoutExpired(cmd=argv, timeout=10)
        return subprocess.CompletedProcess(argv, int(self.mode), stdout=b"", stderr=b"")


def test_toast_failure_never_raises() -> None:
    for mode in ("raise", "timeout", "1"):  # 起不来 / 超时 / 非零退出
        sink = ToastSink(runner=BoomRunner(mode))
        sink.failure(module="tickhub", ports=(8000,), attempt=1, max_attempts=3, reason="崩了")
        sink.gave_up(module="tickhub", ports=(8000,), attempts=3, reason="崩了")


def test_toast_disabled_skips_the_runner() -> None:
    runner = BoomRunner()
    sink = ToastSink(runner=runner, enabled=False)

    sink.failure(module="tickhub", ports=(8000,), attempt=1, max_attempts=3, reason="崩了")

    assert runner.calls == []


def test_toast_script_escapes_quotes() -> None:
    script = _toast_script('模"块', '原<因>&引"号')

    assert _toast_text('模"块') == "模&quot;块"
    assert "模&quot;块" in script
    assert "原&lt;因&gt;&amp;引&quot;号" in script
    assert "powershell" in " ".join(["powershell", "-Command", script])


def test_multi_notifier_isolates_broken_sinks() -> None:
    class Boom:
        def failure(self, **kwargs: object) -> None:
            raise RuntimeError("炸")

        def gave_up(self, **kwargs: object) -> None:
            raise RuntimeError("炸")

        def recovered(self, **kwargs: object) -> None:
            raise RuntimeError("炸")

    class Ok:
        def __init__(self) -> None:
            self.seen: list[str] = []

        def failure(self, **kwargs: object) -> None:
            self.seen.append("failure")

        def gave_up(self, **kwargs: object) -> None:
            self.seen.append("gave_up")

        def recovered(self, **kwargs: object) -> None:
            self.seen.append("recovered")

    good = Ok()
    notifier = MultiNotifier([Boom(), good])

    notifier.failure(module="m", ports=(1,), attempt=1, max_attempts=3, reason="r")
    notifier.gave_up(module="m", ports=(1,), attempts=3, reason="r")
    notifier.recovered(module="m", pid=1)

    assert good.seen == ["failure", "gave_up", "recovered"]


def test_event_log_sink_writes_and_never_raises(tmp_path: Path) -> None:
    sink = EventLogSink(tmp_path / "notifications.log", max_lines=2)

    sink.failure(module="tickhub", ports=(8000,), attempt=1, max_attempts=3, reason="崩了")
    sink.gave_up(module="tickhub", ports=(8000,), attempts=3, reason="崩了")
    sink.recovered(module="tickhub", pid=9)  # 超上限：只留最近 2 条

    lines = (tmp_path / "notifications.log").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert "GAVE_UP" in lines[0] and "RECOVERED" in lines[1]

    blocked = EventLogSink(tmp_path / "a-directory", max_lines=2)  # 路径被目录占住 → OSError
    (tmp_path / "a-directory").mkdir()
    blocked.failure(module="tickhub", ports=(8000,), attempt=1, max_attempts=3, reason="崩了")


def test_default_notifier_bundles_both_channels(tmp_path: Path) -> None:
    notifier = build_default_notifier(tmp_path / "notifications.log", toast_enabled=False)

    assert isinstance(notifier, MultiNotifier)
    assert len(notifier.sinks) == 2
    assert isinstance(notifier.sinks[0], EventLogSink)
    assert isinstance(notifier.sinks[1], ToastSink)
    assert notifier.sinks[1].enabled is False


def test_notification_copy_carries_module_ports_and_reason() -> None:
    title = failure_title("tickhub", 2, 3)
    body = failure_body("tickhub", (8000, 8787), "退出码 1", "http://127.0.0.1:5950/")
    gave_up = gave_up_title("tickhub", 3)

    assert "tickhub" in title and "2/3" in title
    assert "8000/8787" in body and "退出码 1" in body and "5950" in body
    assert "需人工处理" in gave_up
