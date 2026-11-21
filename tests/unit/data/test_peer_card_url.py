"""服务发现的地址口径：卡优先、算术兜底、显式 env 最高（F12 补采实测暴露）。

2026-11-21 现场：5951 的覆盖看板「补采」真跑一次 valuation_history 回
`degraded / peer 全部不可用，回放本地缓存`——根因是 `build_peers` 只会把
`sources.yaml` 的 `default_url` 按 `+10000` 平移，而 tickhub 实际监听 8000
（自述卡里写的也是 8000）。卡不参与解析时，读数层必然敲空门。
"""

from __future__ import annotations

import types

from ladderstock.data.provider_registry import build_peers


def _doc(url: str = "http://127.0.0.1:8000/mcp", *, env_name: str = "LDST_TICKHUB_MCP_URL") -> dict:
    return {
        "peers": {
            "tickhub": {
                "product": "TickHub-CN",
                "mcp_name": "tickhub-cn",
                "url_env": env_name,
                "default_url": url,
                "timeout_sec": 10,
            }
        }
    }


def test_card_port_wins_over_offset(monkeypatch) -> None:
    monkeypatch.setenv("LDST_PORT_OFFSET", "10000")
    monkeypatch.delenv("LDST_TICKHUB_MCP_URL", raising=False)
    controls = {"tickhub": types.SimpleNamespace(mcp_port=8000)}
    peer = build_peers(_doc(), controls=controls)["tickhub"]
    # 卡上的 8000 是实际监听端口：用它时不再平移（平移会得到 18000 的空门）
    assert peer.url == "http://127.0.0.1:8000/mcp"


def test_offset_arithmetic_when_no_card(monkeypatch) -> None:
    monkeypatch.setenv("LDST_PORT_OFFSET", "10000")
    monkeypatch.delenv("LDST_TICKHUB_MCP_URL", raising=False)
    peer = build_peers(_doc(), controls={})["tickhub"]
    # 卡缺失/失效时退回算术兜底，语义与旧口径一致
    assert peer.url == "http://127.0.0.1:18000/mcp"


def test_cards_off_env_keeps_arithmetic(monkeypatch) -> None:
    monkeypatch.setenv("LDST_PORT_OFFSET", "10000")
    monkeypatch.setenv("LDST_PEER_CARDS", "0")
    monkeypatch.delenv("LDST_TICKHUB_MCP_URL", raising=False)
    peer = build_peers(_doc())["tickhub"]
    assert peer.url == "http://127.0.0.1:18000/mcp"


def test_url_env_wins_over_card_and_offset(monkeypatch) -> None:
    monkeypatch.setenv("LDST_PORT_OFFSET", "10000")
    monkeypatch.setenv("LDST_TICKHUB_MCP_URL", "http://10.0.0.5:9999/mcp")
    controls = {"tickhub": types.SimpleNamespace(mcp_port=8000)}
    peer = build_peers(_doc(), controls=controls)["tickhub"]
    # 显式配置的地址不平移、也不被卡覆盖
    assert peer.url == "http://10.0.0.5:9999/mcp"


def test_card_port_keeps_path_and_host(monkeypatch) -> None:
    monkeypatch.setenv("LDST_PORT_OFFSET", "10000")
    monkeypatch.delenv("LDST_TICKHUB_MCP_URL", raising=False)
    controls = {"tickhub": types.SimpleNamespace(mcp_port=8765)}
    peer = build_peers(_doc("http://127.0.0.1:8000/tools/mcp/"), controls=controls)["tickhub"]
    assert peer.url == "http://127.0.0.1:8765/tools/mcp/"
