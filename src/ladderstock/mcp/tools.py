"""MCP 工具注册表（只读白名单 + 1 个发起研究 + 3 个复盘类 + 1 个研究进度
+ 1 个候选池 + 1 个行业画像 + 2 个资讯工具）。

隐私硬约束：交易日志与持仓（`journal.sqlite`）**永不出现在**任何工具的输出 schema 中。
R6 的两个资讯工具（`news_stream` / `event_calendar`）同样只读：默认不写本机库，
`store=true` 才落 `data/news.sqlite`（公开数据，与隐私库分库）。
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
    # R7 新增 1 个：候选池打分。**只读**、只出打分结果，域沿用 `sentiment`
    # （与 `ldst pool` 的信封域一致）。工具名不加 `ladder_` 前缀：任务里点名的就是
    # `candidate_pool`，且没有规则要求前缀（`tests/contract/test_mcp_tools.py` 按清单比对）。
    # 分项「地位」对外叫 `standing`——`position` 是下面的隐私关键字。
    _date_tool("candidate_pool", "当日候选池评分：强度/换手/地位/资金/题材分项与总分", "sentiment"),
    # R5 新增 1 个：申万一级行业五维画像。**只读**、只回放本机缓存，
    # 域沿用 `sentiment`（与 `ldst industry show` 的信封域一致）。
    # 入参按**行业名**定位，没有 `date`：行业快照的口径是「本机缓存里的最新一批」，
    # 各维度自己给出 `trade_date` / `as_of`，拿请求日期冒充数据日期会改口径
    # （`docs/specs/SPEC-INDUSTRY-METRICS.md` §1.2），所以这里故意不套 `_date_tool`。
    ToolSpec(
        "industry_snapshot",
        "申万一级行业五维画像：景气/资金/估值/动量/拥挤度，缺数显式 null 并给原因",
        True,
        {
            "type": "object",
            "properties": {
                "industry": {
                    "type": "string",
                    "description": "申万一级行业名（与 config/industry.yaml 一致，如「食品饮料」）",
                }
            },
            "required": ["industry"],
        },
        "sentiment",
    ),
    # R6 资讯雷达：统一资讯流 + 事件日历（都只读；模型叙述默认关闭）
    ToolSpec(
        "news_stream",
        "公告/研报/快讯/政策的统一资讯流（去重合并 + 规则事件抽取，只落本机）",
        True,
        _date_schema(
            {
                "until": {
                    "type": "string",
                    "format": "date",
                    "description": "区间右端 YYYY-MM-DD；缺省等于 date（窗口上限 31 天）",
                },
                "sources": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "栏目名（announcement_cninfo / flash_cls / ...）；缺省全部",
                },
                "codes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "证券代码过滤；公告类栏目需要它",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 500,
                    "description": "每栏取数上限（默认 100）",
                },
                "ai": {
                    "type": "boolean",
                    "default": False,
                    "description": "模型叙述：默认 false，只给规则已抽出的事件补一句叙述",
                },
                "store": {
                    "type": "boolean",
                    "default": False,
                    "description": "默认不写本机库（保持本工具只读）；true 才落 data/news.sqlite",
                },
            }
        ),
        "news",
    ),
    ToolSpec(
        "event_calendar",
        "事件日历：财报披露日 / 解禁日 / 股东大会 / 政策发布窗口；无数据源的小节显式标 no_source",
        True,
        {
            "type": "object",
            "properties": {
                "month": {
                    "type": "string",
                    "pattern": r"^\d{4}-\d{2}$",
                    "description": "月份 YYYY-MM",
                },
                "codes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "按代码过滤；解禁栏目必需",
                },
                "fetch": {
                    "type": "boolean",
                    "default": True,
                    "description": "false = 只读本机已入库事件，不向 peer 取数",
                },
            },
            "required": ["month"],
        },
        "news",
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
