"""自述卡（服务发现）的契约：模块登记真实地址，客户端查表而不是算端口。

2026-11-21 现场：5951 按 `+10000` 推算，去敲 18000/18790/18765 全空，把 6 个在跑的
模块判成「已停止」。修法是让「谁提供服务谁登记地址」，算术降级为兜底。
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from ladderstock.core import discovery
from ladderstock.core.boundary import PEER_CONTROL, _apply_card


@pytest.fixture(autouse=True)
def _clear_cache():
    discovery.clear_cache()
    yield
    discovery.clear_cache()


def test_write_then_read_card_roundtrip(tmp_path):
    path = discovery.write_card(
        tmp_path,
        module="tickhub",
        ports={"mcp": 18000, "web": 18787},
        product="TickHub-CN",
        env="test",
        pid=6432,
        version="1.1.0",
    )

    assert path == discovery.card_path(tmp_path)
    card = discovery.read_card(tmp_path)
    assert card is not None
    assert card["module"] == "tickhub"
    assert card["mcp"] == 18000
    assert card["web"] == 18787
    assert card["version"] == "1.1.0"


def test_card_without_ports_is_ignored(tmp_path):
    discovery.write_card(tmp_path / "m", module="wire", ports={})
    assert discovery.read_card(tmp_path / "m") is None


def test_broken_json_is_ignored(tmp_path):
    path = discovery.card_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{ not json", encoding="utf-8")
    assert discovery.read_card(tmp_path) is None


def test_resolve_requires_a_listening_port(tmp_path, monkeypatch):
    discovery.write_card(tmp_path, module="tickhub", ports={"mcp": 18000})
    monkeypatch.setattr(discovery, "port_open", lambda port, timeout=0.4: True)
    assert discovery.resolve(tmp_path) is not None

    discovery.clear_cache()
    monkeypatch.setattr(discovery, "port_open", lambda port, timeout=0.4: False)
    assert discovery.resolve(tmp_path) is None


def test_resolve_cache_avoids_repeated_probe(tmp_path, monkeypatch):
    discovery.write_card(tmp_path, module="tickhub", ports={"mcp": 18000})
    calls: list[int] = []

    def _open(port, timeout=0.4):
        calls.append(port)
        return True

    monkeypatch.setattr(discovery, "port_open", _open)
    discovery.resolve(tmp_path)
    discovery.resolve(tmp_path)
    assert calls == [18000]  # 第二次走缓存

    discovery.clear_cache()
    discovery.resolve(tmp_path)
    assert calls == [18000, 18000]


def test_boundary_card_overrides_shifted_ports(tmp_path, monkeypatch):
    """卡上有真实地址 → 用卡上的端口，并把 discovered 标成 True。"""
    ctl = replace(PEER_CONTROL["tickhub"], directory=str(tmp_path), mcp_port=18000, web_port=18787)
    discovery.write_card(tmp_path, module="tickhub", ports={"mcp": 8000, "web": 8787})
    monkeypatch.setattr(discovery, "port_open", lambda port, timeout=0.4: True)

    resolved = _apply_card(ctl)

    assert resolved.mcp_port == 8000
    assert resolved.web_port == 8787
    assert resolved.discovered is True


def test_boundary_without_card_keeps_arithmetic(tmp_path):
    """没有卡 → 一个字段都不改（旧口径零回归）。"""
    ctl = replace(PEER_CONTROL["tickhub"], directory=str(tmp_path), mcp_port=18000, web_port=18787)

    resolved = _apply_card(ctl)

    assert resolved.mcp_port == 18000
    assert resolved.web_port == 18787
    assert resolved.discovered is False


def test_boundary_stale_card_falls_back(tmp_path, monkeypatch):
    """卡过期（端口连不上）→ 仍按推算口径，不采信陈旧登记。"""
    ctl = replace(PEER_CONTROL["tickhub"], directory=str(tmp_path), mcp_port=18000, web_port=18787)
    discovery.write_card(tmp_path, module="tickhub", ports={"mcp": 8000})
    monkeypatch.setattr(discovery, "port_open", lambda port, timeout=0.4: False)

    resolved = _apply_card(ctl)

    assert resolved.mcp_port == 18000
    assert resolved.discovered is False
