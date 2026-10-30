"""MCP 工具注册表（只读白名单 + 1 个发起研究工具 + 3 个复盘类工具 + 1 个研究进度工具）。

隐私硬约束：交易日志与持仓（`journal.sqlite`）**永不出现在**任何工具的输出 schema 中。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    read_only: bool
    input_schema: dict[str, Any] = field(default_factory=dict)
    domain: str = "review"


def _date_schema(extra: dict[str, Any] | None = None) -> dict[str, Any]:
    props: dict[str, Any] = {
        "date": {"type": "string", "format": "date", "description": "交易日 YYYY-MM-DD"}
    }
    if extra:
        props.update(extra)
    return {"type": "object", "properties": props, "required": ["date"]}


def _date_tool(name: str, description: str, domain: str) -> ToolSpec:
    return ToolSpec(name, description, True, _date_schema(), domain)


TOOL_SPECS: tuple[ToolSpec, ...] = (
    # 原有 7 个只读工具
    _date_tool("ladder_get_watch", "盘中盯盘信号与阈值状态", "watch"),
    _date_tool("ladder_get_sentiment_series", "情绪温度与晋级率时序", "sentiment"),
    _date_tool("ladder_get_limit_ladder", "连板天梯与梯队断层", "sentiment"),
    _date_tool("ladder_get_theme_rotation", "题材轮动与强度", "sentiment"),
    _date_tool("ladder_get_dragon_tiger", "龙虎榜与席位结构", "sentiment"),
    _date_tool("ladder_get_leader", "龙头股识别与地位变化", "sentiment"),
    ToolSpec("ladder_get_sources_health", "数据源主备与降级状态", True, {}, "meta"),
    # 1 个发起研究工具（唯一非只读）
    ToolSpec(
        "ladder_start_research",
        "发起一次复盘研究任务（异步，返回任务 ID）",
        False,
        _date_schema({"sections": {"type": "array", "items": {"type": "string"}}}),
        "review",
    ),
    # M3 新增 3 个
    _date_tool("ladder_get_review", "某日复盘结构化结果", "review"),
    _date_tool("ladder_get_validation", "明日验证条件与对账结果", "validation"),
    _date_tool("ladder_get_sentiment", "情绪温度、晋级率、梯队断层", "sentiment"),
    # R1-1 个股研究管线（只读进度；`code` 必填、`date` 缺省取最近一次）
    ToolSpec(
        "research_status",
        "个股研究六段（company/financial/forecast/valuation/risk/report）当前进度，只读",
        True,
        {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "证券代码（6 位，可带 .SH/.SZ/.BJ）"},
                "date": {
                    "type": "string",
                    "format": "date",
                    "description": "研究日期 YYYY-MM-DD；缺省取该代码最近一次",
                },
            },
            "required": ["code"],
        },
        "research",
    ),
)

TOOL_NAMES: tuple[str, ...] = tuple(spec.name for spec in TOOL_SPECS)

# 隐私边界：这些关键字不得出现在任何工具名/描述/schema 中
FORBIDDEN_KEYWORDS: tuple[str, ...] = ("journal", "position", "holding", "交易日志", "持仓", "自选")


def tool_listing() -> list[dict[str, Any]]:
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "read_only": spec.read_only,
            "domain": spec.domain,
            "input_schema": spec.input_schema,
        }
        for spec in TOOL_SPECS
    ]
