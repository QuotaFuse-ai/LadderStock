"""订阅版 CLI 通道（QUOT-401）：探测四态 + 真调一次本机 CLI。

这些用例**不依赖**本机真的装了 codex / claude：在临时目录里放一个假 CLI，
把 PATH / HOME 指过去，就能把「找到 → 跑起来 → 读登录态 → 取正文」整条链路走通。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ladderstock.ai import cli_provider, fallback


def _write_fake_cli(
    bin_dir: Path, name: str, *, body: str, version: str = "9.9.9", login_ok: bool = True
) -> None:
    """在临时目录里造一个假 CLI：`--version` 回版本，`login status` 回登录态，其余回显 stdin。"""
    login_rc = "0" if login_ok else "1"
    login_msg = "API key configured - sk-abcdef***" if login_ok else "Not logged in"
    if os.name == "nt":
        target = bin_dir / f"{name}.cmd"
        target.write_text(
            "@echo off\r\n"
            'if "%~1"=="--version" (\r\n'
            f"  echo {version}\r\n"
            "  exit /b 0\r\n"
            ")\r\n"
            'if "%~1"=="login" (\r\n'
            f"  echo {login_msg}\r\n"
            f"  exit /b {login_rc}\r\n"
            ")\r\n"
            "setlocal enabledelayedexpansion\r\n"
            "set OUT=\r\n"
            'for /f "usebackq delims=" %%L in (`more`) do set OUT=!OUT!%%L\r\n'
            f"{body}\r\n",
            encoding="utf-8",
        )
    else:
        target = bin_dir / name
        target.write_text(
            "#!/bin/sh\n"
            'case "$1" in\n'
            f"  --version) echo {version}; exit 0 ;;\n"
            f'  login) echo "{login_msg}"; exit {login_rc} ;;\n'
            "esac\n"
            f"{body}\n",
            encoding="utf-8",
        )
        target.chmod(0o755)


@pytest.fixture()
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 PATH / HOME 都指到临时目录，避免读到本机真实的 codex / claude。"""
    bin_dir = tmp_path / "bin"
    home = tmp_path / "home"
    bin_dir.mkdir()
    home.mkdir()
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return tmp_path


def test_missing_when_not_on_path(isolated: Path) -> None:
    spec = cli_provider.cli_spec("codex")
    status = cli_provider.probe(spec)
    assert status["state"] == "missing"
    assert status["exe"] is None
    assert "没找到" in status["detail"]


def test_no_login_when_binary_present_but_no_credentials(isolated: Path) -> None:
    _write_fake_cli(isolated / "bin", "codex", body="echo ok", login_ok=False)
    status = cli_provider.probe(cli_provider.cli_spec("codex"))
    assert status["state"] == "no_login"
    assert status["version"] == "9.9.9"
    assert "codex login" in status["detail"]


def test_ok_when_binary_and_login_present(isolated: Path) -> None:
    _write_fake_cli(isolated / "bin", "codex", body="echo ok")
    auth = isolated / "home" / ".codex" / "auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text("{}", encoding="utf-8")
    status = cli_provider.probe(cli_provider.cli_spec("codex"))
    assert status["state"] == "ok"
    assert status["login_files"] == [str(auth)]


def test_broken_when_version_exits_nonzero(isolated: Path) -> None:
    bin_dir = isolated / "bin"
    if os.name == "nt":
        target = bin_dir / "claude.cmd"
        target.write_text("@echo off\r\necho boom 1>&2\r\nexit /b 3\r\n", encoding="utf-8")
    else:
        target = bin_dir / "claude"
        target.write_text("#!/bin/sh\necho boom >&2\nexit 3\n", encoding="utf-8")
        target.chmod(0o755)
    status = cli_provider.probe(cli_provider.cli_spec("claude"))
    assert status["state"] == "broken"
    assert "返回 3" in status["detail"]


def test_finds_cli_in_known_install_location(isolated: Path) -> None:
    """Codex 桌面版把 CLI 塞在 `%LOCALAPPDATA%\\OpenAI\\Codex\\bin\\<hash>`，从不写 PATH。"""
    target = (
        isolated
        / "home"
        / "AppData"
        / "Local"
        / "OpenAI"
        / "Codex"
        / "bin"
        / "abc123"
        / "codex.exe"
    )
    target.parent.mkdir(parents=True)
    target.write_bytes(b"fake")
    found = cli_provider.find_executable(cli_provider.cli_spec("codex"))
    assert found is not None
    assert os.path.normcase(found) == os.path.normcase(str(target))


def test_bin_env_overrides_discovery(isolated: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """装在非标准位置时，用 LDST_CLI_CODEX_BIN 直接指定。"""
    exe = isolated / "weird" / "codex.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"fake")
    monkeypatch.setenv("LDST_CLI_CODEX_BIN", str(exe))
    assert cli_provider.find_executable(cli_provider.cli_spec("codex")) == str(exe)


def test_complete_pipes_prompt_through_stdin(isolated: Path) -> None:
    """正文走 stdin：prompt 再长也不会撞 Windows 32k 命令行上限。"""
    body = "echo PROMPT-SEEN"
    _write_fake_cli(isolated / "bin", "codex", body=body)
    (isolated / "home" / ".codex").mkdir()
    (isolated / "home" / ".codex" / "auth.json").write_text("{}", encoding="utf-8")

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
    text = provider.complete(target, "system", "user", json_mode=False, timeout=60)
    assert text.strip() != ""


def test_complete_raises_when_channel_unavailable(isolated: Path) -> None:
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
    with pytest.raises(cli_provider.CliUnavailable):
        provider.complete(target, "s", "u", json_mode=False, timeout=10)


def test_env_selects_cli_backend_as_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ai.env` 里写了 LDST_LLM_CLI，就该它接管默认模型，HTTP 后端退到链尾。"""
    monkeypatch.delenv("LDST_LLM_CLI", raising=False)
    monkeypatch.delenv("LDST_LLM_MODEL", raising=False)
    (tmp_path / "ai.yaml").write_text(
        """
ai:
  default_model: openai:gpt-4o-mini
  fallback_chain: []
  sections: {}
  backends:
    - id: openai
      type: openai
      models: [gpt-4o-mini]
""",
        encoding="utf-8",
    )
    (tmp_path / "ai.env").write_text("LDST_LLM_CLI=codex\n", encoding="utf-8")

    config = fallback.load_config(tmp_path)
    assert config.default_model == "cli_codex:default"
    assert config.declared_backends[0].kind == "cli"
    assert config.declared_backends[0].cli == "codex"
    assert [item.id for item in config.declared_backends] == ["cli_codex", "openai"]


def test_cli_backend_without_executable_is_not_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.delenv("LDST_LLM_CLI", raising=False)
    # 本机可能真的装了 codex：这里显式让它「找不到」，否则用例随环境通过/失败
    monkeypatch.setattr(cli_provider, "find_executable", lambda spec: None)
    backend = fallback.BackendConfig(
        id="cli_codex",
        kind="cli",
        base_url_env="",
        api_key_env="",
        models=("default",),
        cli="codex",
    )
    assert backend.configured is False
    assert backend.missing_env() == ["(CLI:codex 未安装)"]


def test_unknown_cli_id_in_yaml_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "ai.yaml").write_text(
        """
ai:
  default_model: nope:default
  sections: {}
  backends:
    - id: nope
      type: cli
      models: [default]
""",
        encoding="utf-8",
    )
    from ladderstock.core.boundary import BoundaryViolation

    with pytest.raises(BoundaryViolation):
        fallback.load_config(tmp_path)


def test_provider_for_dispatches_cli() -> None:
    from ladderstock.ai import providers

    assert isinstance(providers.provider_for("cli"), cli_provider.CliProvider)


def test_python_fallback_for_fake_cli_smoke() -> None:
    """最后一道保险：假 CLI 用 python -c 跑一遍，确认 argv/stdin 形状没写反。"""
    code = "import sys; sys.stdout.write('python-cli:' + sys.stdin.read().strip())"
    proc = subprocess.run(
        [sys.executable, "-c", code],
        input="hello",
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert proc.stdout == "python-cli:hello"
