"""离线 peer 桩：所有测试都只打本地假数据，**不发任何网络请求**。

`FakeTransport` 实现 `provider_registry.Transport` 协议，按 tool 名分派；
`SOURCES_DOC` / `SYNC_DOC` 是 `config/sources.yaml`、`config/sync.yaml` 的
最小子集，够覆盖主源 → 同 peer 备源 → 缓存回放三条路径。

这里刻意把 `retries` 全部设为 0：一个测试关注「降级顺序」时不该被重试次数干扰；
要验证有界重试的用例自己 deepcopy 后改 `retries`。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from ladderstock.data.provider_registry import (
    PeerEndpoint,
    ProviderRegistry,
    SourceUnavailable,
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
                "fallback": ["wire_timeline"],
                "watermark_field": "data_as_of",
                "mode": "replace",
                "optional": True,
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
        return handler(dict(params))

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
    records: list[dict[str, Any]],
    *,
    fetched_at: str = "2026-10-24T15:05:00+08:00",
    cache_hit: bool = False,
    source: str = "stub",
) -> dict[str, Any]:
    """TickHub-CN `th-cn tool call` 的返回形状（ok/data/meta）。"""
    return {
        "ok": True,
        "data": list(records),
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


def wire_payload(day: str = "2026-10-24") -> dict[str, Any]:
    """Wire 叙事信封：没有行概念，整包落一行。"""
    return {
        "data_as_of": day,
        "fetched_at": f"{day}T08:30:00+08:00",
        "source_status": "ok",
        "freshness": "realtime",
        "confidence": "medium",
        "payload": {"digest": "题材面情绪回暖", "themes": ["半导体", "机器人"]},
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
