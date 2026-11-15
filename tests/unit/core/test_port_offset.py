from __future__ import annotations

import ladderstock.core.boundary as boundary
from ladderstock.core.boundary import (
    PEER_CONTROL,
    PORTS_OFFSET_ENV,
    port_offset,
    resolve_peer_control,
)

TEST_OFFSET = 10000


def test_offset_defaults_to_zero_when_unset(monkeypatch) -> None:
    # 正式服不设 LDST_PORT_OFFSET：必须按 0 处理，否则生产端口全被平移
    monkeypatch.delenv(PORTS_OFFSET_ENV, raising=False)

    assert port_offset() == 0


def test_offset_ignores_invalid_values(monkeypatch) -> None:
    monkeypatch.setenv(PORTS_OFFSET_ENV, "abc")
    assert port_offset() == 0

    monkeypatch.setenv(PORTS_OFFSET_ENV, "-10000")
    assert port_offset() == 0

    monkeypatch.setenv(PORTS_OFFSET_ENV, "   ")
    assert port_offset() == 0


def test_offset_reads_value(monkeypatch) -> None:
    monkeypatch.setenv(PORTS_OFFSET_ENV, str(TEST_OFFSET))
    assert port_offset() == TEST_OFFSET


def test_production_ports_unchanged_without_offset(monkeypatch, tmp_path) -> None:
    # 零回归：不设偏移时面板口径与今天完全一致
    monkeypatch.setattr(boundary, "_install_root", lambda: tmp_path)
    (tmp_path / "LadderStock").mkdir()
    monkeypatch.delenv(PORTS_OFFSET_ENV, raising=False)

    resolved = resolve_peer_control({})

    assert resolved["tickhub"].mcp_port == 8000
    assert resolved["tickhub"].web_port == 8787
    assert resolved["senate"].mcp_port == 8765
    assert resolved["proxyfleet"].extra_ports == (8789,)


def test_test_ports_shifted_by_offset(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(boundary, "_install_root", lambda: tmp_path)
    (tmp_path / "LadderStock").mkdir()

    resolved = resolve_peer_control({"LDST_PORT_OFFSET": str(TEST_OFFSET)})

    assert resolved["tickhub"].mcp_port == 18000
    assert resolved["tickhub"].web_port == 18787
    assert resolved["tickhub_us"].mcp_port == 18002
    assert resolved["wire"].mcp_port == 18791
    assert resolved["wire"].web_port == 18790
    assert resolved["senate"].mcp_port == 18765
    assert resolved["senate"].web_port == 18786
    assert resolved["financecoffer"].mcp_port == 18100
    assert resolved["mem0"].mcp_port == 19100
    assert resolved["proxyfleet"].extra_ports == (18789,)
    assert resolved["qdrant"].web_port == 16333


def test_start_commands_shift_with_offset(monkeypatch, tmp_path) -> None:
    # 面板显示端口是假通过的高发点：实际启停命令里的 --port 必须一起偏移
    monkeypatch.setattr(boundary, "_install_root", lambda: tmp_path)
    (tmp_path / "LadderStock").mkdir()

    resolved = resolve_peer_control({"LDST_PORT_OFFSET": str(TEST_OFFSET)})

    us_command = resolved["tickhub_us"].start_commands[0]
    assert us_command[-1] == "18002"

    senate_commands = resolved["senate"].start_commands
    assert senate_commands[0][-1] == "18765"
    assert senate_commands[1][-1] == "18786"


def test_start_command_without_port_is_untouched(monkeypatch, tmp_path) -> None:
    # Wire 的入口不带 --port（端口在其自身 .env），偏移不得改动命令本身
    monkeypatch.setattr(boundary, "_install_root", lambda: tmp_path)
    (tmp_path / "LadderStock").mkdir()

    resolved = resolve_peer_control({"LDST_PORT_OFFSET": str(TEST_OFFSET)})

    assert resolved["wire"].start_commands == PEER_CONTROL["wire"].start_commands


def test_offset_is_positive_int_only(monkeypatch) -> None:
    monkeypatch.setenv(PORTS_OFFSET_ENV, "10.5")
    assert port_offset() == 0
