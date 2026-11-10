"""回测标的范围（M1 只支持单标的 / 手工列表）。

标的域（用户拍板 + 规格 §5.6）：**只做 A 股主板 / 创业板普通股**，排除
ST、科创板（688/689）、北交所（8xx/4xx）、ETF/LOF 基金（5xx/15x/16x/18x）、
可转债（11x/12x，在深市以 12x 表示）。

「为什么不做科创板」——M1 的回测撮合按主板 10% 涨跌停口径；科创板 20%、
北交所 30%、创业板特例 20%（注册制后）口径不同，进 M2 开独立单处理。
创业板现在仍是 20% —— 这里**创业板也按 20%**（match.py 里 `limit_pct()` 实现），
主板按 10%，ST/科创板/北交所一律不允许进入回测（见本节排除清单）。

当前「单标的 / 手工列表」只是范围声明，不做跨票联动、不做仓位分配。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final

_CODE_RE: Final = re.compile(r"^\d{6}$")

SHANGHAI_MAIN_PREFIXES: Final = ("600", "601", "603", "605")
SHANGHAI_STAR_PREFIXES: Final = ("688", "689")  # 科创板：M1 排除
SHENZHEN_MAIN_PREFIXES: Final = ("000", "001", "002", "003")  # 主板
SHENZHEN_CHINEXT_PREFIXES: Final = ("300", "301")  # 创业板：允许（20% 涨跌停）
BEIJING_PREFIXES: Final = ("43", "83", "87", "88")  # 北交所：M1 排除
FUND_PREFIXES: Final = ("51", "52", "56", "58", "15", "16", "18")  # ETF/LOF：M1 排除
CONVERTIBLE_BOND_PREFIXES: Final = ("11", "12")  # 沪市/深市可转债：M1 排除

#: 涨跌停幅度（10 = 10%，按主板口径；创业板 20%）
LIMIT_PCT_MAIN: Final = 0.10
LIMIT_PCT_CHINEXT: Final = 0.20


class UniverseError(ValueError):
    """标的超出 M1 允许范围，或列表为空。"""


@dataclass(frozen=True, slots=True)
class Symbol:
    code: str
    board: str  # "sh_main" | "sz_main" | "chinext"

    def limit_pct(self) -> float:
        return LIMIT_PCT_CHINEXT if self.board == "chinext" else LIMIT_PCT_MAIN


def classify(code: str) -> Symbol:
    """识别 A 股代码所属板块；超出 M1 范围抛 ``UniverseError``。"""
    text = str(code).strip().upper()
    if not _CODE_RE.match(text):
        raise UniverseError(f"代码 {text or repr(code)} 不是 6 位数字（收到 {code!r}）")
    for prefixes, label, exclusion in (
        (SHANGHAI_STAR_PREFIXES, "科创板", "ST/科创板 M1 排除"),
        (BEIJING_PREFIXES, "北交所", "北交所 M1 排除"),
        (FUND_PREFIXES, "ETF/LOF", "基金 M1 排除"),
        (CONVERTIBLE_BOND_PREFIXES, "可转债", "可转债 M1 排除"),
    ):
        if text.startswith(prefixes):
            raise UniverseError(f"代码 {text} 属于{label}（{exclusion}）")
    if text.startswith(SHANGHAI_MAIN_PREFIXES):
        return Symbol(code=text, board="sh_main")
    if text.startswith(SHENZHEN_MAIN_PREFIXES):
        return Symbol(code=text, board="sz_main")
    if text.startswith(SHENZHEN_CHINEXT_PREFIXES):
        return Symbol(code=text, board="chinext")
    raise UniverseError(f"代码 {text} 未落在 A 股主板/创业板普通股前缀内（M1 不支持）")


def single(code: str) -> tuple[Symbol, ...]:
    """单标的：返回一元元组；超出范围抛 ``UniverseError``。"""
    return (classify(code),)


def manual_list(codes: Sequence[str]) -> tuple[Symbol, ...]:
    """手工列表：逐个分类，任何一只超范围就整批报错（不让半批过关）。"""
    seen: set[str] = set()
    parsed: list[Symbol] = []
    for code in codes:
        symbol = classify(code)
        if symbol.code in seen:
            raise UniverseError(f"标的列表里有重复：{symbol.code}")
        seen.add(symbol.code)
        parsed.append(symbol)
    if not parsed:
        raise UniverseError("标的列表不能为空")
    return tuple(parsed)


def codes(symbols: Iterable[Symbol]) -> list[str]:
    return [s.code for s in symbols]


__all__ = [
    "LIMIT_PCT_CHINEXT",
    "LIMIT_PCT_MAIN",
    "Symbol",
    "UniverseError",
    "classify",
    "codes",
    "manual_list",
    "single",
]
