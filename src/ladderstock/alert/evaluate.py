"""规则求值与「何时算触发」的状态机（纯函数，不落库、不联网）。

`evaluate_rule` 只看**此刻的数据**回答「条件是否成立」；`should_fire` 负责
「成立之后要不要再报一次」。两者分开是因为这两个问题的失败面不同：前者缺数据
要如实说 `no_data`，后者是轰炸抑制，静默即可。

求值口径（数字只能来自入参 `SymbolContext`，本模块不取数）：

- `price_above` / `price_below`：`latest_price` 与 threshold 比较；
- `ma_break_down` / `ma_break_up`：`mean(closes[-window:])`（含最新一根）；
  现价在均线下方 / 上方即成立。**没有历史时不硬算**（`closes` 不足 window 根
  → `no_data`，宁可不报，不拿短窗口凑）；
- `volume_spike`：`volumes[-1] / mean(volumes[-window-1:-1]) >= threshold`
  （当日量比前 N 日均量；分母必须 > 0）；
- `pnl_drop` / `pnl_gain`：`(latest_price − unit_cost) / unit_cost`（百分号口径）；
  `unit_cost` 为 None —— 没持仓或成本缺失 —— 是 `no_data` 不是 0%。

状态机（`should_fire`）：

- `armed`（初始）：触发即报，进 `cooldown`；
- `cooldown`：冷却期内再触发不报；冷却结束后条件仍成立 → **继续不报**（避免
  开盘后每小时重报同一条），等条件离开（`triggered=False` 出现一次）重臂回 `armed`；
- `fired`（历史遗留态）：语义同 `cooldown`（无 cooldown_until 时视同冷却已过，
  等待重臂）。

这是「越线报一次」的口径，与 `watch/signal.py` 的阈值状态机（`above` 去重）同族
——持续状态不刷屏，离开再进入才算新事件。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from ladderstock.alert.rules import (
    MA_BREAK_DOWN,
    MA_BREAK_UP,
    PNL_DROP,
    PNL_GAIN,
    PRICE_ABOVE,
    PRICE_BELOW,
    VOLUME_SPIKE,
)

STATE_ARMED = "armed"
STATE_COOLDOWN = "cooldown"
STATE_FIRED = "fired"

#: volume_spike 分母的下限：日均量小于这个值（手）说明票几乎没有成交，
#: 「倍数」会被噪声放大到离谱，不报。
MIN_AVG_VOLUME = 100.0


@dataclass(frozen=True, slots=True)
class SymbolContext:
    """一只票的全部求值原料（`run.py` 负责填，本模块只消费）。"""

    code: str
    #: 现价（`watch_prices` 的 `latest_price`；None = 没取到，下游不得顶替）
    latest_price: float | None = None
    prev_close: float | None = None
    change_pct: float | None = None
    price_reason: str | None = None
    #: 收盘序列（旧 → 新，**含最新一根**；不足窗口长度时均线类报 no_data）
    closes: list[float] = field(default_factory=list)
    #: 成交量序列（旧 → 新，与 closes 同长；None 值已在构建期被剔除）
    volumes: list[float] = field(default_factory=list)
    #: 持仓成本（journal/position 的 cost；无持仓 / 未取到 = None）
    unit_cost: float | None = None
    as_of: str | None = None


@dataclass(frozen=True, slots=True)
class EvalOutcome:
    """一次求值的结果：`no_data` 时 triggered 恒 False。"""

    triggered: bool
    status: str  # "ok" | "no_data"
    value: float | None
    detail: str = ""


def _mean(numbers: list[float]) -> float | None:
    if not numbers:
        return None
    return sum(numbers) / len(numbers)


def evaluate_rule(rule: dict[str, Any], ctx: SymbolContext) -> EvalOutcome:
    kind = str(rule.get("kind") or "")
    raw_threshold = rule.get("threshold")
    threshold = float(raw_threshold) if raw_threshold is not None else 0.0
    window = int(rule.get("window") or 0)

    if kind in (PRICE_ABOVE, PRICE_BELOW):
        if ctx.latest_price is None:
            return EvalOutcome(False, "no_data", None, ctx.price_reason or "现价未取到")
        if kind == PRICE_ABOVE:
            triggered = ctx.latest_price >= threshold
        else:
            triggered = ctx.latest_price <= threshold
        return EvalOutcome(triggered, "ok", ctx.latest_price, f"现价 {ctx.latest_price:g}")

    if kind in (MA_BREAK_DOWN, MA_BREAK_UP):
        if ctx.latest_price is None:
            return EvalOutcome(False, "no_data", None, ctx.price_reason or "现价未取到")
        if len(ctx.closes) < max(2, window):
            return EvalOutcome(
                False,
                "no_data",
                None,
                f"本地历史只有 {len(ctx.closes)} 根，不足 MA{window} 所需",
            )
        ma = _mean(ctx.closes[-window:])
        if ma is None or ma <= 0:
            return EvalOutcome(False, "no_data", None, "均线算不出（历史价非正）")
        if kind == MA_BREAK_DOWN:
            triggered = ctx.latest_price < ma
        else:
            triggered = ctx.latest_price > ma
        return EvalOutcome(
            triggered, "ok", ctx.latest_price, f"现价 {ctx.latest_price:g} / MA{window} {ma:.3f}"
        )

    if kind == VOLUME_SPIKE:
        if not ctx.volumes or ctx.volumes[-1] is None:
            return EvalOutcome(False, "no_data", None, "当日成交量未取到")
        prior = ctx.volumes[max(0, len(ctx.volumes) - 1 - window) : len(ctx.volumes) - 1]
        if len(prior) < max(2, min(window, 3)):
            return EvalOutcome(False, "no_data", None, f"历史量不足（{len(prior)} 根），算不出均量")
        avg = _mean(prior)
        if avg is None or avg < MIN_AVG_VOLUME:
            return EvalOutcome(False, "no_data", None, "日均量过小，倍数口径噪声过大")
        ratio = ctx.volumes[-1] / avg
        return EvalOutcome(
            ratio >= threshold,
            "ok",
            ratio,
            f"量比 {ratio:.2f} 倍（前 {len(prior)} 日均量 {avg:.0f}）",
        )

    if kind in (PNL_DROP, PNL_GAIN):
        if ctx.latest_price is None or ctx.unit_cost is None or ctx.unit_cost <= 0:
            return EvalOutcome(
                False, "no_data", None, "现价或持仓成本缺失（无持仓时配 pnl 规则无意义）"
            )
        pnl_pct = (ctx.latest_price - ctx.unit_cost) / ctx.unit_cost * 100.0
        if kind == PNL_DROP:
            triggered = pnl_pct <= -abs(threshold)
        else:
            triggered = pnl_pct >= abs(threshold)
        return EvalOutcome(
            triggered, "ok", pnl_pct, f"现价 {ctx.latest_price:g} / 成本 {ctx.unit_cost:g}"
        )

    return EvalOutcome(False, "no_data", None, f"未知规则类型 {kind!r}")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def should_fire(
    rule: dict[str, Any],
    outcome: EvalOutcome,
    state: dict[str, Any],
    *,
    now: datetime | None = None,
) -> tuple[bool, str, str | None]:
    """返回 `(是否报, 新状态, 下次冷却到期时刻)`。

    - `triggered=False` 且原状态不是 armed → **重臂**：新状态回 armed（这是
      「越线报一次」口径的另一半：离开才算事件结束）；
    - `triggered=True` 且 armed（或冷却已过且已重臂）→ 报，进 cooldown；
    - 其余不报、状态原样。
    """
    moment = now or datetime.now().astimezone()
    cooldown_min = int(rule.get("cooldown_min") or 60)
    status = str(state.get("status") or STATE_ARMED)
    cooldown_until = _parse_iso(state.get("cooldown_until"))

    if not outcome.triggered:
        if status in (STATE_COOLDOWN, STATE_FIRED):
            return False, STATE_ARMED, None  # 重臂：条件离开
        return False, status, None

    # triggered 走到这：armed → 报；cooldown 未过 → 不报；冷却已过但从未重臂 → 不报
    if status == STATE_ARMED:
        until = (moment + timedelta(minutes=cooldown_min)).isoformat(timespec="seconds")
        return True, STATE_COOLDOWN, until
    if cooldown_until is not None and moment < cooldown_until:
        return False, STATE_COOLDOWN, state.get("cooldown_until")
    return False, STATE_COOLDOWN, state.get("cooldown_until")


__all__ = [
    "EvalOutcome",
    "STATE_ARMED",
    "STATE_COOLDOWN",
    "STATE_FIRED",
    "SymbolContext",
    "evaluate_rule",
    "should_fire",
]
