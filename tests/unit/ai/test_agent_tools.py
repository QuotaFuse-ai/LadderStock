"""agent 工具（`build_tools`）的边界与取数行为。

这批工具是「模型自主取数」的唯一入口，所以只测三件事：

1. 工具清单别悄悄变少（`Assistant` 的路由提示词是照着这批名字写的）；
2. 本机缓存读得到就**不联网**，读不到才现取，且来源标注要跟着变（`本机缓存 · daily_k`
   vs `现取 · tickhub tencent_kline`）——标错来源比取不到更糟；
3. 取不到时给模型的是一句原因，不是编出来的 0 或空数组。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ladderstock.ai.agent_runtime import ToolResult, build_tools
from ladderstock.data import duckdb_store

EXPECTED_TOOLS = {
    "lookup_symbols",
    "lookup_quote",
    "lookup_index",
    "lookup_watchlist",
    "kline_daily",
    "limit_ladder",
    "board_rank",
    "fund_flow",
    "dragon_tiger",
    "hot_rank",
    "news_flash",
    "finance_fundamentals",
    # 研究链路扩展：公告检索 / 财报三表 / 风险史 / 下院辩论 / 通用取数
    "announcement_search",
    "finance_statements",
    "risk_history",
    "senate_debate",
    "query_tool",
    # F2/F3：自主补数（ensure_data 起任务、data_task_status 查句柄、sector_digest 板块摘要）
    "ensure_data",
    "data_task_status",
    "sector_digest",
}


@pytest.fixture()
def tools(tmp_path: Path) -> dict[str, Any]:
    return {spec.name: spec for spec in build_tools(tmp_path)}


def test_tool_inventory_is_stable(tools: dict[str, Any]) -> None:
    assert set(tools) == EXPECTED_TOOLS
    assert all(spec.description and spec.label for spec in tools.values())


def test_kline_reads_local_cache_without_network(
    tmp_path: Path, tools: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def _no_network(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("本机有缓存时不该联网")

    monkeypatch.setattr("ladderstock.data.provider_registry.ProviderRegistry", _no_network)
    duckdb_store.write_dataset(
        "daily_k",
        [
            {
                "code": "600000",
                "open": 10.1,
                "close": 10.2,
                "high": 10.3,
                "low": 10.0,
                "volume": 1000,
            },
        ],
        source="test:tencent_kline",
        default_trade_date=date(2026, 11, 13),
        root=tmp_path,
    )
    duckdb_store.write_dataset(
        "daily_k",
        [
            {
                "code": "600000",
                "open": 10.2,
                "close": 10.4,
                "high": 10.5,
                "low": 10.1,
                "volume": 1200,
            },
        ],
        source="test:tencent_kline",
        default_trade_date=date(2026, 11, 16),
        root=tmp_path,
        mode="append",
    )
    result = tools["kline_daily"].invoke({"symbol": "600000", "days": 2})
    assert isinstance(result, ToolResult)
    assert [s["name"] for s in result.sources or []] == ["本机缓存 · daily_k"]
    assert result.sources[0]["as_of"] == "2026-11-16"
    rows = json.loads(result.text)["rows"]
    assert [row["close"] for row in rows] == [10.2, 10.4]
    assert result.metrics["table"]["columns"][2] == "收盘"


def test_kline_falls_back_to_live_fetch_when_cache_misses(
    tmp_path: Path, tools: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    class _Registry:
        def __init__(self, root: Any = None) -> None:
            self.root = root

        def fetch_live(self, dataset: str, *, params: dict[str, Any], **kwargs: Any) -> Any:
            calls.append({"dataset": dataset, "params": params})
            payload = {
                "records": [
                    {
                        "code": "688111",
                        "date": "2026-11-16",
                        "trade_date": "2026-11-16",
                        "open": 217.0,
                        "close": 216.53,
                        "high": 218.0,
                        "low": 216.0,
                        "volume": 1.0,
                    }
                ]
            }
            return SimpleNamespace(envelope=SimpleNamespace(payload=payload))

    monkeypatch.setattr("ladderstock.data.provider_registry.ProviderRegistry", _Registry)
    result = tools["kline_daily"].invoke({"symbol": "688111", "days": 5})
    assert calls and calls[0]["dataset"] == "kline_chart"
    assert calls[0]["params"]["code"] == "688111"
    assert [s["name"] for s in result.sources or []] == ["现取 · tickhub tencent_kline"]
    assert result.sources[0]["as_of"] == "2026-11-16"
    assert json.loads(result.text)["rows"][0]["close"] == 216.53


def test_kline_reports_gap_when_both_paths_fail(
    tmp_path: Path, tools: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("上游 502")

    monkeypatch.setattr("ladderstock.data.provider_registry.ProviderRegistry", _boom)
    result = tools["kline_daily"].invoke({"symbol": "688111"})
    assert "未取到" in result.text
    assert "502" in result.text
    assert result.metrics is None and result.sources is None


def test_cached_tool_explains_empty_cache(tmp_path: Path, tools: dict[str, Any]) -> None:
    result = tools["hot_rank"].invoke({"limit": 5})
    assert "未取到" in result.text
    assert "hot_rank" in result.text


def test_cached_tool_marks_as_of_and_caption(tmp_path: Path, tools: dict[str, Any]) -> None:
    duckdb_store.write_dataset(
        "hot_rank",
        [
            {
                "code": "600000",
                "name": "浦发银行",
                "rank": 1,
                "heat": 9000,
                "change_pct": 2.7,
                "tag": "银行",
            },
            {
                "code": "000001",
                "name": "平安银行",
                "rank": 2,
                "heat": 8000,
                "change_pct": 1.1,
                "tag": "银行",
            },
        ],
        source="test:hot_rank",
        default_trade_date=date(2026, 11, 13),
        root=tmp_path,
    )
    result = tools["hot_rank"].invoke({"limit": 2})
    # 表头必须带数据日；滞后超过阈值时会在后面追加「已滞后 N 天」提示，
    # 所以这里断言包含而非 endswith（滞后提示是后加的口径，见 test_cached_tool_code_filter）。
    assert "2026-11-13 本机缓存）" in result.metrics["table"]["caption"]
    assert result.sources[0]["name"] == "本机缓存 · hot_rank"
    assert result.sources[0]["as_of"] == "2026-11-13"


def test_lookup_symbols_resolves_colloquial_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """「茅台」这种口语称呼必须能落到 600519——这是第①步的核心诉求。"""
    from ladderstock.web.symbols import SymbolPlane

    monkeypatch.setattr(
        SymbolPlane,
        "search",
        lambda self, query, limit=8: {
            "payload": {"matches": [{"symbol": "600519", "name": "贵州茅台", "market": "SH"}]}
        },
    )
    tools = {spec.name: spec for spec in build_tools(tmp_path)}
    result = tools["lookup_symbols"].invoke({"query": "茅台"})
    doc = json.loads(result.text)
    assert doc["matches"][0]["symbol"] == "600519"


def test_lookup_symbols_asks_instead_of_guessing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ladderstock.web.symbols import SymbolPlane

    monkeypatch.setattr(
        SymbolPlane, "search", lambda self, query, limit=8: {"payload": {"matches": []}}
    )
    tools = {spec.name: spec for spec in build_tools(tmp_path)}
    result = tools["lookup_symbols"].invoke({"query": "不存在的公司"})
    assert "没有匹配" in result.text
    assert "不要猜代码" in result.text
