"""回测策略协议 + 3 个内置策略（QUOT-500 §5.1）。

**协议（不可破）**：策略只看到「``t`` 及之前」的 K 线（策略输入是 ``history = bars[:t+1]``），
只输出**目标持仓**（``1.0`` = 满仓、``0.0`` = 空仓），不给成交价、不给成交时点。
撮合层（``match.py``）永远在 ``t+1`` 按开盘价 ± 滑点成交，所以即便策略故意「知道明天」，
也只能在一根更晚的 K 线上成交，收益天然被扣掉——这一点由
``tests/unit/backtest/test_lookahead.py`` 用「知道明天」策略验证。

三个内置：

1. ``ma_cross``  双均线交叉：``short_ma`` 上穿 ``long_ma`` → 1，下穿 → 0（默认 5 / 20）；
2. ``momentum``  N 日动量：``close[t] / close[t−N] − 1 > threshold`` → 1，否则 0
   （默认 N=20, 阈值=0）；
3. ``breakout``  N 日最高价突破：``close[t]`` 超过前 N 日最高（**不含 t**，避免 t 自身脏数据
   抬高阈值）→ 1，跌破 N 日最低 → 0（默认 N=20）。与「海龟」不同的点是退出条件用当日出的
   ``N`` 日最低价而不是 ATR 止损——M1 没引入波动率仓位。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final, Protocol, cast

from ladderstock.backtest.feed import Bar


class StrategyError(ValueError):
    """参数非法 / 未知策略名。"""


class Strategy(Protocol):
    name: str

    def params(self) -> dict[str, Any]: ...

    def target(self, history: Sequence[Bar]) -> float:
        """``history`` 是 ``t`` 及之前（含）的有序 K 线；返回目标仓位 ``[0, 1]``。"""
        ...


def _clamp_target(value: float, *, where: str) -> float:
    if not math.isfinite(value) or value < 0 or value > 1:
        raise StrategyError(f"{where}: 目标仓位必须是 [0, 1] 内的有限数，收到 {value!r}")
    return value


@dataclass(slots=True)
class MACross:
    """双均线交叉；默认 5 / 20。"""

    short_window: int = 5
    long_window: int = 20
    name: str = "ma_cross"

    def __post_init__(self) -> None:
        if not isinstance(self.short_window, int) or self.short_window < 1:
            raise StrategyError(f"short_window 必须是 >= 1 的整数，收到 {self.short_window!r}")
        if not isinstance(self.long_window, int) or self.long_window < 1:
            raise StrategyError(f"long_window 必须是 >= 1 的整数，收到 {self.long_window!r}")
        if self.short_window >= self.long_window:
            raise StrategyError(
                f"short_window({self.short_window}) 必须小于 long_window({self.long_window})"
            )

    def params(self) -> dict[str, Any]:
        return {
            "strategy": self.name,
            "short_window": self.short_window,
            "long_window": self.long_window,
        }

    def target(self, history: Sequence[Bar]) -> float:
        long = self.long_window
        # 信号只在足够长的历史下计算：不足长均线 -> 空仓（不用未来数据补）
        if len(history) < long:
            return 0.0
        closes = [bar.close for bar in history[-long:]]
        long_ma = sum(closes) / long
        # 短均线用长均线同一组尾部数据中的后 short_window 段
        short_ma = sum(closes[-self.short_window:]) / self.short_window
        return _clamp_target(1.0 if short_ma > long_ma else 0.0, where=self.name)


@dataclass(slots=True)
class Momentum:
    """N 日动量：``close[t] / close[t−N] − 1 > threshold`` → 满仓，否则空仓。"""

    lookback: int = 20
    threshold: float = 0.0
    name: str = "momentum"

    def __post_init__(self) -> None:
        if not isinstance(self.lookback, int) or self.lookback < 1:
            raise StrategyError(f"lookback 必须是 >= 1 的整数，收到 {self.lookback!r}")
        if isinstance(self.threshold, bool) or not isinstance(self.threshold, int | float):
            raise StrategyError(f"threshold 必须是数值，收到 {self.threshold!r}")
        if not math.isfinite(float(self.threshold)):
            raise StrategyError(f"threshold 必须是有限数值，收到 {self.threshold!r}")

    def params(self) -> dict[str, Any]:
        return {
            "strategy": self.name,
            "lookback": self.lookback,
            "threshold": float(self.threshold),
        }

    def target(self, history: Sequence[Bar]) -> float:
        n = self.lookback
        # 需要 [t-N] 的一根；不足 N 根（含 t）时空仓
        if len(history) < n + 1:
            return 0.0
        base_close = history[-n - 1].close
        if base_close <= 0:
            return 0.0
        ratio = history[-1].close / base_close - 1.0
        return _clamp_target(1.0 if ratio > self.threshold else 0.0, where=self.name)


@dataclass(slots=True)
class Breakout:
    """N 日突破：``close[t]`` 超过**不含 t** 的前 N 日最高 → 1；跌破前 N 日最低 → 0。"""

    lookback: int = 20
    name: str = "breakout"

    def __post_init__(self) -> None:
        if not isinstance(self.lookback, int) or self.lookback < 1:
            raise StrategyError(f"lookback 必须是 >= 1 的整数，收到 {self.lookback!r}")

    def params(self) -> dict[str, Any]:
        return {"strategy": self.name, "lookback": self.lookback}

    def target(self, history: Sequence[Bar]) -> float:
        n = self.lookback
        # 必须至少有 N+1 根：前面 N 根算通道，最后一根用来比较
        if len(history) < n + 1:
            return 0.0
        window = history[-n - 1 : -1]
        current_close = history[-1].close
        high = max(bar.high for bar in window)
        # 纯函数不做「状态保持」：只看**当根**是否仍高于通道下沿，否则空仓。
        # 这样做的好处是「会不会因为策略状态记忆而污染回测」这个风险在 M1 完全规避，
        # 代价是每根在通道中部都可以空仓出场（M2 再引入有状态策略）。
        low = min(bar.low for bar in window)
        if current_close >= high:
            return 1.0
        if current_close <= low:
            return 0.0
        return 1.0  # 通道内维持「已在场内」的语义：只在跌破下沿时出场


BUILTIN: Final[dict[str, type]] = {
    "ma_cross": MACross,
    "momentum": Momentum,
    "breakout": Breakout,
}

BUILTIN_NAMES: Final[tuple[str, ...]] = tuple(BUILTIN.keys())


@dataclass(frozen=True, slots=True)
class ParamSpec:
    """策略参数元数据（页面表单用）；默认值必须与策略 dataclass 的默认值一致。"""

    key: str
    label: str
    type: str  # "int" | "float"
    default: int | float
    min: float
    max: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "type": self.type,
            "default": self.default,
            "min": self.min,
            "max": self.max,
        }


@dataclass(frozen=True, slots=True)
class StrategySpec:
    """内置策略清单条目：清单必须来自后端常量（前端不许硬编码）。"""

    name: str
    label: str
    params: tuple[ParamSpec, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "params": [param.as_dict() for param in self.params],
        }


#: 页面参数区的策略目录；与 ``BUILTIN`` 同名同序，新增策略时两处一起加
CATALOG: Final[dict[str, StrategySpec]] = {
    "ma_cross": StrategySpec(
        name="ma_cross",
        label="双均线交叉",
        params=(
            ParamSpec("short_window", "短均线窗口", "int", 5, 1, 250),
            ParamSpec("long_window", "长均线窗口", "int", 20, 2, 500),
        ),
    ),
    "momentum": StrategySpec(
        name="momentum",
        label="N 日动量",
        params=(
            ParamSpec("lookback", "回看天数", "int", 20, 1, 250),
            ParamSpec("threshold", "触发阈值（小数）", "float", 0.0, -0.5, 0.5),
        ),
    ),
    "breakout": StrategySpec(
        name="breakout",
        label="N 日突破",
        params=(ParamSpec("lookback", "通道窗口", "int", 20, 1, 250),),
    ),
}


def catalog() -> list[dict[str, Any]]:
    """按 ``BUILTIN_NAMES`` 的顺序返回策略清单（``GET /api/backtest/strategies``）。"""
    return [CATALOG[name].as_dict() for name in BUILTIN_NAMES]


def make(name: str, **kwargs: Any) -> Strategy:
    """按名字构造内置策略；未知参数直接报错（不吞掉）。"""
    cls = BUILTIN.get(name)
    if cls is None:
        raise StrategyError(f"未知策略 {name!r}（内置：{', '.join(BUILTIN_NAMES)}）")
    try:
        return cast(Strategy, cls(**kwargs))
    except TypeError as exc:
        raise StrategyError(f"策略 {name!r} 参数不匹配：{exc}") from exc


def describe(strategy: Strategy) -> dict[str, Any]:
    return dict(strategy.params())


__all__ = [
    "BUILTIN",
    "BUILTIN_NAMES",
    "Breakout",
    "CATALOG",
    "MACross",
    "Momentum",
    "ParamSpec",
    "Strategy",
    "StrategyError",
    "StrategySpec",
    "catalog",
    "describe",
    "make",
]
