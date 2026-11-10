"""QUOT-500 运行卡契约：字段齐、每指标带 calc_id、数值全有限、三份产物同源。

运行卡是前端唯一数据源（页面只读、不重算），所以契约本身要有测试钉住：

1. **顶层字段齐全**且类型正确——前端按这些字段渲染，少一个就是白屏；
2. **每指标一个 ``calc_id``**（不是整卡一个），且同一份结果重复计算 id 不变；
3. **所有数值有限**：JSON 以 ``allow_nan=False`` 写出，任何 NaN/inf 都会落盘失败——
   这里直接对 ``metrics`` / ``equity_curve`` / ``cost_tiers`` 逐个字段做 ``isfinite``；
4. **三份产物同源**：JSON / Markdown / HTML 里的 run_id 与校验门结论一致；
5. **撮合日志字段**包含 ``t_plus_one_blocked``（A 股 T+1 的可观测标记）。
"""

from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from ladderstock.backtest.config import load_config
from ladderstock.backtest.feed import Bar
from ladderstock.backtest.match import CostModel, Engine
from ladderstock.backtest.runner import run_backtest
from ladderstock.backtest.score import compute
from tests.unit.backtest.conftest import Scripted, flat_bar, write_daily_k

CASH = 1_000_000.0
TOP_LEVEL = {
    "schema_version",
    "run_id",
    "generated_at",
    "symbol",
    "board",
    "limit_pct",
    "strategy",
    "strategy_params",
    "period",
    "data",
    "assumptions",
    "config",
    "config_sources",
    "initial_cash",
    "final_equity",
    "open_position_pnl",
    "metrics",
    "metrics_display",
    "metrics_calc_ids",
    "equity_curve",
    "benchmark",
    "trades",
    "gate",
    "calc_ids",
    "artifacts",
}
METRIC_NAMES = {
    "cumulative_return",
    "annualized_return",
    "max_drawdown",
    "sharpe",
    "calmar",
    "win_rate",
    "profit_loss_ratio",
    "trade_count",
    "avg_turnover",
    "avg_holding_days",
    "exposure_ratio",
}


def _wavy(count: int = 160) -> list[Bar]:
    bars: list[Bar] = []
    price = 10.0
    begin = date.fromisoformat("2024-02-01")
    cycle = [0.03, 0.02, 0.015, -0.01, -0.02, -0.015]
    for index in range(count):
        price = round(price * (1.0 + cycle[index % len(cycle)]), 2)
        bars.append(flat_bar((begin + timedelta(days=index)).isoformat(), price))
    return bars


def _run(tmp_path: Path):
    data_root = tmp_path / "data"
    bars = _wavy()
    write_daily_k(data_root, "600000", bars)
    return run_backtest(
        strategy="momentum",
        symbol="600000",
        start=bars[0].date,
        end=bars[-1].date,
        config=load_config(None),
        initial_cash=CASH,
        data_root=data_root,
        artifacts_root_dir=tmp_path / "artifacts",
    )


def test_top_level_contract(tmp_path: Path) -> None:
    card = _run(tmp_path).card
    assert TOP_LEVEL <= set(card), TOP_LEVEL - set(card)
    assert card["schema_version"] == "1"
    assert card["symbol"] == "600000"
    assert card["board"] == "sh_main"
    assert card["limit_pct"] == pytest.approx(0.10)
    assert card["data"]["source"] == "daily_k"
    assert card["data"]["adjust"] == "qfq"
    assert card["data"]["fingerprint"]
    assert card["period"]["trading_days"] == 252
    assert card["assumptions"], "执行假设必须随卡输出（人要能读到规则）"
    assert card["artifacts"] == {
        "json": f"{card['run_id']}.json",
        "markdown": f"{card['run_id']}.md",
        "html": f"{card['run_id']}.html",
    }


def test_every_metric_has_its_own_calc_id(tmp_path: Path) -> None:
    card = _run(tmp_path).card
    assert set(card["metrics"]) == METRIC_NAMES
    ids = card["metrics_calc_ids"]
    assert set(ids) == METRIC_NAMES, "每个指标都必须有自己的 calc_id"
    assert all(isinstance(value, str) and value.startswith("c_") for value in ids.values())
    # 同一指标不同口径 → 不同 id；这里至少确认 id 没有全部撞成同一个
    assert len(set(ids.values())) == len(METRIC_NAMES)
    # 整卡级别的口径 id
    assert set(card["calc_ids"]) == {"run_card", "data", "gate", "benchmark"}
    assert all(value.startswith("c_") for value in card["calc_ids"].values())


def test_metric_calc_id_is_stable_for_same_inputs() -> None:
    bars = _wavy(60)
    costs = CostModel(commission=0.00025, slippage=0.001, stamp_duty=0.001)
    strategy = Scripted({bar.date: 1.0 for bar in bars[:-1]})
    first = compute(
        Engine(strategy=strategy, costs=costs, initial_cash=CASH, limit_pct=0.10).run(bars),
        bars,
        data_fp="160:2024-02-01:x:deadbeef",
    )
    second = compute(
        Engine(
            strategy=Scripted({bar.date: 1.0 for bar in bars[:-1]}),
            costs=costs,
            initial_cash=CASH,
            limit_pct=0.10,
        ).run(bars),
        bars,
        data_fp="160:2024-02-01:x:deadbeef",
    )
    assert first.calc_ids == second.calc_ids
    # 换数据指纹 ⇒ 指标 id 必须跟着变（不许把不同数据算成「同一结果」）
    third = compute(
        Engine(strategy=strategy, costs=costs, initial_cash=CASH, limit_pct=0.10).run(bars),
        bars,
        data_fp="160:2024-02-01:x:cafebabe",
    )
    assert third.calc_ids != first.calc_ids


def test_all_numbers_are_finite(tmp_path: Path) -> None:
    card = _run(tmp_path).card
    for name, value in card["metrics"].items():
        if value is None:
            assert name in {"sharpe", "calmar", "win_rate", "profit_loss_ratio"}, name
            continue
        assert math.isfinite(float(value)), f"metrics.{name} 非有限：{value!r}"
    for point in card["equity_curve"]:
        assert math.isfinite(point["equity"])
        assert math.isfinite(point["drawdown"])
        assert 0.0 <= point["drawdown"] <= 1.0 + 1e-12
    for tier in card["gate"]["cost_tiers"]:
        for key in ("cumulative_return", "annualized_return", "max_drawdown", "final_equity"):
            assert math.isfinite(tier[key]), f"cost_tiers.{key} 非有限"
    # 严格 JSON：allow_nan=False 时任何 NaN/inf 都会抛出
    json.dumps(card, allow_nan=False)


def test_gate_block_shape(tmp_path: Path) -> None:
    gate = _run(tmp_path).card["gate"]
    assert set(gate) >= {
        "passed",
        "status",
        "red_reasons",
        "warnings",
        "checks",
        "segments",
        "cost_tiers",
    }
    assert gate["status"] == ("pass" if gate["passed"] else "fail")
    assert [check["key"] for check in gate["checks"]] == [
        "min_trades",
        "out_of_sample",
        "cost_sensitivity",
    ]
    assert [seg["label"] for seg in gate["segments"]] == ["样本内", "样本外"]
    assert len(gate["cost_tiers"]) == 3


def test_three_artifacts_are_consistent(tmp_path: Path) -> None:
    result = _run(tmp_path)
    card = result.card
    assert result.json_path and result.markdown_path and result.html_path
    for path in (result.json_path, result.markdown_path, result.html_path):
        assert path.is_file() and path.stat().st_size > 0
    on_disk = json.loads(result.json_path.read_text(encoding="utf-8"))
    assert on_disk["run_id"] == card["run_id"]
    md = result.markdown_path.read_text(encoding="utf-8")
    html = result.html_path.read_text(encoding="utf-8")
    assert card["run_id"] in md
    assert card["run_id"] in html
    # 校验门结论在三份产物里一致（Markdown / HTML 用中文「通过 / 未通过」呈现）
    passed = card["gate"]["passed"]
    expect_md = "校验门：通过（绿）" if passed else "校验门：未通过（红）"
    expect_html = "通过（绿）" if passed else "未通过（红）"
    assert expect_md in md
    assert expect_html in html


def test_trade_log_exposes_t_plus_one_marker() -> None:
    bars = [
        flat_bar("2025-05-12", 10.0),
        flat_bar("2025-05-13", 10.1),
        flat_bar("2025-05-14", 10.2),
    ]
    strategy = Scripted({"2025-05-12": 1.0, "2025-05-13": 0.0})
    costs = CostModel(commission=0.00025, slippage=0.001, stamp_duty=0.001)
    result = Engine(strategy=strategy, costs=costs, initial_cash=CASH, limit_pct=0.10).run(bars)
    rows: list[dict[str, Any]] = [row.to_dict() for row in result.log]
    assert all("t_plus_one_blocked" in row for row in rows)
    assert any(row["t_plus_one_blocked"] for row in rows) is False
    # 买入当天就翻空：买入落 05-13，卖出只能 05-14 —— 当天不存在回转
    buy = next(row for row in rows if row["executed_side"] == "buy")
    sell = next(row for row in rows if row["executed_side"] == "sell")
    assert (buy["date"], sell["date"]) == ("2025-05-13", "2025-05-14")
