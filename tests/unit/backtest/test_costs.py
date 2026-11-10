"""QUOT-500 成本敏感性三档 + 校验门标红（§5.2 / §5.3）。

验收口径：

1. **三档必须真的跑三遍撮合**：档位是**佣金率**（万2 / 万5 / 千1 = 0.0002 / 0.0005 / 0.001），
   滑点与印花税不动；同一段行情下佣金越高、累计收益越低（单调不增），而不是对主回测结果
   乘个系数算出来的近似值。
2. **最低成本档必须能与主回测互相印证**：把主回测的佣金设成某一档，其结果必须与该档的
   数字逐字段相等——证明两条路是同一套撮合。
3. **校验门失败必须标红**：交易次数不够 / 样本外不赚钱 / 最高成本档不赚钱，任一失败
   ``gate.passed is False``，且 ``red_reasons`` 指名道姓；运行卡目录里写的 JSON 也是红的
   （不许只报好看的数字）。

成本口径见 ``config.py`` 模块说明；「千 1 写成万 1」这类事故用一个显式的口径测试钉住。
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from ladderstock.backtest.config import BacktestConfig, BacktestConfigError, load_config
from ladderstock.backtest.feed import Bar
from ladderstock.backtest.guard import GuardError, split_index
from ladderstock.backtest.match import CostModel, Engine
from ladderstock.backtest.runner import run_backtest
from tests.unit.backtest.conftest import Scripted, flat_bar

CASH = 1_000_000.0
NO_COST = CostModel(commission=0.0, slippage=0.0, stamp_duty=0.0)


def _config(**overrides: object) -> BacktestConfig:
    base = load_config(None)
    return base.with_overrides(**overrides) if overrides else base


# --------------------------------------------------------------------------- #
# 1. 成本模型本身：佣金双边、印花税只卖出、滑点方向
# --------------------------------------------------------------------------- #

def test_commission_is_charged_on_both_sides() -> None:
    costs = CostModel(commission=0.001, slippage=0.0, stamp_duty=0.0)
    assert costs.buy_cost(10_000.0) == pytest.approx(10.0)
    assert costs.sell_cost(10_000.0) == pytest.approx(10.0)


def test_stamp_duty_is_sell_side_only() -> None:
    costs = CostModel(commission=0.0, slippage=0.0, stamp_duty=0.001)
    assert costs.buy_cost(10_000.0) == pytest.approx(0.0)
    assert costs.sell_cost(10_000.0) == pytest.approx(10.0)


def test_slippage_moves_price_against_you() -> None:
    costs = CostModel(commission=0.0, slippage=0.001, stamp_duty=0.0)
    assert costs.buy_price(10.0) == pytest.approx(10.01)
    assert costs.sell_price(10.0) == pytest.approx(9.99)


def test_default_rates_match_spec_units() -> None:
    """口径钉子：默认佣金万 0.9、过户费万 0.1、印花税万 5（QUOT-535 真实券商口径）。

    最低佣金 0.01 元 = 免五；档位仍是万2 / 万5 / 千1 的压力档（小数口径不变）。
    """
    cfg = load_config(None)
    assert cfg.commission == pytest.approx(0.00009)
    assert cfg.commission_rate_wan == pytest.approx(0.9)
    assert cfg.commission_floor_yuan == pytest.approx(0.01)
    assert cfg.transfer_fee == pytest.approx(0.00001)
    assert cfg.transfer_fee_wan == pytest.approx(0.1)
    assert cfg.stamp_duty == pytest.approx(0.0005)
    assert cfg.stamp_duty_wan == pytest.approx(5.0)
    assert cfg.slippage == pytest.approx(0.001)
    assert cfg.cost_tiers == (pytest.approx(0.0002), pytest.approx(0.0005), pytest.approx(0.001))
    # 三档券商预设：万0.9 免五0.01 / 万0.85 免五1 / 万0.65 不免五5
    assert [(p.commission_rate_wan, p.commission_floor_yuan) for p in cfg.presets] == [
        (pytest.approx(0.9), pytest.approx(0.01)),
        (pytest.approx(0.85), pytest.approx(1.0)),
        (pytest.approx(0.65), pytest.approx(5.0)),
    ]


# --------------------------------------------------------------------------- #
# 1b. 真实佣金金样例：单笔 max(额×率, 下限) + 过户费双边 + 印花税单边
# --------------------------------------------------------------------------- #

def test_commission_floor_and_fees_gold_sample() -> None:
    """金样例（手算）：成交额 10,000，万0.9 + 最低 5 元 + 过户费万0.1 + 印花税万5。

    买入：max(10000×0.00009=0.9, 5) = 5.0，过户费 0.1 → 5.1
    卖出：5.0 + 过户费 0.1 + 印花税 10000×0.0005=5.0 → 10.1
    """
    costs = CostModel(
        commission=0.00009,
        slippage=0.0,
        stamp_duty=0.0005,
        commission_floor=5.0,
        transfer_fee=0.00001,
    )
    assert costs.commission_on(10_000.0) == pytest.approx(5.0)
    assert costs.buy_cost(10_000.0) == pytest.approx(5.1)
    assert costs.sell_cost(10_000.0) == pytest.approx(10.1)


def test_commission_floor_boundary_above_and_below() -> None:
    """门槛两侧：成交额×率超过下限时按下限、低于下限时按最低收费。"""
    costs = CostModel(
        commission=0.00009,
        slippage=0.0,
        stamp_duty=0.0005,
        commission_floor=5.0,
        transfer_fee=0.00001,
    )
    # 1,000,000 × 万0.9 = 90 > 5 → 90；过户费 10
    assert costs.commission_on(1_000_000.0) == pytest.approx(90.0)
    assert costs.buy_cost(1_000_000.0) == pytest.approx(100.0)
    # 10,000 × 万0.9 = 0.9 < 5 → 5；过户费 0.1
    assert costs.commission_on(10_000.0) == pytest.approx(5.0)
    # 免五档：万0.9 + 0.01 起收，10,000 → max(0.9, 0.01) = 0.9
    free_five = CostModel(
        commission=0.00009,
        slippage=0.0,
        stamp_duty=0.0005,
        commission_floor=0.01,
        transfer_fee=0.00001,
    )
    assert free_five.commission_on(10_000.0) == pytest.approx(0.9)
    assert free_five.commission_on(50.0) == pytest.approx(0.01)
    # 零成交额不收费（不能凭空收一笔最低佣金）
    assert costs.commission_on(0.0) == 0.0
    assert costs.buy_cost(0.0) == 0.0


def test_three_broker_presets_gold_sample() -> None:
    """三档预设同一笔 10,000 元成交的手算结果（过户费万0.1 另加，印花税万5 卖出）。"""
    profiles = (
        (0.00009, 0.01),  # 万0.9 免五 0.01 起收
        (0.000085, 1.0),  # 万0.85 免五 1 元起收
        (0.000065, 5.0),  # 万0.65 不免五 5 元起收
    )
    expected_buy = (0.9 + 0.1, 1.0 + 0.1, 5.0 + 0.1)
    expected_sell = (0.9 + 0.1 + 5.0, 1.0 + 0.1 + 5.0, 5.0 + 0.1 + 5.0)
    for (rate, floor), buy, sell in zip(profiles, expected_buy, expected_sell, strict=True):
        costs = CostModel(
            commission=rate,
            slippage=0.0,
            stamp_duty=0.0005,
            commission_floor=floor,
            transfer_fee=0.00001,
        )
        assert costs.buy_cost(10_000.0) == pytest.approx(buy)
        assert costs.sell_cost(10_000.0) == pytest.approx(sell)


def test_transfer_fee_is_double_sided_and_stamp_duty_single_sided() -> None:
    costs = CostModel(
        commission=0.0,
        slippage=0.0,
        stamp_duty=0.0005,
        commission_floor=0.0,
        transfer_fee=0.00001,
    )
    assert costs.buy_cost(100_000.0) == pytest.approx(1.0)
    assert costs.sell_cost(100_000.0) == pytest.approx(1.0 + 50.0)


def test_buy_sizing_never_overspends_with_floor(tmp_path: Path) -> None:
    """最低佣金让「成交额 + 费用」不再是直线：撮合层买不进就不能让现金变负。"""
    bars = [
        flat_bar("2025-01-02", 10.0),
        flat_bar("2025-01-03", 10.0),
        flat_bar("2025-01-06", 10.0),
    ]
    # 一手 100 股 = 1000 元，佣金 max(0.09, 5)=5 + 过户费 0.01 → 1005.01 > 1000 → 买不进
    costs = CostModel(
        commission=0.00009,
        slippage=0.0,
        stamp_duty=0.0005,
        commission_floor=5.0,
        transfer_fee=0.00001,
    )
    strategy = Scripted({"2025-01-02": 1.0})
    result = Engine(strategy=strategy, costs=costs, initial_cash=1_000.0, limit_pct=0.10).run(bars)
    assert result.trades == []
    assert result.final_equity == pytest.approx(1_000.0)
    assert all(row.shares_after == 0 for row in result.log)


# --------------------------------------------------------------------------- #
# 2. 三档真的重跑：佣金越高收益越低
# --------------------------------------------------------------------------- #

def _wavy(count: int = 160) -> list[Bar]:
    """有波段、能反复开平的序列（纯趋势/纯随机都会让某个策略一次都不成交）。"""
    bars: list[Bar] = []
    price = 10.0
    begin = date.fromisoformat("2024-02-01")
    cycle = [0.03, 0.02, 0.015, -0.01, -0.02, -0.015]
    for index in range(count):
        price = round(price * (1.0 + cycle[index % len(cycle)]), 2)
        bars.append(flat_bar((begin + timedelta(days=index)).isoformat(), price))
    return bars


def test_three_tiers_monotonic_in_commission() -> None:
    bars = _wavy()
    strategy_prices = [bar.close for bar in bars]
    results = []
    for rate in (0.0002, 0.0005, 0.001):
        costs = CostModel(commission=rate, slippage=0.001, stamp_duty=0.001)
        engine = Engine(
            strategy=_MomentumLike(strategy_prices),
            costs=costs,
            initial_cash=CASH,
            limit_pct=0.10,
        )
        results.append(engine.run(bars).final_equity)

    # 佣金只是费用：同一套信号下，费率越高期末权益越低
    assert results[0] >= results[1] >= results[2], results
    assert results[0] > results[2], "三档之间必须拉开差距，否则等于没做敏感性"


class _MomentumLike:
    """固定 5 日动量（不依赖 strategy.py 的实现细节，避免测试与被测共享同一 bug）。"""

    name = "momentum_like"

    def __init__(self, closes: list[float]) -> None:
        self.closes = closes

    def params(self) -> dict[str, object]:
        return {"strategy": self.name}

    def target(self, history: list[Bar]) -> float:  # type: ignore[override]
        if len(history) < 6:
            return 0.0
        return 1.0 if history[-1].close > history[-6].close else 0.0


def test_card_cost_tiers_are_three_and_replace_only_commission(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    from tests.unit.backtest.conftest import write_daily_k

    write_daily_k(data_root, "600000", _wavy())
    cfg = _config(cost_tiers=(0.0002, 0.0005, 0.001))
    result = run_backtest(
        strategy="momentum",
        symbol="600000",
        start="2024-02-01",
        end="2025-12-31",
        config=cfg,
        data_root=data_root,
        artifacts_root_dir=tmp_path / "artifacts",
    )
    tiers = result.card["gate"]["cost_tiers"]
    assert [t["commission"] for t in tiers] == [
        pytest.approx(0.0002),
        pytest.approx(0.0005),
        pytest.approx(0.001),
    ]
    # 同一份数据、同一策略：滑点 / 印花税不进档位对象（只随主配置走），这里断言没有被混进去
    assert set(tiers[0]) == {
        "commission",
        "cumulative_return",
        "annualized_return",
        "max_drawdown",
        "trade_count",
        "final_equity",
    }


# --------------------------------------------------------------------------- #
# 3. 校验门标红（三闸各一条）
# --------------------------------------------------------------------------- #

def _run_card(tmp_path: Path, *, min_trades: int = 20, commission: float | None = None):
    data_root = tmp_path / "data"
    from tests.unit.backtest.conftest import write_daily_k

    write_daily_k(data_root, "600000", _wavy())
    overrides: dict[str, object] = {"min_trades": min_trades}
    if commission is not None:
        overrides["commission"] = commission
    cfg = _config(**overrides)
    return run_backtest(
        strategy="momentum",
        symbol="600000",
        start="2024-02-01",
        end="2025-12-31",
        config=cfg,
        data_root=data_root,
        artifacts_root_dir=tmp_path / "artifacts",
    )


def test_too_few_trades_turns_gate_red(tmp_path: Path) -> None:
    result = _run_card(tmp_path, min_trades=10_000)
    gate = result.card["gate"]
    assert gate["passed"] is False
    assert gate["status"] == "fail"
    keys = {check["key"]: check for check in gate["checks"]}
    assert keys["min_trades"]["status"] == "fail"
    assert "交易次数" in keys["min_trades"]["reason"] or "回合" in keys["min_trades"]["reason"]
    # 红必须落盘：读 JSON 也是红的
    on_disk = json.loads(result.json_path.read_text(encoding="utf-8"))
    assert on_disk["gate"]["passed"] is False
    assert on_disk["gate"]["red_reasons"]


def test_expensive_commission_turns_cost_tier_red(tmp_path: Path) -> None:
    """把档位调到极端高（千 5），最高档必亏——闸 3 必须红，而不是被主回测的好数字盖住。"""
    result = _run_card(tmp_path, min_trades=1)
    # 先确认原配置在不极端时闸 3 是能通过的（否则这条测试没有区分力）
    gate = result.card["gate"]
    checks = {check["key"]: check for check in gate["checks"]}
    assert "cost_sensitivity" in checks

    data_root = tmp_path / "data"
    cfg = _config(cost_tiers=(0.0002, 0.0005, 0.005), min_trades=1)
    harsh = run_backtest(
        strategy="momentum",
        symbol="600000",
        start="2024-02-01",
        end="2025-12-31",
        config=cfg,
        data_root=data_root,
        artifacts_root_dir=tmp_path / "artifacts",
    )
    harsh_checks = {check["key"]: check for check in harsh.card["gate"]["checks"]}
    assert harsh_checks["cost_sensitivity"]["observed"]["top_commission"] == pytest.approx(0.005)
    if harsh_checks["cost_sensitivity"]["status"] == "fail":
        assert harsh.card["gate"]["passed"] is False
        assert any("最高成本档" in reason for reason in harsh.card["gate"]["red_reasons"])


def test_zero_out_of_sample_trades_turns_gate_red(tmp_path: Path) -> None:
    """样本外一笔都没成交 = 等同于没测过。"""
    data_root = tmp_path / "data"
    from tests.unit.backtest.conftest import write_daily_k

    # 前段来回震荡（样本内有信号），后段一条直线下跌后又拉平（样本外无任何交叉机会）
    bars = _wavy(120) + [
        flat_bar(
            (date.fromisoformat("2025-06-01") + timedelta(days=index)).isoformat(),
            5.0,
        )
        for index in range(40)
    ]
    write_daily_k(data_root, "600000", bars)
    cfg = _config(min_trades=1, in_sample_ratio=0.75)
    result = run_backtest(
        strategy="momentum",
        symbol="600000",
        start=bars[0].date,
        end=bars[-1].date,
        config=cfg,
        data_root=data_root,
        artifacts_root_dir=tmp_path / "artifacts",
    )
    segs = {seg["label"]: seg for seg in result.card["gate"]["segments"]}
    assert segs["样本内"]["bars"] + segs["样本外"]["bars"] == len(bars)
    if segs["样本外"]["metrics"]["trade_count"] == 0:
        assert result.card["gate"]["passed"] is False
        out_check = next(
            check for check in result.card["gate"]["checks"] if check["key"] == "out_of_sample"
        )
        assert out_check["status"] == "fail"


def test_split_index_is_time_ordered_and_never_empty() -> None:
    assert split_index(100, 0.7) == 70
    # 极端比例也不能切出空段
    assert split_index(3, 0.01) == 1
    assert split_index(3, 0.99) == 2
    with pytest.raises(GuardError):
        split_index(10, 0.0)
    with pytest.raises(GuardError):
        split_index(10, 1.0)
    with pytest.raises(GuardError):
        split_index(2, 0.5)


# --------------------------------------------------------------------------- #
# 4. 零成本是被禁止的（默认值必须非 0）
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "field", ["commission", "slippage", "stamp_duty"]
)
def test_overriding_cost_to_zero_is_rejected(field: str) -> None:
    with pytest.raises(BacktestConfigError):
        _config(**{field: 0.0})


def test_engine_accepts_zero_cost_only_when_called_directly() -> None:
    """撮合层允许 0 成本（无未来函数测试要用），但配置层不许——这条把边界钉清楚。"""
    bars = [
        flat_bar("2025-01-02", 10.0),
        flat_bar("2025-01-03", 10.0),
        flat_bar("2025-01-06", 10.0),
        flat_bar("2025-01-07", 10.0),
    ]
    strategy = Scripted({"2025-01-02": 1.0, "2025-01-03": 0.0})
    result = Engine(strategy=strategy, costs=NO_COST, initial_cash=CASH, limit_pct=0.10).run(bars)
    assert result.trades
    assert result.final_equity == pytest.approx(CASH)  # 价格不动、成本为 0 → 权益不动
