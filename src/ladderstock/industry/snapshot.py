"""装配层：`industry_snapshot(industry)`（R5，QUOT-143）。

一句话：**只读本机缓存回放，按 spec 的公式算五维，缺的显式给 `null`。**

- 读取路径与 `core/pool.py` 完全一致 —— `data/archive.py`（Parquet 优先 → DuckDB 兜底），
  本模块不发起任何网络请求，也不 import 其他子模块的内部代码（只走 DATASET_CATALOG
  登记过的数据集）；
- 每个指标键都在 `provenance.values` 里出现（R3 形状，SPEC-RESEARCH-PROVENANCE.md §1）：
  有数 → 指向本次真正读到的数据集引用位；无数 → `sources: []` + `unverified: true`；
- 上游取不到的分项（北向行业拆分、申万行业指数历史）是**结构性缺口**，
  永远 `null` + 固定 reason，等上游工具就绪，不在这里猜（DEC-018 / ROADMAP §4）。

口径表：`docs/specs/SPEC-INDUSTRY-METRICS.md`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ladderstock.core.ladder import CODE_KEYS, as_float, pick
from ladderstock.data import archive
from ladderstock.industry import metrics
from ladderstock.industry.catalog import IndustryCatalog, IndustryEntry, load_catalog

#: 五维固定顺序（对外键名即口径顺序，不可增删）
DIMENSIONS: tuple[str, ...] = ("prosperity", "capital", "valuation", "momentum", "crowding")
DIMENSION_LABELS: dict[str, str] = {
    "prosperity": "景气",
    "capital": "资金",
    "valuation": "估值",
    "momentum": "动量",
    "crowding": "拥挤度",
}

#: 本模块读的数据集（顺序即溯源顺序）
SNAPSHOT_DATASETS: tuple[str, ...] = (
    "sw_industry_map",
    "finance_indicators",
    "valuation_history",
    "fund_flow",
    "industry_fund_flow",
    "daily_k",
)

#: 信封 `data_as_of` 的依据数据集：行业归属是五维的共同前提，它没记录时日期给 null
#: （CLI 与 MCP 两个出口共用这一个常量，避免同一份快照在两个面上时点不一致）
META_PRIMARY_DATASET = "sw_industry_map"

#: 口径标签（DEC-018：两套分类不混算，所以每个字段都带自己的 caliber）
CALIBER_SW = "sw_constituents"  # 申万一级成分股聚合
CALIBER_BOARD_PREFIX = "board:"  # 板块口径，后缀是缓存里的实际 source
CALIBER_POOL = "cache_pool"  # 分母是本地缓存代码池，不是全市场

NAME_KEYS: tuple[str, ...] = ("name", "board_name", "stock_name", "名称")
BOARD_NAME_KEYS: tuple[str, ...] = ("board_name", "name", "名称")
BOARD_CODE_KEYS: tuple[str, ...] = ("board_code", "code")
TRADE_DATE_KEYS: tuple[str, ...] = ("trade_date", "date")

REVENUE_YOY_KEYS: tuple[str, ...] = ("revenue_yoy", "revenue_growth_yoy", "营收同比")
PROFIT_YOY_KEYS: tuple[str, ...] = ("profit_yoy", "net_profit_yoy", "净利润同比")
ROE_KEYS: tuple[str, ...] = ("roe", "roe_weighted", "净资产收益率")
MAIN_NET_KEYS: tuple[str, ...] = ("main_net", "main_net_inflow", "主力净流入")
MAIN_NET_PCT_KEYS: tuple[str, ...] = ("main_net_pct", "main_net_ratio", "主力净流入占比")
CHANGE_PCT_KEYS: tuple[str, ...] = ("change_pct", "pct_chg", "涨跌幅")
PE_KEYS: tuple[str, ...] = ("pe_ttm", "pe")
PB_KEYS: tuple[str, ...] = ("pb",)
TURNOVER_KEYS: tuple[str, ...] = ("turnover_rate", "turnover", "换手率")
AMOUNT_KEYS: tuple[str, ...] = ("amount", "成交额")
CLOSE_KEYS: tuple[str, ...] = ("close", "收盘")

#: 结构性缺口：上游没有对应工具/数据，字段永远显式 null（ROADMAP §4 判定「接不上」）
NORTHBOUND_REASON = "北向资金上游只有市场级（northbound_flow 无行业拆分），行业北向净流入接不上"
INDEX_MOMENTUM_REASON = (
    "申万行业指数历史不可得：TickHub-CN sw_industry_index 实测「东财未发布该代码的 K 线，"
    "且无零鉴权替代源」，指数口径涨跌幅不降级为成分股聚合（那是另一个字段）"
)

FORMULAS: dict[str, str] = {
    "prosperity": "成分股最近一期报表的 revenue_yoy / profit_yoy / roe 中位数（%）",
    "capital": (
        "近 N 个交易日成分股 main_net 合计（申万口径）"
        "+ 同名板块资金流（板块口径，独立标注）"
    ),
    "valuation": "成分股每日中位数 PE/PB → 窗口内经验分位（<= 当前值的占比）",
    "momentum": "成分股等权涨跌幅（%）+ 成交额占本地缓存代码池比（%）",
    "crowding": "成分股每日中位数换手率 → 窗口内经验分位 + 主力净流入活跃度集中度",
}

#: 五维对外承诺的指标字段（口径见 SPEC-INDUSTRY-METRICS.md）。
#: `available` / `missing` / `metrics_present` 只看这些键：`reason`、`caliber` 之类
#: 说明性字段永远有值，拿它们判断「有没有数」会让空缓存也报 available=true。
METRIC_FIELDS: dict[str, tuple[str, ...]] = {
    "prosperity": ("revenue_yoy_median", "profit_yoy_median", "roe_median"),
    "capital": (
        "constituents.main_net_inflow_nd",
        "constituents.main_net_inflow_nd_sampled",
        "board.main_net_inflow",
        "board.main_net_pct",
        "board.change_pct",
        "northbound",
    ),
    "valuation": ("pe_median", "pe_percentile", "pb_median", "pb_percentile"),
    "momentum": ("index_change_pct", "amount_share_pct"),
    "crowding": ("turnover_median", "turnover_percentile", "fund_concentration_pct"),
}


def metric_fields(name: str, windows: dict[str, Any]) -> tuple[str, ...]:
    """一个维度承诺输出的指标字段（momentum 的 N 日键随 `momentum_windows` 变化）。"""
    if name == "momentum":
        return (
            "index_change_pct",
            *(f"change_pct_{window}d" for window in windows["momentum_windows"]),
            "amount_share_pct",
        )
    return METRIC_FIELDS[name]


def metric_paths(windows: dict[str, Any]) -> list[str]:
    return [f"{name}.{field}" for name in DIMENSIONS for field in metric_fields(name, windows)]


def _dimension_skeleton(name: str, windows: dict[str, Any], reason: str | None) -> dict[str, Any]:
    """字段齐全、值全 `null` 的占位维度。

    「每个行业每个字段要么有值要么显式 null」是验收条款，所以行业没绑定代码、
    或者名字不在清单里时，也要把整套键位铺出来——省得下游按行业猜 schema。
    """
    block: dict[str, Any] = {"caliber": None, "available": False, "reason": reason}
    for path in metric_fields(name, windows):
        node = block
        parts = path.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = None
    return block


@dataclass
class _Source:
    """一个数据集的本次读取结果。"""

    dataset: str
    records: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    @property
    def used(self) -> bool:
        return bool(self.records)

    def retrieved_at(self) -> str | None:
        """缓存里这批数据的入库时间（`_ingested_at`），不是本次运行时间。

        溯源时间必须来自数据本身，否则「同一 fixture 两次运行」会写出两个
        不同的 `retrieved_at`，幂等验收直接挂。读不到就如实 `None`。
        """
        stamps = sorted({str(row.get("_ingested_at") or "") for row in self.records})
        stamps = [stamp for stamp in stamps if stamp]
        return stamps[-1] if stamps else None

    def as_of(self) -> str | None:
        days = sorted({str(pick(row, TRADE_DATE_KEYS) or "") for row in self.records})
        days = [day for day in days if day]
        return days[-1] if days else None


class _Reader:
    """一次快照内共享的缓存读取（31 个行业列表也只读一遍）。"""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root
        self._sources: dict[str, _Source] = {}

    def source(self, dataset: str) -> _Source:
        if dataset not in self._sources:
            try:
                records = archive.read_dataset(dataset, None, root=self.root)
            except Exception as exc:  # noqa: BLE001 - 缓存读失败要显式可见，不让命令整条崩
                self._sources[dataset] = _Source(dataset, [], f"缓存读取失败：{exc}")
            else:
                self._sources[dataset] = _Source(dataset, list(records))
        return self._sources[dataset]


# --------------------------------------------------------------------------- #
# 行级小工具（缓存行的日期/代码键名在不同数据集里有别名）
# --------------------------------------------------------------------------- #

def _day(row: dict[str, Any]) -> str:
    """行里的「计入日期」文本，缺日期当空串（空串不参与任何聚合）。"""
    return str(pick(row, TRADE_DATE_KEYS) or "")


def _code(row: dict[str, Any]) -> str:
    return str(pick(row, CODE_KEYS) or "").strip()


def _sorted_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按（日期, 代码）升序——两次运行读同一份缓存必须得到同一顺序。"""
    return sorted(rows, key=lambda row: (_day(row), _code(row)))


def _days_of(rows: list[dict[str, Any]]) -> list[str]:
    return sorted({_day(row) for row in rows if _day(row)})


def _by_code(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in _sorted_rows(rows):
        if code := _code(row):
            grouped.setdefault(code, []).append(row)
    return grouped


def _latest_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """每个代码取「计入日期」最大的一行（同日多行取最后一条，排序后确定性）。"""
    return {code: series[-1] for code, series in _by_code(rows).items()}


def _on_day(rows: list[dict[str, Any]], day: str) -> dict[str, Any] | None:
    """某代码系列里指定日期的那一行（`rows` 已按日期升序，同日取最后一条）。"""
    matched = [row for row in rows if _day(row) == day]
    return matched[-1] if matched else None


# --------------------------------------------------------------------------- #
# 归属（成分表）
# --------------------------------------------------------------------------- #

def current_membership(records: list[dict[str, Any]]) -> dict[str, str]:
    """每个代码当前（计入日期最晚一条）的申万一级代码。

    重复行以「计入日期」最新者为准；同一天重复出现的同一代码按后写覆盖，
    两次运行读同一份缓存 ⇒ 结果一致（幂等验收的前提）。
    """
    latest: dict[str, str] = {}
    for row in _sorted_rows(records):
        code = _code(row)
        l1 = str(row.get("l1_code") or "").strip()
        if code and l1:
            latest[code] = l1
    return latest


def constituents_of(records: list[dict[str, Any]], l1_code: str) -> tuple[list[str], str | None]:
    """返回 `(成分代码（升序）, 归属数据时点)`。"""
    membership = current_membership(records)
    codes = sorted(code for code, l1 in membership.items() if l1 == l1_code)
    days = _days_of([row for row in records if str(row.get("l1_code") or "").strip() == l1_code])
    return codes, (days[-1] if days else None)


def _daily_median(
    records: list[dict[str, Any]], codes: set[str], keys: tuple[str, ...], *, digits: int
) -> dict[str, float]:
    """按交易日聚合成分股的中位数：`{trade_date: median}`。"""
    buckets: dict[str, list[Any]] = {}
    for row in records:
        if _code(row) not in codes:
            continue
        day = _day(row)
        value = as_float(pick(row, keys))
        if not day or value is None:
            continue
        buckets.setdefault(day, []).append(value)
    out: dict[str, float] = {}
    for day, values in buckets.items():
        median = metrics.median(values, digits=digits)
        if median is not None:
            out[day] = median
    return out


def _window_metric(
    daily: dict[str, float], lookback_days: int, *, digits: int
) -> tuple[float | None, float | None, int]:
    """`(当前值, 窗口内经验分位, 窗口交易日数)`。"""
    days, values = metrics.window_series(daily, lookback_days=lookback_days)
    if not values:
        return None, None, 0
    current = values[-1]
    percentile = metrics.percentile_rank(values, current, digits=digits)
    return current, percentile, len(values)


# --------------------------------------------------------------------------- #
# 五个维度
# --------------------------------------------------------------------------- #

#: 说明性字段的固定文案（长中文串拆在这里，口径解释与 spec 一一对应）
NO_FINANCE_CACHE = (
    "成分股财务指标本地无缓存，需先 `ldst sync --dataset finance_indicators --codes <代码>`，不估算"
)
FINANCE_EMPTY = "缓存里的财务指标行三个字段全为空，不估算"
FINANCE_BASIS = (
    "东财财务指标原值：营收同比 / 净利润同比为单季或累计口径以字段名为准，"
    "ROE 为累计值非年化"
)
NO_CAPITAL_CACHE = (
    "主力净流入两路口径都无本地缓存：个股侧需同步 fund_flow，"
    "板块侧需同步 industry_fund_flow"
)
NO_BOARD_CACHE = "本地无板块资金流缓存（industry_fund_flow）"
NO_BOARD_BINDING = (
    "该行业未配置同名板块绑定（config/industry.yaml 的 board_name），板块口径字段留 "
    "null——同名≠同成分，不做跨分类换算"
)
PARTIAL_COVERAGE = (
    "成分股资金流覆盖不全：部分样本不冒充全样本，行业合计给 null；"
    "已取到部分的和放在 main_net_inflow_nd_sampled"
)
NO_FUND_CACHE = "本地无成分股资金流缓存（fund_flow）"
PERCENTILE_DEFINITION = (
    "窗口内每日中位数序列中 <= 当前值的天数 / 窗口天数（经验分位，不插值）"
)
NO_VALUATION_CACHE = "本地无成分股估值历史缓存（valuation_history；北交所标的上游无源，实测缺）"
NO_KLINE_CACHE = "本地无成分股日 K 缓存（daily_k），等权涨跌幅与成交额占比都不估算"
NO_AMOUNT_DATA = (
    "成交额不可用：本地缓存代码池当日 amount 存在 null"
    "（TickHub `tencent_kline` 实测 amount 为空），部分样本不冒充全样本"
)
NO_CONSTITUENTS = "本行业没有成分代码（归属未绑定或缓存为空），该字段不估算"
NO_CROWDING_CACHE = "换手与资金集中度都无本地缓存（valuation_history / fund_flow）"
NO_MEMBERSHIP_CACHE = (
    "本地无申万归属缓存（sw_industry_map 为空），需先 "
    "`ldst sync --dataset sw_industry_map --codes <代码>`"
)
CALIBER_POLICY = (
    "申万一级做行业归属；板块资金流按缓存里的实际上游口径逐字段标注，"
    "两套不混算、不互相换算（DEC-018）"
)


def _prosperity(rows: list[dict[str, Any]], codes: list[str]) -> dict[str, Any]:
    """景气：成分股最近一期报表的营收同比 / 净利润同比 / ROE 中位数。"""
    code_set = set(codes)
    subset = [row for row in rows if _code(row) in code_set]
    latest = _latest_rows(subset)
    series = {
        "revenue_yoy_median": [as_float(pick(row, REVENUE_YOY_KEYS)) for row in latest.values()],
        "profit_yoy_median": [as_float(pick(row, PROFIT_YOY_KEYS)) for row in latest.values()],
        "roe_median": [as_float(pick(row, ROE_KEYS)) for row in latest.values()],
    }
    values = {key: metrics.median(items) for key, items in series.items()}
    sample_counts = {key: len(metrics.finite_numbers(items)) for key, items in series.items()}
    has_value = any(value is not None for value in values.values())
    reason = None
    if not has_value:
        reason = NO_FINANCE_CACHE if not latest else FINANCE_EMPTY
    return {
        "caliber": CALIBER_SW,
        "report_basis": FINANCE_BASIS,
        **values,
        "stocks_with_report": len(latest),
        "sample_counts": sample_counts,
        "available": has_value,
        "reason": reason,
    }


def _capital(
    fund_rows: list[dict[str, Any]],
    board_rows: list[dict[str, Any]],
    codes: list[str],
    *,
    fund_days: int,
    board_name: str | None,
) -> dict[str, Any]:
    """资金：个股口径累加（申万成分）+ 板块口径同名绑定，两者分开标注。"""
    constituents = _capital_constituents(fund_rows, codes, fund_days=fund_days)
    board = _capital_board(board_rows, board_name=board_name)
    has_value = (
        constituents["main_net_inflow_nd"] is not None
        or constituents["main_net_inflow_nd_sampled"] is not None
        or board["main_net_inflow"] is not None
    )
    return {
        "caliber": {CALIBER_SW: constituents["caliber"], "board": board["caliber"]},
        "constituents": constituents,
        "board": board,
        "northbound": None,
        "northbound_reason": NORTHBOUND_REASON,
        "available": has_value,
        "reason": None if has_value else NO_CAPITAL_CACHE,
    }


def _capital_constituents(
    rows: list[dict[str, Any]], codes: list[str], *, fund_days: int
) -> dict[str, Any]:
    """申万成分口径的主力净流入合计（个股侧 `fund_flow` 累加）。

    覆盖度是硬门槛：`代码 × 交易日` 的行必须齐，缺一天缺一只就不给合计——
    拿部分样本冒充全样本比给 null 危险得多。已取到部分的和单独放一个键。
    """
    code_set = set(codes)
    grouped = _by_code([row for row in rows if _code(row) in code_set])
    days = _days_of(rows)
    window = days[-fund_days:] if fund_days > 0 else days
    expected = len(codes) * len(window)
    flows: list[Any] = []
    for day in window:
        for code in codes:
            row = _on_day(grouped.get(code, []), day)
            flows.append(as_float(pick(row, MAIN_NET_KEYS)) if row else None)
    usable = [value for value in flows if value is not None]
    complete = bool(codes) and bool(window) and len(usable) == expected
    summed = metrics.sum_values(usable) if usable else None
    reason = None
    if not (complete and usable):
        reason = PARTIAL_COVERAGE if usable else NO_FUND_CACHE
    return {
        "caliber": CALIBER_SW,
        "window_days": len(window),
        "days": window,
        "main_net_inflow_nd": summed if complete else None,
        "main_net_inflow_nd_sampled": summed,
        "unit": "元",
        "sample_count": len({code for code in codes if grouped.get(code)}),
        "coverage": {"expected_rows": expected, "rows_used": len(usable), "complete": complete},
        "reason": reason,
    }


def _capital_board(rows: list[dict[str, Any]], *, board_name: str | None) -> dict[str, Any]:
    """板块口径：只认最近一日、单一上游口径、同名恰好一条。"""
    days = _days_of(rows)
    latest = days[-1] if days else None
    base = {
        "caliber": None,
        "board_code": None,
        "board_name": board_name,
        "matched_by": None,
        "trade_date": latest,
        "main_net_inflow": None,
        "main_net_pct": None,
        "change_pct": None,
        "reason": None,
    }
    if not rows:
        return {**base, "reason": NO_BOARD_CACHE}
    if board_name is None:
        return {**base, "reason": NO_BOARD_BINDING}
    day_rows = [row for row in rows if _day(row) == latest]
    upstream = sorted({str(row.get("_source") or "") for row in day_rows})
    if len(upstream) != 1:
        mixed = f"{latest} 的板块榜混有 {len(upstream)} 个上游口径（{upstream}）"
        return {**base, "reason": f"{mixed}，不跨口径挑数"}
    caliber = f"{CALIBER_BOARD_PREFIX}{upstream[0] or 'unknown'}"
    matched = [
        row for row in day_rows if str(pick(row, BOARD_NAME_KEYS) or "").strip() == board_name
    ]
    if len(matched) != 1:
        ambiguous = f"同名板块 {board_name} 命中 {len(matched)} 条（需要恰好 1 条），"
        return {
            **base,
            "caliber": caliber,
            "matched_by": "name_exact",
            "reason": f"{ambiguous}不给板块口径字段",
        }
    row = matched[0]
    return {
        "caliber": caliber,
        "board_code": str(pick(row, BOARD_CODE_KEYS) or "") or None,
        "board_name": board_name,
        "matched_by": "name_exact",
        "trade_date": latest,
        "main_net_inflow": as_float(pick(row, MAIN_NET_KEYS)),
        "main_net_pct": as_float(pick(row, MAIN_NET_PCT_KEYS)),
        "change_pct": as_float(pick(row, CHANGE_PCT_KEYS)),
        "reason": None,
    }


def _valuation(
    rows: list[dict[str, Any]], codes: list[str], *, lookback_days: int
) -> dict[str, Any]:
    """估值：每日中位数 PE / PB 的窗口经验分位。"""
    code_set = set(codes)
    ratio = metrics.DIGITS_RATIO
    subset = [row for row in rows if _code(row) in code_set]
    pe_daily = _daily_median(subset, code_set, PE_KEYS, digits=ratio)
    pb_daily = _daily_median(subset, code_set, PB_KEYS, digits=ratio)
    pe_current, pe_pct, pe_days = _window_metric(pe_daily, lookback_days, digits=ratio)
    pb_current, pb_pct, pb_days = _window_metric(pb_daily, lookback_days, digits=ratio)
    values = {
        "pe_median": pe_current,
        "pe_percentile": pe_pct,
        "pb_median": pb_current,
        "pb_percentile": pb_pct,
    }
    has_value = any(value is not None for value in values.values())
    return {
        "caliber": CALIBER_SW,
        "percentile_definition": PERCENTILE_DEFINITION,
        **values,
        "window_days": lookback_days,
        "window_days_used": max(pe_days, pb_days),
        "stocks_with_valuation": len(_by_code(subset)),
        "available": has_value,
        "reason": None if has_value else NO_VALUATION_CACHE,
    }


def _momentum(
    rows: list[dict[str, Any]], codes: list[str], *, windows: list[int]
) -> dict[str, Any]:
    """动量：成分股等权涨跌幅 + 成交额占本地缓存代码池比；指数口径永远显式 null。"""
    grouped = _by_code(rows)
    market_codes = sorted(grouped)
    days = _days_of(rows)
    latest = days[-1] if days else None

    closes = {
        code: [as_float(pick(row, CLOSE_KEYS)) for row in grouped.get(code, [])] for code in codes
    }
    change_pct: dict[str, float | None] = {}
    change_sample: dict[str, int] = {}
    for window in windows:
        value, count = metrics.equal_weight_change(closes, window)
        change_pct[f"change_pct_{window}d"] = value
        change_sample[f"change_pct_{window}d"] = count

    day_amounts: dict[str, float | None] = {}
    if latest:
        for code, series in grouped.items():
            row = _on_day(series, latest)
            if row is not None:
                day_amounts[code] = as_float(pick(row, AMOUNT_KEYS))
    industry_amount = (
        metrics.sum_values([day_amounts.get(code) for code in codes]) if codes else None
    )
    market_amount = (
        metrics.sum_values([day_amounts.get(code) for code in market_codes])
        if market_codes
        else None
    )
    share = metrics.ratio_share(industry_amount, market_amount)

    available = any(value is not None for value in change_pct.values()) or share is not None
    amount_reason = None
    if share is None:
        amount_reason = NO_CONSTITUENTS if not codes else NO_AMOUNT_DATA
    return {
        "caliber": {CALIBER_SW: "成分股等权", "amount_share": CALIBER_POOL},
        "index_change_pct": None,
        "index_change_pct_reason": INDEX_MOMENTUM_REASON,
        **change_pct,
        "change_pct_sample_counts": change_sample,
        "trading_days_available": len(days),
        "trade_date": latest,
        "amount_share_pct": share,
        "amount_share_base": (
            f"本地缓存代码池 {len(market_codes)} 只（全市场口径需先同步盘后全市场包）"
        ),
        "market_codes": len(market_codes),
        "amount_industry": industry_amount,
        "amount_market": market_amount,
        "available": available,
        "reason": None if available else NO_KLINE_CACHE,
        "amount_reason": amount_reason,
    }


def _crowding(
    valuation_rows: list[dict[str, Any]],
    fund_rows: list[dict[str, Any]],
    codes: list[str],
    *,
    turnover_lookback_days: int,
    fund_days: int,
) -> dict[str, Any]:
    """拥挤度：换手分位（申万成分口径）+ 主力净流入活跃度集中度（缓存代码池分母）。"""
    code_set = set(codes)
    ratio = metrics.DIGITS_RATIO
    subset = [row for row in valuation_rows if _code(row) in code_set]
    turnover_daily = _daily_median(subset, code_set, TURNOVER_KEYS, digits=ratio)
    current, percentile, days_used = _window_metric(
        turnover_daily, turnover_lookback_days, digits=ratio
    )

    grouped = _by_code(fund_rows)
    market_codes = sorted(grouped)
    dates = _days_of(fund_rows)
    window = dates[-fund_days:] if fund_days > 0 else dates
    industry_abs: list[float] = []
    market_abs: list[float] = []
    gaps = 0
    for day in window:
        for code in market_codes:
            row = _on_day(grouped.get(code, []), day)
            value = as_float(pick(row, MAIN_NET_KEYS)) if row else None
            if value is None:
                gaps += 1
                continue
            market_abs.append(abs(value))
            if code in code_set:
                industry_abs.append(abs(value))
    concentration = metrics.ratio_share(
        metrics.sum_values(industry_abs), metrics.sum_values(market_abs), digits=ratio
    )
    has_value = any(value is not None for value in (current, percentile, concentration))
    return {
        "caliber": {"turnover": CALIBER_SW, "fund_concentration": CALIBER_POOL},
        "turnover_median": current,
        "turnover_percentile": percentile,
        "turnover_window_days_used": days_used,
        "turnover_lookback_days": turnover_lookback_days,
        "fund_concentration_pct": concentration,
        "fund_concentration_base": f"本地缓存代码池 {len(market_codes)} 只主力净流入绝对值合计",
        "fund_window_days": len(window),
        "missing_flow_rows": gaps,
        "available": has_value,
        "reason": None
        if has_value
        else "换手与资金集中度都无本地缓存（valuation_history / fund_flow）",
    }


# --------------------------------------------------------------------------- #
# 溯源（R3 形状）
# --------------------------------------------------------------------------- #

#: 行业清单来自配置文件，指标来自缓存数据集——溯源必须区分这两类来源
CATALOG_SOURCE_ID = "cfg:industry_yaml"
CATALOG_SOURCE_DATASET = "config/industry.yaml"


def _source_for(path: str) -> str | None:
    """指标路径 → 数据来源；结构性缺口返回 `None`（永远没有引用来源）。"""
    if path.startswith(("capital.northbound", "momentum.index_change_pct")):
        return None
    if path.startswith("industry."):
        return CATALOG_SOURCE_DATASET
    if path.startswith("membership."):
        return "sw_industry_map"
    if path.startswith("prosperity."):
        return "finance_indicators"
    if path.startswith("capital.board."):
        return "industry_fund_flow"
    if path.startswith("capital.constituents."):
        return "fund_flow"
    if path.startswith("valuation.") or path.startswith("crowding.turnover"):
        return "valuation_history"
    if path.startswith("crowding."):
        return "fund_flow"
    if path.startswith("momentum."):
        return "daily_k"
    return None


def _walk_leaves(node: Any, prefix: str = "") -> dict[str, Any]:
    """把嵌套 dimensions 摊成 `{点分路径: 叶子值}`（dict 只走到叶子，列表按原样算叶子）。"""
    out: dict[str, Any] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, dict) and value:
                out.update(_walk_leaves(value, path))
            else:
                out[path] = value
    else:
        out[prefix] = node
    return out


def _evidence(
    dataset: str, sources: dict[str, _Source], catalog: IndustryCatalog
) -> dict[str, Any]:
    """R3 `sources` 的一项：缓存数据集给出这批数据的时点，配置清单给出绑定方式。"""
    if dataset == CATALOG_SOURCE_DATASET:
        return {
            "id": CATALOG_SOURCE_ID,
            "dataset": dataset,
            "params": {
                "path": str(catalog.path or ""),
                "caliber": catalog.membership_caliber,
                "bound_via": (catalog.provenance or {}).get("bound_via"),
            },
            "retrieved_at": (catalog.provenance or {}).get("retrieved_at"),
            "used": True,
        }
    source = sources[dataset]
    return {
        "id": f"ds:{dataset}",
        "dataset": dataset,
        "params": {"windows": catalog.window, "as_of": source.as_of()},
        "retrieved_at": source.retrieved_at(),
        "used": source.used,
    }


def _provenance(
    payload: dict[str, Any], sources: dict[str, _Source], catalog: IndustryCatalog
) -> dict[str, Any]:
    """R3 溯源：`values` / `sources` / `unverified` 三键齐全。"""
    leaves: dict[str, Any] = {}
    for key in ("industry", "membership"):
        leaves.update(_walk_leaves(payload[key], key))
    for name in DIMENSIONS:
        leaves.update(_walk_leaves(payload["dimensions"][name], name))

    datasets = sorted({ds for ds in map(_source_for, leaves) if ds})
    evidence = {ds: _evidence(ds, sources, catalog) for ds in datasets}
    refs: dict[str, dict[str, Any]] = {}
    for path in sorted(leaves):
        item = evidence.get(_source_for(path) or "")
        if item is not None and item["used"]:
            refs[path] = {"sources": [item["id"]], "unverified": leaves[path] is None}
        else:
            refs[path] = {"sources": [], "unverified": True}
    return {
        "values": refs,
        "sources": [
            {key: evidence[ds][key] for key in ("id", "dataset", "params", "retrieved_at")}
            for ds in datasets
            if evidence[ds]["used"]
        ],
        "unverified": any(item["unverified"] for item in refs.values()),
    }


# --------------------------------------------------------------------------- #
# 对外
# --------------------------------------------------------------------------- #

def build_snapshot(
    entry: IndustryEntry,
    sources: dict[str, _Source],
    catalog: IndustryCatalog,
    *,
    in_catalog: bool = True,
) -> dict[str, Any]:
    """按已读好的缓存快照算一个行业（`industry list` 复用同一批读取）。"""
    windows = catalog.window
    map_source = sources["sw_industry_map"]
    codes, membership_as_of = (
        ([], None) if not entry.bound else constituents_of(map_source.records, entry.l1_code or "")
    )
    unbound_reason: str | None = None
    if not in_catalog:
        unbound_reason = f"行业 {entry.name!r} 不在 config/industry.yaml 清单里（不猜近似名）"
    elif not entry.bound:
        unbound_reason = (
            "config/industry.yaml 未绑定申万一级代码"
            f"（{entry.note or '锚点归属不一致'}），成分派生维度全部显式 null，不猜码"
        )

    industry_block = {
        "name": entry.name,
        "l1_code": entry.l1_code,
        "caliber": catalog.membership_caliber,
        "in_catalog": in_catalog,
        "bound": entry.bound,
        "anchors": list(entry.anchors),
        "board_name": entry.board_name,
        "enabled": entry.enabled,
        "note": entry.note or None,
        "catalog_provenance": catalog.provenance or None,
    }
    membership_reason = unbound_reason
    if membership_reason is None and not codes:
        membership_reason = NO_MEMBERSHIP_CACHE
    membership_block = {
        "constituent_count": len(codes),
        "classified_codes": len(current_membership(map_source.records)),
        "as_of": membership_as_of,
        "reason": membership_reason,
    }

    if entry.bound:
        dimensions = {
            "prosperity": _prosperity(sources["finance_indicators"].records, codes),
            "capital": _capital(
                sources["fund_flow"].records,
                sources["industry_fund_flow"].records,
                codes,
                fund_days=catalog.window_days("fund_days"),
                board_name=entry.board_name,
            ),
            "valuation": _valuation(
                sources["valuation_history"].records,
                codes,
                lookback_days=catalog.window_days("valuation_lookback_days"),
            ),
            "momentum": _momentum(
                sources["daily_k"].records, codes, windows=list(catalog.window["momentum_windows"])
            ),
            "crowding": _crowding(
                sources["valuation_history"].records,
                sources["fund_flow"].records,
                codes,
                turnover_lookback_days=catalog.window_days("turnover_lookback_days"),
                fund_days=catalog.window_days("fund_days"),
            ),
        }
    else:
        dimensions = {
            name: _dimension_skeleton(name, windows, unbound_reason) for name in DIMENSIONS
        }

    payload: dict[str, Any] = {
        "available": False,
        "industry": industry_block,
        "membership": membership_block,
        "dimensions": dimensions,
        "windows": windows,
        "formulas": FORMULAS,
        "datasets": _dataset_status(sources),
        "caliber_policy": CALIBER_POLICY,
    }
    return _finalize(payload, sources, catalog, reason=unbound_reason)


def _dataset_status(sources: dict[str, _Source]) -> dict[str, Any]:
    """六个输入数据集本次各读到什么（空 / 行数 / 时点 / 读失败原因）。"""
    return {
        dataset: {
            "records": len(sources[dataset].records),
            "as_of": sources[dataset].as_of(),
            "retrieved_at": sources[dataset].retrieved_at(),
            "error": sources[dataset].error,
        }
        for dataset in SNAPSHOT_DATASETS
    }


def _finalize(
    payload: dict[str, Any],
    sources: dict[str, _Source],
    catalog: IndustryCatalog,
    *,
    reason: str | None = None,
) -> dict[str, Any]:
    """补上溯源与可用性汇总——`available` 只看承诺的指标字段，不看说明文字。"""
    payload["provenance"] = _provenance(payload, sources, catalog)

    paths = metric_paths(catalog.window)
    values = {path: _leaf_value(payload["dimensions"], path) for path in paths}
    present = [path for path in paths if values[path] is not None]
    missing = [path for path in paths if values[path] is None]
    dimensions = payload["dimensions"]
    unavailable = [name for name in DIMENSIONS if not dimensions[name].get("available")]
    payload["available"] = bool(present)
    payload["metrics_present"] = sorted(present)
    payload["missing"] = sorted(missing)
    payload["unavailable_dimensions"] = unavailable
    payload["data_source_status"] = (
        "ok" if not unavailable else ("partial" if present else "missing")
    )
    payload["reason"] = reason or (
        None if present else "六个输入数据集在本机缓存里都没有可用记录，五维全部显式 null"
    )
    payload["source_detail"] = _source_detail(sources)
    return payload


def _leaf_value(payload: dict[str, Any], path: str) -> Any:
    node: Any = payload
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def metric_projection(dimensions: dict[str, Any], windows: dict[str, Any]) -> dict[str, Any]:
    """逐行业指标投影：`metric_paths()` 的键 → 快照里的叶子值（R9 / QUOT-276 缺口 #1）。

    键集合与 `metric_paths()` 完全同名同序，**值缺失也保留键并显式给 `null`**——
    前端把「省略键」当解析失败，把 `null` 当「无数据」（SPEC-INDUSTRY-PAGES §2.1）。
    这里只是把 `build_snapshot()` 已经算出的叶子摊平，不新算任何一个数。
    """
    return {path: _leaf_value(dimensions, path) for path in metric_paths(windows)}


def latest_day_per_industry(
    records: list[dict[str, Any]], membership: dict[str, str]
) -> dict[str, str]:
    """每个申万一级代码在该数据集里**自己**的最新数据时点（逐行业，不用全局最大值顶替）。

    行业 A 的资金流停在三天前时，A 那一行的时点就该是三天前——缓存全局最大值会让它
    看起来像有最新数据（SPEC-INDUSTRY-PAGES §2.1「不冒充请求日期」的同一条底线）。
    """
    latest: dict[str, str] = {}
    for row in records:
        l1_code = membership.get(_code(row))
        day = _day(row)
        if l1_code and day and day > latest.get(l1_code, ""):
            latest[l1_code] = day
    return latest


def metric_as_of(
    paths: list[str], days_by_dataset: dict[str, dict[str, str]], l1_code: str | None
) -> dict[str, str | None]:
    """指标路径 → 该行业该字段的数据时点；结构性缺口（北向 / 指数涨跌幅）永远 `None`。

    数据集归属沿用 `_source_for()`，与溯源段同一个映射，不再建第二份字段→数据集表。
    """
    out: dict[str, str | None] = {}
    for path in paths:
        dataset = _source_for(path)
        out[path] = days_by_dataset.get(dataset or "", {}).get(l1_code or "") or None
    return out


def dimension_as_of(path_days: dict[str, str | None]) -> dict[str, str | None]:
    """维度时点 = 该维度用到的字段时点里最新的一个；一个都没有才是 `None`。"""
    out: dict[str, str | None] = {name: None for name in DIMENSIONS}
    for path, day in path_days.items():
        name = path.split(".", 1)[0]
        if day and day > (out.get(name) or ""):
            out[name] = day
    return out


def _source_detail(sources: dict[str, _Source]) -> str:
    parts = []
    for dataset in SNAPSHOT_DATASETS:
        source = sources[dataset]
        state = "空" if not source.records else (source.error or f"{len(source.records)} 行")
        parts.append(f"{dataset}={state}")
    return "本地缓存回放：" + "，".join(parts) + "（零外呼）"


def industry_snapshot(
    industry: str,
    *,
    root: Path | None = None,
    config_root: Path | None = None,
    catalog: IndustryCatalog | None = None,
) -> dict[str, Any]:
    """一个申万一级行业的五维快照（只读本机缓存）。

    `industry` 未出现在清单里 → `available=false` + 候选名称，退出码由 CLI 决定（6）。
    快照没有 `--date` 口径：五维读的是「本机缓存里的最新一批」，各维度自己给出
    它的 `trade_date` / `as_of`，不拿请求日期冒充数据日期。
    """
    active = catalog or load_catalog(config_root)
    entry = active.find(industry)
    reader = _Reader(root)
    sources = {dataset: reader.source(dataset) for dataset in SNAPSHOT_DATASETS}
    payload = (
        _unknown_industry(str(industry or "").strip(), active, sources)
        if entry is None
        else build_snapshot(entry, sources, active)
    )
    payload["requested"] = {"industry": str(industry or "").strip()}
    return payload


def _unknown_industry(
    name: str, catalog: IndustryCatalog, sources: dict[str, _Source]
) -> dict[str, Any]:
    """不在清单里 → 快照结构照旧铺满，只有 reason 换成「不在清单」，另附候选名称。

    复用 `build_snapshot` 是为了让下游按同一种 schema 读任何名称的输出，
    而不是「名字打错时字段少一半」。
    """
    placeholder = IndustryEntry(name=name, l1_code=None)
    payload = build_snapshot(placeholder, sources, catalog, in_catalog=False)
    payload["candidates"] = catalog.names()
    return payload


def industry_list(
    *,
    root: Path | None = None,
    config_root: Path | None = None,
    catalog: IndustryCatalog | None = None,
    with_metrics: bool = False,
) -> dict[str, Any]:
    """清单 + 每个行业的可用性摘要（同一批缓存只读一次，逐行业可复算）。

    `with_metrics=True` 时每个行业行多带三键（R9 / QUOT-276 缺口 #1）：`metrics`
    （`metric_paths()` 同名投影）、`as_of`（逐维数据时点）、`metric_as_of`（逐字段数据时点）。
    默认关——`ldst industry list` 与 MCP 出口的载荷一个键都不变，值也全部来自
    同一次 `build_snapshot()`，不是第二套计算。
    """
    active = catalog or load_catalog(config_root)
    reader = _Reader(root)
    sources = {dataset: reader.source(dataset) for dataset in SNAPSHOT_DATASETS}
    paths = metric_paths(active.window)
    days_by_dataset: dict[str, dict[str, str]] = {}
    if with_metrics:
        membership = current_membership(sources["sw_industry_map"].records)
        days_by_dataset = {
            dataset: latest_day_per_industry(sources[dataset].records, membership)
            for dataset in SNAPSHOT_DATASETS
            if dataset != "sw_industry_map"
        }
    rows = []
    for entry in active.enabled_entries():
        payload = build_snapshot(entry, sources, active)
        row: dict[str, Any] = {
            "name": entry.name,
            "l1_code": entry.l1_code,
            "bound": entry.bound,
            "enabled": entry.enabled,
            "available": payload["available"],
            "constituent_count": payload["membership"]["constituent_count"],
            "dimensions_available": {
                name: bool(payload["dimensions"][name].get("available")) for name in DIMENSIONS
            },
            "data_source_status": payload["data_source_status"],
            "reason": payload["reason"],
        }
        if with_metrics:
            path_days = metric_as_of(paths, days_by_dataset, entry.l1_code)
            row["metrics"] = metric_projection(payload["dimensions"], active.window)
            row["metric_as_of"] = path_days
            row["as_of"] = dimension_as_of(path_days)
        rows.append(row)
    result: dict[str, Any] = {
        "available": any(row["available"] for row in rows),
        "caliber": active.membership_caliber,
        "windows": active.window,
        "count": len(rows),
        "industries": rows,
        "formulas": FORMULAS,
        "dimensions": list(DIMENSIONS),
        "labels": DIMENSION_LABELS,
        "datasets": _dataset_status(sources),
        "source_detail": _source_detail(sources),
        "reason": None
        if any(row["available"] for row in rows)
        else "六个输入数据集在本机缓存里都没有可用记录（行业清单本身可读，指标全部显式 null）",
    }
    if with_metrics:
        # 顶层时点 = 所有行业该维度时点里最新的一个（不是缓存全局最大值，也不是请求日期）
        top: dict[str, str | None] = {}
        for name in DIMENSIONS:
            days = [str(row["as_of"][name]) for row in rows if row["as_of"].get(name)]
            top[name] = max(days) if days else None
        result["as_of"] = top
    return result


__all__ = [
    "CALIBER_BOARD_PREFIX",
    "CALIBER_POOL",
    "CALIBER_SW",
    "CATALOG_SOURCE_DATASET",
    "CATALOG_SOURCE_ID",
    "DIMENSIONS",
    "DIMENSION_LABELS",
    "FORMULAS",
    "META_PRIMARY_DATASET",
    "METRIC_FIELDS",
    "SNAPSHOT_DATASETS",
    "build_snapshot",
    "constituents_of",
    "current_membership",
    "dimension_as_of",
    "industry_list",
    "industry_snapshot",
    "latest_day_per_industry",
    "metric_as_of",
    "metric_fields",
    "metric_paths",
    "metric_projection",
]
