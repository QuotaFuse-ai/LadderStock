"""平台工具注册表（C1 / QUOT-543）：真实发现、命名空间隔离、不编数。

全部用 tmp_path 当数据根、假传输当 peer：不拨网络，也不写仓库 `data/`。
假传输返回的工具形状照抄真实 `tools/list`（name / description / inputSchema），
注册表只做「如实聚合」，所以测试断言的是**聚合逻辑**而不是某天的 TickHub 名单。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from ladderstock.ai import tool_registry
from ladderstock.ai.tool_registry import (
    LOCAL_SOURCE,
    STATUS_OK,
    STATUS_UNREACHABLE,
    ToolNamespace,
    ToolRegistry,
    ToolRegistryError,
)
from ladderstock.cli.main import app
from ladderstock.data.provider_registry import SourceUnavailable
from ladderstock.errors import EXIT_USAGE
from ladderstock.mcp.tools import TOOL_SPECS

runner = CliRunner()

AGENT = "ladderstock-research"
SCOPE = "research"

SOURCES: dict[str, Any] = {
    "peers": {
        "tickhub": {
            "product": "TickHub-CN",
            "mcp_name": "tickhub-cn",
            "default_url": "http://127.0.0.1:8000/mcp",
            "timeout_sec": 10,
        },
        "tickhub_us": {
            "product": "TickHub-US",
            "mcp_name": "tickhub-us",
            "default_url": "http://127.0.0.1:8002/mcp",
            "timeout_sec": 10,
        },
        "wire": {
            "product": "QuotaFuse Wire",
            "mcp_name": "qf-wire",
            "default_url": "http://127.0.0.1:8791/mcp",
            "timeout_sec": 10,
        },
        # 没有 MCP 面的 peer 不得出现在发现列表里（mem0 只有 REST）
        "mem0": {
            "product": "Mem0",
            "mcp_name": None,
            "default_url": "http://127.0.0.1:9100",
        },
    }
}


def tickhub_tool(name: str, description: str = "A 股行情工具") -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "tier": {"type": "string"},
            },
            "required": ["code"],
        },
    }


TICKHUB_TOOLS = [
    tickhub_tool("em_kline", "东方财富日线 K 线"),
    tickhub_tool("spot_quote", "实时行情快照"),
    tickhub_tool("dragon_tiger", "龙虎榜"),
]
WIRE_TOOLS = [
    {
        "name": "wire_search",
        "description": "资讯检索",
        "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
    }
]


class FakeCatalog:
    """按 peer 返回固定清单；`fail` 里的 peer 一律抛不可达。"""

    def __init__(
        self,
        by_peer: dict[str, list[dict[str, Any]]],
        *,
        fail: set[str] | None = None,
    ) -> None:
        self.by_peer = by_peer
        self.fail = fail or set()
        self.calls: list[str] = []

    def list_tools(self, peer: Any, *, timeout: float) -> list[dict[str, Any]]:
        self.calls.append(peer.key)
        if peer.key in self.fail:
            raise SourceUnavailable(f"{peer.product} 不可达")
        return list(self.by_peer.get(peer.key, []))


def make_registry(
    tmp_path: Path,
    *,
    by_peer: dict[str, list[dict[str, Any]]] | None = None,
    fail: set[str] | None = None,
    transport: FakeCatalog | None = None,
) -> tuple[ToolRegistry, FakeCatalog, dict[str, list[dict[str, Any]]]]:
    catalog = by_peer or {"tickhub": TICKHUB_TOOLS, "wire": WIRE_TOOLS}
    fake = transport or FakeCatalog(catalog, fail=fail)
    registry = ToolRegistry(root=tmp_path, sources=SOURCES, transport=fake, timeout=0.1)
    return registry, fake, catalog


# --------------------------------------------------------------------------- #
# 真实形状与计数
# --------------------------------------------------------------------------- #

def test_discovery_mirrors_peer_tools_list(tmp_path: Path) -> None:
    registry, _, catalog = make_registry(tmp_path)
    snapshot = registry.query(ToolNamespace(AGENT, SCOPE), refresh=True)

    tickhub = [entry for entry in snapshot.tools if entry.source == "tickhub"]
    assert [entry.name for entry in tickhub] == sorted(t["name"] for t in catalog["tickhub"])
    assert "em_kline" in {entry.name for entry in tickhub}

    status = next(item for item in snapshot.sources if item.source == "tickhub")
    assert status.status == STATUS_OK
    assert status.tool_count == len(catalog["tickhub"])
    assert len(tickhub) == status.tool_count

    kline = next(entry for entry in snapshot.tools if entry.name == "em_kline")
    assert kline.parameters["required"] == ["code"]
    assert kline.product == "TickHub-CN"
    assert kline.read_only is None  # peer 没声明读写属性，注册表不替它编


def test_local_mcp_tools_are_included_with_their_schema(tmp_path: Path) -> None:
    registry, _, _ = make_registry(tmp_path)
    snapshot = registry.query(ToolNamespace(AGENT, SCOPE), refresh=True)

    local = {entry.name: entry for entry in snapshot.tools if entry.source == LOCAL_SOURCE}
    assert set(local) == {spec.name for spec in TOOL_SPECS}
    assert local["candidate_pool"].read_only is True
    assert local["candidate_pool"].parameters == next(
        spec.input_schema for spec in TOOL_SPECS if spec.name == "candidate_pool"
    )
    status = next(item for item in snapshot.sources if item.source == LOCAL_SOURCE)
    assert status.status == STATUS_OK
    assert status.tool_count == len(TOOL_SPECS)


def test_non_mcp_peers_are_not_probed(tmp_path: Path) -> None:
    registry, fake, _ = make_registry(tmp_path)
    registry.discover(ToolNamespace(AGENT, SCOPE))
    assert "mem0" not in fake.calls
    assert "mem0" not in {peer.key for peer in registry.mcp_peers()}


def test_unreachable_peer_contributes_nothing(tmp_path: Path) -> None:
    registry, _, _ = make_registry(
        tmp_path,
        by_peer={"tickhub": TICKHUB_TOOLS},
        fail={"tickhub", "tickhub_us", "wire"},
    )
    snapshot = registry.query(ToolNamespace(AGENT, SCOPE), refresh=True)

    assert {entry.source for entry in snapshot.tools} == {LOCAL_SOURCE}
    broken = next(item for item in snapshot.sources if item.source == "tickhub")
    assert broken.status == STATUS_UNREACHABLE
    assert broken.tool_count == 0
    assert "不可达" in (broken.error or "")


def test_snapshot_is_written_only_under_declared_namespace(tmp_path: Path) -> None:
    registry, _, _ = make_registry(tmp_path)
    registry.query(ToolNamespace(AGENT, SCOPE), refresh=True)

    path = registry.snapshot_path(ToolNamespace(AGENT, SCOPE))
    assert path.is_file()
    assert path == tmp_path / "ai" / "tool_registry" / AGENT / "research.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["agent_id"] == AGENT
    assert document["kb_scope"] == SCOPE
    assert document["version"] == tool_registry.SNAPSHOT_VERSION
    loaded = registry.load(ToolNamespace(AGENT, SCOPE))
    assert loaded is not None
    assert len(document["tools"]) == len(loaded.tools)

    other = registry.snapshot_path(ToolNamespace("ladderstock-wire", "wire"))
    assert not other.exists()


# --------------------------------------------------------------------------- #
# 命名空间隔离
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("agent_id", "kb_scope"),
    [("", SCOPE), (AGENT, ""), ("../escape", SCOPE), (AGENT, "../escape"), ("a b", SCOPE)],
)
def test_namespace_must_be_complete_and_safe(agent_id: str, kb_scope: str) -> None:
    with pytest.raises(ToolRegistryError) as excinfo:
        ToolNamespace(agent_id, kb_scope)
    assert excinfo.value.code == EXIT_USAGE


def test_wildcard_scope_is_escaped_in_file_name(tmp_path: Path) -> None:
    registry, _, _ = make_registry(tmp_path)
    namespace = ToolNamespace(AGENT, "*")
    registry.query(namespace, refresh=True)
    assert registry.snapshot_path(namespace).name == "~.json"


def test_one_namespace_never_reads_another(tmp_path: Path) -> None:
    registry, _, _ = make_registry(tmp_path)
    registry.query(ToolNamespace(AGENT, SCOPE), refresh=True)

    # 第二个命名空间的传输全挂：拿不到 tickhub 只是它自己的状态，
    # 绝不允许它读走第一个命名空间快照里的工具
    other, _, _ = make_registry(tmp_path, fail={"tickhub", "tickhub_us", "wire"})
    snapshot = other.query(ToolNamespace("ladderstock-wire", "wire"))

    assert {entry.source for entry in snapshot.tools} == {LOCAL_SOURCE}
    assert all(entry.source != "tickhub" for entry in snapshot.tools)


def test_cross_namespace_snapshot_is_refused(tmp_path: Path) -> None:
    registry, _, _ = make_registry(tmp_path)
    source = ToolNamespace(AGENT, SCOPE)
    registry.query(source, refresh=True)

    # 把 A 的快照拷到 B 的路径：文档里的命名空间对不上，读取必须拒绝
    target = registry.snapshot_path(ToolNamespace("ladderstock-wire", "wire"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        registry.snapshot_path(source).read_text(encoding="utf-8"), encoding="utf-8"
    )

    with pytest.raises(ToolRegistryError) as excinfo:
        registry.load(ToolNamespace("ladderstock-wire", "wire"))
    assert "命名空间" in excinfo.value.message


def test_corrupt_snapshot_is_reported_not_guessed(tmp_path: Path) -> None:
    registry, _, _ = make_registry(tmp_path)
    path = registry.snapshot_path(ToolNamespace(AGENT, SCOPE))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(ToolRegistryError) as excinfo:
        registry.load(ToolNamespace(AGENT, SCOPE))
    assert "--refresh" in excinfo.value.message


# --------------------------------------------------------------------------- #
# 检索
# --------------------------------------------------------------------------- #

def test_query_filters_by_text_source_and_limit(tmp_path: Path) -> None:
    registry, _, _ = make_registry(tmp_path)
    namespace = ToolNamespace(AGENT, SCOPE)

    hits = registry.query(namespace, text="KLINE", refresh=True)
    assert [entry.name for entry in hits.tools] == ["em_kline"]

    wire = registry.query(namespace, source="wire")
    assert [entry.name for entry in wire.tools] == ["wire_search"]
    assert all(entry.source == "wire" for entry in wire.tools)

    limited = registry.query(namespace, source="tickhub", limit=1)
    assert len(limited.tools) == 1
    assert limited.total == len(TICKHUB_TOOLS)


def test_unknown_source_is_a_usage_error(tmp_path: Path) -> None:
    registry, _, _ = make_registry(tmp_path)
    with pytest.raises(ToolRegistryError) as excinfo:
        registry.query(ToolNamespace(AGENT, SCOPE), source="tickhub-us")
    assert excinfo.value.code == EXIT_USAGE


def test_query_uses_snapshot_until_refresh(tmp_path: Path) -> None:
    registry, fake, _ = make_registry(tmp_path)
    namespace = ToolNamespace(AGENT, SCOPE)

    registry.query(namespace, refresh=True)
    before = len(registry.query(namespace).tools)

    fake.by_peer["tickhub"] = [*TICKHUB_TOOLS, tickhub_tool("new_tool")]
    assert len(registry.query(namespace).tools) == before  # 未 refresh：读快照

    refreshed = registry.query(namespace, refresh=True)
    assert len(refreshed.tools) == before + 1
    assert "new_tool" in {entry.name for entry in refreshed.tools}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

@pytest.fixture()
def cli_env(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeCatalog:
    """CLI 走真实 `config/sources.yaml` 的 peer 名，但传输换成假目录。"""
    fake = FakeCatalog({"tickhub": TICKHUB_TOOLS, "wire": WIRE_TOOLS}, fail={"tickhub_us"})
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("ladderstock.ai.tool_registry.build_transport", lambda _peers: fake)
    return fake


def test_cli_lists_tools_as_json(cli_env: FakeCatalog, tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        [
            "ai", "tools", "list",
            "--agent-id", AGENT,
            "--kb-scope", SCOPE,
            "--refresh",
            "--output", "json",
        ],
    )
    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["agent_id"] == AGENT
    assert payload["kb_scope"] == SCOPE
    names = {entry["name"] for entry in payload["tools"]}
    assert "em_kline" in names
    statuses = {item["source"]: item for item in payload["sources"]}
    assert statuses["tickhub"]["tool_count"] == len(TICKHUB_TOOLS)
    assert statuses["tickhub_us"]["status"] == STATUS_UNREACHABLE
    assert (tmp_path / "ai" / "tool_registry" / AGENT / "research.json").is_file()


def test_cli_requires_both_namespace_fields(cli_env: FakeCatalog) -> None:
    result = runner.invoke(app, ["ai", "tools", "list", "--kb-scope", SCOPE])
    assert result.exit_code == EXIT_USAGE


def test_cli_table_view_hides_full_schema(cli_env: FakeCatalog) -> None:
    result = runner.invoke(
        app,
        [
            "ai", "tools", "list",
            "--agent-id", AGENT,
            "--kb-scope", SCOPE,
            "--source", "tickhub",
            "--output", "table",
        ],
    )
    assert result.exit_code == 0, result.stderr
    assert "em_kline" in result.stdout
    assert "inputSchema" not in result.stdout
