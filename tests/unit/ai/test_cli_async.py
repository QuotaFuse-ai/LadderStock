"""QUOT-473：异步 CLI 调用——超时与取消都要把整棵进程树带走。

不依赖本机真的装了 codex / claude：直接用当前解释器当「CLI」，验证
`_run_async` 的读 stdin / 超时 / 取消三条路径。
"""

from __future__ import annotations

import asyncio
import csv
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ladderstock.ai import cli_provider, fallback

#: 起一个会再孵一个孙进程的假 CLI：只杀父进程会留下孙进程，正是要防的「悬挂」
_NESTED_CHILD = (
    "import subprocess, sys, time\n"
    "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
    "open(sys.argv[1], 'w').write(str(child.pid))\n"
    "time.sleep(60)\n"
)


def _alive(pid: int) -> bool:
    if os.name == "nt":  # pragma: no cover - Windows 专用
        # 不能用 `/FI ... /NH` 的文案判断：中文系统输出是「没有运行的任务」
        out = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=15,
        ).stdout
        for row in csv.reader(out.splitlines()):
            if len(row) >= 2 and row[1].strip() == str(pid):
                return True
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    stat = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True
    ).stdout.strip()
    return bool(stat) and not stat.startswith("Z")


def _wait_pid_file(path: Path, timeout: float = 20.0) -> int:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            text = path.read_text().strip()
            if text:
                return int(text)
        time.sleep(0.1)
    raise AssertionError("假 CLI 没有按时写出孙进程 PID")


def test_run_async_pipes_stdin_and_stdout() -> None:
    code, out, err = asyncio.run(
        cli_provider._run_async(
            [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read().upper())"],
            stdin_text="hello",
            timeout=30,
        )
    )
    assert code == 0
    assert out == "HELLO"
    assert err == ""


def test_timeout_kills_grandchild(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"

    async def scenario() -> None:
        with pytest.raises(cli_provider.CliTimeout):
            await cli_provider._run_async(
                [sys.executable, "-c", _NESTED_CHILD, str(pid_file)],
                stdin_text=None,
                timeout=2,
            )

    asyncio.run(scenario())
    child = _wait_pid_file(pid_file)
    time.sleep(0.5)
    assert not _alive(child), "超时后孙进程仍然存活（进程树没杀干净）"


def test_cancel_kills_grandchild(tmp_path: Path) -> None:
    pid_file = tmp_path / "child.pid"

    async def scenario() -> None:
        task = asyncio.ensure_future(
            cli_provider._run_async(
                [sys.executable, "-c", _NESTED_CHILD, str(pid_file)],
                stdin_text=None,
                timeout=120,
            )
        )
        child = await asyncio.to_thread(_wait_pid_file, pid_file)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return child

    child = asyncio.run(scenario())
    time.sleep(0.5)
    assert not _alive(child), "取消后孙进程仍然存活（进程树没杀干净）"


def test_acomplete_reuses_prompt_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`acomplete` 与同步 `complete` 用同一套 prompt 拼接与围栏剥离。"""
    seen: dict[str, str] = {}

    async def fake_run(cmd, *, stdin_text, timeout, home=None, extra_env=()):
        seen["stdin"] = stdin_text or ""
        return 0, "```\n正文\n```", ""

    monkeypatch.setattr(cli_provider, "_run_async", fake_run)
    monkeypatch.setattr(
        cli_provider,
        "probe",
        lambda spec: {"state": "ok", "exe": "fake", "home": str(tmp_path), "detail": ""},
    )
    monkeypatch.setattr(cli_provider, "_command", lambda spec, exe, args: ["fake", *args])

    provider = cli_provider.CliProvider()
    target = fallback.ModelTarget(
        backend=fallback.BackendConfig(
            id="cli_codex",
            kind="cli",
            base_url_env="",
            api_key_env="",
            models=("default",),
            cli="codex",
        ),
        model="default",
        label="cli_codex:default",
    )
    text = asyncio.run(
        provider.acomplete(target, "系统提示", "用户问题", json_mode=True, timeout=10)
    )

    assert text == "正文"
    assert "系统提示" in seen["stdin"]
    assert "用户问题" in seen["stdin"]
    assert cli_provider.JSON_ONLY_INSTRUCTION in seen["stdin"]


def test_acomplete_timeout_is_cli_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """真起一个睡着的进程：`acomplete` 超时后抛 `CliTimeout`，不是 `CliUnavailable`。"""
    monkeypatch.setattr(
        cli_provider,
        "probe",
        lambda spec: {"state": "ok", "exe": "fake", "home": str(tmp_path), "detail": ""},
    )
    monkeypatch.setattr(
        cli_provider,
        "_command",
        lambda spec, exe, args: [sys.executable, "-c", "import time; time.sleep(30)"],
    )

    target = fallback.ModelTarget(
        backend=fallback.BackendConfig(
            id="cli_codex",
            kind="cli",
            base_url_env="",
            api_key_env="",
            models=("default",),
            cli="codex",
        ),
        model="default",
        label="cli_codex:default",
    )
    with pytest.raises(cli_provider.CliTimeout):
        asyncio.run(
            cli_provider.CliProvider().acomplete(
                target, "系统", "用户", json_mode=False, timeout=0.2
            )
        )
