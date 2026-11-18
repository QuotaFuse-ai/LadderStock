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
    # 2026-11-18 用户裁决：正式段也彻底去 Docker，FC / ProxyFleet / qdrant 全是
    # 本实例自有的服务（venv / 本地二进制 / services.py 拉起），因此**不再有**
    # 「外部托管不平移」的例外，三者的端口与启动参数一并平移。
    assert resolved["financecoffer"].mcp_port == 18101
    assert resolved["financecoffer"].web_port == 18100
    # QUOT-611 把 mem0 从 Docker peer 下线为进程内 SDK，PEERS/PEER_CONTROL 里不再有
    # 这个 key。这条断言因此不能硬绑「mem0 必须存在」——存在才校验它的偏移，
    # 否则跳过，两个单子才能同时合并进去。
    if "mem0" in resolved:
        assert resolved["mem0"].mcp_port == 19100
    assert resolved["proxyfleet"].web_port == 18788
    assert resolved["proxyfleet"].extra_ports == (18789,)
    assert resolved["qdrant"].web_port == 16333
    assert resolved["qdrant"].mcp_port == 19300


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
    # 不带 --port 的命令（TickHub-US 的 collector）偏移不得改动命令本身
    monkeypatch.setattr(boundary, "_install_root", lambda: tmp_path)
    (tmp_path / "LadderStock").mkdir()

    resolved = resolve_peer_control({"LDST_PORT_OFFSET": str(TEST_OFFSET)})

    assert resolved["tickhub_us"].start_commands[1] == PEER_CONTROL["tickhub_us"].start_commands[1]
    assert resolved["tickhub_us"].start_commands[1] == ("th-us-collector",)


def test_every_peer_shifts_with_offset(monkeypatch, tmp_path) -> None:
    """所有带端口的 peer 都随 offset 平移，不再有「外部托管」例外。

    2026-11-18 用户裁决（连续两次）：Qdrant 正式段也去 Docker，FC 改成 LadderStock
    的本地增益包、ProxyFleet 改 venv 服务——三者都是本实例自有的副本，「全机器只有
    一份、测试服与正式服共用」的前提已经不存在，因此 `external_hosted` 这条例外
    不再被任何 peer 声明。

    库态 peer（memory）不在此列：它没有服务，mcp_port 的 0 只是占位。
    """
    monkeypatch.setattr(boundary, "_install_root", lambda: tmp_path)
    (tmp_path / "LadderStock").mkdir()

    resolved = resolve_peer_control({"LDST_PORT_OFFSET": str(TEST_OFFSET)})
    base = resolve_peer_control({"LDST_PORT_OFFSET": "0"})

    for key, ctl in PEER_CONTROL.items():
        assert ctl.external_hosted is None, f"{key} 仍声明了 external_hosted 例外"
        if ctl.library_only:
            continue
        assert resolved[key].mcp_port == base[key].mcp_port + TEST_OFFSET
        if ctl.web_port is not None:
            assert resolved[key].web_port == base[key].web_port + TEST_OFFSET
        assert resolved[key].extra_ports == tuple(
            port + TEST_OFFSET for port in ctl.extra_ports
        )


def test_offset_is_positive_int_only(monkeypatch) -> None:
    monkeypatch.setenv(PORTS_OFFSET_ENV, "10.5")
    assert port_offset() == 0
