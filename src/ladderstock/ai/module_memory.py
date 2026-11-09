"""子模块记忆命名空间（QUOT-498 第三节）：把 `ai.yaml` 的 `modules.<m>.memory`
翻译成 QUOT-487 记忆通道的检索 / 写入参数。

**本模块不另起记忆通道**：只做「配置 → 命名空间」的换算与校验，真正的 mem0
读写仍归 `ai/memory.py`（QUOT-487，PR #156）。在 #156 收口前，`recall_params` /
`store_params` 的产物就是交给它 `search` / `add` 的载荷片段；调用点必须**同时**
带上 `agent_id` 与 `kb_scope`，只带一个等于跨模块召回。
"""

from __future__ import annotations

from typing import Any

from ladderstock.ai.fallback import MemoryScope
from ladderstock.core.boundary import BoundaryViolation

#: 注入段里标注记忆归属的那一行；QUOT-487 的 `render_memory_block` 按两段拼装后
#: 由调用点在段首补上本行（两段格式见 QUOT-487 `MEMORY_SECTION_HEADER` /
#: `KNOWLEDGE_SECTION_HEADER`）。
MEMORY_SCOPE_LABEL = "【记忆命名空间：{agent_id} / {kb_scope}】"

#: mem0 侧承载知识库范围的 metadata 键（`agent_id` 是 mem0 原生字段，单独传）
KB_SCOPE_KEY = "kb_scope"


def checked_scope(scope: MemoryScope) -> MemoryScope:
    """落库前的最后一道校验：两维都必须非空，否则拒绝检索。"""
    if not scope.agent_id or not scope.kb_scope:
        raise BoundaryViolation(
            "记忆命名空间必须同时给出 agent_id 与 kb_scope，"
            f"收到 {scope.agent_id!r} / {scope.kb_scope!r}；缺一个就等于跨模块召回"
        )
    return scope


def recall_params(scope: MemoryScope, query: str, *, user_id: str, top_k: int) -> dict[str, Any]:
    """`memory.MemoryClient.search` 的载荷：命名空间两维 + 查询条件。"""
    checked_scope(scope)
    return {
        "query": query,
        "user_id": user_id,
        "agent_id": scope.agent_id,
        "top_k": top_k,
        KB_SCOPE_KEY: scope.kb_scope,
    }


def store_params(
    scope: MemoryScope,
    messages: list[dict[str, str]],
    *,
    user_id: str,
    run_id: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """`memory.MemoryClient.add` 的载荷：写入与召回用同一对命名空间字段。"""
    checked_scope(scope)
    payload_metadata: dict[str, Any] = dict(metadata or {})
    payload_metadata[KB_SCOPE_KEY] = scope.kb_scope
    return {
        "messages": messages,
        "user_id": user_id,
        "agent_id": scope.agent_id,
        "run_id": run_id,
        "metadata": payload_metadata,
    }


def scope_label(scope: MemoryScope) -> str:
    """注入段首行：让人（和排查日志）一眼看出这段记忆属于哪个模块。"""
    checked_scope(scope)
    return MEMORY_SCOPE_LABEL.format(agent_id=scope.agent_id, kb_scope=scope.kb_scope)


__all__ = [
    "KB_SCOPE_KEY",
    "MEMORY_SCOPE_LABEL",
    "checked_scope",
    "recall_params",
    "scope_label",
    "store_params",
]
