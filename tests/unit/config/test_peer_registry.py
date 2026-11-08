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
        ("proxyfleet", "/readyz"),
    ],
)
def test_yaml_carries_real_health_paths(key: str, health_path: str) -> None:
    """健康检查路径写进 yaml：三个模块三种路径，不许再写死 /healthz（QUOT-403）。"""
    assert _sources_doc()["peers"][key]["health_path"] == health_path


def test_all_registered_peers_have_repo_for_version_check() -> None:
    """QUOT-402 版本三态检测依赖 repo 字段，注册表里每个 peer 都要带。

    mem0 例外（QUOT-486）：它是本地 WSL compose 部署单元，版本真源是
    qf-mem0/mem0 镜像，不在任何 GitHub Release 上，填上游仓库只会让三态检测
    显示失真的落后/超前，因此显式豁免而不是填一个假 repo。
    proxyfleet 例外（QUOT-485）：本机 D:\\workplace\\ProxyFleet 工作副本，同理豁免。
    qdrant 同理（QUOT-488）：部署单元在 LadderStock 仓的部署目录
    D:\\QuotaFuseModules\\Qdrant（镜像 qdrant/qdrant + 本地 build 的 mcp 镜像），
    版本真源是 compose 里的镜像 tag，同样豁免。
    """
    for key, item in _sources_doc()["peers"].items():
        if key in ("mem0", "proxyfleet", "qdrant"):
            assert item.get("repo") is None
            continue
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


def test_yaml_peers_block_registers_proxyfleet() -> None:
    """QUOT-485：ProxyFleet 从只读组件升级为纳管 peer，登记进 peers 块。"""
    block = _sources_doc()["peers"]
    assert "proxyfleet" in block
    pf = block["proxyfleet"]
    assert pf["product"] == "ProxyFleet"
    assert pf["package"] == "proxyfleet"
    assert pf["cli"] == "pfctl"
    assert pf["mcp_name"] is None  # 面板不直连；LS 侧走 proxyfleet_* 薄封装工具
    assert pf["url_env"] == "LDST_PROXYFLEET_API_URL"
    assert pf["health_path"] == "/readyz"
    assert pf["default_port"] == 8788
    assert pf["repo"] is None


def test_proxyfleet_is_never_a_dataset_source() -> None:
    """QUOT-485 边界红线：纳管 ≠ 取数通道，代理池不许绑数据集、不许被 via。

    四层边界的保护点在 DATASET_CATALOG：升级为 peer 后它具备「被 via」的形式
    资格（PEERS 含它），必须显式守住「没有任何数据集归属它 / sources 不 via 它」。
    """
    from ladderstock.core.boundary import DATASET_CATALOG

    doc = _sources_doc()
    offenders = [s["id"] for s in doc["sources"] if s.get("via") == "proxyfleet"]
    assert offenders == [], f"sources 不许 via proxyfleet：{offenders}"
    bound = [d for d, b in DATASET_CATALOG.items() if b.peer == "proxyfleet"]
    assert bound == [], f"代理池不许绑定数据集：{bound}"


# --------------------------------------------------------------------------- #
# 3. 配置三态：合法 / 非法 / 缺文件（都不许崩、不许变空）
# --------------------------------------------------------------------------- #


def test_build_peers_falls_back_when_block_missing() -> None:
    for block in (None, {}, "not-a-mapping"):
        peers, warnings = build_peers(block)
        assert peers == build_peers(None)[0]
        assert len(peers) == 8
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
