"""战略 C2 桥接层（QUOT-598）：C1 工具注册表 → 自主工具调用环。

C1（`ai/tool_registry.py`）只回答「平台现在有哪些工具」；本模块回答第二个问题：
「这些工具里，哪几个能交给模型**自主调用**，调用怎么落审计、怎么发实时事件」。

四条红线（逐条对应任务书）：

1. **只认白名单里明确只读的工具**：`read_only is True` 才收；`None`（peer 没声明
   readOnlyHint，即未知）与 `False` 一律不收——未知按拒绝处理，不做乐观假设。
   另外**显式剔掉取数 / 实时类**工具（`EXCLUDED_TOOLS`）：本层只读「已经采好的
   数据 / 注册表快照 / 复盘结果」，不在工具环里触发盘内实时取数（禁盘中重采）。
2. **命名空间 + 段白名单双闸**：只从当前 `ToolNamespace` 的快照取条目；模型请求的
   名字必须同时落在**本段白名单**里，否则走 `ai/tools.py:execute` 既有越权路径
   （审计 + `UnauthorizedToolCall`，不重试）。本模块不自建第二套授权。
3. **不看未来**：工具参数里的日期（`date` / `until` / `month`）必须 ≤ 分析基准日，
   越界一律拒绝并把拒因回喂模型，不让工具环取到当日之后的数据。
4. **如实发事件**：每次真实调用（成功或被拒）发一条 `StageSample`（`module_class=cog`），
   绝不为拒绝伪造成功；工具环整段没有调用就不发任何帧。

离线可测：执行只依赖注入的 `Transport`（peer 工具）与本进程 `mcp.server.dispatch`
（本仓工具），测试给假传输即可完全不触网。
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

from ladderstock.ai import tools
from ladderstock.ai.tool_registry import (
    LOCAL_SOURCE,
    ToolEntry,
    ToolNamespace,
    ToolRegistry,
    ToolSnapshot,
)

#: 开关（默认关）：关掉时研究管线停在 R2 的「本仓六个受控工具」，字节级不变
REGISTRY_TOOLS_ENV = "LDST_REGISTRY_TOOLS"
#: 注册表命名空间：`<agent_id>/<kb_scope>`；缺省只给 board 这一支
REGISTRY_NAMESPACE_ENV = "LDST_REGISTRY_NAMESPACE"
DEFAULT_AGENT_ID = "ladderstock-board"
DEFAULT_KB_SCOPE = "*"

#: 实时取数 / 资讯抓取 / 水位探针类工具：即使注册表把它们标成只读也不进工具环
#: （禁盘中重采）。`news_stream` 的 `fetch` 通道与 `event_calendar`（`fetch` 默认 true）
#: 会向 peer 取数，`ladder_get_watch` 读盘中实时信号，`proxyfleet_status` 直接探代理池
#: 当下水位——四者都不属于「已采好的数据 / 注册表快照 / 复盘查询」。
EXCLUDED_TOOLS: frozenset[str] = frozenset(
    {
        "ladder_get_watch",
        "news_stream",
        "event_calendar",
        "proxyfleet_status",
    }
)

#: 参数里的日期键：值不能晚于分析基准日（防看未来）
DATE_ARGUMENT_KEYS: tuple[str, ...] = ("date", "until", "month")

#: 事件归类用的管线模块名（`module_class=cog`，与 `core/stage_events.py` 口径一致）
EVENT_MODULE = "ai"
EVENT_PHASE = "tool_call"


class SourceTransport(Protocol):
    """只用到 `tools/call` 的传输层（与 `data/provider_registry.Transport` 同形）。"""

    def call(
        self, peer: Any, tool: str, params: dict[str, Any], *, timeout: float
    ) -> dict[str, Any]: ...


# --------------------------------------------------------------------------- #
# 纯函数层：过滤 + 描述符 + 日期闸
# --------------------------------------------------------------------------- #


def enabled() -> bool:
    """开关是否打开（`tools.env_enabled` 同一口径：只认 1/true/yes/on，默认关）。"""
    return tools.env_enabled(REGISTRY_TOOLS_ENV)


def namespace_from_env() -> ToolNamespace:
    """读 `LDST_REGISTRY_NAMESPACE`（`<agent_id>/<kb_scope>`），缺省 board / `*`。

    格式非法直接抛 `ToolRegistryError`（与 C1 同一校验），不做「回落全库」兜底。
    """
    raw = os.environ.get(REGISTRY_NAMESPACE_ENV, "").strip()
    if not raw:
        return ToolNamespace(DEFAULT_AGENT_ID, DEFAULT_KB_SCOPE)
    agent_id, separator, kb_scope = raw.partition("/")
    if not separator or not agent_id or not kb_scope:
        raise tools.ToolRefusal(
            "bad_namespace",
            f"{REGISTRY_NAMESPACE_ENV} 需要 <agent_id>/<kb_scope>，收到 {raw!r}",
        )
    return ToolNamespace(agent_id, kb_scope)


def callable_entries(entries: Sequence[ToolEntry]) -> tuple[ToolEntry, ...]:
    """严格过滤：`read_only is True` 且在显式排除集之外（名字去重，先到先得）。"""
    chosen: dict[str, ToolEntry] = {}
    for entry in entries:
        if entry.read_only is not True:
            continue
        if entry.name in EXCLUDED_TOOLS or not entry.name:
            continue
        chosen.setdefault(entry.name, entry)
    return tuple(chosen.values())


def registry_descriptors(entries: Sequence[ToolEntry]) -> list[dict[str, Any]]:
    """与 `tools.tool_descriptors()` 同形的 OpenAI function 描述符。"""
    return [
        {
            "type": "function",
            "function": {
                "name": entry.name,
                "description": entry.description,
                "parameters": dict(entry.parameters) or {"type": "object", "properties": {}},
            },
        }
        for entry in entries
    ]


def check_not_future(
    arguments: Mapping[str, Any], *, base_day: str, today: date | None = None
) -> None:
    """参数里的日期不得晚于分析基准日；不可解析的日期同样拒绝（宁严勿松）。

    基准日取 `min(分析日, 今天)`：拿未来的研究日期问「过去的数据」也一样不许越过今天。
    """
    today = today or date.today()
    try:
        limit = min(date.fromisoformat(base_day), today)
    except ValueError:
        raise tools.ToolRefusal("bad_analysis_day", f"分析基准日不可解析：{base_day!r}") from None
    for key in DATE_ARGUMENT_KEYS:
        raw = arguments.get(key)
        if raw is None:
            continue
        try:
            value = date.fromisoformat(str(raw).strip())
        except ValueError:
            raise tools.ToolRefusal(
                "bad_argument", f"{key} 需要 YYYY-MM-DD 格式的日期，收到 {raw!r}"
            ) from None
        if value > limit:
            raise tools.ToolRefusal(
                "future_date",
                f"{key}={value.isoformat()} 晚于分析基准日 {limit.isoformat()}，不允许看未来数据",
            )


# --------------------------------------------------------------------------- #
# 实时事件：每次真实调用一条 cog 事件
# --------------------------------------------------------------------------- #


def sample_for(
    *,
    stage: str,
    tool: str,
    rejected: bool,
    reason: str | None,
    detail: str,
) -> Any:
    """一次工具调用的 `StageSample`：成功 → ok，被拒 → 拒绝态（绝不伪造成功）。"""
    from ladderstock.core.stage_events import HEALTH_OK, HEALTH_WARN, StageSample

    if not rejected:
        health, label = HEALTH_OK, "成功返回"
    else:
        health, label = HEALTH_WARN, "调用被拒"
        if reason:
            label = f"{label}（{reason}）"
    text = detail.strip().replace("\n", " ")[: tools.MAX_SUMMARY_CHARS]
    reason_text = f"{stage} 段调用工具 {tool}：{label}" + (f"（{text}）" if text else "")
    return StageSample.of(
        f"{stage}/{tool}",
        health=health,
        reason=reason_text,
        module=EVENT_MODULE,
        module_class="cog",
        phase=EVENT_PHASE,
    )


def publish_sample(bus: Any, sample: Any) -> None:
    """发到 `HealthEventBus`；没有总线（CLI / 单测直跑）就静默跳过，不影响主链路。"""
    if bus is None:
        return
    bus.publish_stage(sample)


# --------------------------------------------------------------------------- #
# 执行器
# --------------------------------------------------------------------------- #


@dataclass
class RegistryTools:
    """本段可自主调用的注册表工具：条目 + 执行 + 事件出口。

    - `entries`：严格过滤后的条目（描述符与白名单都从这里来）；
    - `execute`：名字 → 条目 → 本仓进程内分发 / peer `Transport.call`，返回 `{"content": ...}`；
    - `on_call`：每次调用结束（含被拒）的回调，由 `ai/tools.py:execute` 在审计之后调用，
      用来发 cog 阶段事件与 SSE `tool` 帧。
    """

    entries: tuple[ToolEntry, ...]
    transport: SourceTransport
    peers: Mapping[str, Any] = field(default_factory=dict)
    data_root: Any = None
    timeout: float = 10.0
    base_day: str = ""
    today_fn: Callable[[], date] = date.today
    on_call: Callable[[dict[str, Any]], None] | None = None

    def __post_init__(self) -> None:
        self.by_name: dict[str, ToolEntry] = {entry.name: entry for entry in self.entries}

    # ---------------------------------------------------------------- 构造

    def names(self) -> tuple[str, ...]:
        """本执行器能描述、能执行的名字（顺序固定，供自检与测试比对）。"""
        return tuple(sorted(self.by_name))

    def describe(self, name: str) -> str:
        """system 提示里的一行说明；没这名字就是接线错误，直接抛。"""
        return self.by_name[name].description

    def descriptors(self, names: Sequence[str]) -> list[dict[str, Any]]:
        return registry_descriptors([self.by_name[name] for name in names if name in self.by_name])

    # ---------------------------------------------------------------- 执行

    def execute(
        self, ctx: tools.ToolContext, name: str, arguments: Mapping[str, Any]
    ) -> str:
        """跑一次真实调用；抛 `ToolRefusal` 表示被拒（拒因回喂模型），其余异常上抛。

        第二道闸：名字必须是本执行器条目里的（即 read_only 且非取数类），即便上游
        白名单误放了别的名字，这里也只认自己的表。
        """
        entry = self.by_name.get(name)
        if entry is None:
            raise tools.ToolRefusal("unauthorized_tool", f"注册表工具 {name} 不在可用集合内")
        check_not_future(arguments, base_day=self.base_day, today=self.today_fn())
        if entry.source == LOCAL_SOURCE:
            from ladderstock.mcp import server as mcp_server

            try:
                result = mcp_server.dispatch(name, dict(arguments), root=self.data_root)
            except Exception as exc:  # noqa: BLE001 - 本仓工具的可读错误统一当拒绝
                raise tools.ToolRefusal(
                    "tool_error", str(exc).strip() or type(exc).__name__
                ) from None
            return _textify(result)
        peer = self.peers.get(entry.source)
        if peer is None:
            raise tools.ToolRefusal(
                "source_unavailable", f"工具来源 {entry.source} 当前不可达"
            )
        try:
            result = self.transport.call(peer, entry.name, dict(arguments), timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001 - 传输层失败按拒绝回喂，不炸整段
            raise tools.ToolRefusal(
                "source_unavailable", str(exc).strip() or type(exc).__name__
            ) from None
        return _textify(result)

    # ---------------------------------------------------------------- 事件

    def emit(
        self,
        *,
        stage: str,
        tool: str,
        rejected: bool,
        reason: str | None,
        detail: str,
    ) -> None:
        if self.on_call is None:
            return
        self.on_call(
            {
                "stage": stage,
                "tool": tool,
                "rejected": rejected,
                "reason": reason,
                "detail": detail,
            }
        )


def _textify(result: Any) -> str:
    """工具结果 → 回喂模型的文本（超限截断：与 `tools.MAX_OUTPUT_CHARS` 同一口径）。"""
    if isinstance(result, str):
        text = result
    else:
        text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) > tools.MAX_OUTPUT_CHARS:
        text = text[: tools.MAX_OUTPUT_CHARS] + "\n…（已截断）"
    return text


def build_for_stage(
    snapshot: ToolSnapshot,
    *,
    namespace: ToolNamespace,
    stage: str,
    base_day: str,
    transport: SourceTransport,
    peers: Mapping[str, Any],
    data_root: Any,
    timeout: float = 10.0,
    today_fn: Callable[[], date] = date.today,
    on_call: Callable[[dict[str, Any]], None] | None = None,
) -> RegistryTools:
    """从当前命名空间快照装配本段的执行器。

    快照的命名空间必须是请求的那个（快照被换/拷错位置时宁可不给工具，也不交别人的表）。
    """
    if snapshot.namespace != namespace:
        raise tools.ToolRefusal(
            "namespace_mismatch",
            f"工具注册表快照属于 {snapshot.namespace.agent_id}/{snapshot.namespace.kb_scope}，"
            f"不是当前命名空间 {namespace.agent_id}/{namespace.kb_scope}",
        )
    return RegistryTools(
        entries=callable_entries(snapshot.tools),
        transport=transport,
        peers=peers,
        data_root=data_root,
        timeout=timeout,
        base_day=base_day,
        today_fn=today_fn,
        on_call=on_call,
    )


def load_snapshot(
    registry: ToolRegistry, namespace: ToolNamespace
) -> ToolSnapshot | None:
    """读本命名空间快照；没有快照就现发现一次（与 `ToolRegistry.query` 同一口径）。"""
    try:
        return registry.load(namespace) or registry.discover(namespace)
    except Exception:  # noqa: BLE001 - 注册表不可用只是「本次没有注册表工具」，不挡研究
        return None


__all__ = [
    "DATE_ARGUMENT_KEYS",
    "DEFAULT_AGENT_ID",
    "DEFAULT_KB_SCOPE",
    "EVENT_MODULE",
    "EVENT_PHASE",
    "EXCLUDED_TOOLS",
    "REGISTRY_NAMESPACE_ENV",
    "REGISTRY_TOOLS_ENV",
    "RegistryTools",
    "SourceTransport",
    "build_for_stage",
    "callable_entries",
    "check_not_future",
    "enabled",
    "load_snapshot",
    "namespace_from_env",
    "publish_sample",
    "registry_descriptors",
    "sample_for",
]
