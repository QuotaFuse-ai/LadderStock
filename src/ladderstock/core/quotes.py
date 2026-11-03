"""现价查询的唯一口径：从本机 `daily_k` 缓存取「代码 → 最新价」+ 溯源状态。

为什么单独一个模块：`ldst position list` 与 `GET /api/positions`（QUOT-279）要的是同一个数、
同一张字段容忍表（`PRICE_KEYS`）、同一条「取不到就是 `null`」的口径。这个函数原先长在
`cli/position.py` 里，接口层要用就只能反过来 import CLI——那会把 `typer.Exit` 带进请求线程，
也让隐私面的白名单检查（`scripts/check_privacy.py` 第 3 组）多一条「经 CLI 传递引用隐私库」
的旁路。现在 CLI 与 web 都 import 这里，口径只有一份。

只读、不联网：缓存里没有就是没有，`reason` 说清去补哪条命令，不拿成本价冒充现价。
`daily_change_map()` 是同一张表、同一条口径上的第二个问题：「今天比上一个交易日涨了多少」
（持仓当日盈亏，QUOT-380）——它同样只读缓存，所以加这个字段**没有**改变持仓现价的水位口径。
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from ladderstock.core.ladder import CODE_KEYS, as_float, dataset_snapshot, pick
from ladderstock.data import archive
from ladderstock.errors import EXIT_USAGE, LadderStockError

#: `daily_k` 里「收盘价」的候选键（与既有指标口径同一张容差表）
PRICE_KEYS: tuple[str, ...] = ("close", "close_price", "latest_price", "price", "收盘")

PRICE_DATASET = "daily_k"


def price_map(
    codes: list[str], day: str | None, *, root: Path | None = None
) -> tuple[dict[str, float], dict[str, Any]]:
    """从本机 `daily_k` 缓存取最新价（`code -> price`）与溯源状态。

    缺省读缓存里可用的最后一天；缓存没有就是「没有」——`status["reason"]` 说清原因，
    返回空映射，让每条持仓的 `latest_price` 如实为 `null`。

    `root` 默认本机数据目录；接口层把它钉成和页面同一个数据根。
    """
    status: dict[str, Any] = {
        "dataset": PRICE_DATASET,
        "date": None,
        "backend": None,
        "reason": None,
    }
    if not codes:
        status["reason"] = "本机没有持仓，无需查价"
        return {}, status

    if day:
        wanted = _parse_day(day)
    else:
        cached = archive.latest_day(PRICE_DATASET, root=root)
        if cached is None:
            status["reason"] = (
                f"本机缓存里没有 {PRICE_DATASET} 数据（先 ldst sync --dataset {PRICE_DATASET}）"
            )
            return {}, status
        wanted = cached

    records, backend = dataset_snapshot(PRICE_DATASET, wanted, root)
    status["date"] = wanted.isoformat()
    status["backend"] = backend
    if not records:
        status["reason"] = f"{PRICE_DATASET} 在 {wanted.isoformat()} 没有缓存记录"
        return {}, status

    wanted_set = {code.upper() for code in codes}
    prices: dict[str, float] = {}
    for record in records:
        code = str(pick(record, CODE_KEYS) or "").strip().upper()
        if code not in wanted_set or code in prices:
            continue
        price = as_float(pick(record, PRICE_KEYS))
        if price is not None and price > 0:
            prices[code] = price
    if not prices:
        status["reason"] = f"{PRICE_DATASET} 缓存里没有这些代码的收盘价字段"
    return prices, status


#: 当日盈亏回看「上一条」的天数上限：跨过周末与长假够用，不做无界回看
#: （与 `web/server.py:_recent_session_days` 同一套「有分区才算交易日」的判据）
PREV_LOOKBACK_DAYS = 30

#: 缓存里缺 T 日行时的状态词（停牌 / 未同步）：**不退回到更早交易日**假装是当日盈亏
STATUS_MISSING = "missing"
STATUS_OK = "ok"


def daily_change_map(
    codes: list[str], day: str | None, *, root: Path | None = None
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """持仓当日盈亏的唯一算法：`close(T) − close(T−1)`（QUOT-380 拍板口径 4）。

    **只读本机 `daily_k` 缓存，不联网**——与 `price_map()` 同一条口径，
    所以持仓现价的新鲜度没有因为这个字段改变（`price_source` 仍是 `daily_k`）。
    口径变更走的是「改持仓现价来源」那条路，本改版不做（DEC-028 不许拿成本价顶替）。

    每只票回 `{"status": ok|missing, "close": …, "prev_close": …, "change": …|None}`：

    - **T 日没有行** → `missing`（停牌，或这批代码没同步）。这时**绝不**退回到更早的
      交易日去凑一个「看起来像当日盈亏」的数——那是造数；
    - **T 日有行、往前找不到任何一行** → `missing`（没有上一条，涨跌算不出来）；
    - `change` 是 `close − prev_close`，一条持仓的当日盈亏 = `change × qty`，
      由调用方（`journal.position.compute_metrics`）乘股数，这里只出每股的差值。

    T 取 `price_map()` 用的同一天（调用方把 `status["date"]` 传进来），
    两个出口才算的是同一个交易日。
    """
    status: dict[str, Any] = {
        "dataset": PRICE_DATASET,
        "date": None,
        "prev_date": None,
        "backend": None,
        "reason": None,
    }
    if not codes:
        status["reason"] = "本机没有持仓，无需算当日盈亏"
        return {}, status

    wanted = _parse_day(day) if day else archive.latest_day(PRICE_DATASET, root=root)
    if wanted is None:
        status["reason"] = (
            f"本机缓存里没有 {PRICE_DATASET} 数据（先 ldst sync --dataset {PRICE_DATASET}）"
        )
        return {}, status
    status["date"] = wanted.isoformat()

    today_records, backend = dataset_snapshot(PRICE_DATASET, wanted, root=root)
    status["backend"] = backend
    closes = _close_by_code(today_records, codes)
    prev_day = _previous_cached_day(wanted, root=root)
    if prev_day is not None:
        status["prev_date"] = prev_day.isoformat()
        prev_records, _ = dataset_snapshot(PRICE_DATASET, prev_day, root=root)
        prev_closes = _close_by_code(prev_records, codes)
    else:
        prev_closes = {}
        status["reason"] = (
            f"{PRICE_DATASET} 缓存里找不到 {wanted.isoformat()} 之前的交易日，"
            "昨收无从取值"
        )

    result: dict[str, dict[str, Any]] = {}
    for code in (str(item).strip().upper() for item in codes):
        if not code:
            continue
        close = closes.get(code)
        prev_close = prev_closes.get(code)
        if close is None:
            result[code] = {
                "status": STATUS_MISSING,
                "close": None,
                "prev_close": prev_close,
                "change": None,
                "reason": (
                    f"{PRICE_DATASET} 在 {wanted.isoformat()} 没有 {code} 的行（停牌或未同步）"
                ),
            }
            continue
        if prev_close is None:
            result[code] = {
                "status": STATUS_MISSING,
                "close": close,
                "prev_close": None,
                "change": None,
                "reason": (
                    f"{PRICE_DATASET} 缓存里没有 {code} 的上一条记录，算不出 close(T) − close(T−1)"
                ),
            }
            continue
        result[code] = {
            "status": STATUS_OK,
            "close": close,
            "prev_close": prev_close,
            "change": round(close - prev_close, 6),
            "reason": None,
        }
    return result, status


def _close_by_code(records: list[dict[str, Any]], codes: list[str]) -> dict[str, float]:
    wanted = {str(code).strip().upper() for code in codes}
    closes: dict[str, float] = {}
    for record in records:
        code = str(pick(record, CODE_KEYS) or "").strip().upper()
        if code not in wanted or code in closes:
            continue
        value = as_float(pick(record, PRICE_KEYS))
        if value is not None and value > 0:
            closes[code] = value
    return closes


def _previous_cached_day(day: date, *, root: Path | None = None) -> date | None:
    """`day` 之前最近一个**本机有分区**的交易日；找不到就是 `None`（不猜节日）。

    判据与 `web/server.py:_recent_session_days` 同一条：周末直接跳过，法定节假日
    本机本来就没有那天的缓存，自然不进候选——与其猜节日，不如只报数得出来的日子。
    """
    cursor = day - timedelta(days=1)
    for _ in range(PREV_LOOKBACK_DAYS):
        if cursor.weekday() < 5 and archive.has_partition(PRICE_DATASET, cursor, root=root):
            return cursor
        cursor -= timedelta(days=1)
    return None


def _parse_day(value: str) -> date:
    """日期非法是用法错误（exit 2）：CLI 与接口层各自决定怎么把 2 变成人话。"""
    text = str(value or "").strip()
    try:
        parsed = date.fromisoformat(text)
    except ValueError:
        raise LadderStockError(f"日期需要 YYYY-MM-DD，收到 {value!r}", EXIT_USAGE) from None
    if parsed.isoformat() != text:
        raise LadderStockError(f"日期需要 YYYY-MM-DD，收到 {value!r}", EXIT_USAGE)
    return parsed


__all__ = ["PRICE_DATASET", "PRICE_KEYS", "daily_change_map", "price_map"]
