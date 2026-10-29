"""通知渠道抽象（`watch/notifier.py`）：console / desktop 的失败语义。

三条硬约束（CLI.md §B、RELEASE_CHECKLIST）：

1. 投递失败 **绝不抛异常**，只返回 `ok=False, degraded=True`；
2. 未知渠道属于用法错误，`normalize_channel` 直接抛 `UnknownNotifierChannel`；
3. desktop 的壳命令拿不到后端时明说缺什么，不假装成功。
"""

from __future__ import annotations

import io
import sys

import pytest

from ladderstock.watch import notifier
from ladderstock.watch.notifier import (
    CHANNEL_CONSOLE,
    CHANNEL_DESKTOP,
    CHANNELS,
    DEFAULT_CHANNEL,
    DESKTOP_CMD_ENV,
    DeliveryResult,
    UnknownNotifierChannel,
    all_delivered,
    normalize_channel,
    send,
    send_many,
)

# --------------------------------------------------------------------------- #
# 渠道归一
# --------------------------------------------------------------------------- #

def test_default_channel_is_console() -> None:
    assert normalize_channel(None) == CHANNEL_CONSOLE
    assert normalize_channel("") == CHANNEL_CONSOLE
    assert normalize_channel("  ") == CHANNEL_CONSOLE
    assert DEFAULT_CHANNEL == CHANNEL_CONSOLE
    assert CHANNELS == (CHANNEL_CONSOLE, CHANNEL_DESKTOP)


def test_channel_name_is_case_and_space_insensitive() -> None:
    assert normalize_channel(" Desktop ") == CHANNEL_DESKTOP


def test_unknown_channel_is_usage_error() -> None:
    with pytest.raises(UnknownNotifierChannel) as excinfo:
        normalize_channel("telegram")
    assert "console" in str(excinfo.value)
    assert "desktop" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# console
# --------------------------------------------------------------------------- #

def test_console_writes_to_stderr_by_default(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = send(CHANNEL_CONSOLE, "盘前提醒", "09:25 前定下单计划")
    assert result.ok is True
    assert result.degraded is False
    assert result.channel == CHANNEL_CONSOLE
    assert result.sent_at

    captured = capsys.readouterr()
    assert "盘前提醒" in captured.err
    assert "09:25 前定下单计划" in captured.err
    # stdout 只留结构化结果，通知不污染输出
    assert captured.out == ""


def test_console_body_is_optional() -> None:
    stream = io.StringIO()
    result = notifier._send_console("只有标题", "", stream)
    assert result.ok is True
    assert stream.getvalue().strip().endswith("只有标题")


def test_console_write_failure_degrades_without_raising() -> None:
    class BrokenStream:
        def write(self, _text: str) -> int:
            raise OSError("终端已关闭")

        def flush(self) -> None:
            raise OSError("终端已关闭")

    result = notifier._send_console("标题", "正文", BrokenStream())  # type: ignore[arg-type]
    assert result.ok is False
    assert result.degraded is True
    assert "终端已关闭" in result.detail


# --------------------------------------------------------------------------- #
# desktop：壳命令
# --------------------------------------------------------------------------- #

def test_desktop_command_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    marker = tmp_path / "notified.txt"
    # 用 python -c 写文件，避免依赖平台上的特定通知工具
    script = (
        "import os,pathlib;"
        f"pathlib.Path(r'{marker}').write_text("
        "os.environ.get('LDST_NOTIFY_TITLE','') + '|' + os.environ.get('LDST_NOTIFY_BODY',''),"
        "encoding='utf-8')"
    )
    monkeypatch.setenv(DESKTOP_CMD_ENV, f'"{sys.executable}" -c "{script}"')

    result = send(CHANNEL_DESKTOP, "标题A", "正文B")

    assert result.ok is True
    assert result.channel == CHANNEL_DESKTOP
    assert marker.read_text(encoding="utf-8") == "标题A|正文B"


def test_split_command_strips_quotes_on_both_platforms() -> None:
    argv = notifier._split_command('"C:\\Program Files\\py.exe" -c "print(1)"')
    assert argv[0].lower().endswith("py.exe")
    assert argv[1] == "-c"
    assert argv[2] == "print(1)"


def test_desktop_command_failure_degrades(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        DESKTOP_CMD_ENV, f'"{sys.executable}" -c "import sys; sys.exit(3)"'
    )

    result = send(CHANNEL_DESKTOP, "标题", "正文")

    assert result.ok is False
    assert result.degraded is True
    assert "3" in result.detail


def test_desktop_command_missing_binary_degrades(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(DESKTOP_CMD_ENV, "ldst-no-such-notify-binary --flag")

    result = send(CHANNEL_DESKTOP, "标题", "正文")

    assert result.ok is False
    assert result.degraded is True
    assert result.detail


def test_desktop_backend_missing_degrades(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(DESKTOP_CMD_ENV, raising=False)
    monkeypatch.setattr(notifier, "_desktop_backend", lambda: None)

    result = send(CHANNEL_DESKTOP, "标题", "正文")

    assert result.ok is False
    assert result.degraded is True
    assert "桌面通知后端" in result.detail


def test_desktop_backend_exception_degrades(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(DESKTOP_CMD_ENV, raising=False)

    def boom(_title: str, _body: str) -> None:
        raise RuntimeError("toast 被系统拒绝")

    monkeypatch.setattr(notifier, "_desktop_backend", lambda: boom)

    result = send(CHANNEL_DESKTOP, "标题", "正文")

    assert result.ok is False
    assert result.degraded is True
    assert "toast 被系统拒绝" in result.detail


# --------------------------------------------------------------------------- #
# 多渠道路由
# --------------------------------------------------------------------------- #

def test_send_many_isolates_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        DESKTOP_CMD_ENV, f'"{sys.executable}" -c "import sys; sys.exit(1)"'
    )

    results = send_many([CHANNEL_CONSOLE, CHANNEL_DESKTOP], "多渠道路由", "正文")

    assert [item.channel for item in results] == [CHANNEL_CONSOLE, CHANNEL_DESKTOP]
    assert results[0].ok is True
    assert results[1].ok is False
    assert all_delivered(results) is False


def test_delivery_result_as_dict_contract() -> None:
    result = DeliveryResult(
        channel=CHANNEL_CONSOLE, ok=True, detail="ok", sent_at="2026-10-28T09:05:00"
    )

    assert result.as_dict() == {
        "channel": CHANNEL_CONSOLE,
        "ok": True,
        "detail": "ok",
        "degraded": False,
        "sent_at": "2026-10-28T09:05:00",
    }
