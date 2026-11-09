"""确定性展示格式（不依赖 locale、不依赖系统时区、无全局可变状态）。

同一输入在任何机器上输出完全相同的字符串：

- 数字一律定点表示，千分位固定为 ``,``、小数点固定为 ``.``；不使用 locale 的 ``n`` 格式，
  也不引入千分位以外的自定义符号。
- 小数位固定（默认 2 位），舍入沿用 Python 对二进制浮点的 round-half-even 行为，跨平台一致。
- 负数零归一为 ``0.00``，不输出 ``-0.00``；``inf`` / ``NaN`` / 布尔值直接 ``ValueError``。
- 单位映射：
  * ``小数``（比率）→ 百分比：``0.1842`` → ``"18.42%"``；
  * ``%``（已是百分数）→ 原样加 ``%``；
  * ``元 / 万元 / 亿元`` → 先归一到元，再按量级输出 ``亿元 / 万元 / 元``；
  * 其他单位 → ``"<数> <单位>"``（未知单位不猜换算）。
"""

from __future__ import annotations

import math
from typing import Final

from ladderstock.calc.money import UNIT_TO_YUAN
from ladderstock.calc.result import CalcResult

THOUSANDS_SEPARATOR: Final = ","
DECIMAL_POINT: Final = "."
CURRENCY_LABELS: Final = {"CNY": "元", "HKD": "港元", "USD": "美元"}


def _finite(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} 必须是数值，收到 {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} 必须是有限数值，收到 {value!r}")
    return number


def _decimals(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"decimals 必须是 >= 0 的整数，收到 {value!r}")
    return value


def format_number(value: float, *, decimals: int = 2) -> str:
    """定点数字：``1234.5`` → ``"1,234.50"``；负数零 → ``"0.00"``。"""
    number = _finite("value", value)
    digits = _decimals(decimals)
    text = f"{number:,.{digits}f}"
    if text.startswith("-") and float(text.replace(THOUSANDS_SEPARATOR, "")) == 0.0:
        text = text[1:]
    return text


def format_percent(ratio: float, *, decimals: int = 2) -> str:
    """比率转百分比：``0.1842`` → ``"18.42%"``（输入是小数，不是百分数）。"""
    number = _finite("ratio", ratio)
    return f"{format_number(number * 100.0, decimals=decimals)}%"


def format_money(amount_yuan: float, *, currency: str = "CNY", decimals: int = 2) -> str:
    """元金额按量级展示：``123456789`` → ``"1.23 亿元"``。"""
    number = _finite("amount_yuan", amount_yuan)
    if currency not in CURRENCY_LABELS:
        raise ValueError(f"未知币种 {currency!r}")
    label = CURRENCY_LABELS[currency]
    magnitude = abs(number)
    if magnitude >= 1e8:
        scaled, prefix = number / 1e8, "亿"
    elif magnitude >= 1e4:
        scaled, prefix = number / 1e4, "万"
    else:
        scaled, prefix = number, ""
    return f"{format_number(scaled, decimals=decimals)} {prefix}{label}"


def format_quantity(value: float, unit: str, *, decimals: int = 2) -> str:
    """带单位数字：``18.42, "倍"`` → ``"18.42 倍"``；空单位只给数字。"""
    number = _finite("value", value)
    if not isinstance(unit, str):
        raise ValueError(f"unit 必须是字符串，收到 {unit!r}")
    normalized = unit.strip()
    if not normalized:
        return format_number(number, decimals=decimals)
    if normalized == "小数":
        return format_percent(number, decimals=decimals)
    if normalized == "%":
        return f"{format_number(number, decimals=decimals)}%"
    if normalized in UNIT_TO_YUAN:
        return format_money(number * UNIT_TO_YUAN[normalized], currency="CNY", decimals=decimals)
    return f"{format_number(number, decimals=decimals)} {normalized}"


def format_result(result: CalcResult, *, decimals: int = 2) -> str | None:
    """格式化 ``CalcResult``；不可展示（``ok=False`` 或没有 value）返回 ``None``。

    计算层已给出 ``display`` 时直接采用（它是复算层产出的权威展示串）。
    """
    if not result.ok or result.value is None:
        return None
    if result.display is not None:
        return result.display
    unit = result.unit or ""
    if unit in UNIT_TO_YUAN:
        currency = result.details.get("currency", "CNY")
        if not isinstance(currency, str) or currency not in CURRENCY_LABELS:
            currency = "CNY"
        return format_money(
            float(result.value) * UNIT_TO_YUAN[unit], currency=currency, decimals=decimals
        )
    return format_quantity(result.value, unit, decimals=decimals)
