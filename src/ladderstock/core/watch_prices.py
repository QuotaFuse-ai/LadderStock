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

新鲜度（QUOT-478）：`PriceStrip` 多带一个 `cache_replay`——这批自选的取数路径上有没有
出现**明确的缓存回放证据**（上游 `meta.cache_hit`、我方 registry TTL 命中、peer 整体
不可用时的本地回放，三条路都会把信封 `freshness` 写成 `cached`）。`judge_freshness()`
按时点形状判 §5.1 三档，**判不出就回 `None`**：前端保留了「未知」兜底，后端不替用户
猜一个 `realtime`（DEC-052 平面响应也一样，表里出 `null` 不留空档）。
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any, Literal

from ladderstock.core.indices import iso_timestamp
from ladderstock.core.intraday import (
    DEFAULT_TIMEZONE,
    TRADING_WINDOWS,
    local_moment,
)
from ladderstock.core.ladder import as_float
from ladderstock.core.models import Freshness

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
    #: QUOT-478：这批里**有票取到了价**的批次中，是否出现过明确的缓存回放路径
    #: （信封 `freshness=="cached"`：上游 cache_hit / 我方 TTL 命中 / 本地回放）。
    #: 任一批坐实即算——混着来的批次宁可把整条读成「非实时」，不赌多数批清白。
    cache_replay: bool = False

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
    cache_replay = False

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
        chunk_priced = False
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
            chunk_priced = True

        # QUOT-478：这一批确实贡献了读数、且取数路径被明确标记为缓存回放，
        # 缓存证据才算坐实（取了个空批次不算：页面上没有一条价来自那条路）。
        if chunk_priced and getattr(outcome.envelope, "freshness", None) == "cached":
            cache_replay = True

    missing = len(ok_codes) == 0
    status: Status = "missing" if missing else ("ok" if len(ok_codes) == len(wanted) else "partial")
    return PriceStrip(
        prices=prices, status=status, source_detail=detail, cache_replay=cache_replay
    )


def _rows(envelope: Any) -> list[dict[str, Any]]:
    payload = getattr(envelope, "payload", {}) or {}
    records = payload.get("records") if isinstance(payload, dict) else None
    return [row for row in records or [] if isinstance(row, dict)]


#: 「现在」与数据时点之间允许的时钟偏移：peer 时钟略快不构成未来数据
CLOCK_SKEW_TOLERANCE = timedelta(minutes=5)


def _session_window(moment: datetime) -> tuple[time, time] | None:
    """`moment` 此刻所处的连续竞价窗口（与 `intraday.in_trading_session` 同一张表）。"""
    clock = moment.time().replace(microsecond=0)
    for window in TRADING_WINDOWS:
        if window[0] <= clock < window[1]:
            return window
    return None


def judge_freshness(
    as_of: str | None,
    *,
    priced: bool,
    cache_replay: bool = False,
    now: datetime | None = None,
) -> Freshness | None:
    """自选现价的 §5.1 三档判定（QUOT-478）：以 `price_as_of` 与取数路径为据，
    不按「请求时刻」想当然；判不出就回 `None`，不给默认值 `realtime`。

    - **盘中**（Asia/Shanghai 落在连续竞价时段）且时点**落在本次交易会话内**
      （同一自然日、不早于本会话开盘、且不在未来）→ `realtime`；
    - **非交易时段**拿到的是**过去某日的读数**（前一交易日收盘，或今日 15:00 后）
      → `archive`；
    - **明确走缓存回放 / 沿用了旧快照**（`cache_replay`）且时点形状判不出实时/存档
      → `cached`；
    - 其余（没有读数、时点解析不了、盘中但时点对不上、未来时点）→ `None`，
      前端据此出「未知」——这是特意保留的诚实出口。

    两处顺序是拍过板的：

    1. 盘中的缓存证据先于 `realtime`：会话内拿到回放就是「非实时」，不因为时点
       落在本会话内就标实时；
    2. 非交易时段的过去读数判 `archive` **不被缓存证据覆盖**：周末页面上该读的是
       「存档」（数据是周五收盘），缓存只是递送路径。
    """
    if not priced or not as_of:
        return None
    try:
        stamp = datetime.fromisoformat(as_of)
    except ValueError:
        return None
    moment = local_moment(now, DEFAULT_TIMEZONE).replace(tzinfo=None)
    if stamp > moment + CLOCK_SKEW_TOLERANCE:
        # 时点在「未来」且超出时钟偏移容忍，判不出它是什么
        return None
    window = _session_window(moment)
    # A 股周六 / 周日不开市（调休补班日同样不开），周末一律按非交易时段读；
    # 工作日的法定节假日没有交易日历可查，时点对不上就落「判不出」，不猜。
    if window is not None and moment.weekday() < 5:
        if cache_replay:
            return "cached"
        if stamp.date() == moment.date() and stamp.time() >= window[0]:
            return "realtime"
        # 盘中但时点属于上一会话（停牌股）、或早于本会话开盘：判不出，不猜
        return None
    if stamp.date() < moment.date():
        # 过去某日的读数（前一交易日收盘 / 假期前的收盘，无需交易日历，日期自己会说）
        return "archive"
    if stamp.date() == moment.date() and moment.time() >= TRADING_WINDOWS[-1][1]:
        # 同日的收盘后读数（周五 16:14 的价格，周六 / 周日 / 平日晚 22 点都读这）
        return "archive"
    # 同日、尚未收盘、又不在连续竞价时段（集合竞价前 / 午休）：时点形状判不出档位，
    # 明确的缓存证据给 cached，否则诚实回 None。
    return "cached" if cache_replay else None


__all__ = [
    "CLOCK_SKEW_TOLERANCE",
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
    "judge_freshness",
]
