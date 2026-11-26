"""提醒规则的类型与参数口径（纯函数，不落库、不联网）。

七类规则，每类只回答一个问题：

| kind | 问题 | threshold | window |
|---|---|---|---|
| `price_above` | 现价是否涨到 X | 价格（元） | — |
| `price_below` | 现价是否跌破 X | 价格（元） | — |
| `ma_break_down` | 现价是否跌破 N 日均线 | 无（0.0） | 均线天数（默认 20） |
| `ma_break_up` | 现价是否站上 N 日均线 | 无（0.0） | 均线天数（默认 20） |
| `volume_spike` | 量是否放大到前 N 日均量的 K 倍 | 倍数（默认 2.0） | 均量窗口（默认 5） |
| `pnl_drop` | 浮亏是否超过 X% | 百分比（正数，5 = −5%） | — |
| `pnl_gain` | 浮盈是否超过 X% | 百分比（正数，5 = +5%） | — |

三条口径（来自平台红线，不是设计偏好）：

1. **数据取不到 → 不触发**，`evaluate` 返回 `no_data`；绝不拿昨收 / 成本价 /
   0 顶替现价（DEC-028 同口径）。
2. **`pnl_*` 只对持仓有效**：成本来自本机持仓账本（`journal/position`）；没持仓
   或成本缺失 → `no_data` 并写明原因，不猜。
3. `ma_*` 的均线按 **「含最新一根」的滚动窗口** 计算（盘中触发时最新价作为当日
   运行收盘）——这是「价格与自身 N 日均值的位置关系」，不是严格的
   「close 上穿 MA」信号；口径写明，用户想要严格口径时用回测的均线策略。
"""

from __future__ import annotations

import re
from typing import Any

from ladderstock.errors import EXIT_USAGE, LadderStockError

PRICE_ABOVE = "price_above"
PRICE_BELOW = "price_below"
MA_BREAK_DOWN = "ma_break_down"
MA_BREAK_UP = "ma_break_up"
VOLUME_SPIKE = "volume_spike"
PNL_DROP = "pnl_drop"
PNL_GAIN = "pnl_gain"

RULE_KINDS: tuple[str, ...] = (
    PRICE_ABOVE,
    PRICE_BELOW,
    MA_BREAK_DOWN,
    MA_BREAK_UP,
    VOLUME_SPIKE,
    PNL_DROP,
    PNL_GAIN,
)

KIND_LABELS: dict[str, str] = {
    PRICE_ABOVE: "价格涨到",
    PRICE_BELOW: "价格跌破",
    MA_BREAK_DOWN: "跌破均线",
    MA_BREAK_UP: "站上均线",
    VOLUME_SPIKE: "放量",
    PNL_DROP: "浮亏超过",
    PNL_GAIN: "浮盈超过",
}

#: 各类型 window 的缺省值（`price_*` / `pnl_*` 不用窗口，恒 0）
_DEFAULT_WINDOW: dict[str, int] = {
    MA_BREAK_DOWN: 20,
    MA_BREAK_UP: 20,
    VOLUME_SPIKE: 5,
}

#: 各类型 threshold 是否必须为正（`price_*` 允许任意正价；倍数 / 百分比必须 > 0）
_POSITIVE_THRESHOLD: frozenset[str] = frozenset({VOLUME_SPIKE, PNL_DROP, PNL_GAIN})
#: threshold 无意义（均线类不用阈值）的规则
_NO_THRESHOLD: frozenset[str] = frozenset({MA_BREAK_DOWN, MA_BREAK_UP})

_SIX_DIGITS = re.compile(r"^\d{6}$")

#: B 股与 `core/portfolio.py` 同口径拒收（外币计价，提醒口径算错不如不提醒）
_B_SHARE = re.compile(r"^(?:900|200)\d{3}$")


def normalize_symbol(value: Any) -> str:
    """只收 6 位 A 股数字代码（与 watchlist 一致：不带交易所前缀）。"""
    text = str(value or "").strip().upper()
    if not _SIX_DIGITS.match(text):
        raise LadderStockError(
            f"标的代码需要 6 位数字（当前 watchlist 口径，不带前缀），收到 {value!r}", EXIT_USAGE
        )
    if _B_SHARE.match(text):
        raise LadderStockError(
            f"B 股（900/200 开头）暂不支持：按港/美元计价，提醒算的是人民币口径（{text}）",
            EXIT_USAGE,
        )
    return text


def _as_float(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LadderStockError(f"{name} 需要数字，收到 {value!r}", EXIT_USAGE)
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):  # nan / inf
        raise LadderStockError(f"{name} 需要有限数字，收到 {value!r}", EXIT_USAGE)
    return number


def validate_rule(
    kind: str,
    symbol: str,
    threshold: float,
    *,
    window: int | None = None,
    cooldown_min: int = 60,
    channels: list[str] | None = None,
) -> dict[str, Any]:
    """校验并归一一条规则；任何字段非法都是用法错误（exit 2），当场抛出。"""
    text_kind = str(kind or "").strip().lower()
    if text_kind not in RULE_KINDS:
        raise LadderStockError(f"未知规则类型 {kind!r}；可用：{', '.join(RULE_KINDS)}", EXIT_USAGE)
    clean_symbol = normalize_symbol(symbol)

    if text_kind in _NO_THRESHOLD:
        clean_threshold = 0.0
    else:
        clean_threshold = _as_float(threshold, "threshold")
        if clean_threshold <= 0 and text_kind in _POSITIVE_THRESHOLD:
            raise LadderStockError(
                f"{text_kind} 的 threshold 必须为正数，收到 {threshold!r}", EXIT_USAGE
            )
        if text_kind in (PRICE_ABOVE, PRICE_BELOW) and clean_threshold <= 0:
            raise LadderStockError(f"价格阈值必须为正数（元），收到 {threshold!r}", EXIT_USAGE)

    clean_window = 0
    if text_kind in _DEFAULT_WINDOW:
        clean_window = int(window) if window is not None else _DEFAULT_WINDOW[text_kind]
    if clean_window and not 2 <= clean_window <= 250:
        raise LadderStockError(
            f"window 需要在 2–250 之间（{text_kind} 的窗口天数），收到 {window!r}", EXIT_USAGE
        )

    clean_cooldown = int(cooldown_min)
    if not 1 <= clean_cooldown <= 60 * 24 * 7:
        raise LadderStockError(
            f"cooldown_min 需要在 1–10080 分钟之间，收到 {cooldown_min!r}", EXIT_USAGE
        )

    clean_channels: list[str] = []
    for channel in channels or []:
        text = str(channel or "").strip().lower()
        if text and text not in clean_channels:
            clean_channels.append(text)

    return {
        "kind": text_kind,
        "symbol": clean_symbol,
        "threshold": clean_threshold,
        "window": clean_window,
        "cooldown_min": clean_cooldown,
        "channels": clean_channels,
        "enabled": True,
    }


def describe_rule(rule: dict[str, Any]) -> str:
    """把一条规则翻成人话（CLI 表格 / 通知正文共用一份口径）。"""
    kind = rule.get("kind") or ""
    symbol = rule.get("symbol") or ""
    threshold = rule.get("threshold")
    window = rule.get("window") or 0
    label = KIND_LABELS.get(kind, kind)
    if kind in _NO_THRESHOLD:
        return f"{symbol} {label} MA{window}"
    if kind == VOLUME_SPIKE:
        return f"{symbol} {label} {threshold:g} 倍（前 {window} 日均量）"
    if kind in (PNL_DROP, PNL_GAIN):
        sign = "−" if kind == PNL_DROP else "+"
        return f"{symbol} {label} {sign}{threshold:g}%"
    return f"{symbol} {label} {threshold:g}"


__all__ = [
    "KIND_LABELS",
    "MA_BREAK_DOWN",
    "MA_BREAK_UP",
    "PNL_DROP",
    "PNL_GAIN",
    "PRICE_ABOVE",
    "PRICE_BELOW",
    "RULE_KINDS",
    "VOLUME_SPIKE",
    "describe_rule",
    "normalize_symbol",
    "validate_rule",
]
