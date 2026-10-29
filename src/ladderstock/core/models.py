"""核心数据契约。

`LadderEnvelope` 与生态内 `DataEnvelope` / `WireEnvelope` 字段对齐，
用于复盘、情绪、验证等所有结构化产出；交易日志与持仓**不进入**该契约。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Domain = Literal["review", "sentiment", "validation", "trade_log"]
Freshness = Literal["realtime", "cached", "archive"]
Confidence = Literal["high", "medium", "low"]
SourceStatus = Literal["ok", "degraded", "partial"]


class LadderEnvelope(BaseModel):
    """统一产出契约（对齐 QuotaFuse DataEnvelope / WireEnvelope）。"""

    model_config = ConfigDict(extra="forbid")

    domain: Domain = Field(description="review / sentiment / validation / trade_log")
    topic: str = Field(description="领域主题，如 shortline_sentiment")
    event_id: str | None = Field(default=None, description="事件 ID，无则为空")
    data_as_of: date | None = Field(
        description=(
            "数据时点，用于时点回看；没有可信数据时为 null——"
            "不拿请求日期顶替（METRICS.md §4.3 / D-02）"
        )
    )
    payload: dict[str, Any] = Field(default_factory=dict, description="结构化正文")
    source: str = Field(default="ladderstock", description="产出方标识")
    source_detail: str = Field(default="", description="主备源与降级记录")
    fetched_at: datetime = Field(description="抓取/计算时间（带时区）")
    freshness: Freshness = "cached"
    confidence: Confidence = "medium"
    source_status: SourceStatus = "ok"
