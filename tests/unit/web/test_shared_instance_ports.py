"""共用主实例的端口回退：平移端口没有副本时，按基础端口判「运行中」。

2026-11-21 现场：5951（`LDST_PORT_OFFSET=10000`）把面板判成「8 个模块 · 运行中 1」，
用户看到的结论是「子模块全部启动失败」。事实是 6 个模块都跑在基础端口
（8000/8002/8790/8765/8788），只有一份共用实例；面板只按平移后的端口
（18000/18790/…）探测，敲的全是空端口。这里锁住三条口径：

1. 平移端口无人监听、基础端口有人 → 判「运行中」，端口展示真实值，并注明共用实例；
2. 平移端口有人监听（真起了自有副本）→ 不回退，照旧按声明口径；
3. 偏移为 0（正式服）→ 完全不介入。
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from ladderstock.core.boundary import PeerControl
from ladderstock.web.control import ControlPlane, PortOwner


def _peer(directory: Path) -> PeerControl:
    """测试服口径：mcp 声明 18000（= 基础 8000 + 10000），admin 18787。"""
    return PeerControl(
        key="tickhub",
        directory=str(directory),
        import_package="tickhub_pkg",
        mcp_port=18000,
        web_port=18787,
        web_port_label="Admin",
        health_path="/healthz",
        start_commands=(("tickhub", "mcp", "http"),),
        config_keys=(),
    )


def _plane(tmp_path: Path, ports: dict[int, list[PortOwner]]) -> ControlPlane:
    peer_dir = tmp_path / "TickHub-CN"
    peer_dir.mkdir(parents=True, exist_ok=True)
    return ControlPlane(
        data_root=tmp_path,
        peers={"tickhub": _peer(peer_dir)},
        components={},
        sources_loader=lambda: {"tickhub": {"product": "TickHub-CN"}},
        run=lambda *args, **kwargs: SimpleNamespace(ok=True, output="", code=0),
        port_probe=lambda port: ports.get(port, []),
        health_probe=lambda url, timeout: (True, "HTTP 200"),
        spawn=lambda args, *, cwd, log_path: SimpleNamespace(pid=4242),
        http_post=lambda url, payload, headers, timeout: (200, "{}"),
        process_info=lambda pid, cwd: (None, None),
        process_started=lambda pid: datetime(2026, 11, 21, tzinfo=UTC),
        resolve=lambda host: ["127.0.0.1"],
    )


def test_falls_back_to_shared_instance_on_base_port(tmp_path, monkeypatch):
    """共用实例在 8000：判运行中，端口显示 8000，并注明共用。"""
    monkeypatch.setenv("LDST_ENV", "test")
    monkeypatch.setenv("LDST_PORT_OFFSET", "10000")
    owner = PortOwner(pid=7572, process="python.exe")
    plane = _plane(tmp_path, {8000: [owner], 8787: [owner]})

    snap = plane.snapshot("tickhub")

    assert snap["status"] == "running"
    assert snap["ports"]["mcp"] == 8000
    assert snap["ports"]["web"] == 8787
    assert snap["ports"]["shared_instance"] is True
    assert snap["ports"]["live"] == {"18000": 8000, "18787": 8787}
    assert "共用主实例" in snap["detail"]


def test_own_copy_on_shifted_port_is_not_fallback(tmp_path, monkeypatch):
    """真起了自有副本（18000/18787）：不回退，照旧按声明口径。"""
    monkeypatch.setenv("LDST_ENV", "test")
    monkeypatch.setenv("LDST_PORT_OFFSET", "10000")
    owner = PortOwner(pid=9001, process="python.exe")
    plane = _plane(tmp_path, {18000: [owner], 18787: [owner]})

    snap = plane.snapshot("tickhub")

    assert snap["status"] == "running"
    assert snap["ports"]["mcp"] == 18000
    assert snap["ports"]["shared_instance"] is False
    assert "共用主实例" not in snap["detail"]


def test_no_offset_means_zero_change(tmp_path, monkeypatch):
    """正式服（偏移 0）：声明端口无监听就如实报未启动，不试基础端口。"""
    monkeypatch.delenv("LDST_PORT_OFFSET", raising=False)
    monkeypatch.setenv("LDST_ENV", "prod")
    owner = PortOwner(pid=7572, process="python.exe")
    plane = _plane(tmp_path, {8000: [owner], 8787: [owner]})

    snap = plane.snapshot("tickhub")

    assert snap["status"] != "running"
    assert snap["ports"]["mcp"] == 18000
    assert snap["ports"]["shared_instance"] is False


@pytest.mark.parametrize(("offset", "expected"), [("10000", 8000), ("0", 18000)])
def test_registry_row_follows_live_port(tmp_path, monkeypatch, offset, expected):
    """注册表那一行的端口与卡片同源：有实际端口就跟随它。"""
    monkeypatch.setenv("LDST_ENV", "test")
    monkeypatch.setenv("LDST_PORT_OFFSET", offset)
    owner = PortOwner(pid=7572, process="python.exe")
    plane = _plane(tmp_path, {8000: [owner], 8787: [owner]})

    snap = plane.snapshot("tickhub")
    row = plane._registry_row("tickhub", {"default_port": 8000}, snap)

    assert row["default_port"] == expected


def test_card_written_after_panel_start_is_picked_up(tmp_path, monkeypatch):
    """面板先起、模块后登记：下一轮快照就要按新卡判，不能定格在启动时的口径。

    现场形态：面板 08:10 启动（模块还没写卡），模块 08:12 重启并登记真实端口。
    如果只在构造 `ControlPlane` 时解析一次，面板会一直显示「按端口推算」。
    """
    import ladderstock.web.control as control_mod
    from ladderstock.core import boundary, discovery

    peer_dir = tmp_path / "TickHub-CN"
    peer_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("LDST_ENV", "test")
    monkeypatch.setenv("LDST_PORT_OFFSET", "10000")
    # 只认这张卡上的两个端口在监听（不看真实 socket，避免依赖本机进程）
    live = {8000, 8787}
    monkeypatch.setattr(discovery, "port_open", lambda port, timeout=0.4: port in live)
    # 模拟真实的「平移 + 读卡」解析链：解析函数本身不变，只是不读真实 peer 表
    monkeypatch.setattr(
        control_mod,
        "resolve_peer_control",
        lambda: {"tickhub": boundary._apply_card(_peer(peer_dir))},
    )
    discovery.clear_cache()

    owner = PortOwner(pid=7572, process="python.exe")
    plane = ControlPlane(
        data_root=tmp_path,
        components={},
        sources_loader=lambda: {"tickhub": {"product": "TickHub-CN"}},
        run=lambda *args, **kwargs: SimpleNamespace(ok=True, output="", code=0),
        port_probe=lambda port: [owner] if port in live else [],
        health_probe=lambda url, timeout: (True, "HTTP 200"),
        spawn=lambda args, *, cwd, log_path: SimpleNamespace(pid=4242),
        http_post=lambda url, payload, headers, timeout: (200, "{}"),
        process_info=lambda pid, cwd: (None, None),
        process_started=lambda pid: datetime(2026, 11, 21, tzinfo=UTC),
        resolve=lambda host: ["127.0.0.1"],
    )

    before = plane.snapshot("tickhub")
    assert before["ports"]["discovered"] is False

    discovery.write_card(peer_dir, module="tickhub", ports={"mcp": 8000, "web": 8787})
    discovery.clear_cache()

    after = plane.snapshot("tickhub")
    assert after["ports"]["discovered"] is True
    assert after["ports"]["mcp"] == 8000
    assert after["ports"]["web"] == 8787
