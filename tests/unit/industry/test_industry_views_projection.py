"""QUOT-276 投影层（`industry/views.py` + `industry/snapshot.py` 的 `with_metrics`）单测。

这一层的验收底线只有一条：**R9 三个接口的每个数字都能退回 R5 的同一次计算**。
所以这里的断言几乎全是「等式」而不是「范围」：

- `metric_projection()` 的键集合 = `metric_paths()`，一个不多一个不少，缺值显式 `null`；
- `industry_list(with_metrics=True)` 只在 R5 行上**加**键，默认关时载荷不变（CLI/MCP 出口）；
- `history` 序列最后一个点 = 载荷里的当期分位（同一批缓存行、同一个公式）；
- `main_net_daily` 的窗口 = R5 的 `fund_days`，齐样本日之和 = N 日主力净流入；
- 规则判定：`null` 不参与（不把缺失当 0），证据取不全的整条不出，`counts` 与 `signals` 自洽。

HTTP 外壳、错误码与闸门在 `tests/unit/web/test_industry_endpoints.py`。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fixtures.industry_cases import load_case, prepare

from ladderstock.industry import snapshot as snapshot_module
from ladderstock.industry import views
from ladderstock.industry.catalog import load_catalog
from ladderstock.industry.snapshot import industry_list, industry_snapshot, metric_paths

FULL = "I001-all-dimensions"
PARTIAL = "I002-partial-coverage"
EMPTY = "I003-empty-cache"
TARGET = "食品饮料"

DIMENSIONS = list(snapshot_module.DIMENSIONS)
EVIDENCE_KEYS = {
    "path",
    "label",
    "value",
    "unit",
    "as_of",
    "dataset",
    "comparator",
    "threshold",
    "passed",
}


# --------------------------------------------------------------------------- #
# 造盘与取值小工具
# --------------------------------------------------------------------------- #

def seeded(tmp_path: Path, case: str) -> tuple[Path, Path]:
    return prepare(load_case(case), tmp_path)


def snapshot_of(data: Path, config: Path, name: str = TARGET) -> dict[str, Any]:
    return industry_snapshot(name, root=data, config_root=config)


def codes_of(data: Path, payload: dict[str, Any]) -> list[str]:
    """与 R5 同一条归属读法取成分代码（不另开一份映射）。"""
    records = snapshot_module._Reader(data).source("sw_industry_map").records
    l1_code = str(payload["industry"]["l1_code"] or "")
    return snapshot_module.constituents_of(records, l1_code)[0]


def projection(data: Path, config: Path) -> views.CacheProjection:
    return views.CacheProjection(load_catalog(config), data)


# --------------------------------------------------------------------------- #
# 缺口 #1：清单的逐行指标投影
# --------------------------------------------------------------------------- #

def test_metric_projection_keys_match_metric_paths_exactly(tmp_path: Path) -> None:
    """投影的键集合与 `metric_paths()` 同名同序；缺值保留键并写 `null`，不省略。"""
    data, config = seeded(tmp_path, FULL)
    catalog = load_catalog(config)
    payload = snapshot_of(data, config)
    paths = metric_paths(catalog.window)
    projection_rows = snapshot_module.metric_projection(payload["dimensions"], catalog.window)

    assert list(projection_rows.keys()) == list(paths)
    for path in paths:
        assert projection_rows[path] == snapshot_module._leaf_value(payload["dimensions"], path)


def test_list_row_contract_unchanged_without_metrics(tmp_path: Path) -> None:
    """默认关：`ldst industry list` / MCP 出口的载荷一个键都不多（R5 契约不变）。"""
    data, config = seeded(tmp_path, FULL)
    listing = industry_list(root=data, config_root=config)
    row = listing["industries"][0]

    for key in ("metrics", "metric_as_of", "as_of"):
        assert key not in row
    assert "as_of" not in listing


def test_list_with_metrics_adds_exactly_three_row_keys(tmp_path: Path) -> None:
    """开投影只加三键（`metrics` / `metric_as_of` / `as_of`）+ 顶层 `as_of`，其余原样。"""
    data, config = seeded(tmp_path, FULL)
    plain = industry_list(root=data, config_root=config)
    listing = industry_list(root=data, config_root=config, with_metrics=True)
    paths = set(metric_paths(dict(listing["windows"])))

    for before, after in zip(plain["industries"], listing["industries"], strict=True):
        assert set(after) - set(before) == {"metrics", "metric_as_of", "as_of"}
        assert set(after["metrics"]) == paths
        assert set(after["metric_as_of"]) == paths
        assert set(after["as_of"]) == set(DIMENSIONS)
    assert set(listing["as_of"]) == set(DIMENSIONS)


def test_list_row_metrics_equal_snapshot_leaves(tmp_path: Path) -> None:
    """行上的 `metrics[path]` 就是该行业 `show` 载荷里同一路径的叶子——不是第二套计算。"""
    data, config = seeded(tmp_path, FULL)
    listing = industry_list(root=data, config_root=config, with_metrics=True)

    for row in listing["industries"]:
        payload = snapshot_of(data, config, str(row["name"]))
        for path, value in row["metrics"].items():
            assert value == snapshot_module._leaf_value(payload["dimensions"], path), path


def test_list_as_of_is_per_industry_and_never_request_day(tmp_path: Path) -> None:
    """时点逐行业给：景气来自报表期，其余来自日频缓存；结构性缺口永远 `null`。"""
    data, config = seeded(tmp_path, FULL)
    listing = industry_list(root=data, config_root=config, with_metrics=True)
    row = next(item for item in listing["industries"] if item["available"])

    assert row["as_of"]["prosperity"] == "2025-12-31"
    assert row["as_of"]["valuation"] == "2026-01-07"
    for path in ("capital.northbound", "momentum.index_change_pct"):
        assert row["metric_as_of"][path] is None
    for name in DIMENSIONS:
        days = [
            day
            for key, day in row["metric_as_of"].items()
            if key.startswith(f"{name}.") and day
        ]
        assert row["as_of"][name] == (max(days) if days else None)
    assert listing["as_of"]["valuation"] == max(
        str(item["as_of"]["valuation"])
        for item in listing["industries"]
        if item["as_of"]["valuation"]
    )


def test_list_with_metrics_on_empty_cache_keeps_null_keys(tmp_path: Path) -> None:
    """空缓存：键还在、值全 `null`、时点全 `null`，`available=false` 而不是报错。"""
    data, config = seeded(tmp_path, EMPTY)
    listing = industry_list(root=data, config_root=config, with_metrics=True)
    paths = set(metric_paths(dict(listing["windows"])))

    assert listing["available"] is False
    assert paths
    for row in listing["industries"]:
        assert set(row["metrics"]) == paths
        assert all(value is None for value in row["metrics"].values())
        assert all(day is None for day in row["as_of"].values())
    assert all(day is None for day in listing["as_of"].values())


# --------------------------------------------------------------------------- #
# 缺口 #3 / #4 / #7：日频序列与逐股明细
# --------------------------------------------------------------------------- #

def test_history_last_point_equals_current_percentile(tmp_path: Path) -> None:
    """序列最后一个点必须等于当期分位：同一个窗口、同一批行、同一个公式。"""
    data, config = seeded(tmp_path, FULL)
    payload = snapshot_of(data, config)
    history = projection(data, config).history(codes_of(data, payload))
    dimensions = payload["dimensions"]

    assert history["pe_percentile"][-1]["value"] == dimensions["valuation"]["pe_percentile"]
    assert history["pb_percentile"][-1]["value"] == dimensions["valuation"]["pb_percentile"]
    last_turnover = history["turnover_percentile"][-1]["value"]
    assert last_turnover == dimensions["crowding"]["turnover_percentile"]
    for window in payload["windows"]["momentum_windows"]:
        key = f"change_pct_{int(window)}d"
        assert history[key][-1]["value"] == dimensions["momentum"][key]


def test_history_points_are_sorted_numeric_pairs(tmp_path: Path) -> None:
    """每个点都是 `{date, value}` 且日期升序；算不出来的日子不给点（不补 null 点）。"""
    data, config = seeded(tmp_path, FULL)
    payload = snapshot_of(data, config)
    history = projection(data, config).history(codes_of(data, payload))
    assert history

    for series in history.values():
        days = [str(point["date"]) for point in series]
        assert days == sorted(days)
        for point in series:
            assert set(point) == {"date", "value"}
            value = point["value"]
            assert isinstance(value, (int, float)) and not isinstance(value, bool)


def test_history_and_rows_without_codes_stay_empty(tmp_path: Path) -> None:
    """没有成分代码（归属未绑定 / 缓存为空）→ 空序列空表，不估一条出来。

    序列的键**保留**、值是空数组：缺键在 SPEC §2.2 里表示「后端没提供」，与「提供了但没数」
    是两件事，不能混着给。
    """
    data, config = seeded(tmp_path, EMPTY)
    item = projection(data, config)
    history = item.history([])

    assert set(history) >= {"pe_percentile", "pb_percentile", "turnover_percentile"}
    assert all(series == [] for series in history.values())
    assert item.constituent_rows([]) == []
    assert item.main_net_daily([]) == []


def test_main_net_daily_window_and_sum_match_capital(tmp_path: Path) -> None:
    """逐日序列的窗口 = R5 的 `fund_days`；全样本日之和 = 载荷里的 N 日主力净流入。"""
    data, config = seeded(tmp_path, FULL)
    catalog = load_catalog(config)
    payload = snapshot_of(data, config)
    daily = projection(data, config).main_net_daily(codes_of(data, payload))

    assert len(daily) == catalog.window_days("fund_days")
    assert [item["date"] for item in daily] == sorted(str(item["date"]) for item in daily)
    for item in daily:
        assert set(item) == {
            "date",
            "main_net",
            "main_net_sampled",
            "codes_used",
            "codes_expected",
            "complete",
        }
        if not item["complete"]:
            assert item["main_net"] is None

    complete = [item for item in daily if item["complete"]]
    nd = payload["dimensions"]["capital"]["constituents"]["main_net_inflow_nd"]
    if complete and nd is not None:
        total = sum(float(item["main_net"]) for item in complete)
        assert total == pytest.approx(float(nd))


def test_constituent_rows_have_exact_contract_keys(tmp_path: Path) -> None:
    """逐股明细：SPEC §2.2 的九键，一个不多一个不少；一行一个成分代码。"""
    data, config = seeded(tmp_path, FULL)
    payload = snapshot_of(data, config)
    codes = codes_of(data, payload)
    rows = projection(data, config).constituent_rows(codes)

    assert len(rows) == payload["membership"]["constituent_count"]
    assert {row["code"] for row in rows} == set(codes)
    for row in rows:
        assert set(row) == {
            "code",
            "name",
            "pe_ttm",
            "pb",
            "turnover_rate",
            "revenue_yoy",
            "profit_yoy",
            "main_net_nd",
            "as_of",
        }
        assert row["code"]


# --------------------------------------------------------------------------- #
# 缺口 #6：规则判定
# --------------------------------------------------------------------------- #

def metrics_listing(tmp_path: Path, case: str) -> dict[str, Any]:
    data, config = seeded(tmp_path, case)
    return industry_list(root=data, config_root=config, with_metrics=True)


def test_rules_view_matches_spec_ids() -> None:
    """四条规则的 id / direction / 阈值照 SPEC §2.3 定稿，改阈值要升 SPEC 版本。"""
    windows = {"fund_days": 5, "valuation_lookback_days": 250, "turnover_lookback_days": 60}
    rules = {rule.id: rule.view(windows) for rule in views.SIGNAL_RULES}

    assert list(rules) == [
        "capital_valuation",
        "prosperity_valuation",
        "momentum_crowding",
        "crowding_risk",
    ]
    assert rules["capital_valuation"]["expression"] == (
        "capital.constituents.main_net_inflow_nd > 0 且 valuation.pe_percentile < 0.30"
    )
    assert rules["crowding_risk"]["expression"] == (
        "crowding.turnover_percentile > 0.80 且 crowding.fund_concentration_pct > 25"
    )
    assert rules["crowding_risk"]["direction"] == "bearish"
    assert set(rules["capital_valuation"]) == {"id", "title", "direction", "expression"}
    assert {rule.severity for rule in views.SIGNAL_RULES} <= {"info", "warn", "danger"}


def test_signal_evidence_is_complete_or_absent(tmp_path: Path) -> None:
    """出得来就九项齐全（前端缺一项丢整条），出不来就整条不出。"""
    result = views.evaluate_signals(metrics_listing(tmp_path, FULL))
    by_id = {rule.id: rule for rule in views.SIGNAL_RULES}
    assert result["signals"]

    for signal in result["signals"]:
        rule = by_id[str(signal["rule_id"])]
        assert len(signal["evidence"]) == len(rule.predicates)
        for item in signal["evidence"]:
            assert set(item) == EVIDENCE_KEYS
            assert item["path"] and item["label"] and item["as_of"] and item["dataset"]
            assert isinstance(item["value"], (int, float))
            assert isinstance(item["threshold"], (int, float))
        assert signal["as_of"] == max(str(item["as_of"]) for item in signal["evidence"])


def test_signals_are_only_emitted_when_every_condition_holds(tmp_path: Path) -> None:
    """每条信号的全部证据都 `passed=true`；条件成立且字段齐的「规则 × 行业」不会漏。"""
    payload = metrics_listing(tmp_path, FULL)
    result = views.evaluate_signals(payload)

    for signal in result["signals"]:
        assert all(item["passed"] is True for item in signal["evidence"])

    fired = {(str(item["industry"]), str(item["rule_id"])) for item in result["signals"]}
    windows = dict(payload["windows"])
    for rule in views.SIGNAL_RULES:
        for row in payload["industries"]:
            evidence, passed_all, gap = views._rule_outcome(rule, row, windows)
            if evidence and passed_all and not gap:
                assert (str(row["name"]), rule.id) in fired


def test_null_field_never_counts_as_zero(tmp_path: Path) -> None:
    """字段 `null` → 该规则对该行业不判定（把缺失当 0 会让空缓存「遍地是信号」）。"""
    result = views.evaluate_signals(metrics_listing(tmp_path, EMPTY))

    assert result["signals"] == []
    assert result["counts"]["total"] == 0
    assert result["available"] is False
    assert result["reason"]

    row = {
        "name": "测试行业",
        "available": True,
        "metrics": {
            "capital.constituents.main_net_inflow_nd": 1.0,
            "valuation.pe_percentile": None,
        },
        "metric_as_of": {
            "capital.constituents.main_net_inflow_nd": "2026-01-07",
            "valuation.pe_percentile": None,
        },
    }
    fake = {"available": True, "windows": {}, "industries": [row], "reason": None}
    only = views.evaluate_signals(fake, rule_id="capital_valuation")

    assert only["signals"] == []
    assert "跳过" in str(only["reason"])


def test_signals_filters_are_applied_by_backend(tmp_path: Path) -> None:
    """`industry` / `rule` 由后端过滤；`rules[]` 永远给全量四条（页面要展示规则说明）。"""
    payload = metrics_listing(tmp_path, FULL)
    everything = views.evaluate_signals(payload)
    assert everything["signals"]

    name = str(everything["signals"][0]["industry"])
    scoped = views.evaluate_signals(payload, industry=name)
    assert scoped["signals"] and all(item["industry"] == name for item in scoped["signals"])

    unknown = views.evaluate_signals(payload, industry="不存在的行业")
    assert unknown["signals"] == []
    assert "不在 config/industry.yaml 清单里" in str(unknown["reason"])

    one_rule = views.evaluate_signals(payload, rule_id="capital_valuation")
    assert {item["rule_id"] for item in one_rule["signals"]} == {"capital_valuation"}
    assert [item["id"] for item in one_rule["rules"]] == [rule.id for rule in views.SIGNAL_RULES]
    assert set(one_rule["counts"]["by_rule"]) == {"capital_valuation"}


def test_signals_counts_are_self_consistent(tmp_path: Path) -> None:
    """`counts` 三个口径互相咬合：total = 条数 = 多空之和 = by_rule 之和。"""
    counts = views.evaluate_signals(metrics_listing(tmp_path, FULL))["counts"]

    assert set(counts) == {"total", "bullish", "bearish", "by_rule"}
    assert counts["total"] == counts["bullish"] + counts["bearish"]
    assert sum(counts["by_rule"].values()) == counts["total"]


def test_partial_coverage_case_reports_why_it_is_thin(tmp_path: Path) -> None:
    """只有资金流的 case：能判的规则照判，判不了的行业数进 `reason`，不出半条证据。"""
    result = views.evaluate_signals(metrics_listing(tmp_path, PARTIAL))

    assert result["available"] is True
    for item in result["signals"]:
        assert item["evidence"]
        for evidence in item["evidence"]:
            assert evidence["value"] is not None
