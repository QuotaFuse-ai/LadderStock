"""便携包端口自动选择的单测（probe 注入，不碰真实端口）。"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGING = REPO_ROOT / "packaging"
if str(PACKAGING) not in sys.path:
    sys.path.insert(0, str(PACKAGING))

import port_probe  # noqa: E402 - 先插路径再导入


def test_prefers_base_port_when_free() -> None:
    assert port_probe.pick_base_port(5950, probe=lambda p: p == 5950) == 5950


def test_shifts_by_step_when_occupied() -> None:
    occupied = {5950, 5960}

    picked = port_probe.pick_base_port(5950, probe=lambda p: p not in occupied)

    assert picked == 5970


def test_raises_instead_of_silently_picking_bad_port() -> None:
    with pytest.raises(RuntimeError):
        port_probe.pick_base_port(5950, probe=lambda p: False, attempts=3)


def test_offset_matches_existing_mechanism() -> None:
    # 正式服：零偏移，全链路原样
    assert port_probe.offset_for(5950) == 0
    # 基端口后移两档 → 全 peer 端口一起 +20
    assert port_probe.offset_for(5970) == 20
    # 测试服那套 8000 → 18000 也是同一公式
    assert port_probe.offset_for(18000, reference=8000) == 10000
    # 比参考端口还低时不反向平移
    assert port_probe.offset_for(5000) == 0


def test_real_probe_reports_a_bound_port_as_busy() -> None:
    """真实 bind 路径：占住一个端口后，探测应判定为不可用。"""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        busy_port = sock.getsockname()[1]
        assert port_probe.port_is_free(busy_port) is False
    # 释放之后同一个端口应可用
    assert port_probe.port_is_free(busy_port) is True
