"""产业研究五维口径的黄金值夹具跑批（R5，QUOT-143）。

夹具在 `tests/fixtures/industry/I*.json`，读写与注入由 `fixtures/industry_cases.py`
统一提供。`expected.metrics` 刻意写成「该 case 承诺的指标路径全列出」而不是只列有值的
那些——空位必须写 `null`，这样新增承诺字段而夹具没跟上时
`test_golden_fixture_values` 直接变红，验收条款「每个字段要么有值要么显式 null」
才是可证的。期望值全部手算，算式在 `docs/specs/SPEC-INDUSTRY-METRICS.md`。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fixtures.industry_cases import (
    CASES,
    IDS,
    dimensions_leaf,
    load_case,
    prepare,
)

from ladderstock.data import provider_registry
from ladderstock.industry import snapshot as snapshot_module
from ladderstock.industry.catalog import load_catalog
from ladderstock.industry.snapshot import industry_snapshot


def run_case(case: dict[str, Any], tmp_path: Path) -> tuple[dict[str, Any], Any]:
    data, config = prepare(case, tmp_path)
    catalog = load_catalog(config)
    return industry_snapshot(case["industry"], root=data, config_root=config), catalog


# --------------------------------------------------------------------------- #
# 黄金值
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("name", IDS)
def test_golden_fixture_values(name: str, tmp_path: Path) -> None:
    case = load_case(name)
    result, catalog = run_case(case, tmp_path)
    expected = case["expected"]

    assert result["available"] is expected["available"]
    assert result["data_source_status"] == expected["data_source_status"]
    assert result["unavailable_dimensions"] == expected["unavailable_dimensions"]
    flags = {key: bool(block["available"]) for key, block in result["dimensions"].items()}
    assert flags == expected["dimensions_available"]

    for path, want in expected["metrics"].items():
        assert dimensions_leaf(result, path) == want, f"{name}：{path} 期望 {want!r}"

    for path, want in expected.get("notes", {}).items():
        assert dimensions_leaf(result, path) == want, f"{name}：{path} 期望 {want!r}"

    for path, want in expected.get("reasons", {}).items():
        got = dimensions_leaf(result, path)
        if want is None:
            assert got is None, f"{name}：{path} 不该有原因，实际 {got!r}"
        else:
            assert isinstance(got, str) and want in got, (
                f"{name}：{path} 原因里缺 {want!r}，实际 {got!r}"
            )

    for key, want in expected.get("membership", {}).items():
        assert result["membership"][key] == want, f"{name}：membership.{key}"

    # 承诺的指标路径 = 夹具列出的路径；`missing`/`metrics_present`/溯源三处必须自洽
    paths = snapshot_module.metric_paths(catalog.window)
    assert sorted(paths) == sorted(expected["metrics"])
    assert result["metrics_present"] == sorted(
        path for path in paths if expected["metrics"][path] is not None
    )
    assert result["missing"] == sorted(
        path for path in paths if expected["metrics"][path] is None
    )
    for path in paths:
        assert path in result["provenance"]["values"], f"{name}：{path} 没有溯源条目"


@pytest.mark.parametrize("name", IDS)
def test_same_fixture_twice_is_byte_identical(name: str, tmp_path: Path) -> None:
    """幂等验收：同一份缓存连跑两次，JSON 必须逐字节一致。

    时钟不进结果：`retrieved_at` 取的是缓存里的 `_ingested_at`；
    窗口、分位、中位数的输入全部先排序——这条测试就是这两件事的证明。
    """
    case = load_case(name)
    data, config = prepare(case, tmp_path)
    first = industry_snapshot(case["industry"], root=data, config_root=config)
    second = industry_snapshot(case["industry"], root=data, config_root=config)

    dump = json.dumps(first, ensure_ascii=False, sort_keys=True, indent=2, default=str)
    assert dump == json.dumps(second, ensure_ascii=False, sort_keys=True, indent=2, default=str)


def test_snapshot_is_cache_only_and_never_dials_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DEC-006「数据只落本机」的可执行证明：把传输层换成必炸的桩，快照照常出结果。"""

    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("行业快照只读本机缓存，不允许外呼")

    monkeypatch.setattr(provider_registry, "build_transport", boom)
    case = load_case("I001-all-dimensions")
    result, _ = run_case(case, tmp_path)

    assert result["available"] is True
    assert "零外呼" in result["source_detail"]


def test_provenance_points_at_the_datasets_that_contributed(tmp_path: Path) -> None:
    """R3 溯源：有值的指标必须引用到对应数据集，结构性缺口引用为空。"""
    case = load_case("I001-all-dimensions")
    result, _ = run_case(case, tmp_path)
    provenance = result["provenance"]

    assert {item["id"] for item in provenance["sources"]} == {
        "cfg:industry_yaml",
        "ds:daily_k",
        "ds:finance_indicators",
        "ds:fund_flow",
        "ds:industry_fund_flow",
        "ds:sw_industry_map",
        "ds:valuation_history",
    }
    for item in provenance["sources"]:
        assert set(item) == {"id", "dataset", "params", "retrieved_at"}
        assert item["retrieved_at"], item["id"]

    values = provenance["values"]
    assert values["prosperity.roe_median"]["sources"] == ["ds:finance_indicators"]
    assert values["capital.constituents.main_net_inflow_nd"]["sources"] == ["ds:fund_flow"]
    assert values["capital.board.main_net_inflow"]["sources"] == ["ds:industry_fund_flow"]
    assert values["valuation.pe_percentile"]["sources"] == ["ds:valuation_history"]
    assert values["momentum.change_pct_2d"]["sources"] == ["ds:daily_k"]
    assert values["crowding.turnover_median"]["sources"] == ["ds:valuation_history"]
    assert values["membership.as_of"]["sources"] == ["ds:sw_industry_map"]
    assert values["industry.name"]["sources"] == ["cfg:industry_yaml"]
    # 北向与申万指数历史是结构性缺口：没有来源，只能 unverified
    assert values["capital.northbound"]["sources"] == []
    assert values["capital.northbound"]["unverified"] is True
    assert values["momentum.index_change_pct"]["sources"] == []
    assert values["momentum.index_change_pct"]["unverified"] is True
    assert provenance["unverified"] is True


def test_unknown_industry_keeps_the_schema_and_offers_candidates(tmp_path: Path) -> None:
    """打错名字：结构照旧铺满，另给候选清单，不猜近似名。"""
    case = load_case("I003-empty-cache")
    data, config = prepare(case, tmp_path)
    catalog = load_catalog(config)
    in_list = industry_snapshot(case["industry"], root=data, config_root=config)
    wrong = industry_snapshot("食品饮料食", root=data, config_root=config)

    assert wrong["available"] is False
    assert wrong["industry"]["in_catalog"] is False
    assert wrong["industry"]["name"] == "食品饮料食"
    assert wrong["candidates"] == catalog.names()
    assert "不在 config/industry.yaml 清单里" in wrong["reason"]
    assert wrong["metrics_present"] == []
    assert sorted(snapshot_module.metric_paths(catalog.window)) == wrong["missing"]
    assert set(wrong["dimensions"]) == set(snapshot_module.DIMENSIONS)
    # 承诺的键位与清单内行业同一套：下游不必为「名字打错」多写一条分支
    for path in snapshot_module.metric_paths(catalog.window):
        assert dimensions_leaf(wrong, path) is None, path
        assert dimensions_leaf(in_list, path) is None, path


def test_industry_list_reports_availability_per_industry(tmp_path: Path) -> None:
    """`ldst industry list` 的载荷：清单永远可读，逐行业给出可用性与成分数（一批读库）。"""
    case = load_case("I001-all-dimensions")
    data, config = prepare(case, tmp_path)

    result = snapshot_module.industry_list(root=data, config_root=config)
    rows = {row["name"]: row for row in result["industries"]}

    assert result["count"] == 3
    assert result["caliber"] == "sw_l1"
    assert result["available"] is True
    assert rows["食品饮料"]["constituent_count"] == 2
    assert rows["食品饮料"]["available"] is True
    assert rows["食品饮料"]["data_source_status"] == "ok"
    assert rows["综合"]["bound"] is False
    assert rows["综合"]["available"] is False
    assert rows["电子"]["constituent_count"] == 2  # 600003 + 改分类之后的 600004
    assert set(result["dimensions"]) == set(snapshot_module.DIMENSIONS)
    assert result["source_detail"].startswith("本地缓存回放：")
    assert result["source_detail"].endswith("（零外呼）")


def test_windows_and_formulas_travel_with_the_snapshot(tmp_path: Path) -> None:
    """口径自解释：窗口参数、公式原文、缺数策略都随结果出，调用方不必先读文档。"""
    case = load_case("I001-all-dimensions")
    result, catalog = run_case(case, tmp_path)

    assert result["windows"] == catalog.window == {
        "fund_days": 3,
        "valuation_lookback_days": 4,
        "turnover_lookback_days": 3,
        "momentum_windows": [2],
    }
    assert set(result["formulas"]) == set(snapshot_module.DIMENSIONS)
    assert "中位数" in result["formulas"]["prosperity"]
    assert "经验分位" in result["formulas"]["valuation"]
    assert "不混算" in result["caliber_policy"]
    assert result["datasets"]["fund_flow"]["records"] == 9
    assert result["datasets"]["fund_flow"]["as_of"] == "2026-01-07"


def test_cases_cover_every_branch() -> None:
    """夹具本身不许空转：五份分别覆盖齐、闸门、空缓存、板块守卫、未绑定。"""
    assert len(CASES) == 5
    for name in IDS:
        load_case(name)
