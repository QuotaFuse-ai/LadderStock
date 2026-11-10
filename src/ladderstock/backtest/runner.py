"""回测入口：装配 → 撮合 → 计分 → 校验门 → 运行卡（QUOT-500 §5.1）。

``run_backtest`` 是唯一入口（CLI 与 Web 都调它，保证两条路同源、同结论）。
一次调用产出：

1. ``run_id``：``bt_`` + sha256(参数 + 数据指纹)[:8]——同参数同数据必然同 id，
   与生成时间、机器、路径无关；
2. 运行卡 dict（结构与 ``card.py`` 模块说明里的字段表一致）；
3. 落盘三份产物（JSON / Markdown / HTML）。

产物目录：``<data_dir>/backtests/<run_id>/``；被 ``LDST_DATA_DIR`` 重定向
（与 ``feed`` 读库同一根目录），测试与便携安装都不写死路径。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Final

from ladderstock.backtest import card as card_mod
from ladderstock.backtest import guard as guard_mod
from ladderstock.backtest.config import BacktestConfig, load_config
from ladderstock.backtest.feed import BarSeries, FeedError, load_series
from ladderstock.backtest.match import CostModel, Engine, MatchResult
from ladderstock.backtest.score import (
    TRADING_DAYS,
    Metrics,
    benchmark_buy_and_hold,
    compute,
    equity_curve_with_drawdown,
)
from ladderstock.backtest.strategy import Strategy, make
from ladderstock.backtest.universe import single
from ladderstock.calc import CALC_VERSION
from ladderstock.calc.fingerprint import calc_id as calc_fingerprint
from ladderstock.calc.fingerprint import canonical_params
from ladderstock.cli.paths import data_dir

BACKTEST_DIRNAME: Final = "backtests"
DEFAULT_INITIAL_CASH: Final = 1_000_000.0
#: 页面参数区的默认标的（M1 真机验证用 600000）
DEFAULT_SYMBOL: Final = "600000"

#: 执行假设：写死在代码里，同时写进运行卡 ``assumptions``（人要能读到「是什么规则」）
ASSUMPTIONS: Final[tuple[str, ...]] = (
    "信号 t，成交 t+1：策略只看到 t 及之前的 K 线，t+1 开盘价 ± 滑点成交（无未来函数）。",
    "成交价：买入 = 开盘 × (1 + 滑点)；卖出 = 开盘 × (1 − 滑点)。",
    "佣金：按成交额双边收取，单笔 = max(成交额 × 佣金率, 最低佣金)（本次实际值见 config）。",
    "过户费：按成交额双边收取（本次实际值见 config.transfer_fee_wan）。",
    "印花税：只对卖出成交额收取（本次实际值见 config.stamp_duty_wan）。",
    "涨跌停：以昨收为基准，开盘价 >= 涨停价 → 买不进；开盘价 <= 跌停价 → 卖不出。",
    "停牌：当日成交量为 0 视为不可成交，持仓保持不动、当日不下单（不补价、不延后）。",
    "T+1：当日买入的股数当日不可卖出。",
    "整手：买入按 100 股整手取整，不足一手的现金留存。",
    "区间末仍持仓：按最后收盘价结算期末权益；浮盈不计入完成回合（不进胜率/盈亏比）。",
    "年化：按 252 个交易日 / 年；夏普无风险利率取 0；卡玛 = 年化 / 最大回撤。",
    "样本切分：前 70%（由 config.in_sample_ratio 决定）为样本内，后段样本外，按时间顺序、不随机。",
    "基准：该标的「首日收盘买入并持有」（本机无上证指数日线，缺就报缺，不编造）。",
)


class BacktestError(ValueError):
    """回测输入非法的统一出口（策略 / 标的 / 配置 / 数据由各自异常细分，这里汇总为 CLI 文案）。"""


@dataclass(slots=True)
class RunResult:
    """``run_backtest`` 的返回值：``card`` 是运行卡 dict，其余是落盘路径。"""

    run_id: str
    card: dict[str, Any]
    json_path: Path | None = None
    markdown_path: Path | None = None
    html_path: Path | None = None
    #: 被重写的运行卡目录（同 run_id，覆盖旧文件；内容必一致）
    artifacts_dir: Path | None = None


def parse_day(value: str | date, *, field_name: str) -> date:
    if isinstance(value, date):
        return value
    text = str(value).strip()
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise BacktestError(f"{field_name} 不是 YYYY-MM-DD：{text!r}") from exc


def artifacts_root(root: Path | None = None) -> Path:
    return (Path(root) if root is not None else data_dir()) / BACKTEST_DIRNAME


def compute_run_id(*, params: Mapping[str, Any], data_fp: str) -> str:
    """``bt_`` + sha256(规范化参数 + 数据指纹)[:8]；``generated_at`` 等不参与。"""
    if not data_fp:
        raise BacktestError("compute_run_id 需要数据指纹")
    material = canonical_params(params) + "\x1f" + data_fp
    return card_mod.RUN_ID_PREFIX + hashlib.sha256(material.encode("utf-8")).hexdigest()[:8]


def _config_param_block(config: BacktestConfig) -> dict[str, Any]:
    return {
        "commission": config.commission,
        "slippage": config.slippage,
        "stamp_duty": config.stamp_duty,
        "min_trades": config.min_trades,
        "in_sample_ratio": config.in_sample_ratio,
        "cost_tiers": list(config.cost_tiers),
        "commission_floor_yuan": config.commission_floor_yuan,
        "transfer_fee": config.transfer_fee,
    }


def _build_card(
    *,
    run_id: str,
    symbol: str,
    board: str,
    limit_pct: float,
    series: BarSeries,
    strategy: Strategy,
    matched: MatchResult,
    metrics: Metrics,
    gate: guard_mod.GateVerdict,
    config: BacktestConfig,
    initial_cash: float,
    params: Mapping[str, Any],
) -> dict[str, Any]:
    bars = series.bars
    benchmark_curve = benchmark_buy_and_hold(bars, initial_cash=initial_cash)
    benchmark_return = 0.0
    if benchmark_curve:
        first = benchmark_curve[0]["equity"]
        last = benchmark_curve[-1]["equity"]
        if first > 0:
            benchmark_return = last / first - 1.0
    trading_days = TRADING_DAYS
    period = {"start": bars[0].date, "end": bars[-1].date, "trading_days": trading_days}
    base = {"run_id": run_id, "data_fp": series.data_fp, "calc_version": CALC_VERSION, **period}
    whole_ids = {
        "run_card": calc_fingerprint(
            "backtest_run_card", {**base, "params": dict(params)}, data_fp=series.data_fp
        ),
        "data": calc_fingerprint(
            "backtest_data", {"symbol": symbol, "adjust": series.adjust, **period},
            data_fp=series.data_fp,
        ),
        "gate": calc_fingerprint(
            "backtest_gate",
            {
                **base,
                **_config_param_block(config),
                "passed": gate.passed,
            },
            data_fp=series.data_fp,
        ),
        "benchmark": calc_fingerprint(
            "backtest_benchmark_buy_and_hold",
            {**base, "initial_cash": initial_cash},
            data_fp=series.data_fp,
        ),
    }
    return {
        "schema_version": card_mod.SCHEMA_VERSION,
        "run_id": run_id,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "symbol": symbol,
        "board": board,
        "limit_pct": limit_pct,
        "strategy": strategy.name,
        "strategy_params": dict(strategy.params()),
        "period": period,
        "data": {
            "fingerprint": series.data_fp,
            "first_date": series.first_date,
            "last_date": series.last_date,
            "bars": len(bars),
            "source": "daily_k",
            "adjust": series.adjust,
        },
        "assumptions": list(ASSUMPTIONS),
        "config": config.as_dict(),
        "config_sources": config.provenance(),
        "initial_cash": initial_cash,
        "final_equity": matched.final_equity,
        "open_position_pnl": matched.open_position_pnl,
        "metrics": metrics.values(),
        "metrics_display": metrics.display(),
        "metrics_calc_ids": dict(metrics.calc_ids),
        "equity_curve": equity_curve_with_drawdown(matched),
        "benchmark": {
            "name": "买入持有（首日收盘买入，不计成本）",
            "note": (
                "本机 index_daily 只有 000300（沪深 300），没有上证指数日线；M1 不编造基准，"
                "用该标的买入持有做价格对照。基准不计成本，与策略比较时请同时看成本敏感性三档。"
            ),
            "curve": benchmark_curve,
            "cumulative_return": benchmark_return,
        },
        "trades": [t.to_dict() for t in matched.trades],
        "gate": gate.to_dict(),
        "calc_ids": whole_ids,
        "artifacts": card_mod.artifact_names(run_id),
    }


def run_backtest(
    *,
    strategy: str,
    symbol: str,
    start: str | date,
    end: str | date,
    strategy_kwargs: Mapping[str, Any] | None = None,
    config: BacktestConfig | None = None,
    config_path: Path | None = None,
    overrides: Mapping[str, Any] | None = None,
    initial_cash: float = DEFAULT_INITIAL_CASH,
    data_root: Path | None = None,
    artifacts_root_dir: Path | None = None,
) -> RunResult:
    """跑一次回测并落盘运行卡。

    参数：``strategy`` 为内置名（``ma_cross`` / ``momentum`` / ``breakout``），
    ``strategy_kwargs`` 为策略参数（如 ``{"short_window": 5, "long_window": 20}``），
    ``overrides`` 为 CLI 覆盖（键同 ``BacktestConfig`` 字段）——与 ``config_path`` 互斥：
    显式传了 ``config`` 就不再读 yaml（方便测试直接给配置对象）。

    所有输入错误各抛原异常：``StrategyError`` / ``UniverseError`` / ``BacktestConfigError`` /
    ``FeedError`` / ``BacktestError``；调用方（CLI / Web）按类型映射退出码 / HTTP 码。
    """
    if initial_cash <= 0:
        raise BacktestError(f"initial_cash 必须 > 0，收到 {initial_cash!r}")

    # —— 策略（未知名 / 参数不匹配 → StrategyError）
    strat = make(strategy, **dict(strategy_kwargs or {}))

    # —— 标的与涨跌停幅度（超出 M1 标的域 → UniverseError）
    sym = single(symbol)[0]

    # —— 配置：默认读 yaml，CLI 覆盖走 with_overrides（非法 → BacktestConfigError）
    cfg = config if config is not None else load_config(config_path)
    if overrides:
        cfg = cfg.with_overrides(**dict(overrides))

    # —— 数据（缺 → FeedError；缺就报缺）
    start_day = parse_day(start, field_name="from")
    end_day = parse_day(end, field_name="to")
    if start_day > end_day:
        raise BacktestError(f"起始日 {start_day} 晚于结束日 {end_day}")
    series = load_series(sym.code, start=start_day, end=end_day, root=data_root)
    if len(series) < 3:
        raise FeedError(
            f"{sym.code} 在 {start_day}~{end_day} 内只有 {len(series)} 根 K 线，"
            "至少 3 根（样本内外切分各需 1 根）才能回测"
        )

    # —— run_id：参数 + 数据指纹（不含 generated_at）
    params: dict[str, Any] = {
        "schema_version": card_mod.SCHEMA_VERSION,
        "calc_version": CALC_VERSION,
        "symbol": sym.code,
        "board": sym.board,
        "limit_pct": sym.limit_pct(),
        "strategy": strat.params(),
        "period": {"start": start_day.isoformat(), "end": end_day.isoformat()},
        "initial_cash": float(initial_cash),
        "config": _config_param_block(cfg),
    }
    run_id = compute_run_id(params=params, data_fp=series.data_fp)

    # —— 撮合（全区间）
    costs = CostModel(
        commission=cfg.commission,
        slippage=cfg.slippage,
        stamp_duty=cfg.stamp_duty,
        commission_floor=cfg.commission_floor_yuan,
        transfer_fee=cfg.transfer_fee,
    )
    matched = Engine(
        strategy=strat, costs=costs, initial_cash=float(initial_cash), limit_pct=sym.limit_pct()
    ).run(series.bars)

    # —— 计分（每指标带 calc_id）
    metrics = compute(matched, series.bars, data_fp=series.data_fp)

    # —— 校验门（重跑样本内外 + 三档成本）
    gate = guard_mod.evaluate(
        series.bars,
        strategy_factory=lambda: make(strategy, **dict(strategy_kwargs or {})),
        config=cfg,
        initial_cash=float(initial_cash),
        limit_pct=sym.limit_pct(),
        data_fp=series.data_fp,
        baseline=metrics,
    )

    # —— 运行卡
    run_card = _build_card(
        run_id=run_id,
        symbol=sym.code,
        board=sym.board,
        limit_pct=sym.limit_pct(),
        series=series,
        strategy=strat,
        matched=matched,
        metrics=metrics,
        gate=gate,
        config=cfg,
        initial_cash=float(initial_cash),
        params=params,
    )

    # —— 落盘（同 run_id 覆写；内容必然一致，见 tests/unit/backtest/test_run_id.py）
    target_dir = (Path(artifacts_root_dir) if artifacts_root_dir else artifacts_root()) / run_id
    paths = card_mod.write_artifacts(run_card, target_dir)
    return RunResult(
        run_id=run_id,
        card=run_card,
        json_path=paths["json"],
        markdown_path=paths["markdown"],
        html_path=paths["html"],
        artifacts_dir=target_dir,
    )


def load_run_card(run_id: str, *, root: Path | None = None) -> dict[str, Any]:
    """读已落盘的运行卡（Web 端点用）；不存在抛 ``FileNotFoundError``。"""
    if not run_id or run_id != run_id.strip():
        raise BacktestError(f"run_id 不合法：{run_id!r}")
    if any(ch not in "0123456789abcdefghijklmnopqrstuvwxyz_" for ch in run_id):
        raise BacktestError(f"run_id 含非法字符：{run_id!r}")
    path = artifacts_root(root) / run_id / f"{run_id}.json"
    if not path.is_file():
        raise FileNotFoundError(f"运行卡不存在：{path}")
    return json.loads(path.read_text(encoding="utf-8"))


def list_runs(*, root: Path | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """列出已落盘的运行卡摘要（新的在前）；只读目录，不重算任何指标。"""
    base = artifacts_root(root)
    if not base.is_dir():
        return []
    entries: list[tuple[float, dict[str, Any]]] = []
    for child in base.iterdir():
        if not child.is_dir():
            continue
        json_path = child / f"{child.name}.json"
        if not json_path.is_file():
            continue
        try:
            card = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        mtime = json_path.stat().st_mtime
        entries.append(
            (
                mtime,
                {
                    "run_id": card.get("run_id"),
                    "generated_at": card.get("generated_at"),
                    "symbol": card.get("symbol"),
                    "strategy": card.get("strategy"),
                    "period": card.get("period"),
                    "gate_passed": (card.get("gate") or {}).get("passed"),
                    "cumulative_return": (card.get("metrics") or {}).get("cumulative_return"),
                },
            )
        )
    entries.sort(key=lambda item: item[0], reverse=True)
    return [entry for _, entry in entries[: max(0, limit)]]


__all__ = [
    "ASSUMPTIONS",
    "BACKTEST_DIRNAME",
    "BacktestError",
    "DEFAULT_INITIAL_CASH",
    "DEFAULT_SYMBOL",
    "RunResult",
    "artifacts_root",
    "compute_run_id",
    "list_runs",
    "load_run_card",
    "parse_day",
    "run_backtest",
]
