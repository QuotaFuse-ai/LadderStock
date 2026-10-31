"""`ldst sources` 命令体：数据源清单、可达性校验、冷却与降级记录。

注册与实现分离（`cli/main.py` 只 import + 注册）。三条命令都**只读**：

- `sources list`：`sources.yaml` 里的 peer / tool / primary / fallback / cooldown；
- `sources validate`：先用 `core/boundary.chain_problems()` 做**离线**判定
  （catalog ↔ yaml 双向一致、主备同 peer、工具已登记），配置非法退出码 3 且
  **优先于**可达性（DEC-008）；配置合法才探测 `tools/list`，peer 不可达退出码 4；
- `sources health`：`state/health.json` 里未过期的冷却与最近降级事件。

探测失败只说明「这台机器上现在取不到数」，不写任何冷却记录——冷却由真正取数
失败时（provider_registry）写入。
"""

from __future__ import annotations

import sys
from datetime import date, datetime
from typing import Annotated, Any

import typer

from ladderstock.cli.output import OutputOption, emit, error
from ladderstock.core.boundary import (
    DATASET_CATALOG,
    ChainProblem,
    DatasetBinding,
    chain_problems,
    owner_of,
)
from ladderstock.data.provider_registry import ProviderRegistry
from ladderstock.errors import EXIT_CONFIG, EXIT_OK, EXIT_SOURCE, EXIT_USAGE, LadderStockError


def _log(message: str) -> None:
    sys.stderr.write(f"ldst: {message}\n")
    sys.stderr.flush()


def _reason(exc: BaseException) -> str:
    text = str(exc).strip()
    return text or type(exc).__name__


def _parse_since(value: str | None) -> datetime | None:
    """`--since` 接受 ISO 日期或时间串；解析不了按用法错误处理。"""
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        try:
            moment = datetime.combine(date.fromisoformat(text), datetime.min.time())
        except ValueError:
            error(f"--since 需要 ISO 日期或时间，收到 {value!r}")
            raise typer.Exit(EXIT_USAGE) from None
    return moment if moment.tzinfo else moment.astimezone()


# --------------------------------------------------------------------------- #
# list
# --------------------------------------------------------------------------- #

def sources_list(output: OutputOption = None) -> None:
    registry = ProviderRegistry()
    emit({"peers": registry.peer_rows(), "sources": registry.describe()}, output)


# --------------------------------------------------------------------------- #
# validate
# --------------------------------------------------------------------------- #

def _chain_rows(
    registry: ProviderRegistry, datasets: list[str]
) -> tuple[list[dict[str, Any]], bool]:
    """主备合法性：离线可判定，不碰网络。返回 (行, 是否有违规)。"""
    rows: list[dict[str, Any]] = []
    illegal = False
    for dataset in datasets:
        try:
            # 实时类数据集（`spot_quote`）不登记在 sync.yaml，链路只来自 sources.yaml
            specs = (
                registry.chain(dataset)
                if dataset in registry.datasets
                else registry.source_chain(dataset)
            )
        except LadderStockError as exc:
            illegal = True
            rows.append({"dataset": dataset, "ok": False, "chain": "-", "detail": exc.message})
            continue
        except Exception as exc:  # noqa: BLE001 - BoundaryViolation 等运行时契约错误
            illegal = True
            rows.append({"dataset": dataset, "ok": False, "chain": "-", "detail": _reason(exc)})
            continue
        chain = " → ".join(spec.id for spec in specs)
        rows.append(
            {
                "dataset": dataset,
                "ok": True,
                "chain": chain,
                "detail": f"{len(specs)} 个工具，peer={owner_peer(dataset)}",
            }
        )
    return rows, illegal


def owner_peer(dataset: str) -> str:
    return owner_of(dataset).peer


def _probe_rows(registry: ProviderRegistry, peers: list[str]) -> tuple[list[dict[str, Any]], bool]:
    rows: list[dict[str, Any]] = []
    unreachable = False
    for key in peers:
        peer = registry.peers.get(key)
        if peer is None:
            unreachable = True
            rows.append({"peer": key, "reachable": False, "detail": "sources.yaml 缺少该 peer"})
            continue
        try:
            info = registry.transport.probe(peer, timeout=peer.timeout_sec)
        except Exception as exc:  # noqa: BLE001 - 探测失败原样汇报，不写冷却
            unreachable = True
            rows.append({"peer": key, "reachable": False, "detail": _reason(exc)})
            continue
        tools = info.get("tools") if isinstance(info, dict) else None
        names = [str(name) for name in (tools or []) if name]
        preview = ", ".join(names[:3]) + ("…" if len(names) > 3 else "")
        rows.append(
            {
                "peer": key,
                "reachable": True,
                "detail": f"{len(names)} 个工具（{preview}）",
            }
        )
    return rows, unreachable


def sources_validate(
    source_id: Annotated[str | None, typer.Option("--id", help="只校验指定 source id")] = None,
    output: OutputOption = None,
) -> None:
    registry = ProviderRegistry()
    specs = registry.specs
    if source_id is not None and source_id not in specs:
        error(f"未知 source：{source_id}；可选：{', '.join(sorted(specs))}")
        raise typer.Exit(EXIT_USAGE)

    selected = [spec for spec in specs.values() if source_id is None or spec.id == source_id]
    datasets = sorted({spec.dataset for spec in selected})
    problems = _config_problems(registry, source_id, datasets)
    for problem in problems:
        _log(f"配置错误：{problem}")
    config_broken = bool(problems)

    chains, chain_illegal = _chain_rows(registry, datasets)
    if chain_illegal:
        config_broken = True
    if config_broken:
        # 配置本身是坏的，探测 peer 只会给出误导性的「不可达」，直接跳过
        peer_rows: list[dict[str, Any]] = [
            {"peer": key, "reachable": None, "detail": "配置非法，跳过可达性探测"}
            for key in sorted({spec.via for spec in selected})
        ]
        unreachable = False
        reachable: set[str] = set()
    else:
        peers = sorted({spec.via for spec in selected})
        peer_rows, unreachable = _probe_rows(registry, peers)
        reachable = {row["peer"] for row in peer_rows if row["reachable"]}

    sources: list[dict[str, Any]] = []
    for spec in selected:
        binding = _binding(spec.dataset)
        registered = binding is not None and spec.tool in {
            binding.tool,
            *binding.fallback_tools,
        }
        peer_matches = binding is not None and binding.peer == spec.via
        sources.append(
            {
                "source": spec.id,
                "dataset": spec.dataset,
                "peer": spec.via,
                "owner_peer": binding.peer if binding is not None else "-",
                "tool": spec.tool,
                "primary": spec.primary,
                "tool_registered": registered,
                "peer_matches": peer_matches,
                "peer_reachable": None if config_broken else spec.via in reachable,
                "ok": registered and peer_matches and spec.via in reachable,
            }
        )

    # 配置错误优先：peer 全下线时也要能看出「配置本身是坏的」（docs/CLI.md §F）
    if config_broken:
        code = EXIT_CONFIG
    elif unreachable:
        code = EXIT_SOURCE
    else:
        code = EXIT_OK
    emit(
        {
            "ok": code == EXIT_OK,
            "reason": "config" if config_broken else ("unreachable" if unreachable else ""),
            "problems": [problem.as_dict() for problem in problems],
            "peers": peer_rows,
            "chains": chains,
            "sources": sources,
            "warnings": [str(problem) for problem in problems],
            "exit_code": code,
        },
        output,
    )
    raise typer.Exit(code)


def _config_problems(
    registry: ProviderRegistry, source_id: str | None, datasets: list[str]
) -> list[ChainProblem]:
    """catalog ↔ yaml 的双向一致性问题；`--id` 时只保留与该源/其数据集相关的条目。"""
    problems = chain_problems(registry.sources_doc, registry.sync_doc)
    if source_id is None:
        return problems
    scope = set(datasets)
    return [
        problem
        for problem in problems
        if problem.record == source_id or problem.record in scope
    ]


def _binding(dataset: str) -> DatasetBinding | None:
    return DATASET_CATALOG.get(dataset)


# --------------------------------------------------------------------------- #
# health
# --------------------------------------------------------------------------- #

def sources_health(
    since: Annotated[str | None, typer.Option(help="起始时间（ISO）")] = None,
    output: OutputOption = None,
) -> None:
    registry = ProviderRegistry()
    moment = _parse_since(since)
    payload = registry.health.report(since=moment, limit=100)
    degraded = [
        row for row in registry.describe() if row.get("cooldown_until") not in (None, "-")
    ]
    payload["sources_in_cooldown"] = degraded
    payload["peers"] = registry.peer_rows()
    emit(payload, output)


def register(sources_app: typer.Typer) -> None:
    """把 `sources` 三条子命令挂到 main.py 已建好的 Typer 组上。"""
    sources_app.command("list", help="数据源与主备顺序")(sources_list)
    sources_app.command("validate", help="校验数据源可用性")(sources_validate)
    sources_app.command("health", help="最近失败冷却与降级记录")(sources_health)
