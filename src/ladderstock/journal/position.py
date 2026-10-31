"""我的持仓：本机隐私库读写 + 本地计算（DEC-006 隐私边界）。

字段（QUOT-145 §要求-4）：`code` / `cost`（成本价）/ `qty`（数量）/ `entry_date`（建仓日）/
`note`（备注），另有可选 `industry`（行业标注，供行业分布统计）。

三条硬约束：

1. **只依赖 `data/sqlite_store` 与标准库**：本模块不 import 适配器、MCP、AI，不产生任何网络请求；
2. **不出本机**：返回值只交给 CLI `emit()` 打到本机终端，不进 `LadderEnvelope`、
   不进 MCP 工具清单、不进 AI 上下文（`docs/MCP_TOOLS.md` §2、`scripts/check_privacy.py` 第 3 组）；
3. **不自动导出**：只有 `ldst position list --export <path>` 显式指定才写文件。

`industry` 为什么由用户标注：本机缓存只有**板块层**数据集（`industry_board` / `concept_board`），
没有「个股 → 行业」的成分映射（`config/sources.yaml` 里未登记），
所以行业分布只统计本机标注；一条都没标注时如实给 `available=false` + 原因，
**绝不拿题材或概念去猜行业**。

价格只从调用方注入（`cli/position.py` 读本机 `daily_k` 缓存），取不到就是 `null`——
本模块不读行情，也就不会把「行情可用性问题」和「隐私库可用性问题」混在一个出口里。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from ladderstock.data import sqlite_store
from ladderstock.errors import EXIT_NOT_FOUND, EXIT_USAGE, LadderStockError

#: 本模块输出的记录字段（隐私测试用它断言不外溢）
POSITION_FIELDS: tuple[str, ...] = (
    "id",
    "code",
    "cost",
    "qty",
    "entry_date",
    "industry",
    "note",
    "updated_at",
)

#: 没有标注行业的持仓归进这个桶（只在已有标注时出现，避免凭空造行业）
UNLABELED_INDUSTRY = "未标注"

_COLUMNS = ", ".join(POSITION_FIELDS)
_SELECT = f"SELECT {_COLUMNS} FROM positions"

#: 可写字段 → 校验用的中文名（`update` 用它判断「至少改了一个」）
EDITABLE: tuple[str, ...] = ("cost", "qty", "entry_date", "industry", "note")


def _clean_code(code: str) -> str:
    cleaned = str(code or "").strip().upper()
    if not cleaned:
        raise LadderStockError("--code 不能为空", EXIT_USAGE)
    return cleaned


def _positive(value: float, name: str) -> float:
    if value is None or float(value) <= 0:
        raise LadderStockError(f"--{name} 必须大于 0，收到 {value!r}", EXIT_USAGE)
    return float(value)


def add_position(
    code: str,
    cost: float,
    qty: float,
    *,
    entry_date: str | date | None = None,
    note: str = "",
    industry: str = "",
    upsert: bool = False,
    root: Path | None = None,
) -> dict[str, Any]:
    """新增一条持仓，返回落库后的行（只写本机 `data/journal.sqlite`）。

    `upsert=True` 时按 `code` 覆盖同一条记录（`POST /api/positions` 用）；缺省 `False`
    保持 CLI `ldst position add` 的口径——重复代码是用法错误。缺 `entry_date` 时新增
    取今天、覆盖则保留原建仓日，不把「没填」当成「改到今天」。
    """
    clean_code = _clean_code(code)
    clean_cost = _positive(cost, "cost")
    clean_qty = _positive(qty, "qty")
    day = sqlite_store.parse_day(entry_date)

    conn = sqlite_store.connect(root=root, create=True)
    assert conn is not None  # create=True 时不会返回 None
    try:
        exists = conn.execute(
            "SELECT 1 FROM positions WHERE code = ?", (clean_code,)
        ).fetchone()
        if exists and not upsert:
            raise LadderStockError(
                f"{clean_code} 已在本机持仓里；要改请用 ldst position update --code {clean_code}",
                EXIT_USAGE,
            )
        if upsert:
            # 单条 UPSERT：`entry_date` 为空时只有新增走今天，覆盖保留旧值。
            conn.execute(
                "INSERT INTO positions (code, cost, qty, entry_date, industry, note, updated_at)"
                " VALUES (?, ?, ?, COALESCE(?, ?), ?, ?, ?)"
                " ON CONFLICT(code) DO UPDATE SET"
                " cost = excluded.cost, qty = excluded.qty,"
                " entry_date = CASE WHEN ? IS NULL THEN positions.entry_date"
                " ELSE excluded.entry_date END,"
                " industry = excluded.industry, note = excluded.note,"
                " updated_at = excluded.updated_at",
                (
                    clean_code,
                    clean_cost,
                    clean_qty,
                    day.isoformat() if day else None,
                    date.today().isoformat(),
                    industry.strip(),
                    note.strip(),
                    sqlite_store.now_iso(),
                    day.isoformat() if day else None,
                ),
            )
        else:
            conn.execute(
                "INSERT INTO positions (code, cost, qty, entry_date, industry, note, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    clean_code,
                    clean_cost,
                    clean_qty,
                    (day or date.today()).isoformat(),
                    industry.strip(),
                    note.strip(),
                    sqlite_store.now_iso(),
                ),
            )
        row = conn.execute(f"{_SELECT} WHERE code = ?", (clean_code,)).fetchone()
    finally:
        sqlite_store.close(conn)
    if row is None:  # pragma: no cover - 插入后读不到属于磁盘异常
        raise LadderStockError("写入持仓失败：插入后读不到记录", EXIT_USAGE)
    return dict(row)


def list_positions(
    *, code: str | None = None, root: Path | None = None
) -> list[dict[str, Any]]:
    """查本机持仓；库不存在时返回空列表（不建库）。按建仓日 + 代码排序，结果可复算。"""
    conn = sqlite_store.connect(root=root, create=False)
    if conn is None:
        return []
    try:
        params: list[Any] = []
        where = ""
        if code and code.strip():
            where = " WHERE code = ?"
            params.append(code.strip().upper())
        rows = conn.execute(
            f"{_SELECT}{where} ORDER BY entry_date, code", params
        ).fetchall()
        return sqlite_store.rows_to_dicts(rows)
    finally:
        sqlite_store.close(conn)


def update_position(
    code: str,
    *,
    cost: float | None = None,
    qty: float | None = None,
    entry_date: str | date | None = None,
    industry: str | None = None,
    note: str | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """改一条持仓（只改显式传入的字段）；代码不在库里时 6（无结果）。"""
    clean_code = _clean_code(code)
    changes: dict[str, Any] = {}
    if cost is not None:
        changes["cost"] = _positive(cost, "cost")
    if qty is not None:
        changes["qty"] = _positive(qty, "qty")
    if entry_date is not None:
        day = sqlite_store.parse_day(entry_date)
        if day is None:
            raise LadderStockError(f"--date 需要 YYYY-MM-DD，收到 {entry_date!r}", EXIT_USAGE)
        changes["entry_date"] = day.isoformat()
    if industry is not None:
        changes["industry"] = industry.strip()
    if note is not None:
        changes["note"] = note.strip()
    if not changes:
        raise LadderStockError(
            "没有任何要改的字段（--cost / --qty / --date / --industry / --note 至少给一个）",
            EXIT_USAGE,
        )

    conn = sqlite_store.connect(root=root, create=True)
    assert conn is not None
    try:
        found = conn.execute(
            "SELECT 1 FROM positions WHERE code = ?", (clean_code,)
        ).fetchone()
        if not found:
            raise LadderStockError(
                f"本机持仓里没有 {clean_code}（先 ldst position add）", EXIT_NOT_FOUND
            )
        assignments = ", ".join(f"{key} = ?" for key in changes)
        conn.execute(
            f"UPDATE positions SET {assignments}, updated_at = ? WHERE code = ?",
            [*changes.values(), sqlite_store.now_iso(), clean_code],
        )
        row = conn.execute(f"{_SELECT} WHERE code = ?", (clean_code,)).fetchone()
    finally:
        sqlite_store.close(conn)
    return dict(row or {})


def remove_position(code: str, *, root: Path | None = None) -> dict[str, Any]:
    """删一条持仓（只删本机记录，不产生任何文件）；代码不在库里时 6（无结果）。"""
    clean_code = _clean_code(code)
    conn = sqlite_store.connect(root=root, create=True)
    assert conn is not None
    try:
        row = conn.execute(f"{_SELECT} WHERE code = ?", (clean_code,)).fetchone()
        if row is None:
            raise LadderStockError(f"本机持仓里没有 {clean_code}", EXIT_NOT_FOUND)
        removed = dict(row)
        conn.execute("DELETE FROM positions WHERE code = ?", (clean_code,))
    finally:
        sqlite_store.close(conn)
    return {"removed": True, "code": clean_code, "qty": removed["qty"], "cost": removed["cost"]}


# --------------------------------------------------------------------------- #
# 本地计算（盈亏 / 集中度 / 行业分布）
# --------------------------------------------------------------------------- #

def _pct(value: float) -> float:
    return round(value * 100.0, 1)


def _shares(rows: list[dict[str, Any]], weights: list[float]) -> list[dict[str, Any]]:
    total = sum(weights)
    if total <= 0:
        return []
    ordered = sorted(zip(weights, rows, strict=True), key=lambda pair: (-pair[0], pair[1]["code"]))
    return [
        {
            "code": row["code"],
            "weight_pct": _pct(weight / total),
            "cost": round(float(row["cost"]) * float(row["qty"]), 2),
        }
        for weight, row in ordered
    ]


def _concentration(
    rows: list[dict[str, Any]], weights: list[float], basis: str
) -> dict[str, Any]:
    total = sum(weights)
    if not rows or total <= 0:
        return {
            "available": False,
            "reason": "本机没有持仓" if not rows else "持仓成本合计为 0，无法计算权重",
            "basis": basis,
            "top1_pct": None,
            "top3_pct": None,
            "hhi": None,
            "holdings": [],
        }
    shares = _shares(rows, weights)
    # 按权重降序取前三：`rows` 是「建仓日 + 代码」顺序，直接切片会把最轻的算进前三
    fractions = sorted((weight / total for weight in weights), reverse=True)
    return {
        "available": True,
        "reason": None,
        "basis": basis,
        "top1_pct": shares[0]["weight_pct"],
        # 前三合计按原始比例相加后只舍入一次：把逐行已舍入的百分比再相加会凑出 100.1%
        "top3_pct": _pct(sum(fractions[:3])),
        "hhi": round(sum(fraction * fraction for fraction in fractions), 4),
        "holdings": shares,
    }


def _sector_distribution(
    rows: list[dict[str, Any]], weights: list[float], basis: str
) -> dict[str, Any]:
    labeled = [row for row in rows if str(row.get("industry") or "").strip()]
    if not labeled:
        return {
            "available": False,
            "reason": (
                "本机没有「个股 → 行业」映射数据集，且持仓都未标注 --industry；"
                "请用 ldst position update --industry <行业> 标注（不猜行业）"
            ),
            "basis": basis,
            "labeled_count": 0,
            "industries": [],
        }
    buckets: dict[str, float] = {}
    for row, weight in zip(rows, weights, strict=True):
        key = str(row.get("industry") or "").strip() or UNLABELED_INDUSTRY
        buckets[key] = buckets.get(key, 0.0) + weight
    total = sum(buckets.values())
    industries = [
        {"industry": name, "weight_pct": _pct(value / total), "cost": round(value, 2)}
        for name, value in sorted(buckets.items(), key=lambda item: (-item[1], item[0]))
    ]
    return {
        "available": True,
        "reason": None,
        "basis": basis,
        "labeled_count": len(labeled),
        "unlabeled_count": len(rows) - len(labeled),
        "industries": industries,
    }


def compute_metrics(
    rows: list[dict[str, Any]],
    prices: dict[str, float] | None = None,
    *,
    as_of: str | date | None = None,
) -> dict[str, Any]:
    """持仓本地计算：浮动盈亏、集中度、行业分布（全部只读入参，不碰网络）。

    `prices` 是调用方从**本机行情缓存**取到的最新价（代码 → 价格）；取不到的代码
    一律 `null`，其盈亏如实缺席，并计入 `valuation.unpriced_cost`。
    """
    price_map = prices or {}
    day = sqlite_store.parse_day(as_of)

    items: list[dict[str, Any]] = []
    cost_weights: list[float] = []
    value_weights: list[float] = []
    priced_cost = 0.0
    unpriced_cost = 0.0
    unrealized = 0.0

    for row in rows:
        code = str(row["code"])
        cost = float(row["cost"])
        qty = float(row["qty"])
        basis_cost = cost * qty
        price = price_map.get(code)
        market_value = round(price * qty, 2) if price is not None else None
        pnl = round((price - cost) * qty, 2) if price is not None else None
        pnl_pct = _pct(price / cost - 1.0) if price is not None and cost > 0 else None

        cost_weights.append(basis_cost)
        value_weights.append(market_value if market_value is not None else basis_cost)
        if price is None:
            unpriced_cost += basis_cost
        else:
            priced_cost += basis_cost
            unrealized += pnl or 0.0

        items.append(
            {
                "code": code,
                "cost": cost,
                "qty": qty,
                "entry_date": row.get("entry_date"),
                "industry": str(row.get("industry") or ""),
                "note": str(row.get("note") or ""),
                "latest_price": price,
                "market_value": market_value,
                "unrealized_pnl": pnl,
                "unrealized_pnl_pct": pnl_pct,
            }
        )

    total_cost = sum(cost_weights)
    # 全部持仓都取到价才改用市值口径；否则退回成本口径（不拿估算价凑市值）
    fully_priced = bool(rows) and unpriced_cost == 0.0
    basis = "market_value" if fully_priced else "cost"
    weights = value_weights if fully_priced else cost_weights

    return {
        "local_only": True,
        "store": "local_sqlite",
        "data_source_status": "ok" if rows else "missing",
        "as_of": day.isoformat() if day else None,
        "price_source": "daily_k",
        "position_count": len(rows),
        "valuation": {
            "total_cost": round(total_cost, 2),
            "market_value": round(sum(value_weights), 2) if fully_priced else None,
            "priced_cost": round(priced_cost, 2),
            "unpriced_cost": round(unpriced_cost, 2),
            "coverage_pct": _pct(priced_cost / total_cost) if total_cost > 0 else None,
            "unrealized_pnl": round(unrealized, 2) if priced_cost > 0 else None,
            "unrealized_pnl_pct": (
                _pct(unrealized / priced_cost) if priced_cost > 0 else None
            ),
        },
        "items": items,
        "concentration": _concentration(items, weights, basis),
        "sector_distribution": _sector_distribution(items, weights, basis),
    }


__all__ = [
    "POSITION_FIELDS",
    "UNLABELED_INDUSTRY",
    "add_position",
    "compute_metrics",
    "list_positions",
    "remove_position",
    "update_position",
]
