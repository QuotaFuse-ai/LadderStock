"""QUOT-403：sources.yaml 的 peers 块是 peer 注册表的唯一真源。

覆盖（issue 验收口径）：
1. yaml peers 块登记了 financecoffer（第 5 个 peer）；
2. boundary.PEERS 由 yaml 构造，与 yaml 逐字段一致；
3. 配置三态：合法（含 FC）/ 非法 yaml（回退内置默认 + warning 带原因）/ 缺文件（同前）；
4. `config validate` 用的 JSON Schema 接受现行 sources.yaml（peers 块动态化后不再枚举键）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator

from ladderstock.cli.paths import project_root
from ladderstock.core.boundary import (
    PEER_CONTROL,
    PEER_DIR_ENV,
    PEERS,
    build_peers,
    load_peers_from_dir,
)

CONFIG = project_root() / "config"


def _sources_doc() -> dict[str, Any]:
    return yaml.safe_load((CONFIG / "sources.yaml").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# 1. yaml 登记了 financecoffer
# --------------------------------------------------------------------------- #


def test_yaml_peers_block_registers_financecoffer() -> None:
    block = _sources_doc()["peers"]
    assert "financecoffer" in block
    fc = block["financecoffer"]
    assert fc["product"] == "FinanceCoffer"
    assert fc["package"] == "financecoffer"
    assert fc["cli"] == "fc"
    assert fc["mcp_name"] is None  # 没有 MCP 面，探测走 HTTP
    assert fc["url_env"] == "LDST_FINANCECOFFER_URL"
    assert fc["health_path"] == "/health"
    assert fc["default_port"] == 8100
    assert fc["repo"] == "QuotaFuse-ai/FinanceCoffer"


@pytest.mark.parametrize(
    ("key", "health_path"),
    [
        ("tickhub", "/healthz"),
        ("tickhub_us", "/healthz"),
        ("wire", "/api/health"),
        ("senate", "/healthz"),
        ("financecoffer", "/health"),
    ],
)
def test_yaml_carries_real_health_paths(key: str, health_path: str) -> None:
    """健康检查路径写进 yaml：三个模块三种路径，不许再写死 /healthz（QUOT-403）。"""
    assert _sources_doc()["peers"][key]["health_path"] == health_path


def test_all_registered_peers_have_repo_for_version_check() -> None:
    """QUOT-402 版本三态检测依赖 repo 字段，注册表里每个 peer 都要带。"""
    for key, item in _sources_doc()["peers"].items():
        assert item.get("repo"), f"peers.{key} 缺 repo 字段"


# --------------------------------------------------------------------------- #
# 2. boundary.PEERS 与 yaml 一致
# --------------------------------------------------------------------------- #


def test_boundary_peers_match_yaml_block() -> None:
    built, warnings = build_peers(_sources_doc()["peers"])
    assert warnings == []
    assert built == PEERS


def test_boundary_peers_include_financecoffer() -> None:
    fc = PEERS["financecoffer"]
    assert fc.mcp_name is None
    assert fc.health_path == "/health"
    assert fc.default_port == 8100
    assert fc.repo == "QuotaFuse-ai/FinanceCoffer"


# --------------------------------------------------------------------------- #
# 3. 配置三态：合法 / 非法 / 缺文件（都不许崩、不许变空）
# --------------------------------------------------------------------------- #


def test_build_peers_falls_back_when_block_missing() -> None:
    for block in (None, {}, "not-a-mapping"):
        peers, warnings = build_peers(block)
        assert peers == build_peers(None)[0]
        assert len(peers) == 5
        assert warnings and "回退内置默认" in warnings[0]


def test_load_peers_from_dir_missing_file_falls_back(tmp_path: Path) -> None:
    peers, warnings = load_peers_from_dir(tmp_path)
    assert peers == build_peers(None)[0]
    assert len(warnings) == 1
    assert "读取失败" in warnings[0] and "sources.yaml" in warnings[0]


def test_load_peers_from_dir_broken_yaml_falls_back(tmp_path: Path) -> None:
    (tmp_path / "sources.yaml").write_text("peers: [unclosed\n  bad: :", encoding="utf-8")
    peers, warnings = load_peers_from_dir(tmp_path)
    assert peers == build_peers(None)[0]
    assert warnings and "回退内置默认" in warnings[0]


def test_build_peers_skips_broken_entry_and_keeps_the_rest() -> None:
    block: dict[str, Any] = {
        "tickhub": {"product": "TickHub-CN", "package": "tickhub-cn", "cli": "th-cn"},
        "bad_string": "oops",
        "no_fields": {"product": "只有名字"},
    }
    peers, warnings = build_peers(block, fallbacks={})
    assert set(peers) == {"tickhub"}
    assert len(warnings) == 2


def test_build_peers_accepts_entry_without_cli_for_import() -> None:
    """QUOT-420 导入的 peer 不带命令字段：缺 cli 不能把已登记条目丢掉。"""
    block = {
        "gamma": {
            "product": "Gamma",
            "package": "gamma-pkg",
            "default_port": 8123,
            "health_path": "/healthz",
            "repo": "QuotaFuse-ai/Gamma",
        }
    }
    peers, warnings = build_peers(block, fallbacks={})
    assert warnings == []
    assert peers["gamma"].cli == ""
    assert peers["gamma"].default_port == 8123


def test_build_peers_empty_after_skips_falls_back() -> None:
    peers, warnings = build_peers({"bad": "oops"})
    assert peers == build_peers(None)[0]
    assert any("回退内置默认" in warning for warning in warnings)


def test_yaml_fields_backfill_from_fallbacks() -> None:
    """yaml 没带的字段（consumption 等）从内置默认按 key 回填，不猜。"""
    block = {"tickhub": {"product": "TickHub-CN", "package": "tickhub-cn", "cli": "th-cn"}}
    peers, _ = build_peers(block)
    assert peers["tickhub"].consumption == "只消费"
    assert peers["tickhub"].default_port == 8000
    assert peers["tickhub"].health_path == "/healthz"


# --------------------------------------------------------------------------- #
# 4. 控制面元数据（PeerControl）与注册表对齐
# --------------------------------------------------------------------------- #


def test_financecoffer_control_metadata() -> None:
    ctl = PEER_CONTROL["financecoffer"]
    assert ctl.repo == "QuotaFuse-ai/FinanceCoffer"
    assert ctl.mcp_port == 8100
    assert ctl.web_port is None
    assert ctl.health_path == "/health"
    assert ctl.start_commands == ()  # WSL 服务：控制面不接管启停
    assert ctl.managed is False
    assert PEER_DIR_ENV["financecoffer"] == "LDST_PEER_DIR_FINANCECOFFER"


def test_every_controlled_peer_has_dir_env_and_sibling() -> None:
    from ladderstock.core.boundary import PEER_SIBLING_DIRS

    for key in PEER_CONTROL:
        assert key in PEER_DIR_ENV, f"{key} 缺 PEER_DIR_ENV 条目"
        assert key in PEER_SIBLING_DIRS, f"{key} 缺 PEER_SIBLING_DIRS 条目"


# --------------------------------------------------------------------------- #
# 5. `ldst config validate` 的 Schema 接受现行 yaml
# --------------------------------------------------------------------------- #


def test_sources_yaml_passes_its_schema() -> None:
    schema = json.loads((CONFIG / "schema" / "sources.schema.json").read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(_sources_doc()),
        key=lambda e: list(e.path),
    )
    assert errors == [], [err.message for err in errors]


def test_schema_rejects_peer_entry_without_new_required_fields() -> None:
    """health_path / default_port / role 成为必填： peers 块不再是只写 URL 的半个登记。"""
    schema = json.loads((CONFIG / "schema" / "sources.schema.json").read_text(encoding="utf-8"))
    bad = {"peers": {"x": {"product": "X", "package": "x", "cli": "x"}}, "sources": []}
    errors = list(Draft202012Validator(schema).iter_errors(bad))
    assert errors
