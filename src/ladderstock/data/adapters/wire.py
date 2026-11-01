"""QuotaFuse Wire 返回 → `LadderEnvelope`。

Wire 的 `WireEnvelope` 与本项目 `LadderEnvelope` 字段同构，只做三处收紧：
`domain` 收进本项目枚举、`payload` 只取叙事相关字段、缺省 `source` 补成 `wire`。

**入参装配**（QUOT-328）：Wire 的 `wire_get_digest(topic, date=None)` 里 `topic` 是
**必填的赛道 slug**（`ai` / `semiconductor` / …，见 Wire 的 `config/topics.yaml`），
中文赛道名会被服务端判「未知赛道」。空参调用不是「取全部」，是直接吃一个校验错误，
所以这里按 slug 逐个装配；slug 由 `config/sync.yaml` 提供，不在代码里硬编码。
"""

from __future__ import annotations

from typing import Any, cast

from ladderstock.core.boundary import owner_of
from ladderstock.core.models import LadderEnvelope, SourceStatus
from ladderstock.errors import EXIT_CONFIG, EXIT_SOURCE, LadderStockError

_ALLOWED_STATUS = ("ok", "degraded", "partial")

#: `topic`（赛道 slug）必填的 Wire 工具。`wire_get_timeline` 不在这里——它要的是
#: `event_id`（单个事件的下钻），按日同步没有事件维度，所以它不进 `theme_news` 的
#: 同步链路：`config/sync.yaml` 里 `fallback: []` 是显式声明（`chain()` 认显式空值），
#: 它在 `config/sources.yaml` 仍作为同 peer 备源登记在册。理由见 DEC-043。
TOPIC_REQUIRED_TOOLS: frozenset[str] = frozenset({"wire_get_digest"})


def needs_topic(tool: str) -> bool:
    """该 Wire 工具是否要求 `topic`（赛道 slug）入参。"""
    return tool in TOPIC_REQUIRED_TOOLS


def tool_params(tool: str, *, topic: str) -> dict[str, Any]:
    """按工具拼真实入参：一次调用只覆盖一个赛道，空 slug 就是装配错误。"""
    if tool in TOPIC_REQUIRED_TOOLS and not str(topic or "").strip():
        raise LadderStockError(
            f"{tool} 的 topic（赛道 slug）必填，不能空参调用（空参上游回校验错误）",
            EXIT_CONFIG,
        )
    return {"topic": topic}



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
