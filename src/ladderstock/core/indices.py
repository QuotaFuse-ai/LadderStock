"""首页指数条：A 股四个 + 美股两个 + 港股两个指数的实时报价投影（QUOT-380 / QUOT-471 / QUOT-560）。

**白名单写死在本层八条**，不向 peer 动态要清单：实测 `index_list` 是按涨幅排序的
100 条榜，不含首页这些指数，还混进老三板个股（`400224 苏阳光5`）。榜是日单一的，
照它建注册表等于首页那几格「今天有、明天没」。

三块指数走三条不同的路（**不是**同一工具换参数）：

- **A 股 4 格**（SH/SZ）走 `index_quote`（peer `tickhub`），一次批量请求拿全。
- **美股 2 格**（US）走 `index_quote_us` → `th_us_get_quote`（peer `tickhub_us`），
  **逐 symbol** 一次一个（该工具必填单个 `symbol`，没有批量入参）。
- **港股 2 格**（HK）走 `index_quote_hk` → 同一个 `th_us_get_quote`，同样逐 symbol，
  入参是规范形 `HSI.HK` / `HSTECH.HK`（QUOT-560：peer 侧加了港股指数别名表，
  tencent/sina 免费公开源实测可取，零 key、Tier C 非商用）。首页一次拉取 =
  1 个 CN 批量请求 + 4 个 US peer 请求。

A 股那批实测出来的坑（2026-11-03 对真实 peer `index_quote` 复测），判据逐条对着它们写：

1. **入参必须带 `sh`/`sz` 前缀，且只允许前缀形式**。裸 `000001` 不报错——它回
   **平安银行**（QUOT-379 当时记录的是 `isError`，现在已变成静默回错票，只会更危险）；
   点号写法 `000001.SH` 同样回平安银行。入参由 `INDEX_WHITELIST` 拼，外部传不进来。
2. **绝不能用 `spot_quote` / `tencent_quote_batch` 取指数**：批量工具剥掉前缀后按 6 位
   代码回键，指数与个股撞车（实测四回三、`000001` 成了平安银行且 `failed=0`）。
   指数只走 `index_quote` 这一个工具。
3. **返回行的 `code` 不带前缀**，所以匹配按 `(market, code)`，不按 `code` 建字典。
   `is_index` 一并核验：它是必要条件之一，但**不是充分条件**（平安银行那条也标
   `is_index: true`），单看它挡不住坑 1。

美股 / 港股那批（2026-11-06 / 2026-11-11 对真实 peer `th_us_get_quote` 复测）：回包形状
与 CN 不同（`payload` 对象，非 `ok/data/meta` 壳），由 `data/adapters/tickhub_us.py`
落成一行；`compliance_tier=C`（`sina` / `tencent`）与警告句落进信封 `source_detail`，
「非商用」前提在数据层可见。港股行的 `market=HK`、`symbol=HSI.HK`（规范形），
匹配键与美股同用 `(market, symbol)`。

数字照 peer 原样投影，不做二次换算、不补 0（DEC-028）；取不到就是
`available=false` + 原因——不自采（DEC-006 闸门 4）、不换供应商、不猜价。
非交易时段 peer 回上一交易日收盘，`as_of` 如实带出，配 `FOOTNOTE` 说明是快照。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from ladderstock.core.ladder import as_float

_log = logging.getLogger(__name__)

#: A 股批量取数入口：`DATASET_CATALOG` 里登记的 `index_quote`（peer `tickhub`，同工具名）
DATASET = "index_quote"

#: 美股逐 symbol 取数入口：`DATASET_CATALOG` 里登记的 `index_quote_us`
#: （peer `tickhub_us` / 工具 `th_us_get_quote`）。**不进 sync 台账**，同 `index_quote`。
DATASET_US = "index_quote_us"

#: 港股逐 symbol 取数入口：`DATASET_CATALOG` 里登记的 `index_quote_hk`
#: （同一个 peer / 工具，入参 `HSI.HK` / `HSTECH.HK`，QUOT-560）。同样不进 sync 台账。
DATASET_HK = "index_quote_hk"

#: 首页脚注（`HOME-V2-DATA-SURFACE.md` A-1 的口径：这不是逐笔实时）
FOOTNOTE = "快照，不是逐笔实时"

#: 整批取数失败时写进 `reason` 的前缀
FETCH_FAILED = "index_quote 取数失败"
FETCH_US_FAILED = "index_quote_us 取数失败"
FETCH_HK_FAILED = "index_quote_hk 取数失败"
NO_ROWS = "index_quote 没有返回任何匹配的行"

Status = Literal["ok", "partial", "missing"]


@dataclass(frozen=True)
class IndexSpec:
    """一格指数。`code` 是给 UI 的稳定键（带前缀），`market`/`symbol` 是 peer 行里的形状。"""

    code: str
    market: str
    symbol: str
    name: str


#: A 股四格：一次 `index_quote` 批量请求拿全（peer `tickhub`）
CN_WHITELIST: tuple[IndexSpec, ...] = (
    IndexSpec("sh000001", "SH", "000001", "上证指数"),
    IndexSpec("sh000300", "SH", "000300", "沪深300"),
    IndexSpec("sz399001", "SZ", "399001", "深证成指"),
    IndexSpec("sz399006", "SZ", "399006", "创业板指"),
)

#: 美股两格：逐个 `symbol` 打 `th_us_get_quote`（peer `tickhub_us`）
#: `market` 与 `symbol` 同形（适配器把 `symbol` 落成行键，`market` 默认 `US`）
US_WHITELIST: tuple[IndexSpec, ...] = (
    IndexSpec("DJI", "US", "DJI", "道琼斯"),
    IndexSpec("IXIC", "US", "IXIC", "纳斯达克综合"),
)

#: 港股两格：逐个 `symbol` 打 `th_us_get_quote`（peer `tickhub_us`，QUOT-560）。
#: `symbol` 用 peer 的规范形 `HSI.HK` / `HSTECH.HK`（回包 `symbol` 同形，匹配键才对得上）；
#: `code` 仍是给 UI 的稳定键 `HSI` / `HSTECH`，前端按它渲染，不随上游写法变。
HK_WHITELIST: tuple[IndexSpec, ...] = (
    IndexSpec("HSI", "HK", "HSI.HK", "恒生指数"),
    IndexSpec("HSTECH", "HK", "HSTECH.HK", "恒生科技"),
)

#: 首页八格全表（顺序即上屏顺序）：A 股 → 美股 → 港股
INDEX_WHITELIST: tuple[IndexSpec, ...] = CN_WHITELIST + US_WHITELIST + HK_WHITELIST

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
    """一次请求取回八格。

    取数按块分头走、互不连坐：A 股一次的批量 `index_quote` 失败不影响美股 / 港股，
    美股或港股某一 symbol 失败也只影响那一格。某一块整体不可用 → 那一块的格子出原因，
    其余照常显示。
    """
    cells: list[dict[str, Any]] = []

    # --- A 股 4 格：一次批量请求 ---
    cn_rows, cn_reason = _fetch_cn(registry)
    cells.extend(_cells_for(CN_WHITELIST, _index_by_market_code(cn_rows), reason=cn_reason))

    # --- 美股 2 格：逐 symbol，一次首页拉取 = 2 个上游请求 ---
    us_rows, us_reason, us_detail, us_fails = _fetch_per_symbol(
        registry, DATASET_US, US_WHITELIST, FETCH_US_FAILED
    )
    cells.extend(
        _cells_for(US_WHITELIST, _index_by_symbol(us_rows), reason=us_reason, cell_reasons=us_fails)
    )

    # --- 港股 2 格：同一个 peer 工具，逐 symbol（QUOT-560），再 2 个上游请求 ---
    hk_rows, hk_reason, hk_detail, hk_fails = _fetch_per_symbol(
        registry, DATASET_HK, HK_WHITELIST, FETCH_HK_FAILED
    )
    cells.extend(
        _cells_for(HK_WHITELIST, _index_by_symbol(hk_rows), reason=hk_reason, cell_reasons=hk_fails)
    )

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
    miss_reason: str | None = None if filled else _miss_detail(cn_reason, us_reason, hk_reason)
    return IndexStrip(
        payload=_payload(cells, available=bool(filled), reason=miss_reason),
        status=status,
        day=day,
        source_detail=_source_detail(us_detail, hk_detail, cn_reason, us_reason, hk_reason),
    )


def _fetch_cn(registry: Any) -> tuple[list[dict[str, Any]], str | None]:
    """A 股一批：`index_quote` 一次拿四行。失败 → 空行 + 原因（不抛）。"""
    params = {"codes": ",".join(spec.code for spec in CN_WHITELIST)}
    try:
        outcome = registry.fetch_live(DATASET, params=params)
    except Exception as exc:  # noqa: BLE001 - 首页不能因为一个上游就 500
        failure = f"{FETCH_FAILED}：{getattr(exc, 'message', None) or exc}"
        _log.warning("index_quote 取数失败：%s", failure)
        return [], failure
    rows = _rows(outcome.envelope)
    reason: str | None = None if rows else f"{NO_ROWS}（index_quote 返回 0 行）"
    return rows, reason


def _fetch_per_symbol(
    registry: Any,
    dataset: str,
    specs: tuple[IndexSpec, ...],
    fail_prefix: str,
) -> tuple[list[dict[str, Any]], str | None, str, dict[str, str]]:
    """美股 / 港股共用：逐 `symbol` 调 `th_us_get_quote`（peer `tickhub_us`，一 symbol 一次）。

    任何一格失败都只影响那一格：单格失败的原因按 symbol 记进第四个返回值，
    由 `_cells_for` 落到那一格上（不冒充「上游静默丢行」）；整块全失败时另回一个
    整体原因。返回的行用 `symbol` 建键；第三个返回值是该块的合规详述（从信封
    `payload` 读 `compliance_tier` / `warnings`——`ProviderRegistry` 会在成功路径
    用轨迹覆盖信封 `source_detail`，所以合规字段从载荷读，不靠它）。
    """
    rows: list[dict[str, Any]] = []
    fail_reasons: list[str] = []
    fail_by_symbol: dict[str, str] = {}
    tiers: set[str] = set()
    warnings: list[str] = []
    for spec in specs:
        params = {"symbol": spec.symbol, "commercial_use": False}
        try:
            outcome = registry.fetch_live(dataset, params=params)
        except Exception as exc:  # noqa: BLE001 - 一格失败不连坐另一格
            failure = f"{fail_prefix}（{spec.symbol}）：{getattr(exc, 'message', None) or exc}"
            _log.warning("%s", failure)
            fail_reasons.append(failure)
            fail_by_symbol[spec.symbol] = failure
            continue
        rows.extend(_rows(outcome.envelope))
        tier, warns = _peer_compliance(outcome.envelope)
        if tier:
            tiers.add(tier)
        warnings.extend(w for w in warns if w not in warnings)
    reason: str | None = _block_overall_reason(dataset, rows, fail_reasons)
    return rows, reason, _peer_detail(tiers, warnings), fail_by_symbol


def _peer_compliance(envelope: Any) -> tuple[str, list[str]]:
    """从 US peer 信封 `payload` 取 (compliance_tier, warnings)——合规字段的落地位置。"""
    payload = getattr(envelope, "payload", {}) or {}
    tier = str(payload.get("compliance_tier") or "").strip().upper()
    raw = payload.get("warnings")
    warns = [str(w).strip() for w in raw if str(w).strip()] if isinstance(raw, list) else []
    return tier, warns


def _peer_detail(tiers: set[str], warnings: list[str]) -> str:
    """美股 / 港股块 `source_detail`：`th_us_get_quote` + 合规分级 + 警告句（不吞）。"""
    if not tiers and not warnings:
        return ""
    tier_text = "/".join(sorted(tiers)) if tiers else "未知"
    parts = [f"th_us_get_quote（compliance_tier={tier_text}"]
    if warnings:
        parts.append("；" + "；".join(warnings))
    parts.append("）")
    return "".join(parts)


def _block_overall_reason(
    dataset: str, rows: list[dict[str, Any]], fail_reasons: list[str]
) -> str | None:
    """整块的兜底原因：**只在该块所有格都没数据**时才有值（单格失败留给按格原因）。"""
    if rows:
        return None
    if fail_reasons:
        return "；".join(fail_reasons)
    return f"{dataset} 没有返回任何匹配的行"


def _source_detail(
    us_detail: str,
    hk_detail: str,
    cn_reason: str | None,
    us_reason: str | None,
    hk_reason: str | None,
) -> str:
    """信封 `source_detail`：peer 成功详述（含合规字段）+ 各块失败原因。

    美股 / 港股走同一个工具，两块都成功时详述常常逐字相同——去重保序，不重复刷屏。
    """
    parts = [us_detail, hk_detail, *[x for x in (cn_reason, us_reason, hk_reason) if x]]
    return "；".join(dict.fromkeys(x for x in parts if x))


def _index_by_market_code(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """按 `(market, code)` 建键（坑 3：只按 `code` 会把 sh000001 与 sz000001 撞成一格）。

    `is_index=false` 的行不算数：它是必要条件（挡住把个股行当指数）。`is_index=true`
    仍不是充分条件（平安银行那条也标 true），所以 `(market, code)` 精确匹配不能省。
    """
    matched: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        if not row.get("is_index"):
            continue
        market = str(row.get("market") or "").strip().upper()
        symbol = str(row.get("code") or "").strip()
        if market and symbol:
            matched.setdefault((market, symbol), row)
    return matched


def _index_by_symbol(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """美股 / 港股按 `(market, symbol)` 建键：这类行没有 `code`，`symbol` 才是行键。

    `market` 缺省按 `US`（美股回包不带 market 字段）；港股行带 `market=HK`，照读。
    """
    matched: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        market = str(row.get("market") or "US").strip().upper()
        symbol = str(row.get("symbol") or row.get("code") or "").strip()
        if market and symbol:
            matched.setdefault((market, symbol), row)
    return matched


def _cells_for(
    specs: tuple[IndexSpec, ...],
    by_key: Mapping[tuple[str, str], dict[str, Any]],
    *,
    reason: str | None = None,
    cell_reasons: Mapping[str, str] | None = None,
) -> list[dict[str, Any]]:
    """按 `specs` 的顺序出格：匹配到就照抄数值，匹配不到就 `null` + 原因。

    缺格原因三档，按真实程度取：该 symbol 自己的取数失败（`cell_reasons`）→
    整块原因（`reason`）→ 「上游没回这一行」的兜底。失败不能冒充静默丢行。

    `is_index` 只有 CN 行有，所以这里**不强制**该字段——CN 行的坑 1 由
    `(market, code)` 精确匹配 + 白名单入参双重兜住（`_index_by_market_code` 已经
    把裸 `000001` 那条拼不到 `(SH, 000001)` 上）。
    """
    cells: list[dict[str, Any]] = []
    for spec in specs:
        row = by_key.get((spec.market, spec.symbol))
        if row is None:
            miss = (cell_reasons or {}).get(spec.symbol) or reason or _cell_miss_detail(spec)
            cells.append(_missing_cell(spec, miss))
            continue
        cells.append(_filled_cell(spec, row))
    return cells


def _missing_cell(spec: IndexSpec, reason: str) -> dict[str, Any]:
    return {
        "code": spec.code,
        "market": spec.market,
        "name": spec.name,
        "available": False,
        "price": None,
        "change": None,
        "change_pct": None,
        "prev_close": None,
        "as_of": None,
        "reason": reason,
    }


def _filled_cell(spec: IndexSpec, row: Mapping[str, Any]) -> dict[str, Any]:
    cell: dict[str, Any] = {
        "code": spec.code,
        "market": spec.market,
        "name": spec.name,
        "available": True,
        "reason": None,
        "as_of": _row_as_of(row),
        "row_timestamp": str(row.get("timestamp") or ""),
    }
    # 照抄，不换算：peer 给 `price` 我们就显 `price`，缺一个字段就是 `null`
    for key in _VALUE_KEYS:
        cell[key] = as_float(row.get(key))
    return cell


def _row_as_of(row: Mapping[str, Any]) -> str | None:
    """`as_of`：CN 行是 14 位行情时间，US/HK 行可能直接给 ISO 或 `YYYY-MM-DD HH:MM:SS`。"""
    raw = str(row.get("timestamp") or row.get("as_of") or "")
    return iso_timestamp(raw) or (raw or None)


def _cells(
    by_market_code: Mapping[tuple[str, str], dict[str, Any]] | None = None,
    *,
    reason: str | None = None,
) -> list[dict[str, Any]]:
    """兼容旧签名：只用 A 股白名单出格（保留给既有测试 / 调用方）。"""
    return _cells_for(CN_WHITELIST, by_market_code or {}, reason=reason)


def _payload(cells: list[dict[str, Any]], *, available: bool, reason: str | None) -> dict[str, Any]:
    """摘掉内部字段后的载荷——对外每一格只有契约里的字段。"""
    return {
        "available": available,
        "reason": reason,
        "footnote": FOOTNOTE,
        # 上屏主口径仍是 A 股那条（页面脚注「源：」跟它）；多源在 `datasets` 里全带出
        "price_source": DATASET,
        "datasets": [DATASET, DATASET_US, DATASET_HK],
        "cells": [
            {key: value for key, value in cell.items() if key not in _INTERNAL_KEYS}
            for cell in cells
        ],
    }


def _cell_miss_detail(spec: IndexSpec) -> str:
    dataset = {"US": DATASET_US, "HK": DATASET_HK}.get(spec.market, DATASET)
    return (
        f"{dataset} 未返回 ({spec.market}, {spec.symbol}) 的行"
        f"（入参 {spec.code} 被上游静默丢弃，不补值）"
    )


def _miss_detail(cn_reason: str | None, us_reason: str | None, hk_reason: str | None) -> str:
    """全盘无数据时的整体原因（三块原因都并进来）。"""
    parts = [x for x in (cn_reason, us_reason, hk_reason) if x]
    return "；".join(parts) or NO_ROWS


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
    "DATASET_US",
    "DATASET_HK",
    "FOOTNOTE",
    "CN_WHITELIST",
    "US_WHITELIST",
    "HK_WHITELIST",
    "INDEX_WHITELIST",
    "iso_timestamp",
    "IndexSpec",
    "IndexStrip",
    "fetch_indices",
]
