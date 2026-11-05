"""自选现价：已登记的 `spot_quote` → `tencent_quote_batch`，只个股、只投影，不算指标。

QUOT-380 第 5 项（约 2h）+ DEC-052 §2.2 的字段口径。三条硬约束写在这里而不是散在调用方：

1. **只走已登记数据集**。`spot_quote` 在 `core/boundary.py:326` 有主源，取数只经
   `ProviderRegistry.fetch_live()`；**不自采、不换供应商**（DEC-006），也不为了少写代码
   去借别的工具。
2. **取不到就是 `null` + 原因**，**绝不拿成本价顶替**（DEC-028）。一条自选的价拿不到
   不影响其他条：限流 / 单批失败只丢那一批的读数。
3. **只用于个股**。`tencent_quote_batch` 会把 `sh`/`sz` 前缀剥掉再按 6 位代码回键，
   指数与个股代码撞车时**静默给错票**——指数条走的是 `core/indices.py` + `index_quote`，
   这里不接指数代码，也不接受带前缀的入参（`journal/watchlist.py` 存的就是裸 6 位）。

指标列（QUOT-427 / DEC-060）：`pe_ttm` / `pb` / `turnover_pct` 同样只做投影，不算、
不缓存。上游命名不统一，按 `PE_TTM_KEYS` / `PB_KEYS` / `TURNOVER_PCT_KEYS` 的优先级取
第一个**有限数值**；`0` 是有效读数（亏损股 PE 可以为负，停牌换手可以是 0），**不当缺失**。
三项里缺哪一项就写进该条的 `metrics_reason`，全齐时为 `null`。
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

from ladderstock.core.indices import iso_timestamp
from ladderstock.core.ladder import as_float

DATASET = "spot_quote"
QUOTE_TOOL = "tencent_quote_batch"

#: PE(TTM) 的键名容忍表（顺序即优先级；`pe` 是腾讯批量报价的现网名）
PE_TTM_KEYS: tuple[str, ...] = ("pe_ttm", "pe")
#: PB 的键名容忍表（`pb_mrq` 是部分上游的市净率口径名）
PB_KEYS: tuple[str, ...] = ("pb", "pb_mrq")
#: 换手率的键名容忍表（单位是 %，与 `spot_quote.turnover_rate` 一致，不做换算）
TURNOVER_PCT_KEYS: tuple[str, ...] = ("turnover_rate", "turnover", "换手率", "换手")

#: (输出字段, 容忍键, 缺省原因里的列名)
_METRIC_FIELDS: tuple[tuple[str, tuple[str, ...], str], ...] = (
    ("pe_ttm", PE_TTM_KEYS, "PE(TTM)"),
    ("pb", PB_KEYS, "PB"),
    ("turnover_pct", TURNOVER_PCT_KEYS, "换手%"),
)

#: 一次请求最多带多少只票（与盘中快照同一档，`core/intraday.py:QUOTE_CHUNK`）
PRICE_CHUNK = 50

Status = Literal["ok", "partial", "missing"]

#: 单条自选的取价结果
PRICE_OK = "ok"
PRICE_UNAVAILABLE = "unavailable"

NO_CODES = "本机没有自选，无需取价"


@dataclass(frozen=True)
class PriceStrip:
    """一批自选的现价读数 + 这一批的整体状态（状态给页面用，逐条原因给行用）。"""

    prices: dict[str, dict[str, Any]]
    status: Status
    source_detail: str

    @property
    def as_of(self) -> str | None:
        stamps = [
            item["price_as_of"]
            for item in self.prices.values()
            if item.get("price_as_of")
        ]
        return max(stamps) if stamps else None


def _chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _finite(value: Any) -> float | None:
    """只认有限数值；`0` 原样保留，`nan` / `inf` / 空值都是缺。"""
    number = as_float(value)
    if number is None or not math.isfinite(number):
        return None
    return number


def _metric(record: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        number = _finite(record.get(key))
        if number is not None:
            return number
    return None


def _metrics(record: dict[str, Any] | None, *, code: str) -> dict[str, Any]:
    """投影三列指标 + 逐条原因；三项全齐时 `metrics_reason` 为 `null`。"""
    values: dict[str, Any] = {}
    missing: list[str] = []
    for name, keys, label in _METRIC_FIELDS:
        value = _metric(record or {}, keys)
        values[name] = value
        if value is None:
            missing.append(label)

    if not missing:
        values["metrics_reason"] = None
    elif record is None:
        values["metrics_reason"] = (
            f"上游 {QUOTE_TOOL} 没有返回 {code}，{' / '.join(missing)} 无法投影"
        )
    else:
        values["metrics_reason"] = f"上游 {QUOTE_TOOL} 不返回 {' / '.join(missing)}"
    return values


def fetch_prices(registry: Any, codes: list[str]) -> PriceStrip:
    """按批取现价，返回 `code -> 读数字段`；**任何异常都不向上抛**。

    自选是「本机想看什么」，报价是「上游此刻有什么」——上游 502 / 限流不该让
    `GET /api/watchlist` 变成 500，所以这里逐批兜住，把原因写进那几条的 `price_reason`。
    """
    wanted = [str(code).strip() for code in codes if str(code).strip()]
    if not wanted:
        return PriceStrip(prices={}, status="missing", source_detail=NO_CODES)

    prices: dict[str, dict[str, Any]] = {
        code: {
            "available": False,
            "name": None,
            "market": None,
            "latest_price": None,
            "change": None,
            "change_pct": None,
            "prev_close": None,
            "price_as_of": None,
            "price_status": PRICE_UNAVAILABLE,
            "price_reason": "这批自选没有取到报价",
            **_metrics(None, code=code),
        }
        for code in wanted
    }
    detail = ""
    ok_codes: set[str] = set()

    for chunk in _chunks(wanted, PRICE_CHUNK):
        try:
            outcome = registry.fetch_live(
                DATASET, params={"codes": ",".join(chunk), "max_codes": len(chunk)}
            )
        except Exception as exc:  # noqa: BLE001 - 上游任何失败都只是「这会儿取不到」
            reason = str(getattr(exc, "message", None) or exc) or "取数失败"
            limited = getattr(exc, "rate_limited", False)
            for code in chunk:
                prices[code]["price_reason"] = (
                    f"上游限流，这会儿取不到：{reason}"
                    if limited
                    else f"{DATASET} 取数失败：{reason}"
                )
                prices[code].update(_metrics(None, code=code))
            continue

        detail = detail or str(getattr(outcome.envelope, "source_detail", "") or "")
        rows = _rows(outcome.envelope)
        by_code: dict[str, dict[str, Any]] = {}
        for row in rows:
            code = str(row.get("code") or "").strip()
            if code:
                by_code.setdefault(code, row)

        for code in chunk:
            quoted = by_code.get(code)
            if quoted is None:
                prices[code]["price_reason"] = f"{DATASET} 没有返回 {code} 的报价"
                prices[code].update(_metrics(None, code=code))
                continue
            price = quoted.get("price")
            if price is None or float(price) <= 0:
                prices[code]["price_reason"] = f"{code} 的现价为空（停牌或上游没给）"
                prices[code].update(_metrics(quoted, code=code))
                continue
            prices[code] = {
                "available": True,
                "name": quoted.get("name"),
                "market": quoted.get("market"),
                "latest_price": float(price),
                "change": quoted.get("change"),
                "change_pct": quoted.get("change_pct"),
                "prev_close": quoted.get("prev_close"),
                "price_as_of": iso_timestamp(str(quoted.get("timestamp") or "")),
                "price_status": PRICE_OK,
                "price_reason": None,
                **_metrics(quoted, code=code),
            }
            ok_codes.add(code)

    missing = len(ok_codes) == 0
    status: Status = "missing" if missing else ("ok" if len(ok_codes) == len(wanted) else "partial")
    return PriceStrip(prices=prices, status=status, source_detail=detail)


def _rows(envelope: Any) -> list[dict[str, Any]]:
    payload = getattr(envelope, "payload", {}) or {}
    records = payload.get("records") if isinstance(payload, dict) else None
    return [row for row in records or [] if isinstance(row, dict)]


__all__ = [
    "DATASET",
    "PB_KEYS",
    "PE_TTM_KEYS",
    "PRICE_CHUNK",
    "PRICE_OK",
    "PRICE_UNAVAILABLE",
    "PriceStrip",
    "QUOTE_TOOL",
    "TURNOVER_PCT_KEYS",
    "fetch_prices",
]
