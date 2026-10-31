"""`ldst industry` 的命令面契约（QUOT-143，docs/CLI.md §D‴）。

钉四件事：

1. 只读本机缓存，`--output json` 的 stdout 必须是纯 JSON；
2. 指标不可用 → 退出码 **6**，但 stdout 仍给完整结构化结果（DEC-012）；
3. 配置读不动 → **3**，不静默兜底成空清单；
4. 外层信封与 `ldst pool` 同一条装配路径（域 `sentiment`、topic `industry_snapshot`）。

数据与配置都落在本次测试的 tmp 里（`fixtures/industry_cases.prepare`），
不碰仓库的 `data/`，也不依赖别的任务往本机同步了什么。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fixtures.industry_cases import load_case, prepare
from typer.testing import CliRunner

from ladderstock.cli.main import app

runner = CliRunner()

#: `LadderEnvelope` 契约字段 + 缓存溯源两件套（与 `ldst pool` 完全同一份装配路径）
ENVELOPE_KEYS = {
    "domain",
    "topic",
    "event_id",
    "data_as_of",
    "payload",
    "source",
    "source_detail",
    "fetched_at",
    "freshness",
    "confidence",
    "source_status",
    "cached_at",
    "backend",
}


def _envelope_without_fetched_at(text: str) -> dict[str, Any]:
    """命令面的幂等比较：`fetched_at` 是信封的取值时刻，每次运行必然不同。

    去掉它之后必须逐字段相等——这正好把「幂等」的责任压回数据本身：
    溯源时间来自缓存的 `_ingested_at`，聚合输入一律先排序，没有 now() 参与公式。
    """
    doc = json.loads(text)
    doc.pop("fetched_at")
    return doc


def test_list_is_always_readable_even_with_empty_cache(
    repo: Path, tmp_path: Path, monkeypatch: Any
) -> None:
    """清单是配置视图：本机一个字节都没同步也 0 退出，指标缺就逐行显式 null。"""
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path / "data"))
    result = runner.invoke(app, ["industry", "list", "--output", "json"])

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["count"] == 31
    assert payload["caliber"] == "sw_l1"
    assert payload["available"] is False
    assert payload["windows"]["fund_days"] == 5
    names = {row["name"] for row in payload["industries"]}
    assert {"食品饮料", "电子", "综合"} <= names
    assert all(row["data_source_status"] == "missing" for row in payload["industries"])
    assert all(row["constituent_count"] == 0 for row in payload["industries"])


def test_show_with_empty_cache_exits_6_but_prints_the_full_shape(
    repo: Path, tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path / "data"))
    result = runner.invoke(app, ["industry", "show", "食品饮料", "--output", "json"])

    assert result.exit_code == 6, result.stdout
    assert "行业指标不可用" in result.stderr
    envelope = json.loads(result.stdout)  # 6 也要有可解析的机器可读结果
    assert set(envelope) == ENVELOPE_KEYS
    assert envelope["domain"] == "sentiment"
    assert envelope["topic"] == "industry_snapshot"
    assert envelope["source_status"] == "partial"
    assert envelope["data_as_of"] is None
    metrics = envelope["payload"]
    assert metrics["data_source_status"] == "missing"
    assert metrics["available"] is False
    assert sorted(metrics["dimensions"]) == [
        "capital",
        "crowding",
        "momentum",
        "prosperity",
        "valuation",
    ]
    assert all(block["available"] is False for block in metrics["dimensions"].values())


def test_show_seeded_cache_exits_0_and_is_idempotent(
    repo: Path, tmp_path: Path, monkeypatch: Any
) -> None:
    case = load_case("I001-all-dimensions")
    data, config = prepare(case, tmp_path)
    monkeypatch.setenv("LDST_DATA_DIR", str(data))
    monkeypatch.setenv("LDST_CONFIG_DIR", str(config))

    first = runner.invoke(app, ["industry", "show", "食品饮料", "--output", "json"])
    second = runner.invoke(app, ["industry", "show", "食品饮料", "--output", "json"])

    assert first.exit_code == 0, first.stderr
    assert second.exit_code == 0, second.stderr
    # 幂等验收落在命令面：除取值时刻外两次运行逐字段一致
    assert _envelope_without_fetched_at(first.stdout) == _envelope_without_fetched_at(
        second.stdout
    )

    envelope = json.loads(first.stdout)
    assert envelope["data_as_of"] == "2026-01-05"
    metrics = envelope["payload"]["dimensions"]
    assert metrics["prosperity"]["revenue_yoy_median"] == 10.5
    assert metrics["capital"]["constituents"]["main_net_inflow_nd"] == 4500.0
    assert metrics["capital"]["board"]["main_net_inflow"] == 123456789.0
    assert metrics["valuation"]["pe_percentile"] == 0.25
    assert metrics["momentum"]["change_pct_2d"] == 5.0
    assert metrics["crowding"]["fund_concentration_pct"] == 85.0


def test_show_unknown_industry_exits_6_and_lists_candidates(
    repo: Path, tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path / "data"))
    result = runner.invoke(app, ["industry", "show", "白酒", "--output", "json"])

    assert result.exit_code == 6
    assert "不在 config/industry.yaml 清单里" in result.stderr
    payload = json.loads(result.stdout)["payload"]
    assert payload["industry"]["in_catalog"] is False
    assert "食品饮料" in payload["candidates"]


def test_missing_config_exits_3_without_inventing_a_list(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """没有清单就没有行业：按 3 退出，不能退化成「空清单 + 0」。"""
    monkeypatch.setenv("LDST_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path / "data"))

    listing = runner.invoke(app, ["industry", "list", "--output", "json"])
    showing = runner.invoke(app, ["industry", "show", "食品饮料", "--output", "json"])

    assert listing.exit_code == 3
    assert showing.exit_code == 3
    assert "config/industry.yaml" in listing.stderr


def test_help_lists_the_two_leaves(repo: Path) -> None:
    result = runner.invoke(app, ["industry", "--help"])
    assert result.exit_code == 0
    assert "list" in result.stdout
    assert "show" in result.stdout
