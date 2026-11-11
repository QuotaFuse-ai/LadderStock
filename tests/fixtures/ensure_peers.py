"""打开即补取（QUOT-555 / DEC-080）的离线 peer 桩。

把四个补取组（theme / news / pool / industry）需要的数据集登记进一份**最小** sources/sync，
代码池刻意只给两只票（`600519` / `000858`），让「一次补取 = 有界几次调用」可断言，
也用来钉住 industry 走的是 base_pool 常规口径、**不是**锚点扩池（DEC-074 付费动作）。

news 组复用 `fake_peers.make_news_registry` + `news_samples.news_handlers`（另有其形状）。
所有 handler 只回本地假数据，`FakeTransport` 从不拨号。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fixtures.fake_peers import (
    FakeTransport,
    make_registry,
    tickhub_payload,
    wire_digest_tool_result,
)

from ladderstock.data.provider_registry import ProviderRegistry

#: 补取组用到的数据集 → (peer, tool)。与真实 config/sources.yaml 的主源同名。
ENSURE_TOOL_BY_DATASET: dict[str, tuple[str, str]] = {
    "theme_news": ("wire", "wire_get_digest"),
    "limit_pool": ("tickhub", "limit_up_pool"),
    "concept_board": ("tickhub", "concept_boards"),
    "fund_flow": ("tickhub", "stock_fund_flow"),
    "concept_membership": ("tickhub", "stock_concept_blocks"),
    "fund_flow_realtime": ("tickhub", "stock_fund_flow_batch"),
    "sw_industry_map": ("tickhub", "sw_industry_history"),
    "finance_indicators": ("tickhub", "eastmoney_finance_indicators"),
    "valuation_history": ("tickhub", "baostock_valuation_history"),
    "industry_fund_flow": ("tickhub", "board_fund_flow"),
    "daily_k": ("tickhub", "tencent_kline"),
}

#: 常规口径代码池（base_pool 的子集）：industry 锚点与它重合，补取后即可见成分。
ENSURE_CODES: tuple[str, ...] = ("600519", "000858")

#: 逐只数据集（`code` 必填，走 sync_code_pool）；其余整批 / 赛道另算。
PER_CODE_DATASETS = {
    "daily_k",
    "fund_flow",
    "sw_industry_map",
    "finance_indicators",
    "valuation_history",
}

L1_BY_CODE = {"600519": "340000", "000858": "340000"}


def ensure_sources_doc(datasets: list[str]) -> dict[str, Any]:
    """按数据集生成最小 sources.yaml（每个数据集一个主源，无备源）。"""
    sources: list[dict[str, Any]] = []
    for dataset in datasets:
        peer, tool = ENSURE_TOOL_BY_DATASET[dataset]
        sources.append(
            {
                "id": f"ensure_{dataset}",
                "dataset": dataset,
                "via": peer,
                "tool": tool,
                "primary": True,
                "fallback": [],
                "timeout_sec": 1,
                "retries": 0,
                "cooldown_sec": 120,
            }
        )
    return {
        "peers": {
            "tickhub": {
                "product": "TickHub-CN",
                "cli": "th-cn",
                "mcp_name": "th-cn",
                "url_env": "LDST_TEST_FAKE_TICKHUB_URL",
                "default_url": "http://127.0.0.1:1/mcp",
                "timeout_sec": 1,
            },
            "wire": {
                "product": "QuotaFuse Wire",
                "cli": "qfw",
                "mcp_name": "qf-wire",
                "url_env": "LDST_TEST_FAKE_WIRE_URL",
                "default_url": "http://127.0.0.1:1/mcp",
                "timeout_sec": 1,
            },
        },
        "sources": sources,
    }


def ensure_sync_doc(
    datasets: list[str], *, codes: tuple[str, ...] = ENSURE_CODES
) -> dict[str, Any]:
    """按数据集生成最小 sync.yaml：逐只数据集给常规代码池，theme_news 给两个赛道。"""
    declared: dict[str, Any] = {}
    for dataset in datasets:
        _peer, tool = ENSURE_TOOL_BY_DATASET[dataset]
        entry: dict[str, Any] = {
            "primary": f"ensure_{dataset}",
            "fallback": [],
            "watermark_field": "data_as_of" if dataset == "theme_news" else "trade_date",
            "mode": "append" if dataset in PER_CODE_DATASETS else "replace",
        }
        if dataset in PER_CODE_DATASETS:
            entry["codes"] = list(codes)
        if dataset == "theme_news":
            entry["optional"] = True
            entry["topics"] = ["ai", "semiconductor"]
        if dataset in ("concept_membership", "fund_flow_realtime"):
            entry["optional"] = True
        declared[dataset] = entry
    return {"sync": {"datasets": declared, "cold_hot": {"intraday_cache_ttl_sec": 60}}}


def make_ensure_registry(
    transport: FakeTransport,
    root: Path | None = None,
    *,
    datasets: list[str],
    codes: tuple[str, ...] = ENSURE_CODES,
    ttl_sec: float = 0.0,
) -> ProviderRegistry:
    return make_registry(
        transport,
        root,
        sources=ensure_sources_doc(datasets),
        sync=ensure_sync_doc(datasets, codes=codes),
        ttl_sec=ttl_sec,
    )


def ensure_transport(day: str = "2026-10-24", *, failing: tuple[str, ...] = ()) -> FakeTransport:
    """四个组（除 news）的最小真实形状 handler；`failing` 里的工具改由 errors 制造失败。"""

    def wire_digest(params: dict[str, Any]) -> dict[str, Any]:
        return wire_digest_tool_result(str(params["topic"]), day)

    def limit_pool(_params: dict[str, Any]) -> dict[str, Any]:
        return tickhub_payload(
            [
                {"code": "600519", "name": "贵州茅台", "boards": 3,
                 "trade_date": day, "turnover_rate": 8.0},
                {"code": "000858", "name": "五粮液", "boards": 1,
                 "trade_date": day, "turnover_rate": 3.0},
            ]
        )

    def concept_board(_params: dict[str, Any]) -> dict[str, Any]:
        return tickhub_payload([{"name": "白酒", "change_pct": 4.0, "trade_date": day}])

    def concept_membership(_params: dict[str, Any]) -> dict[str, Any]:
        return tickhub_payload([{"code": "600519", "name": "白酒", "trade_date": day}])

    def fund_flow(params: dict[str, Any]) -> dict[str, Any]:
        return tickhub_payload(
            {
                "code": str(params.get("code") or "600519"),
                "name": "stub",
                "flows": [{"date": day, "main_net": 1000.0, "main_net_pct": 2.0}],
            }
        )

    def fund_flow_batch(_params: dict[str, Any]) -> dict[str, Any]:
        return tickhub_payload(
            [{"code": "600519", "trade_date": day, "main_net": 1000.0, "main_net_pct": 2.0}]
        )

    def sw(params: dict[str, Any]) -> dict[str, Any]:
        code = str(params["code"])
        return tickhub_payload(
            {
                "code": code,
                "as_of": None,
                "current": {"l1_code": L1_BY_CODE.get(code, "340000")},
                "history": [
                    {"code": code, "start_date": "2026-01-05",
                     "l1_code": L1_BY_CODE.get(code, "340000"), "l1_name": "食品饮料"}
                ],
            }
        )

    def finance(params: dict[str, Any]) -> dict[str, Any]:
        return tickhub_payload(
            {
                "code": str(params["code"]),
                "indicators": [
                    {"report_date": "2026-09-30 00:00:00",
                     "revenue_yoy": 10.0, "profit_yoy": 12.0, "roe": 15.0}
                ],
            }
        )

    def valuation(params: dict[str, Any]) -> dict[str, Any]:
        return tickhub_payload(
            {
                "code": str(params["code"]),
                "rows": [{"date": day, "close": 10.0, "pe_ttm": 12.0, "pb": 1.5,
                          "turnover_rate": 1.2}],
            }
        )

    def kline(params: dict[str, Any]) -> dict[str, Any]:
        return tickhub_payload(
            {
                "code": str(params["code"]),
                "klines": [{"date": day, "close": 10.0, "amount": 1e8, "change_pct": 1.0}],
            }
        )

    def board_flow(_params: dict[str, Any]) -> dict[str, Any]:
        return tickhub_payload(
            {
                "scope": "industry",
                "total": 1,
                "page": 1,
                "page_size": 300,
                "items": [{"code": "BK0477", "name": "食品饮料", "main_net": 1e8,
                           "main_net_pct": 2.0, "change_pct": 1.0}],
            }
        )

    handlers: dict[str, Any] = {
        "wire_get_digest": wire_digest,
        "limit_up_pool": limit_pool,
        "concept_boards": concept_board,
        "stock_concept_blocks": concept_membership,
        "stock_fund_flow": fund_flow,
        "stock_fund_flow_batch": fund_flow_batch,
        "sw_industry_history": sw,
        "eastmoney_finance_indicators": finance,
        "baostock_valuation_history": valuation,
        "tencent_kline": kline,
        "board_fund_flow": board_flow,
    }
    errors = {tool: ConnectionError(f"{tool} 上游不可达（桩）") for tool in failing}
    return FakeTransport(
        handlers={tool: fn for tool, fn in handlers.items() if tool not in failing},
        errors=errors,
        tools=sorted(handlers),
    )


__all__ = [
    "ENSURE_CODES",
    "ENSURE_TOOL_BY_DATASET",
    "ensure_sources_doc",
    "ensure_sync_doc",
    "ensure_transport",
    "make_ensure_registry",
]