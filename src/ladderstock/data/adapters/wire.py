"""QuotaFuse Wire 返回 → `LadderEnvelope`。

Wire 的 `WireEnvelope` 与本项目 `LadderEnvelope` 字段同构，只做三处收紧：
`domain` 收进本项目枚举、`payload` 只取叙事相关字段、缺省 `source` 补成 `wire`。
"""

from __future__ import annotations

from typing import Any, cast

from ladderstock.core.boundary import owner_of
from ladderstock.core.models import LadderEnvelope, SourceStatus
from ladderstock.errors import EXIT_SOURCE, LadderStockError

_ALLOWED_STATUS = ("ok", "degraded", "partial")


def from_wire_envelope(
    dataset: str, envelope: dict[str, Any], *, topic: str | None = None
) -> LadderEnvelope:
    binding = owner_of(dataset)
    if binding.peer != "wire":
        raise LadderStockError(
            f"数据集 {dataset} 归属 {binding.peer}，不能按 Wire 信封解析", EXIT_SOURCE
        )

    payload = envelope.get("payload") or {}
    raw_status = str(envelope.get("source_status") or "ok")
    status: SourceStatus = (
        cast(SourceStatus, raw_status) if raw_status in _ALLOWED_STATUS else "partial"
    )

    return LadderEnvelope(
        domain="review",
        topic=topic or str(envelope.get("topic") or binding.dataset),
        event_id=envelope.get("event_id"),
        data_as_of=envelope["data_as_of"],
        payload={"dataset": dataset, "tool": binding.tool, **payload},
        source="wire",
        source_detail=str(envelope.get("source_detail") or binding.tool),
        fetched_at=envelope["fetched_at"],
        freshness=envelope.get("freshness") or "cached",
        confidence=envelope.get("confidence") or "medium",
        source_status=status,
    )
