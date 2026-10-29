"""StockSenate 返回 → `LadderEnvelope`（只读）。

Senate 的 `DataEnvelope` 用 `code` 而非 `topic`，状态字段叫 `data_source_status`。
这里只读消费：**不发起合议、不复现分析师/辩论/风控流程**，只取已生成的报告摘要。
"""

from __future__ import annotations

from typing import Any

from ladderstock.core.boundary import owner_of
from ladderstock.core.models import LadderEnvelope, SourceStatus
from ladderstock.errors import EXIT_SOURCE, LadderStockError

_STATUS_MAP: dict[str, SourceStatus] = {"ok": "ok", "degraded": "degraded", "missing": "partial"}


def from_data_envelope(
    dataset: str, envelope: dict[str, Any], *, topic: str | None = None
) -> LadderEnvelope:
    binding = owner_of(dataset)
    if binding.peer != "senate":
        raise LadderStockError(
            f"数据集 {dataset} 归属 {binding.peer}，不能按 Senate 信封解析", EXIT_SOURCE
        )

    status = _STATUS_MAP.get(str(envelope.get("data_source_status") or "ok"), "partial")
    code = envelope.get("code")

    return LadderEnvelope(
        domain="review",
        topic=topic or str(code or binding.dataset),
        event_id=envelope.get("event_id") or code,
        data_as_of=envelope["data_as_of"],
        payload={"dataset": dataset, "tool": binding.tool, "report": envelope.get("payload") or {}},
        source="stock_senate",
        source_detail=str(envelope.get("source_detail") or binding.tool),
        fetched_at=envelope["fetched_at"],
        freshness=envelope.get("freshness") or "cached",
        confidence=envelope.get("confidence") or "medium",
        source_status=status,
    )
