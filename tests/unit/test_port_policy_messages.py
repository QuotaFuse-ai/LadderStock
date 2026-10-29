"""旧端口提示与两类 `OSError` 文案（QUOT-112 / DEC-004）。

这里锁三件事：

1. 显式敲旧端口（5940 / 8770）只多打一行提示，退出码与后续行为不变，
   绝不自动换端口、绝不重试；
2. 绑定策略拒绝（`require_binding_policy` 抛的 `OSError`）只说策略问题，
   **不能**再说「端口无法监听 / 请用 --port」——那不是端口问题；
3. 真正的 `EADDRINUSE`（含 Windows `WSAEADDRINUSE`）才说「已被占用」并建议换端口。
"""

from __future__ import annotations

import errno
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ladderstock.cli.legacy_ports import LEGACY_PORTS, legacy_port_hint, warn_legacy_port
from ladderstock.cli.main import app
from ladderstock.cli.run import WSAEADDRINUSE, is_addr_in_use
from ladderstock.errors import EXIT_CONFIG, EXIT_OK

runner = CliRunner()

HINT_5940 = "ldst: 5940 是旧端口，v1.0 起固定用 5950；本程序不会自动切换端口（DEC-004）"
HINT_8770 = "ldst: 8770 是旧端口，v1.0 起固定用 5951；本程序不会自动切换端口（DEC-004）"


@pytest.fixture()
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo: Path) -> Path:
    """隔离数据目录，并清掉远程绑定开关（默认必须只回环）。"""
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    for name in (
        "LDST_WEB_ALLOW_REMOTE",
        "LDST_WEB_TOKEN",
        "LDST_MCP_ALLOW_REMOTE",
        "LDST_MCP_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


# --------------------------------------------------------------------------- #
# 1. 旧端口提示
# --------------------------------------------------------------------------- #


def test_legacy_port_map_and_text() -> None:
    assert LEGACY_PORTS == {5940: 5950, 8770: 5951}
    assert legacy_port_hint(5940) == HINT_5940
    assert legacy_port_hint(8770) == HINT_8770


@pytest.mark.parametrize("port", [5950, 5951, 8000, 0, 5941])
def test_non_legacy_port_has_no_hint(port: int) -> None:
    assert legacy_port_hint(port) == ""


def test_warn_legacy_port_writes_stderr_only(capsys: pytest.CaptureFixture[str]) -> None:
    assert warn_legacy_port(5940) is True
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"{HINT_5940}\n"
    assert warn_legacy_port(5950) is False


def test_run_with_legacy_port_prints_hint_and_still_runs(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    launched: dict[str, object] = {}

    def fake_launch(*, host: str, port: int) -> None:
        launched.update(host=host, port=port)

    monkeypatch.setattr("ladderstock.cli.main._port_free", lambda _port: True)
    monkeypatch.setattr("ladderstock.cli.main.launch_dashboard", fake_launch)

    result = runner.invoke(app, ["run", "--port", "5940"])

    assert result.exit_code == EXIT_OK, result.stderr
    assert HINT_5940 in result.stderr
    # 只提示：端口原样透传，没有任何「自动切到 5950」的行为
    assert launched == {"host": "127.0.0.1", "port": 5940}


def test_mcp_http_with_legacy_port_prints_hint_before_starting(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    monkeypatch.setattr("ladderstock.cli.mcp.port_free", lambda _port, _host: True)
    monkeypatch.setattr(
        "ladderstock.mcp.server.create_http_app", lambda **_kw: object()
    )
    monkeypatch.setattr(
        "uvicorn.run", lambda *_a, **_kw: calls.append("uvicorn.run")
    )

    result = runner.invoke(app, ["mcp", "http", "--port", "8770"])

    assert result.exit_code == EXIT_OK, result.stderr
    assert HINT_8770 in result.stderr
    assert calls == ["uvicorn.run"]


# --------------------------------------------------------------------------- #
# 2. 两类 OSError：策略拒绝 vs 真正的端口占用
# --------------------------------------------------------------------------- #


def _addr_in_use(exc: OSError) -> bool:
    return is_addr_in_use(exc)


def test_is_addr_in_use_recognises_posix_and_windows_forms() -> None:
    assert _addr_in_use(OSError(errno.EADDRINUSE, "Address already in use"))

    windows = OSError(10048, "Address already in use")
    windows.winerror = WSAEADDRINUSE  # type: ignore[attr-defined]
    assert _addr_in_use(windows)

    assert not _addr_in_use(OSError(errno.EACCES, "Permission denied"))


def test_run_refusing_non_loopback_is_not_reported_as_port_error(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("ladderstock.cli.main._port_free", lambda _port: True)

    result = runner.invoke(app, ["run", "--host", "0.0.0.0", "--port", "5978"])

    assert result.exit_code == EXIT_CONFIG, result.stderr
    stderr = result.stderr
    assert "拒绝监听非回环地址" in stderr, stderr
    assert "DEC-004" in stderr
    # 端口本身没问题：不能让人照着「换端口」越试越糊涂
    assert "无法监听" not in stderr, stderr
    assert "请用 --port" not in stderr, stderr
    assert "已被占用" not in stderr, stderr
    # 拒绝发生在启动之前：不能出现「看板已启动」
    assert "看板已启动" not in stderr, stderr


def test_mcp_http_refusing_non_loopback_is_not_reported_as_port_error(
    isolated_env: Path,
) -> None:
    result = runner.invoke(app, ["mcp", "http", "--host", "0.0.0.0"])

    assert result.exit_code == EXIT_CONFIG, result.stderr
    assert "拒绝监听非回环地址" in result.stderr, result.stderr
    assert "请用 --port" not in result.stderr, result.stderr
    assert "无法监听" not in result.stderr, result.stderr


def test_run_real_addr_in_use_keeps_port_wording(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`serve()` 阶段才撞上的 EADDRINUSE：说「已被占用」并建议 `--port`。"""

    def busy(_app: object, *, host: str, port: int) -> None:
        raise OSError(errno.EADDRINUSE, "Address already in use")

    monkeypatch.setattr("ladderstock.cli.main._port_free", lambda _port: True)
    monkeypatch.setattr("ladderstock.config.migrate.ensure_migrated", lambda **_kw: None)
    monkeypatch.setattr("ladderstock.web.server.create_app", lambda **_kw: object())
    monkeypatch.setattr("ladderstock.web.server.serve", busy)

    result = runner.invoke(app, ["run", "--port", "5979"])

    assert result.exit_code == EXIT_CONFIG, result.stderr
    assert "5979 已被占用" in result.stderr, result.stderr
    assert "请用 --port" in result.stderr, result.stderr
    assert "无法监听" not in result.stderr, result.stderr


def test_mcp_http_real_addr_in_use_keeps_port_wording(
    isolated_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def busy(*_a: object, **_kw: object) -> None:
        raise OSError(errno.EADDRINUSE, "Address already in use")

    monkeypatch.setattr("ladderstock.cli.mcp.port_free", lambda _port, _host: True)
    monkeypatch.setattr(
        "ladderstock.mcp.server.create_http_app", lambda **_kw: object()
    )
    monkeypatch.setattr("uvicorn.run", busy)

    result = runner.invoke(app, ["mcp", "http", "--port", "5979"])

    assert result.exit_code == EXIT_CONFIG, result.stderr
    assert "5979 已被占用" in result.stderr, result.stderr
    assert "请用 --port" in result.stderr, result.stderr
    assert "无法监听" not in result.stderr, result.stderr
