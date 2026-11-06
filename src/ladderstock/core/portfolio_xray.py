"""持仓体检（X 光）：权重、集中度、行业暴露、相关系数与缺口（QUOT-437）。

口径（与参考实现 `xrayView.ts` 同一套，只保留本仓能算的部分）：

1. **权重按现价市值算**，`weight = 市值 / 已取价持仓的市值合计`（小数，`0.125` = 12.5%）。
   缺价的持仓**不进分母**，列进 `gaps`，`weight` 为 `None`——不按 0 计入，也不拿成本价顶替。
2. **行业只用用户标注**：`positions.industry` 为空的一律归「未标注」，不按代码前缀猜，
   也不读任何「个股 → 行业」映射（本仓没有这份数据集）。
3. **相关系数只用本机 `daily_k`**：两只都至少要有 `MIN_CORR_DAYS` 个共同交易日，
   否则该项为 `None` + 原因；不足两只持仓时没有可比的。
4. 指标只描述数字：不打分、不排序好坏、不给建议。

本模块是纯函数：不读文件、不联网、不碰数据库。读盘入口在 `web/radar.py`
（现价 `price_map()`、日线 `load_close_series()`、行业 `journal.position.list_positions()`）。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from ladderstock.core.portfolio import EPS, HoldingRow, round4

#: 未标注行业归入的桶名（与 `journal/position.py` 的行业分布同一个词）
UNLABELED = "未标注"

#: 相关系数至少需要的共同交易日数（收益序列长度）
MIN_CORR_DAYS = 20

#: 输出口径说明：页面原样展示，不另写一份
NOTES: tuple[str, ...] = (
    "权重按现价市值算；缺价的持仓不进分母，单独列在缺口里",
    "行业只用你在持仓记录里标注的行业；没标注的归「未标注」，不按代码猜",
    f"相关系数只用本机 daily_k 日线，两只都要有至少 {MIN_CORR_DAYS} 个共同交易日",
)

#: 相关系数算不出来时的兜底原因
NO_PAIR_REASON = f"没有两只同时具备至少 {MIN_CORR_DAYS} 个共同交易日的持仓"


def _weight(value: float, total: float) -> float | None:
    if total <= EPS:
        return None
    return round4(value / total)


def _label_of(symbol: str, industries: Mapping[str, str]) -> str:
    """行业标注：去空白后为空就归「未标注」；不做任何代码 → 行业的推断。"""
    return str(industries.get(symbol) or "").strip() or UNLABELED


def _returns(series: Mapping[str, float]) -> dict[str, float]:
    """收盘价序列 → 日收益（小数）。缺前收 / 价格非正的日期直接跳过，不补 0。"""
    points: list[tuple[str, float]] = []
    for raw_day, raw_price in series.items():
        try:
            price = float(raw_price)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(price) or price <= 0:
            continue
        points.append((str(raw_day)[:10], price))
    points.sort()
    out: dict[str, float] = {}
    for (_, prev), (day, price) in zip(points, points[1:]):
        if prev > 0:
            out[day] = price / prev - 1.0
    return out


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    syy = sum((y - mean_y) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    value = sxy / math.sqrt(sxx * syy)
    return max(-1.0, min(1.0, value))


def _correlation_block(
    symbols: Sequence[str],
    closes: Mapping[str, Mapping[str, float]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """两两相关系数 + 汇总指标；返回 (每对结果, metrics.corr 块)。"""
    returns = {symbol: _returns(closes.get(symbol, {})) for symbol in symbols}
    pairs: list[dict[str, Any]] = []
    first: str | None = None
    last: str | None = None
    max_samples = 0
    for left_index, left in enumerate(symbols):
        for right in symbols[left_index + 1 :]:
            left_returns = returns.get(left) or {}
            right_returns = returns.get(right) or {}
            common = sorted(set(left_returns) & set(right_returns))
            if len(common) < MIN_CORR_DAYS:
                continue
            value = _pearson(
                [left_returns[day] for day in common],
                [right_returns[day] for day in common],
            )
            if value is None:
                continue
            pairs.append(
                {
                    "pair": [left, right],
                    "corr": round4(value),
                    "samples": len(common),
                }
            )
            first = common[0] if first is None else min(first, common[0])
            last = common[-1] if last is None else max(last, common[-1])
            max_samples = max(max_samples, len(common))

    if pairs:
        strongest = max(pairs, key=lambda row: row["corr"])
        average = round4(sum(abs(row["corr"]) for row in pairs) / len(pairs))
        reason = None
    else:
        strongest = None
        average = None
        reason = NO_PAIR_REASON

    block = {
        "available": strongest is not None,
        "reason": reason,
        "max_pair": strongest,
        "avg_abs_corr": average,
        "pairs": pairs,
        "pairs_count": len(pairs),
        "min_days": MIN_CORR_DAYS,
        "window": (
            {"first": first, "last": last, "days": max_samples}
            if pairs
            else {"first": None, "last": None, "days": None}
        ),
    }
    return pairs, block


def build_view(
    holdings: Sequence[HoldingRow],
    prices: Mapping[str, float],
    industries: Mapping[str, str],
    closes: Mapping[str, Mapping[str, float]],
    *,
    as_of: str | None,
) -> dict[str, Any]:
    """接口层入口：持仓 + 现价 + 行业标注 + 日线 → X 光载荷（平面 JSON）。"""
    items: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    priced: list[dict[str, Any]] = []

    for row in holdings:
        symbol = str(row.symbol).strip().upper()
        price = prices.get(symbol)
        market_value = round4(price * row.shares) if price is not None else None
        item = {
            "symbol": symbol,
            "name": row.name,
            "market_value": market_value,
            "weight": None,
        }
        items.append(item)
        if market_value is None:
            gaps.append(
                {
                    "symbol": symbol,
                    "name": row.name,
                    "affects": "weight",
                    "reason": (
                        "本机 daily_k 缓存里没有这只的收盘价（停牌或未同步），"
                        "没算进权重分母"
                    ),
                }
            )
        else:
            priced.append(item)

    total_value = round4(sum(float(item["market_value"]) for item in priced)) if priced else None
    for item in priced:
        item["weight"] = _weight(float(item["market_value"]), total_value or 0.0)
    items.sort(key=lambda item: (-(item["market_value"] or 0.0), item["symbol"]))
    weighted = sorted(
        (item for item in priced if item["weight"] is not None),
        key=lambda item: (-float(item["weight"]), item["symbol"]),
    )
    weights = [float(item["weight"]) for item in weighted]

    buckets: dict[str, list[dict[str, Any]]] = {}
    for item in priced:
        label = _label_of(item["symbol"], industries)
        buckets.setdefault(label, []).append(item)
    industry_buckets = [
        {
            "industry": label,
            "labeled": label != UNLABELED,
            "market_value": round4(sum(float(item["market_value"]) for item in rows)),
            "weight": _weight(
                sum(float(item["market_value"]) for item in rows), total_value or 0.0
            ),
            "symbols": [item["symbol"] for item in sorted(rows, key=lambda r: r["symbol"])],
        }
        for label, rows in buckets.items()
    ]
    industry_buckets.sort(key=lambda row: (-(row["weight"] or 0.0), row["industry"]))

    correlation_targets = [str(item["symbol"]) for item in priced]
    if len(correlation_targets) >= 2:
        _, corr = _correlation_block(correlation_targets, closes)
        for symbol in correlation_targets:
            if len(_returns(closes.get(symbol, {}))) < MIN_CORR_DAYS:
                gaps.append(
                    {
                        "symbol": symbol,
                        "name": next(
                            (row.name for row in holdings if row.symbol == symbol), ""
                        ),
                        "affects": "correlation",
                        "reason": (
                            f"本地日线不足 {MIN_CORR_DAYS} 个交易日，算不出相关系数；"
                            "权重不受影响"
                        ),
                    }
                )
    else:
        _, corr = _correlation_block([], closes)
        corr["reason"] = (
            "持仓少于 2 只，没有可比的相关系数"
            if priced
            else "还没有取到现价的持仓，算不出相关系数"
        )

    return {
        "as_of": as_of,
        "total_value": total_value,
        "items": items,
        "top1": weights[0] if weights else None,
        "top3": round4(sum(weights[:3])) if weights else None,
        "top5": round4(sum(weights[:5])) if weights else None,
        "industry_buckets": industry_buckets,
        "industry_labeled_count": sum(
            1 for item in priced if _label_of(item["symbol"], industries) != UNLABELED
        ),
        "industry_unlabeled_count": sum(
            1 for item in priced if _label_of(item["symbol"], industries) == UNLABELED
        ),
        "metrics": {"corr": corr},
        "gaps": gaps,
        "notes": list(NOTES),
    }


__all__ = [
    "MIN_CORR_DAYS",
    "NOTES",
    "NO_PAIR_REASON",
    "UNLABELED",
    "build_view",
]
