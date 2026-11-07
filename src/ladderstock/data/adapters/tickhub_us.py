"""TickHub-US 返回 → `LadderEnvelope`（QUOT-471）。

TickHub-US 的 `th_us_get_*` 工具回的不是 TickHub-CN 的 `ok/data/meta` 壳，而是
它自己的 `DataEnvelope` 形状（实测 `th_us_get_quote`，2026-11-06）：

```json
{"schema_version": "1", "domain": "quote", "symbol": "DJI",
 "payload": {"price": 51570.0898, "prev_close": 51523.0586, "change": 47.03,
             "change_pct": 0.09, "currency": "USD", ...},
 "source": "sina", "compliance_tier": "C", "freshness": "realtime",
 "fetched_at": "...", "cache_hit": false, "trace_id": "...",
 "warnings": ["新浪财经 属 Tier C：商用需事先书面许可，当前为非商用调用。"],
 "attempts": 1}
```

与 CN 适配器的两处实质差别，判据对着它们写：

1. **形状不同**：CN 的 `data` 是三态（列表 / 对象+记录数组 / 快照），US 直接是
   `payload` 对象，没有「记录行」概念——一个 `symbol` 一次调用回一条快照。所以本适配器
   **不展开行**，整包落一行，`symbol` 当幂等键的语义由调用方（`core/indices.py`）负责。
2. **合规字段必须原样带出**：回包带 `compliance_tier` 与 `warnings`，是**上游对数据可用
   边界的声明**（`sina` = Tier C，商用需事先书面许可）。CN 适配器只映射 `meta.confidence`，
   这两个字段一个都没落地（`HOME-V2-DATA-SURFACE.md` C-2 第 3 条）。本适配器把它们落进
   信封的 `source_detail`，**不吞、不降级措辞**——「非商用」这个前提必须在数据这一层可见，
   不能只靠调用约定维持。
3. **`commercial_use` 是必填入参、且是产品对外承诺**（DEC-006 语境）：本层只负责按
   调用方给的入参取数，不替产品决定填 true 还是 false；填 `true` 时上游对 Tier C 直接
   `E_COMPLIANCE` 拒回，那时这里按普通失败抛错，不做任何绕过。

不做任何字段填充：点位 / 涨跌取不到就是 `None`（调用方显 `—`），不补 0、不猜价。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from ladderstock.core.boundary import owner_of
from ladderstock.core.models import Confidence, Freshness, LadderEnvelope
from ladderstock.errors import EXIT_SOURCE, LadderStockError

#: `compliance_tier` 的取值 → 信封 `confidence`。上游的 tier 是**合规分级**不是数据质量，
#: 但两者在控制面上都回答「这批数据能信到什么程度」，映射关系与 CN 的 A/B/C 一致：
#: Tier S（官方源）→ high，Tier B → medium，Tier C/D（二手 / 需授权）→ low。
#: 未知 tier 一律 medium（不猜 high，避免把来源不明的数据抬成 A 级）。
COMPLIANCE_CONFIDENCE: dict[str, Confidence] = {
    "S": "high",
    "A": "high",
    "B": "medium",
    "C": "low",
    "D": "low",
}


def from_us_tool_response(
    dataset: str,
    response: dict[str, Any],
    *,
    data_as_of: date | None = None,
    topic: str | None = None,
    tool: str | None = None,
) -> LadderEnvelope:
    """把 `th-us tool call <tool>` 的返回包成信封。

    `dataset` 必须是 `DATASET_CATALOG` 里归属 `tickhub_us` 的登记名——未登记直接抛，
    与 CN 适配器同一条闸门（DEC-006 闸门 1/2）。
    """
    binding = owner_of(dataset)
    if binding.peer != "tickhub_us":
        raise LadderStockError(
            f"数据集 {dataset} 归属 {binding.peer}，不能按 TickHub-US 返回解析", EXIT_SOURCE
        )
    actual = tool or binding.tool

    body = _unwrap(response)
    if body.get("error"):
        raise LadderStockError(
            f"TickHub-US {actual} 返回失败：{_failure_text(body)}", EXIT_SOURCE
        )

    payload = body.get("payload")
    if not isinstance(payload, dict):
        raise LadderStockError(
            f"TickHub-US {actual} 返回缺 payload 对象"
            f"（收到 {type(payload).__name__}）",
            EXIT_SOURCE,
        )

    upstream = str(body.get("source") or "unknown")
    tier = str(body.get("compliance_tier") or "").strip().upper()
    warnings = _warnings(body)
    confidence = COMPLIANCE_CONFIDENCE.get(tier, "medium")
    freshness: Freshness = "cached" if body.get("cache_hit") else "realtime"
    fetched_at = _parse_dt(body.get("fetched_at"))
    as_of = data_as_of or _parse_date(payload.get("as_of")) or fetched_at.date()

    return LadderEnvelope(
        domain="review",
        topic=topic or binding.dataset,
        event_id=_text(body.get("trace_id")),
        data_as_of=as_of,
        # 整包落一行：US 快照没有「记录数组」，`symbol` 只是行的标识，不是幂等键
        # （首页只是现取现显，不落库）。
        payload={
            "dataset": dataset,
            "tool": actual,
            "symbol": _text(body.get("symbol")) or _text(payload.get("symbol")),
            "records": [_record(body, payload)],
            "compliance_tier": tier or None,
            "warnings": warnings,
            "batch_requests": None,
        },
        source="tickhub_us",
        source_detail=_detail(actual, upstream, tier, warnings),
        fetched_at=fetched_at,
        freshness=freshness,
        confidence=confidence,
        source_status="ok",
    )


def _record(body: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """一行快照：`symbol` + 价格字段照抄，不做任何二次换算（DEC-028）。"""
    row: dict[str, Any] = {
        "symbol": _text(body.get("symbol")) or _text(payload.get("symbol")),
        "market": _text(body.get("market")) or _text(payload.get("market")) or "US",
    }
    for key in ("price", "prev_close", "change", "change_pct", "currency", "as_of",
                "timestamp", "name"):
        if key in payload:
            row[key] = payload.get(key)
    return row


def _detail(actual: str, upstream: str, tier: str, warnings: list[str]) -> str:
    """`source_detail`：工具@上游 + 合规分级 + 警告句（合规前提必须在数据层可见）。"""
    head = f"{actual}@{upstream}"
    tail = f"compliance_tier={tier}" if tier else "compliance_tier=未知"
    if warnings:
        tail = f"{tail}；{'；'.join(warnings)}"
    return f"{head}（{tail}）"


def _unwrap(response: dict[str, Any]) -> dict[str, Any]:
    """真实 FastMCP 把 `model_dump_json()` 放进 `structuredContent.result`（QUOT-328）。

    `provider_registry._to_envelope` 拿到的是 `transport.call` 的原样返回，US 侧这层壳
    与 Wire 一样要在这里拆掉；已经是解开的对象就直接用。
    """
    structured = response.get("structuredContent")
    if isinstance(structured, dict):
        inner = structured.get("result")
        if isinstance(inner, str):
            try:
                decoded = json.loads(inner)
            except ValueError:
                return response
            return decoded if isinstance(decoded, dict) else response
        if isinstance(inner, dict):
            return inner
    return response


def _warnings(body: dict[str, Any]) -> list[str]:
    raw = body.get("warnings")
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    return []


def _failure_text(body: dict[str, Any]) -> str:
    error = body.get("error")
    if isinstance(error, dict):
        code = str(error.get("code") or "").strip()
        message = str(error.get("message") or error.get("name") or "").strip()
        parts = [x for x in (code, message) if x]
        if parts:
            return " ".join(parts)
    return str(body.get("message") or "未知错误")


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        text = value.strip()
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            if len(text) == 8 and text.isdigit():
                try:
                    return date(int(text[:4]), int(text[4:6]), int(text[6:]))
                except ValueError:
                    return None
    return None


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    return datetime.now().astimezone()


__all__ = ["COMPLIANCE_CONFIDENCE", "from_us_tool_response"]
