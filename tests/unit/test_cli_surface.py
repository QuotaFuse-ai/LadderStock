"""CLI 表面契约：31 条基线命令（+ R7 增量 `pool` / `position`）全部注册且行为符合约定。

数据层命令（`sync` / `sources`）已经实现，本文件用离线假传输执行它们：
基线契约不该拨真 peer（既慢又不可判定），也不该把冷却记录写进仓库的 `data/`。
"""

from __future__ import annotations

import json

import pytest
from fixtures.command_matrix import COMMAND_MATRIX
from fixtures.fake_peers import FakeTransport
from typer.testing import CliRunner

from ladderstock.cli.main import app

runner = CliRunner()

HELP_GROUPS = (
    "run",
    "review",
    "validate",
    "sentiment",
    "ladder",
    "sync",
    "sources",
    "watch",
    "notify",
    "journal",
    "pool",
    "position",
    "export",
    "state",
    "ai",
    "mcp",
    "config",
    "schedule",
    "doctor",
)


@pytest.fixture(autouse=True)
def _offline_data_layer(repo, tmp_path, monkeypatch):
    """数据目录钉在 tmp、传输层换成空桩：不碰网络，也不污染仓库 `data/`。

    空桩对任何工具都抛 `SourceUnavailable`，等价于「三个 peer 全挂」，
    正好用来断言数据层命令的失败退出码（4 / 8）。
    """
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "ladderstock.data.provider_registry.build_transport",
        lambda _peers: FakeTransport(),
    )


def test_help_lists_every_command_group(repo) -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for name in HELP_GROUPS:
        assert name in result.stdout, f"--help 缺少 {name}"


@pytest.mark.parametrize(("argv", "expected"), COMMAND_MATRIX)
def test_command_registered_and_exits_as_baseline(repo, argv, expected) -> None:
    result = runner.invoke(app, list(argv))
    assert result.exit_code in expected, (
        f"{' '.join(argv)} 退出码 {result.exit_code}，期望 {sorted(expected)}\n"
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )


def test_config_migrate_is_implemented_and_idempotent(repo, tmp_path) -> None:
    """`config migrate` 已实现：`--dry-run` 出计划、退出 0，且不改动任何文件。"""
    result = runner.invoke(app, ["config", "migrate", "--dry-run", "--output", "json"])
    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    # 仓库配置已是 v2：无事可做就不谎报有步骤
    assert payload["status"] == "up_to_date"
    assert payload["steps"] == []
    assert "backups" in payload and payload["backups"] == []


def test_review_requires_date(repo) -> None:
    result = runner.invoke(app, ["review"])
    assert result.exit_code == 2
    assert "--date" in result.stderr


def test_mcp_http_refuses_occupied_port(repo, tmp_path) -> None:
    """端口被占用时直接 3（DEC-004：不自动换端口），且不会真的拉起 uvicorn。

    `mcp http` 是长驻服务，不进 COMMAND_MATRIX；这里用「先占住端口」把阻塞路径
    变成可断言的失败路径。
    """
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    try:
        result = runner.invoke(app, ["mcp", "http", "--port", str(port)])
    finally:
        sock.close()

    assert result.exit_code == 3, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "端口" in result.stderr
    assert "DEC-004" in result.stderr


def test_journal_add_never_announces_remote_write(repo) -> None:
    result = runner.invoke(
        app,
        ["journal", "add", "--code", "600000", "--side", "buy", "--price", "10", "--qty", "100"],
    )
    # 施工期：只做注册与提示，不落盘；不得出现任何网络/上传字样
    assert "http" not in result.stdout.lower()
    assert "upload" not in result.stdout.lower()
