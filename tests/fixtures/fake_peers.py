"""离线 peer 桩：所有测试都只打本地假数据，**不发任何网络请求**。

`FakeTransport` 实现 `provider_registry.Transport` 协议，按 tool 名分派；
`SOURCES_DOC` / `SYNC_DOC` 是 `config/sources.yaml`、`config/sync.yaml` 的
最小子集，够覆盖主源 → 同 peer 备源 → 缓存回放三条路径。

这里刻意把 `retries` 全部设为 0：一个测试关注「降级顺序」时不该被重试次数干扰；
要验证有界重试的用例自己 deepcopy 后改 `retries`。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from ladderstock.data.provider_registry import (
    PeerEndpoint,
    ProviderRegistry,
    SourceUnavailable,
    _unwrap_mcp_result,
)

# peer 地址只用回环占位，桩 transport 从不去拨；环境变量名刻意取不存在的名字，
# 避免真的把 `LDST_*_MCP_URL` 读进来。
SOURCES_DOC: dict[str, Any] = {
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
        "senate": {
            "product": "StockSenate",
            "cli": "ssenate",
            "mcp_name": "stock-senate",
            "url_env": "LDST_TEST_FAKE_SENATE_URL",
            "default_url": "http://127.0.0.1:1/mcp",
            "timeout_sec": 1,
        },
    },
    "sources": [
        {
            "id": "tickhub_kline",
            "dataset": "daily_k",
            "via": "tickhub",
            "tool": "tencent_kline",
            "primary": True,
            "fallback": ["tickhub_tdx_package"],
            "timeout_sec": 1,
            "retries": 0,
            "cooldown_sec": 60,
        },
        {
            "id": "tickhub_tdx_package",
            "dataset": "daily_k",
            "via": "tickhub",
            "tool": "tdx_daily_package",
            "primary": False,
            "fallback": [],
            "timeout_sec": 1,
            "retries": 0,
            "cooldown_sec": 120,
        },
        {
            "id": "tickhub_limit_pool",
            "dataset": "limit_pool",
            "via": "tickhub",
            "tool": "limit_up_pool",
            "primary": True,
            "fallback": ["tickhub_ths_limit_pool"],
            "timeout_sec": 1,
            "retries": 0,
            "cooldown_sec": 60,
        },
        {
            "id": "tickhub_ths_limit_pool",
            "dataset": "limit_pool",
            "via": "tickhub",
            "tool": "ths_limit_up_pool",
            "primary": False,
            "fallback": [],
            "timeout_sec": 1,
            "retries": 0,
            "cooldown_sec": 60,
        },
        {
            # 与真实 config/sources.yaml 同构：`wire_timeline` 在这里是**能力登记**
            # （同 peer、工具在册），但 `sync.yaml` 的 theme_news 链路显式排除了它（DEC-043）。
            "id": "wire_digest",
            "dataset": "theme_news",
            "via": "wire",
            "tool": "wire_get_digest",
            "primary": True,
            "fallback": ["wire_timeline"],
            "timeout_sec": 1,
            "retries": 0,
            "cooldown_sec": 120,
        },
        {
            "id": "wire_timeline",
            "dataset": "theme_news",
            "via": "wire",
            "tool": "wire_get_timeline",
            "primary": False,
            "fallback": [],
            "timeout_sec": 1,
            "retries": 0,
            "cooldown_sec": 120,
        },
        {
            "id": "senate_reports",
            "dataset": "council_memo",
            "via": "senate",
            "tool": "report_query",
            "primary": True,
            "fallback": [],
            "timeout_sec": 1,
            "retries": 0,
            "cooldown_sec": 180,
        },
    ],
}

SYNC_DOC: dict[str, Any] = {
    "sync": {
        "datasets": {
            "daily_k": {
                "primary": "tickhub_kline",
                "fallback": ["tickhub_tdx_package"],
                "watermark_field": "trade_date",
                "mode": "append",
                # `tencent_kline` 的 code 是必填（QUOT-233），桩里的池子给一只票，
                # 让「一次同步 = 一次调用」的断言保持原样；多只票的扇出另有专门用例。
                "codes": ["600000"],
            },
            "limit_pool": {
                "primary": "tickhub_limit_pool",
                "fallback": ["tickhub_ths_limit_pool"],
                "watermark_field": "trade_date",
                "mode": "replace",
            },
            "theme_news": {
                "primary": "wire_digest",
                # 显式空值 = 「这条链只有主源」，`chain()` 不回填 sources.yaml 的
                # fallback（DEC-043）：`wire_get_timeline` 要 `event_id`，按日摘要没有事件维度。
                "fallback": [],
                "watermark_field": "data_as_of",
                "mode": "replace",
                "optional": True,
                # 赛道池（QUOT-328）：`wire_get_digest` 的 `topic` 必填、只认 Wire 的赛道
                # slug，一个 slug 一次调用。桩里给两个，够锁「扇出次数 = slug 数」。
                "topics": ["ai", "semiconductor"],
            },
            "council_memo": {
                "primary": "senate_reports",
                "fallback": [],
                "watermark_field": "data_as_of",
                "mode": "replace",
                "optional": True,
            },
        },
        "cold_hot": {"intraday_cache_ttl_sec": 60, "history_backend": "duckdb"},
    }
}


@dataclass(frozen=True)
class Call:
    """一次 peer 工具调用，供断言「打了谁、带了什么参数」。"""

    peer: str
    tool: str
    params: dict[str, Any]


@dataclass
class FakeTransport:
    """按 tool 名分派的离线传输。

    - `handlers[tool]` 返回响应字典；不在表里的工具视为不可用；
    - `errors[tool]` 直接抛指定异常（用来模拟超时 / 拒绝连接）；
    - `probe_error` 让 `probe()` 也失败（`sources validate` 的不可达分支）。
    """

    handlers: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = field(default_factory=dict)
    errors: dict[str, Exception] = field(default_factory=dict)
    tools: list[str] = field(default_factory=list)
    probe_error: Exception | None = None
    calls: list[Call] = field(default_factory=list)
    probes: list[str] = field(default_factory=list)

    def call(
        self, peer: PeerEndpoint, tool: str, params: dict[str, Any], *, timeout: float
    ) -> dict[str, Any]:
        self.calls.append(Call(peer=peer.key, tool=tool, params=dict(params)))
        failure = self.errors.get(tool)
        if failure is not None:
            raise failure
        handler = self.handlers.get(tool)
        if handler is None:
            raise SourceUnavailable(f"{peer.mcp_name}/{tool} 未在桩里定义")
        # 真实 `McpTransport.call` 在返回业务 JSON 前会跑 `_unwrap_mcp_result`
        # （拆 `structuredContent` / `content[0].text` 两层壳，QUOT-328 的第二处缺陷就在
        # 这一层）。桩走同一个函数，handler 才能既回解开的信封、也回真实的字符串壳。
        return _unwrap_mcp_result(handler(dict(params)))

    def probe(self, peer: PeerEndpoint, *, timeout: float) -> dict[str, Any]:
        self.probes.append(peer.key)
        if self.probe_error is not None:
            raise self.probe_error
        return {"peer": peer.key, "tools": list(self.tools)}

    @property
    def tools_called(self) -> list[str]:
        return [call.tool for call in self.calls]


def make_registry(
    transport: FakeTransport,
    root: Path | None = None,
    *,
    sources: dict[str, Any] | None = None,
    sync: dict[str, Any] | None = None,
    ttl_sec: float = 0.0,
) -> ProviderRegistry:
    """用假配置建注册表；`ttl_sec=0` 关掉短窗缓存，避免污染调用次数断言。"""
    return ProviderRegistry(
        sources=sources if sources is not None else SOURCES_DOC,
        sync=sync if sync is not None else SYNC_DOC,
        transport=transport,
        root=root,
        ttl_sec=ttl_sec,
    )


# --------------------------------------------------------------------------- #
# 响应构造函数：形状对齐真实 peer，但不含任何域名
# --------------------------------------------------------------------------- #

def tickhub_payload(
    records: Any,
    *,
    fetched_at: str = "2026-10-24T15:05:00+08:00",
    cache_hit: bool = False,
    source: str = "stub",
) -> dict[str, Any]:
    """TickHub-CN `th-cn tool call` 的返回形状（ok/data/meta）。

    `data` 可以是记录列表，也可以是工具自己的包裹形状（cninfo 的
    `{"code": ..., "announcements": [...]}`、lockup 的 `{"history": [...], "upcoming": [...]}`），
    与真实返回一致——资讯层必须两种形状都会翻，这里就不替他抹平。
    """
    data = records if isinstance(records, (list, dict)) else list(records)
    return {
        "ok": True,
        "data": data,
        "meta": {
            "source": source,
            "fetched_at": fetched_at,
            "cache_hit": cache_hit,
            "confidence": "A",
            "trace_id": "trace-stub-1",
        },
    }


def kline_records(day: str = "2026-10-24") -> list[dict[str, Any]]:
    return [
        {"code": "600000", "trade_date": day, "close": 10.5},
        {"code": "000001", "trade_date": day, "close": 12.1},
    ]


def kline_tool_payload(
    code: str,
    days: list[str],
    *,
    fetched_at: str = "2026-10-24T15:05:00+08:00",
    period: str = "daily",
    adjust: str = "qfq",
) -> dict[str, Any]:
    """`tencent_kline` 的**真实**返回形状（QUOT-233）：`data` 是对象，K 线在 `klines` 里。

    与 `tickhub_payload(kline_records(...))` 的区别就是要锁住这一点——
    按「记录列表」解析真实返回会把整包压成 1 行、`code` 为空、日期取成抓取时间。
    """
    return {
        "ok": True,
        "data": {
            "code": code,
            "period": period,
            "adjust": adjust,
            "klines": [
                {"date": day, "open": 10.0, "close": 10.5, "high": 10.8, "low": 9.9, "volume": 1e5}
                for day in days
            ],
        },
        "meta": {
            "source": "stub",
            "fetched_at": fetched_at,
            "cache_hit": False,
            "confidence": "A",
            "trace_id": f"trace-stub-{code}",
        },
    }


def limit_records(day: str = "2026-10-24") -> list[dict[str, Any]]:
    return [
        {"code": "600519", "trade_date": day, "limit_times": 2},
        {"code": "300750", "trade_date": day, "limit_times": 1},
        {"code": "601318", "trade_date": day, "limit_times": 1},
    ]


def wire_digest_envelope(
    topic: str,
    day: str = "2026-10-24",
    *,
    status: str = "ok",
    items: int = 3,
) -> dict[str, Any]:
    """`wire_get_digest(topic, date=None)` 的**真实**返回形状（QUOT-328 实测）。

    外层是 Wire 的 `WireEnvelope`，内层是它的 `TopicDigest`：赛道名在 `payload.topic`
    （**slug**，Wire 的 `config/topics.yaml` 的 `id`），只有 `summary` / `key_points` /
    `items`，**没有任何强度或涨停家数字段**——题材强度那列本来就取不到，只能显式 `null`。
    LLM 上游不可达时 Wire 回 `status="partial"` + 一句兜底 summary（外层信封的
    `source_status` 也跟着降），不是报错——实测 2026-11-01 的 12 个赛道全是这一形状。
    """
    return {
        "data_as_of": day,
        "fetched_at": f"{day}T08:30:00+08:00",
        "source_status": "ok" if status == "ok" else "partial",
        "freshness": "realtime",
        "confidence": "medium",
        "payload": {
            "topic": topic,
            "date": day,
            "summary": "题材面情绪回暖" if status == "ok" else "共采集若干资讯；模型当前不可用。",
            "key_points": [f"要点 {i}" for i in range(items)] if status == "ok" else [],
            "items": [{"title": f"资讯 {i}", "url": f"about:item-{i}"} for i in range(items)],
            "generated_at": f"{day}T08:29:00+08:00",
            "provider": "stub",
            "model": "stub-model",
            "partial": status == "partial",
            "status": status,
            "error": None,
            "fallback": False,
        },
    }


def wire_digest_tool_result(topic: str, day: str = "2026-10-24", **kwargs: Any) -> dict[str, Any]:
    """把上面的信封**序列化成字符串**：真实 FastMCP 把 `model_dump_json()` 放进
    `structuredContent.result`，`_unwrap_mcp_result` 不认这一层壳就会落一行空壳。
    """
    return {
        "structuredContent": {
            "result": json.dumps(wire_digest_envelope(topic, day, **kwargs), ensure_ascii=False)
        }
    }


def senate_payload(day: str = "2026-10-24") -> dict[str, Any]:
    """Senate 已生成报告摘要（只读，不发起合议）。"""
    return {
        "code": "600000",
        "data_as_of": day,
        "fetched_at": f"{day}T20:00:00+08:00",
        "data_source_status": "ok",
        "payload": {"summary": "维持观察", "report_id": "r-1"},
    }


def day(value: str) -> date:
    return date.fromisoformat(value)


# --------------------------------------------------------------------------- #
# R6 资讯雷达：数据集 → 工具登记表，与 config/sources.yaml + config/sync.yaml 同构
# --------------------------------------------------------------------------- #

#: 每个资讯数据集只有一个 owning peer + 一条工具族（DEC-006 闸门 1/3）
NEWS_TOOL_BY_DATASET: dict[str, str] = {
    "announcement_cninfo": "cninfo_announcements",
    "announcement_szse": "szse_announcements",
    "research_report": "eastmoney_reports",
    "news_eastmoney_stock": "eastmoney_stock_news",
    "news_cctv": "cctv_news",
    "flash_cls": "cls_telegraph",
    "flash_wallstreetcn": "wallstreetcn_lives",
    "flash_eastmoney": "eastmoney_global_news",
    "lockup_calendar": "lockup_expiry",
    "earnings_preview": "earnings_forecast",
}

#: 备源一律同 peer（闸门 3），id → (dataset, tool)
NEWS_FALLBACK_TOOLS: dict[str, tuple[str, str]] = {
    "announcement_cninfo": ("tickhub_announce_cninfo_backup", "announcements_backup"),
    "announcement_szse": ("tickhub_announce_szse_backup", "announcements_backup"),
    "research_report": ("tickhub_research_report_sina", "sina_research_reports"),
}


def news_sources_doc() -> dict[str, Any]:
    """资讯数据集的最小 `sources.yaml`（主源 + 同 peer 备源）。"""
    sources: list[dict[str, Any]] = []
    for dataset, tool in NEWS_TOOL_BY_DATASET.items():
        fallbacks: list[str] = []
        if dataset in NEWS_FALLBACK_TOOLS:
            fallback_id, fallback_tool = NEWS_FALLBACK_TOOLS[dataset]
            fallbacks.append(fallback_id)
            sources.append(
                {
                    "id": fallback_id,
                    "dataset": dataset,
                    "via": "tickhub",
                    "tool": fallback_tool,
                    "primary": False,
                    "fallback": [],
                    "timeout_sec": 1,
                    "retries": 0,
                    "cooldown_sec": 120,
                }
            )
        sources.append(
            {
                "id": f"tickhub_{dataset}",
                "dataset": dataset,
                "via": "tickhub",
                "tool": tool,
                "primary": True,
                "fallback": fallbacks,
                "timeout_sec": 1,
                "retries": 0,
                "cooldown_sec": 120,
            }
        )
    return {"peers": dict(SOURCES_DOC["peers"]), "sources": sources}


def news_sync_doc() -> dict[str, Any]:
    """资讯数据集的 `sync.yaml`：`chain()` 只认登记过的数据集，所以这步不能省。"""
    datasets = {
        dataset: {
            "primary": f"tickhub_{dataset}",
            "fallback": (
                [NEWS_FALLBACK_TOOLS[dataset][0]] if dataset in NEWS_FALLBACK_TOOLS else []
            ),
            "watermark_field": "data_as_of",
            "mode": "append",
            "optional": True,
        }
        for dataset in NEWS_TOOL_BY_DATASET
    }
    return {"sync": {"datasets": datasets, "cold_hot": {"intraday_cache_ttl_sec": 60}}}


def make_news_registry(
    transport: FakeTransport, root: Path | None = None, *, ttl_sec: float = 0.0
) -> ProviderRegistry:
    return make_registry(
        transport,
        root,
        sources=news_sources_doc(),
        sync=news_sync_doc(),
        ttl_sec=ttl_sec,
    )
