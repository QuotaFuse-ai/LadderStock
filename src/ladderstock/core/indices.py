"""首页指数条：A 股四个指数的实时报价投影（QUOT-380，事实来源 QUOT-379 盘点 A-1）。

**白名单写死在本层四条**，不向 peer 动态要清单：实测 `index_list` 是按涨幅排序的
100 条榜，不含这四个指数，还混进老三板个股（`400224 苏阳光5`）。榜是日单一的，
照它建注册表等于首页那四格「今天有、明天没」。

三个实测出来的坑（2026-11-03 对真实 peer `index_quote` 复测），判据逐条对着它们写：

1. **入参必须带 `sh`/`sz` 前缀，且只允许前缀形式**。裸 `000001` 不报错——它回
   **平安银行**（QUOT-379 当时记录的是 `isError`，现在已变成静默回错票，只会更危险）；
   点号写法 `000001.SH` 同样回平安银行。入参由 `INDEX_WHITELIST` 拼，外部传不进来。
2. **绝不能用 `spot_quote` / `tencent_quote_batch` 取指数**：批量工具剥掉前缀后按 6 位
   代码回键，指数与个股撞车（实测四回三、`000001` 成了平安银行且 `failed=0`）。
   指数只走 `index_quote` 这一个工具。
3. **返回行的 `code` 不带前缀**，所以匹配按 `(market, code)`，不按 `code` 建字典。
   `is_index` 一并核验：它是必要条件之一，但**不是充分条件**（平安银行那条也标
   `is_index: true`），单看它挡不住坑 1。

数字照 peer 原样投影，不做二次换算、不补 0（DEC-028）；取不到就是
`available=false` + 原因——不自采（DEC-006 闸门 4）、不换供应商、不猜价。
非交易时段 peer 回上一交易日收盘，`as_of` 如实带出，配 `FOOTNOTE` 说明是快照。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from ladderstock.core.ladder import as_float

#: 取数唯一入口：`DATASET_CATALOG` 里登记的 `index_quote`（peer `tickhub`，同工具名）
DATASET = "index_quote"

#: 首页脚注（`HOME-V2-DATA-SURFACE.md` A-1 的口径：这不是逐笔实时）
FOOTNOTE = "快照，不是逐笔实时"

#: 整批取数失败时写进 `reason` 的前缀
FETCH_FAILED = "index_quote 取数失败"
NO_ROWS = "index_quote 没有返回任何匹配的行"

Status = Literal["ok", "partial", "missing"]


@dataclass(frozen=True)
class IndexSpec:
    """一格指数。`code` 是给 UI 的稳定键（带前缀），`market`/`symbol` 是 peer 行里的形状。"""

    code: str
    market: str
    symbol: str
    name: str


#: 首页四格，只留 A 股（恒指 / 恒生科技无源；道指 / 纳指本仓没有 `tickhub_us` 取数适配器）
INDEX_WHITELIST: tuple[IndexSpec, ...] = (
    IndexSpec("sh000001", "SH", "000001", "上证指数"),
    IndexSpec("sh000300", "SH", "000300", "沪深300"),
    IndexSpec("sz399001", "SZ", "399001", "深证成指"),
    IndexSpec("sz399006", "SZ", "399006", "创业板指"),
)

#: 每格对外的数值字段：peer 行里的键 → 载荷里的键，一一对应，不做任何加工
_VALUE_KEYS: tuple[str, ...] = ("price", "change", "change_pct", "prev_close")

#: peer 行情时间在载荷里不透出（对外只有 `as_of` 一个口径），投影前先摘掉
_INTERNAL_KEYS: frozenset[str] = frozenset({"row_timestamp"})


@dataclass(frozen=True)
class IndexStrip:
    """一次投影的结果，交给接口层套信封（`payload` 是上屏的四格）。"""

    payload: dict[str, Any]
    status: Status
    day: date | None
    source_detail: str


def fetch_indices(registry: Any) -> IndexStrip:
    """一次请求取回四格。peer 不可用 / 没有匹配行 → 那一格 `available=false` + 原因。"""
    params = {"codes": ",".join(spec.code for spec in INDEX_WHITELIST)}
    try:
        outcome = registry.fetch_live(DATASET, params=params)
    except Exception as exc:  # noqa: BLE001 - 首页不能因为一个上游就 500
        reason = f"{FETCH_FAILED}：{getattr(exc, 'message', None) or exc}"
        cells = _cells(reason=reason)
        return IndexStrip(
            payload=_payload(cells, available=False, reason=reason),
            status="missing",
            day=None,
            source_detail="",
        )

    envelope = outcome.envelope
    rows = _rows(envelope)
    # 按 (market, code) 建键：只按 `code` 建字典会把 sh000001 与 sz000001 撞成一格（坑 3）
    matched: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        market = str(row.get("market") or "").strip().upper()
        symbol = str(row.get("code") or "").strip()
        if market and symbol:
            matched.setdefault((market, symbol), row)

    cells = _cells(by_market_code=matched)
    filled = [cell for cell in cells if cell["available"]]
    day = max(
        (parsed for cell in filled if (parsed := _row_day(cell.get("row_timestamp")))),
        default=None,
    )
    status: Status
    if not filled:
        status = "missing"
    elif len(filled) == len(cells):
        status = "ok"
    else:
        status = "partial"
    miss_reason: str | None = None if filled else _miss_detail(rows)
    return IndexStrip(
        payload=_payload(cells, available=bool(filled), reason=miss_reason),
        status=status,
        day=day,
        source_detail=str(getattr(envelope, "source_detail", "") or ""),
    )


def _cells(
    by_market_code: Mapping[tuple[str, str], dict[str, Any]] | None = None,
    *,
    reason: str | None = None,
) -> list[dict[str, Any]]:
    """固定顺序的四格：匹配到就照抄数值，匹配不到就 `null` + 原因（顺序就是首页顺序）。"""
    cells: list[dict[str, Any]] = []
    for spec in INDEX_WHITELIST:
        row = (by_market_code or {}).get((spec.market, spec.symbol))
        if row is None or not row.get("is_index"):
            cells.append(
                {
                    "code": spec.code,
                    "market": spec.market,
                    "name": spec.name,
                    "available": False,
                    "price": None,
                    "change": None,
                    "change_pct": None,
                    "prev_close": None,
                    "as_of": None,
                    "reason": reason or _cell_miss_detail(spec),
                }
            )
            continue
        cell: dict[str, Any] = {
            "code": spec.code,
            "market": spec.market,
            "name": spec.name,
            "available": True,
            "reason": None,
            "as_of": iso_timestamp(str(row.get("timestamp") or "")),
            "row_timestamp": str(row.get("timestamp") or ""),
        }
        # 照抄，不换算：peer 给 `price` 我们就显 `price`，缺一个字段就是 `null`
        for key in _VALUE_KEYS:
            cell[key] = as_float(row.get(key))
        cells.append(cell)
    return cells


def _payload(cells: list[dict[str, Any]], *, available: bool, reason: str | None) -> dict[str, Any]:
    """摘掉内部字段后的载荷——对外每一格只有契约里的字段。"""
    return {
        "available": available,
        "reason": reason,
        "footnote": FOOTNOTE,
        "price_source": DATASET,
        "datasets": [DATASET],
        "cells": [
            {key: value for key, value in cell.items() if key not in _INTERNAL_KEYS}
            for cell in cells
        ],
    }


def _cell_miss_detail(spec: IndexSpec) -> str:
    return (
        f"index_quote 未返回 ({spec.market}, {spec.symbol}) 的行"
        f"（入参 {spec.code} 被上游静默丢弃，不补值）"
    )


def _miss_detail(rows: list[dict[str, Any]]) -> str:
    seen = ", ".join(
        f"({row.get('market')}, {row.get('code')})" for row in rows[:4]
    )
    return f"{NO_ROWS}（返回 {len(rows)} 行：{seen or '无'}）"


def _row_day(raw: Any) -> date | None:
    text = iso_timestamp(str(raw or ""))
    return date.fromisoformat(text[:10]) if text else None


def iso_timestamp(text: str) -> str | None:
    """peer 的行情时间 `YYYYMMDDHHMMSS` → `YYYY-MM-DDTHH:MM:SS`（同一时刻的另一种写法）。

    不补时区：上游没给，猜一个就是造数。解析不了就是 `null`，数值字段照旧保留。
    """
    digits = "".join(char for char in (text or "") if char.isdigit())
    if len(digits) != 14:
        return None
    return (
        f"{digits[0:4]}-{digits[4:6]}-{digits[6:8]}"
        f"T{digits[8:10]}:{digits[10:12]}:{digits[12:14]}"
    )


def _rows(envelope: Any) -> list[dict[str, Any]]:
    payload = getattr(envelope, "payload", {}) or {}
    records = payload.get("records")
    if not isinstance(records, list):
        return []
    return [dict(row) for row in records if isinstance(row, Mapping)]


__all__ = [
    "DATASET",
    "FOOTNOTE",
    "INDEX_WHITELIST",
    "iso_timestamp",
    "IndexSpec",
    "IndexStrip",
    "fetch_indices",
]
