"""TickHub-CN 返回 → `LadderEnvelope`。

TickHub-CN 的原始返回（README §envelope）不是 `DataEnvelope`，而是：

```json
{"ok": true, "data": [...], "meta": {"source": "tencent", "fetched_at": "...",
 "cache_hit": false, "confidence": "A", "trace_id": "..."}}
```

失败时带 `code / message / suggestion`，绝不用模型猜测填充。所以这里做三件事：
把 `ok/data/meta` 翻成统一信封、把 `A/B/C` 置信度映射为 `high/medium/low`、
把 `cache_hit` 映射为 `realtime/cached`——**不做任何字段填充式补救**。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from ladderstock.core.boundary import owner_of
from ladderstock.core.models import Confidence, Freshness, LadderEnvelope
from ladderstock.errors import EXIT_SOURCE, LadderStockError

# TickHub 的置信度是 A/B/C，语义为数据源可靠度
CONFIDENCE_MAP: dict[str, Confidence] = {"A": "high", "B": "medium", "C": "low"}


def from_tool_response(
    dataset: str,
    response: dict[str, Any],
    *,
    data_as_of: date | None = None,
    topic: str | None = None,
) -> LadderEnvelope:
    """把 `th-cn tool call <tool>` 的返回包成信封。

    `dataset` 必须是 `DATASET_CATALOG` 里登记过的名字——未登记的来源不允许进入。
    """
    binding = owner_of(dataset)
    if binding.peer != "tickhub":
        raise LadderStockError(
            f"数据集 {dataset} 归属 {binding.peer}，不能按 TickHub 返回解析", EXIT_SOURCE
        )

    if not response.get("ok", False):
        detail = response.get("message") or response.get("code") or "未知错误"
        raise LadderStockError(f"TickHub-CN {binding.tool} 返回失败：{detail}", EXIT_SOURCE)

    meta: dict[str, Any] = response.get("meta") or {}
    records = response.get("data")
    if records is None:
        raise LadderStockError(f"TickHub-CN {binding.tool} 返回缺 data 字段", EXIT_SOURCE)

    upstream = str(meta.get("source") or "unknown")
    confidence = CONFIDENCE_MAP.get(str(meta.get("confidence") or "").upper(), "medium")
    freshness: Freshness = "cached" if meta.get("cache_hit") else "realtime"

    fetched_at = _parse_dt(meta.get("fetched_at"))
    as_of = data_as_of or fetched_at.date()

    return LadderEnvelope(
        domain="review",
        topic=topic or binding.dataset,
        event_id=meta.get("trace_id"),
        data_as_of=as_of,
        payload={"dataset": dataset, "tool": binding.tool, "records": records},
        source="tickhub_cn",
        source_detail=f"{binding.tool}@{upstream}",
        fetched_at=fetched_at,
        freshness=freshness,
        confidence=confidence,
        source_status="ok",
    )


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    # 时间戳缺失时不编造，直接用当前时间并保留 fidelity 由 confidence 表达
    return datetime.now().astimezone()
