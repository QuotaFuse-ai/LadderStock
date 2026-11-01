from __future__ import annotations

import ladderstock.core.boundary as boundary
from ladderstock.core.boundary import PEER_CONTROL, resolve_peer_control


def test_env_override_wins(monkeypatch, tmp_path) -> None:
    # 环境变量优先于同级目录探测，且只认固定键
    monkeypatch.setattr(boundary, "_REPO_ROOT", tmp_path / "LadderStock")
    (tmp_path / "LadderStock").mkdir()
    (tmp_path / "TickHub-CN").mkdir()

    resolved = resolve_peer_control({"LDST_PEER_DIR_TICKHUB": r"D:\peers\TickHub-CN"})

    assert resolved["tickhub"].directory == r"D:\peers\TickHub-CN"


def test_sibling_directory_discovered(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(boundary, "_REPO_ROOT", tmp_path / "LadderStock")
    (tmp_path / "LadderStock").mkdir()
    (tmp_path / "TickHub-CN").mkdir()
    (tmp_path / "quotafuse-wire").mkdir()

    resolved = resolve_peer_control({})

    assert resolved["tickhub"].directory == str(tmp_path / "TickHub-CN")
    assert resolved["wire"].directory == str(tmp_path / "quotafuse-wire")


def test_missing_directory_keeps_placeholder(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(boundary, "_REPO_ROOT", tmp_path / "LadderStock")
    (tmp_path / "LadderStock").mkdir()

    resolved = resolve_peer_control({"LDST_PEER_DIR_SENATE": "   "})

    assert resolved["senate"] is PEER_CONTROL["senate"]
