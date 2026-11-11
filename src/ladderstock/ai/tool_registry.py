"""平台工具注册表（战略 C1 / QUOT-543）。

智能体层要自主调用工具（C2），前提是**先能发现**平台到底有哪些工具。这里把
「当下真实可用」的工具聚合成带元数据的注册表，并提供按命名空间隔离的查询入口。

工具来源只有两类，名单不手写：

1. `ladderstock` 自己的 MCP 工具（`mcp.tools.TOOL_SPECS`，进程内常量）；
2. `config/sources.yaml` 里声明了 MCP 面的生态 peer（tickhub / tickhub_us /
   wire / senate），逐个走真实 `tools/list`。

**不编数**是硬约束：条目全部来自某次真实返回；peer 不可达就记
`status=unreachable` + 0 条，不回填历史名单、不猜工具名。`em_kline` 之类在不在，
以及 TickHub 有多少个工具，以 peer 当下的 `tools/list` 回答为准。

命名空间隔离（红线）：查询与落盘必须同时带 `agent_id` / `kb_scope`
（与 `ai/fallback.py:MemoryScope` 同一口径）；快照只落在
`<data_dir>/ai/tool_registry/<agent_id>/<kb_scope>.json`，读也只读这一个文件，
别的命名空间既不读也不写。缺任一维直接报错，不做「默认全库」兜底。

快照是**缓存**而不是真源：`query(refresh=True)`（CLI `--refresh`）会重新发现并
覆盖本命名空间快照；查询结果里的 `generated_at` 与每源 `status` 说明数据何时、
从谁那里来。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast

from ladderstock.ai.checkpoint import atomic_write
from ladderstock.ai.fallback import is_valid_agent_id, is_valid_kb_scope
from ladderstock.cli.paths import data_dir
from ladderstock.data.provider_registry import (
    PeerEndpoint,
    build_peers,
    build_transport,
    load_sources_config,
)
from ladderstock.errors import EXIT_CONFIG, EXIT_USAGE, LadderStockError
from ladderstock.mcp.tools import tool_listing

#: 本命名空间快照在 `<data_dir>` 下的落点
REGISTRY_SUBDIR = Path("ai") / "tool_registry"
#: 本仓 MCP 工具在注册表里的来源名
LOCAL_SOURCE = "ladderstock"
LOCAL_PRODUCT = "LadderStock"
SNAPSHOT_VERSION = 1
#: 发现是「列工具」不是「取数」：不该为任何一个可选 peer 卡住十几秒
DISCOVERY_TIMEOUT_SEC = 2.0
MAX_ERROR_CHARS = 300

#: 来源状态取值：ok = 真实返回；unreachable = 连不上/握手失败；skipped = 该 peer 没有 MCP 面
STATUS_OK = "ok"
STATUS_UNREACHABLE = "unreachable"


class ToolRegistryError(LadderStockError):
    """注册表自身的错误（命名空间非法 / 快照损坏）。"""


class ToolCatalogTransport(Protocol):
    """只用到 `tools/list` 的传输层；测试注入假实现即可完全离线。"""

    def list_tools(self, peer: PeerEndpoint, *, timeout: float) -> list[dict[str, Any]]: ...


# --------------------------------------------------------------------------- #
# 命名空间
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ToolNamespace:
    """一次查询/落盘的命名空间：`agent_id` 与 `kb_scope` 必须成对给出。

    校验口径与 `ai/fallback.py` 的 `MemoryScope` 一致（同一个正则），
    两维缺一即报错——只带一个等于跨命名空间检索。
    """

    agent_id: str
    kb_scope: str

    def __post_init__(self) -> None:
        if not is_valid_agent_id(self.agent_id):
            raise ToolRegistryError(
                f"agent_id 非法（{self.agent_id!r}）：只允许字母数字与 . _ -，且不超过 80 字符",
                EXIT_USAGE,
            )
        if not is_valid_kb_scope(self.kb_scope):
            raise ToolRegistryError(
                f"kb_scope 非法（{self.kb_scope!r}）：只允许字母数字与 . _ - *，且不超过 64 字符",
                EXIT_USAGE,
            )

    @property
    def path_token(self) -> str:
        """`kb_scope` 进文件名前的转义：`*` 在 Windows 上是非法文件名字符。

        正则不允许 `~`，所以 `*` → `~` 是单射，不会和合法 scope 撞名。
        """
        return self.kb_scope.replace("*", "~")

    def document(self) -> dict[str, str]:
        return {"agent_id": self.agent_id, "kb_scope": self.kb_scope}


# --------------------------------------------------------------------------- #
# 条目与快照
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class ToolEntry:
    """注册表里的一项：谁提供的、干什么、参数长什么样。"""

    name: str
    source: str
    product: str
    description: str
    parameters: Mapping[str, Any]
    read_only: bool | None = None
    domain: str = ""

    def document(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source": self.source,
            "product": self.product,
            "description": self.description,
            "parameters": dict(self.parameters),
            "read_only": self.read_only,
            "domain": self.domain,
        }

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> ToolEntry:
        parameters = doc.get("parameters")
        read_only = doc.get("read_only")
        return cls(
            name=str(doc.get("name") or ""),
            source=str(doc.get("source") or ""),
            product=str(doc.get("product") or ""),
            description=str(doc.get("description") or ""),
            parameters=dict(parameters) if isinstance(parameters, Mapping) else {},
            read_only=read_only if isinstance(read_only, bool) else None,
            domain=str(doc.get("domain") or ""),
        )


@dataclass(frozen=True)
class SourceStatus:
    """一次发现里某个来源的如实状态：成没成、几条、为什么没成。"""

    source: str
    product: str
    status: str
    tool_count: int
    error: str | None = None

    def document(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "product": self.product,
            "status": self.status,
            "tool_count": self.tool_count,
            "error": self.error,
        }

    @classmethod
    def from_document(cls, doc: Mapping[str, Any]) -> SourceStatus:
        count = doc.get("tool_count")
        return cls(
            source=str(doc.get("source") or ""),
            product=str(doc.get("product") or ""),
            status=str(doc.get("status") or ""),
            tool_count=count if isinstance(count, int) else 0,
            error=str(doc["error"]) if doc.get("error") else None,
        )


@dataclass(frozen=True)
class ToolSnapshot:
    """某命名空间在某一时刻的注册表快照（`total` 是过滤前的条数）。"""

    namespace: ToolNamespace
    generated_at: str
    sources: tuple[SourceStatus, ...]
    tools: tuple[ToolEntry, ...]
    total: int

    def document(self) -> dict[str, Any]:
        return {
            "version": SNAPSHOT_VERSION,
            **self.namespace.document(),
            "generated_at": self.generated_at,
            "count": len(self.tools),
            "total": self.total,
            "sources": [status.document() for status in self.sources],
            "tools": [entry.document() for entry in self.tools],
        }


# --------------------------------------------------------------------------- #
# 注册表
# --------------------------------------------------------------------------- #

def local_entries() -> list[ToolEntry]:
    """本仓 MCP 工具：名字与 schema 都来自 `TOOL_SPECS`，不在这里重抄一份。"""
    return [
        ToolEntry(
            name=str(row["name"]),
            source=LOCAL_SOURCE,
            product=LOCAL_PRODUCT,
            description=str(row.get("description") or ""),
            parameters=row.get("input_schema") or {},
            read_only=row.get("read_only") if isinstance(row.get("read_only"), bool) else None,
            domain=str(row.get("domain") or ""),
        )
        for row in tool_listing()
    ]


def _peer_entries(peer: PeerEndpoint, tools: Sequence[Mapping[str, Any]]) -> list[ToolEntry]:
    entries: list[ToolEntry] = []
    for tool in tools:
        name = tool.get("name")
        if not name:
            continue
        schema = tool.get("inputSchema")
        entries.append(
            ToolEntry(
                name=str(name),
                source=peer.key,
                product=peer.product,
                description=str(tool.get("description") or ""),
                parameters=dict(schema) if isinstance(schema, Mapping) else {},
                domain=peer.key,
            )
        )
    return entries


def _error_text(exc: BaseException) -> str:
    text = str(exc).strip() or type(exc).__name__
    return text[:MAX_ERROR_CHARS]


class ToolRegistry:
    """发现 + 命名空间快照 + 查询。

    `root` / `sources` / `transport` 都可注入：测试用临时数据目录与假传输，
    生产默认读 `config/sources.yaml` 与 `HttpMcpTransport`。
    """

    def __init__(
        self,
        *,
        root: Path | None = None,
        sources: dict[str, Any] | None = None,
        peers: dict[str, PeerEndpoint] | None = None,
        transport: ToolCatalogTransport | None = None,
        timeout: float = DISCOVERY_TIMEOUT_SEC,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.root = Path(root) if root is not None else data_dir()
        self.sources_doc = sources if sources is not None else load_sources_config()
        self.peers = peers if peers is not None else build_peers(self.sources_doc)
        # 默认传输是 HttpMcpTransport：它实现了 list_tools（见 provider_registry）
        self.transport: ToolCatalogTransport = (
            transport
            if transport is not None
            else cast(ToolCatalogTransport, build_transport(self.peers))
        )
        self.timeout = timeout
        self._clock = clock or (lambda: datetime.now(UTC))

    # ---------------------------------------------------------------- 来源

    def mcp_peers(self) -> list[PeerEndpoint]:
        """只取声明了 MCP 面的 peer；mem0 / FinanceCoffer / qdrant 没有 MCP，不列入。"""
        rows: list[PeerEndpoint] = []
        for key, item in (self.sources_doc.get("peers") or {}).items():
            if not (item or {}).get("mcp_name"):
                continue
            peer = self.peers.get(str(key))
            if peer is not None:
                rows.append(peer)
        return rows

    def source_names(self) -> list[str]:
        return [LOCAL_SOURCE, *(peer.key for peer in self.mcp_peers())]

    # ---------------------------------------------------------------- 发现

    def discover(self, namespace: ToolNamespace) -> ToolSnapshot:
        """向每个来源要一次真实工具清单，落本命名空间快照并返回。

        单个 peer 失败不挡其它来源：失败源记 `unreachable` + 0 条，
        注册表照常给出「其它来源现在有什么」。
        """
        entries: list[ToolEntry] = []
        statuses: list[SourceStatus] = []

        local = local_entries()
        entries.extend(local)
        statuses.append(
            SourceStatus(LOCAL_SOURCE, LOCAL_PRODUCT, STATUS_OK, len(local))
        )

        for peer in self.mcp_peers():
            try:
                tools = self.transport.list_tools(peer, timeout=self.timeout)
            except Exception as exc:  # noqa: BLE001 - 单个来源失败只影响它自己的状态行
                statuses.append(
                    SourceStatus(
                        peer.key,
                        peer.product,
                        STATUS_UNREACHABLE,
                        0,
                        _error_text(exc),
                    )
                )
                continue
            discovered = _peer_entries(peer, tools)
            entries.extend(discovered)
            statuses.append(
                SourceStatus(peer.key, peer.product, STATUS_OK, len(discovered))
            )

        entries.sort(key=lambda entry: (entry.source, entry.name))
        snapshot = ToolSnapshot(
            namespace=namespace,
            generated_at=self._clock().isoformat(timespec="seconds"),
            sources=tuple(statuses),
            tools=tuple(entries),
            total=len(entries),
        )
        self.save(snapshot)
        return snapshot

    # ---------------------------------------------------------------- 快照落盘

    def snapshot_path(self, namespace: ToolNamespace) -> Path:
        return self.root / REGISTRY_SUBDIR / namespace.agent_id / f"{namespace.path_token}.json"

    def save(self, snapshot: ToolSnapshot) -> Path:
        path = self.snapshot_path(snapshot.namespace)
        atomic_write(path, json.dumps(snapshot.document(), ensure_ascii=False, indent=2) + "\n")
        return path

    def load(self, namespace: ToolNamespace) -> ToolSnapshot | None:
        """只读本命名空间那一个文件；不存在返回 None（由调用方决定是否重新发现）。"""
        path = self.snapshot_path(namespace)
        if not path.is_file():
            return None
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ToolRegistryError(
                f"工具注册表快照不可读（{path}）：{exc}；可用 --refresh 重建", EXIT_CONFIG
            ) from None
        if not isinstance(doc, dict):
            raise ToolRegistryError(f"工具注册表快照结构非法：{path}", EXIT_CONFIG)
        if doc.get("version") != SNAPSHOT_VERSION:
            raise ToolRegistryError(
                f"工具注册表快照版本不匹配（{doc.get('version')!r}）：{path}；可用 --refresh 重建",
                EXIT_CONFIG,
            )
        if (
            doc.get("agent_id") != namespace.agent_id
            or doc.get("kb_scope") != namespace.kb_scope
        ):
            # 文件路径已经按命名空间分开，这里再校验一次文档内容：
            # 文件被换掉/拷错位置时宁可报错，也不把别人的工具表交出去
            raise ToolRegistryError(
                f"工具注册表快照的命名空间与请求不一致：{path}（拒绝读取）", EXIT_CONFIG
            )
        tools = doc.get("tools")
        sources = doc.get("sources")
        return ToolSnapshot(
            namespace=namespace,
            generated_at=str(doc.get("generated_at") or ""),
            sources=tuple(
                SourceStatus.from_document(item)
                for item in (sources if isinstance(sources, list) else [])
                if isinstance(item, Mapping)
            ),
            tools=tuple(
                ToolEntry.from_document(item)
                for item in (tools if isinstance(tools, list) else [])
                if isinstance(item, Mapping) and item.get("name")
            ),
            total=int(doc["total"]) if isinstance(doc.get("total"), int) else 0,
        )

    # ---------------------------------------------------------------- 查询

    def query(
        self,
        namespace: ToolNamespace,
        *,
        text: str | None = None,
        source: str | None = None,
        limit: int = 0,
        refresh: bool = False,
    ) -> ToolSnapshot:
        """按条件检索本命名空间可用工具。

        - `refresh=True`：重新向 peer 发现并覆盖快照；否则优先读快照，没有才现发现；
        - `text`：名字/描述的子串（大小写不敏感）；
        - `source`：来源过滤，必须是本次快照登记过的来源（打错即报错，不静默返回空）；
        - `limit`：最多返回多少条，0 = 全部；`total` 始终是过滤后的总数。
        """
        if refresh:
            snapshot = self.discover(namespace)
        else:
            snapshot = self.load(namespace) or self.discover(namespace)

        if source and source not in {status.source for status in snapshot.sources}:
            known = "、".join(self.source_names()) or "无"
            raise ToolRegistryError(
                f"未知工具来源 {source!r}（已知：{known}）", EXIT_USAGE
            )

        tools = snapshot.tools
        if source:
            tools = tuple(entry for entry in tools if entry.source == source)
        needle = (text or "").strip().casefold()
        if needle:
            tools = tuple(
                entry
                for entry in tools
                if needle in entry.name.casefold() or needle in entry.description.casefold()
            )
        total = len(tools)
        if limit and limit > 0:
            tools = tools[:limit]
        return ToolSnapshot(
            namespace=snapshot.namespace,
            generated_at=snapshot.generated_at,
            sources=snapshot.sources,
            tools=tools,
            total=total,
        )


__all__ = [
    "LOCAL_PRODUCT",
    "LOCAL_SOURCE",
    "REGISTRY_SUBDIR",
    "SNAPSHOT_VERSION",
    "STATUS_OK",
    "STATUS_UNREACHABLE",
    "SourceStatus",
    "ToolCatalogTransport",
    "ToolEntry",
    "ToolNamespace",
    "ToolRegistry",
    "ToolRegistryError",
    "ToolSnapshot",
    "local_entries",
]
