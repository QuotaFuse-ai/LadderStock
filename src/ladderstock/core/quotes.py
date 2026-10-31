"""现价查询的唯一口径：从本机 `daily_k` 缓存取「代码 → 最新价」+ 溯源状态。

为什么单独一个模块：`ldst position list` 与 `GET /api/positions`（QUOT-279）要的是同一个数、
同一张字段容忍表（`PRICE_KEYS`）、同一条「取不到就是 `null`」的口径。这个函数原先长在
`cli/position.py` 里，接口层要用就只能反过来 import CLI——那会把 `typer.Exit` 带进请求线程，
也让隐私面的白名单检查（`scripts/check_privacy.py` 第 3 组）多一条「经 CLI 传递引用隐私库」
的旁路。现在 CLI 与 web 都 import 这里，口径只有一份。

只读、不联网：缓存里没有就是没有，`reason` 说清去补哪条命令，不拿成本价冒充现价。
"""

from __future__ import annotations

from datetime import date
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


__all__ = ["PRICE_DATASET", "PRICE_KEYS", "price_map"]
