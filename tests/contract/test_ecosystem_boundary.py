"""四层边界必须**可执行**，而不只是文档里的一句话（DEC-006）。

对应文档第八节的四层职责与第九节的禁区：

| 层 | 产品 | LadderStock 的关系 |
|---|---|---|
| 数据底座 | TickHub-CN | 只消费 |
| 资讯感知 | QuotaFuse Wire | 可选消费 |
| 投研合议 | StockSenate | 只读消费 |
| 短线复盘 | LadderStock | 本项目 |

这里的每个断言都是一条验收口径，测试工程师可以逐条复核。
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
import yaml

from ladderstock.cli.paths import project_root, repo_root
from ladderstock.core.boundary import (
    DATASET_CATALOG,
    FORBIDDEN_HOSTS,
    FORBIDDEN_IMPORTS,
    PEERS,
    BoundaryViolation,
    assert_narrative_only,
    audit_sources,
    merge_narrative,
    owner_of,
    scan_no_collection,
)
from ladderstock.data.adapters import (
    from_data_envelope,
    from_tool_response,
    from_wire_envelope,
)
from ladderstock.errors import LadderStockError

CONFIG = project_root() / "config"


def _load(name: str) -> dict[str, Any]:
    return yaml.safe_load((CONFIG / f"{name}.yaml").read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
# 1. 数据集归属：没有归属就不许同步
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name", sorted(DATASET_CATALOG))
def test_catalog_entry_has_peer_and_tool(name: str) -> None:
    binding = DATASET_CATALOG[name]
    assert binding.peer in PEERS, f"{name} 的 peer {binding.peer} 不在生态三件套里"
    assert binding.tool, f"{name} 没有绑定任何工具，等于自建采集"
    assert binding.mode in {"append", "replace"}
    assert binding.watermark_field


def test_unknown_dataset_is_rejected() -> None:
    with pytest.raises(BoundaryViolation):
        owner_of("my_own_scraper_output")


def test_source_tree_contains_no_self_collection() -> None:
    result = scan_no_collection(repo_root())
    assert result.ok, result.violations


def test_config_contains_no_vendor_hosts_or_collectors() -> None:
    for path in sorted(CONFIG.glob("*.yaml")):
        text = path.read_text(encoding="utf-8").lower()
        for host in FORBIDDEN_HOSTS:
            assert host not in text, f"{path.name} 直连行情站点 {host}"
        for lib in FORBIDDEN_IMPORTS:
            assert f"import {lib}" not in text, f"{path.name} 引用采集库 {lib}"


# --------------------------------------------------------------------------- #
# 2. 配置与目录必须一致：主备同 peer
# --------------------------------------------------------------------------- #

def test_sync_datasets_are_registered_and_match_catalog() -> None:
    datasets = _load("sync")["sync"]["datasets"]
    assert datasets, "sync.yaml 至少要同步一个数据集"
    for name, cfg in datasets.items():
        binding = owner_of(name)
        assert cfg["mode"] == binding.mode, f"{name} 的增量模式与目录不一致"
        assert cfg["watermark_field"] == binding.watermark_field


def test_sources_and_sync_respect_boundary() -> None:
    problems = audit_sources(_load("sources"), _load("sync"))
    assert problems == [], problems


def test_every_sync_dataset_has_a_primary_source() -> None:
    sources = _load("sources")["sources"]
    primaries = {s["dataset"] for s in sources if s["primary"]}
    for name in _load("sync")["sync"]["datasets"]:
        assert name in primaries, f"{name} 没有主源"


def test_peers_block_matches_ecosystem() -> None:
    peers = _load("sources")["peers"]
    assert set(peers) == set(PEERS)
    for key, peer in peers.items():
        assert peer["package"] == PEERS[key].package
        assert peer["cli"] == PEERS[key].cli
        assert peer["mcp_name"] == PEERS[key].mcp_name


# --------------------------------------------------------------------------- #
# 3. 入口唯一：三个 peer 的返回只能经 adapter 变成信封
# --------------------------------------------------------------------------- #

TICKHUB_LIMIT_POOL = {
    "ok": True,
    "data": [{"code": "600000", "name": "浦发银行", "boards": 2}],
    "meta": {
        "source": "tencent",
        "fetched_at": "2026-10-27T15:05:00+08:00",
        "cache_hit": False,
        "confidence": "A",
        "trace_id": "trace-1",
    },
}


def test_tickhub_adapter_maps_envelope() -> None:
    env = from_tool_response("limit_pool", TICKHUB_LIMIT_POOL)
    assert env.source == "tickhub_cn"
    assert env.source_status == "ok"
    assert env.confidence == "high"
    assert env.freshness == "realtime"
    assert env.data_as_of == date(2026, 10, 27)
    assert env.event_id == "trace-1"
    assert env.payload["tool"] == "limit_up_pool"
    assert env.payload["records"] == TICKHUB_LIMIT_POOL["data"]


def test_tickhub_cache_hit_is_marked_cached() -> None:
    response = {
        **TICKHUB_LIMIT_POOL,
        "meta": {**TICKHUB_LIMIT_POOL["meta"], "cache_hit": True, "confidence": "C"},
    }
    env = from_tool_response("limit_pool", response)
    assert env.freshness == "cached"
    assert env.confidence == "low"


def test_tickhub_adapter_refuses_failure_payload() -> None:
    bad = {"ok": False, "code": "UPSTREAM_DOWN", "message": "数据源不可用"}
    with pytest.raises(LadderStockError):
        from_tool_response("limit_pool", bad)


def test_adapters_refuse_foreign_datasets() -> None:
    with pytest.raises(LadderStockError):
        from_tool_response("theme_news", TICKHUB_LIMIT_POOL)
    with pytest.raises(LadderStockError):
        from_wire_envelope("limit_pool", {})
    with pytest.raises(LadderStockError):
        from_data_envelope("theme_news", {})


def test_wire_adapter_keeps_narrative_only() -> None:
    env = from_wire_envelope(
        "theme_news",
        {
            "domain": "news",
            "topic": "semiconductor",
            "event_id": "evt-9",
            "data_as_of": "2026-10-27",
            "payload": {"summary": "半导体设备招标加速"},
            "source": "wire",
            "source_detail": "wire_get_digest",
            "fetched_at": "2026-10-27T20:00:00+08:00",
            "freshness": "realtime",
            "confidence": "high",
            "source_status": "degraded",
        },
    )
    assert env.source == "wire"
    assert env.source_status == "degraded"
    assert env.payload["dataset"] == "theme_news"
    assert env.payload["summary"] == "半导体设备招标加速"


def test_senate_adapter_maps_missing_to_partial() -> None:
    env = from_data_envelope(
        "council_memo",
        {
            "domain": "research",
            "code": "shortline_memo",
            "data_as_of": "2026-10-27",
            "payload": {"verdict": "观察"},
            "source": "stock-senate",
            "source_detail": "report_query",
            "fetched_at": "2026-10-27T21:00:00+08:00",
            "freshness": "cached",
            "confidence": "medium",
            "data_source_status": "missing",
        },
    )
    assert env.source == "stock_senate"
    assert env.source_status == "partial"
    assert env.event_id == "shortline_memo"


# --------------------------------------------------------------------------- #
# 4. 程序算数、AI 解读：AI 不能改数字
# --------------------------------------------------------------------------- #

METRICS = {"temperature": 62.5, "promotion_rate": 0.41, "ladder_height": 6}


def test_ai_narrative_is_appended_not_merged() -> None:
    merged = merge_narrative(METRICS, {"narrative": "情绪修复，但高标断层"})
    assert merged["temperature"] == 62.5
    assert merged["ai"]["narrative"] == "情绪修复，但高标断层"
    assert "narrative" not in METRICS


def test_ai_cannot_overwrite_metrics() -> None:
    with pytest.raises(BoundaryViolation):
        merge_narrative(METRICS, {"temperature": 80.0})


def test_ai_cannot_smuggle_new_fields() -> None:
    with pytest.raises(BoundaryViolation):
        assert_narrative_only({"promotion_rate_estimate": 0.5}, METRICS)
