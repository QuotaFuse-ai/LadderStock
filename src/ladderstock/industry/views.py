"""R9 只读投影层：把 R5 快照补成产业三接口需要的形状（QUOT-276，SPEC-INDUSTRY-PAGES §7）。

一句话：**只补投影与判定，不改口径、不新增采集**。

- **缺口 #3 `history`**：R5 只在内部把「每日中位数序列」压成一个当期分位就丢掉，这里把
  那条序列的逐日滚动分位铺出来。用的是同一批缓存行（`snapshot._Reader`）、同一批公式
  （`industry.metrics`），所以序列最后一个点必然等于载荷里的当期分位——契约测试钉住这一点。
- **缺口 #4 `constituents`**：R5 只给 `constituent_count`，这里按成分股逐股摊开
  估值 / 报表 / 资金三个数据集的最新一行；任一字段缓存里没有就显式 `null`。
- **缺口 #7 逐日 `main_net`**：R5 只有 N 日合计，这里给同一个 N 日窗口的逐日序列，
  两把键沿用 R5 的策略（齐了才给 `main_net`，部分样本只进 `main_net_sampled`）。
- **缺口 #6 规则信号**：判定口径按 DEC-028 §2.1 放后端，规则只消费 `metric_paths()`
  里已有的字段；`null` 不参与判定，证据链不完整的规则整条不出（不展示半条证据）。

这里是 `industry/snapshot.py` 的同包内部投影层，直接复用它的行级小工具（`_by_code` /
`_daily_median` / `_latest_rows` …）与 `_source_for()` 的字段→数据集映射——
宁可跨模块引用包内私有函数，也不复制第二套口径（D-07）。

不做的事：不新增数据集、不新增指标、不插值、不补 0、不改 R5 载荷的任何一个键。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ladderstock.core.ladder import as_float, pick
from ladderstock.industry import metrics, snapshot
from ladderstock.industry.catalog import IndustryCatalog

#: 动量序列的点数上限：估值 / 换手的分位序列受窗口约束，动量窗口本身不设上限，
#: 这里单独收口（页面只画 sparkline，更多点不改变结论，只放大响应体）。
MOMENTUM_POINTS_MAX = 60

#: 序列点：`{"date": "YYYY-MM-DD", "value": number}`（算不出来的日子不给点，不补 null）
Point = dict[str, Any]

NO_CONSTITUENT_CODES = "本行业没有成分代码（归属未绑定或缓存为空），逐股明细与日频序列都不估算"


# --------------------------------------------------------------------------- #
# 一次缓存读取 + 三种投影
# --------------------------------------------------------------------------- #

class CacheProjection:
    """六个输入数据集的一次读取，供日频序列 / 逐股明细 / 逐日资金流使用。

    读法与 R5 装配层完全一致（`snapshot._Reader`：Parquet 优先 → DuckDB 兜底，零外呼），
    读失败的数据集进 `errors`，调用方据此降级成空序列而不是 500。
    """

    def __init__(self, catalog: IndustryCatalog, root: Path | None = None) -> None:
        reader = snapshot._Reader(root)
        self.catalog = catalog
        self.sources: dict[str, snapshot._Source] = {
            dataset: reader.source(dataset) for dataset in snapshot.SNAPSHOT_DATASETS
        }
        self.membership = snapshot.current_membership(self.sources["sw_industry_map"].records)

    @property
    def errors(self) -> list[str]:
        """本次读不动的数据集（`_Reader` 会把异常收进 `error`，不让整条链路崩）。"""
        return [
            f"{dataset}：{source.error}"
            for dataset, source in self.sources.items()
            if source.error
        ]

    def codes_for(self, l1_code: str | None) -> list[str]:
        """该申万一级代码的成分股，与 R5 同一条归属读法。"""
        if not l1_code:
            return []
        codes, _as_of = snapshot.constituents_of(self.sources["sw_industry_map"].records, l1_code)
        return codes

    def _rows(self, dataset: str, codes: set[str]) -> list[dict[str, Any]]:
        return [row for row in self.sources[dataset].records if snapshot._code(row) in codes]

    # ---------------------------------------------------------------- 缺口 #3

    def history(self, codes: list[str]) -> dict[str, list[Point]]:
        """当期分位所用的日频序列：`pe_percentile` / `pb_percentile` / `turnover_percentile`
        + 每个动量窗口的 `change_pct_{n}d`。

        第 i 个点的窗口是「截至第 i 日的最近 lookback 个交易日」，所以最后一个点与
        R5 当期值同一个算法、同一个数；早于窗口的数据不参与，也不回头补。
        """
        code_set = set(codes)
        history: dict[str, list[Point]] = {}
        valuation = self._rows("valuation_history", code_set)
        digits = metrics.DIGITS_RATIO
        for key, keys, lookback in (
            (
                "pe_percentile",
                snapshot.PE_KEYS,
                self.catalog.window_days("valuation_lookback_days"),
            ),
            (
                "pb_percentile",
                snapshot.PB_KEYS,
                self.catalog.window_days("valuation_lookback_days"),
            ),
            (
                "turnover_percentile",
                snapshot.TURNOVER_KEYS,
                self.catalog.window_days("turnover_lookback_days"),
            ),
        ):
            daily = snapshot._daily_median(valuation, code_set, keys, digits=digits)
            history[key] = _rolling_percentile(daily, lookback)
        windows = [int(item) for item in self.catalog.window.get("momentum_windows") or []]
        history.update(self._momentum_history(code_set, windows))
        return history

    def _momentum_history(self, code_set: set[str], windows: list[int]) -> dict[str, list[Point]]:
        """逐日等权涨跌幅：每个截止日只用「当日及以前」的收盘价跑同一个 `equal_weight_change`。"""
        grouped = snapshot._by_code(self._rows("daily_k", code_set))
        series: dict[str, tuple[list[str], list[float]]] = {}
        for code in sorted(grouped):
            stamps: list[str] = []
            closes: list[float] = []
            for row in grouped[code]:
                value = as_float(pick(row, snapshot.CLOSE_KEYS))
                if value is None:
                    continue
                stamps.append(snapshot._day(row))
                closes.append(value)
            series[code] = (stamps, closes)
        timeline = sorted({day for stamps, _ in series.values() for day in stamps})
        points = timeline[-MOMENTUM_POINTS_MAX:]
        counts = {code: _counts_through(stamps, timeline) for code, (stamps, _) in series.items()}
        out: dict[str, list[Point]] = {f"change_pct_{window}d": [] for window in windows}
        for window in windows:
            series_out: list[Point] = []
            for day in points:
                prefix = {
                    code: values[: counts[code][day]] for code, (_s, values) in series.items()
                }
                value, _used = metrics.equal_weight_change(prefix, window)
                if value is not None:
                    series_out.append({"date": day, "value": value})
            out[f"change_pct_{window}d"] = series_out
        return out

    # ---------------------------------------------------------------- 缺口 #4

    def constituent_rows(self, codes: list[str]) -> list[dict[str, Any]]:
        """成分股逐股快照：估值 / 报表 / 日线各取该股最新一行，N 日主力净流入按股权合计。

        `main_net_nd` 与 R5 的行业合计同一门槛：窗口内缺一天的样本不冒充全样本，给 `null`。
        """
        code_set = {code for code in codes if code}
        valuation = snapshot._latest_rows(self._rows("valuation_history", code_set))
        finance = snapshot._latest_rows(self._rows("finance_indicators", code_set))
        bars = snapshot._latest_rows(self._rows("daily_k", code_set))
        fund_grouped = snapshot._by_code(self._rows("fund_flow", code_set))
        window = self._fund_window()
        rows: list[dict[str, Any]] = []
        for code in sorted(code_set):
            latest = valuation.get(code)
            report = finance.get(code)
            bar = bars.get(code)
            rows_for_code = fund_grouped.get(code, [])
            day_values = [
                as_float(pick(row, snapshot.MAIN_NET_KEYS))
                if (row := snapshot._on_day(rows_for_code, day)) is not None
                else None
                for day in window
            ]
            revenue = as_float(pick(report, snapshot.REVENUE_YOY_KEYS)) if report else None
            profit = as_float(pick(report, snapshot.PROFIT_YOY_KEYS)) if report else None
            rows.append(
                {
                    "code": code,
                    "name": _stock_name(latest, report, bar),
                    "pe_ttm": as_float(pick(latest, snapshot.PE_KEYS)) if latest else None,
                    "pb": as_float(pick(latest, snapshot.PB_KEYS)) if latest else None,
                    "turnover_rate": (
                        as_float(pick(latest, snapshot.TURNOVER_KEYS)) if latest else None
                    ),
                    "revenue_yoy": revenue,
                    "profit_yoy": profit,
                    "main_net_nd": metrics.sum_values(day_values),
                    "as_of": _max_day(*(_day_of(item) for item in (latest, report, bar))),
                }
            )
        return rows

    # ---------------------------------------------------------------- 缺口 #7

    def main_net_daily(self, codes: list[str]) -> list[dict[str, Any]]:
        """逐日主力净流入（R5 只有 N 日合计，「连续 N 日」规则等这条序列）。

        与 R5 的行业合计同一个 N 日窗口：`main_net` 只在当日全成分齐了才给，
        已取到部分的和放 `main_net_sampled`，`codes_used` / `codes_expected` 说清差多少。
        """
        code_set = {code for code in codes if code}
        grouped = snapshot._by_code(self._rows("fund_flow", code_set))
        expected = len(code_set)
        out: list[dict[str, Any]] = []
        for day in self._fund_window():
            values: list[float] = []
            for code in sorted(code_set):
                row = snapshot._on_day(grouped.get(code, []), day)
                value = as_float(pick(row, snapshot.MAIN_NET_KEYS)) if row else None
                if value is not None:
                    values.append(value)
            summed = metrics.sum_values(values) if values else None
            complete = bool(expected) and len(values) == expected
            out.append(
                {
                    "date": day,
                    "main_net": summed if complete else None,
                    "main_net_sampled": summed,
                    "codes_used": len(values),
                    "codes_expected": expected,
                    "complete": complete,
                }
            )
        return out

    def _fund_window(self) -> list[str]:
        """资金流的 N 日窗口，取法与 `snapshot._capital_constituents` 一致（同一批交易日）。"""
        fund_days = self.catalog.window_days("fund_days")
        days = snapshot._days_of(self.sources["fund_flow"].records)
        return days[-fund_days:] if fund_days > 0 else days


# --------------------------------------------------------------------------- #
# 序列小工具
# --------------------------------------------------------------------------- #

def _rolling_percentile(daily: dict[str, float], lookback_days: int) -> list[Point]:
    """每日序列的滚动经验分位：分位算法与保留位数都和 R5 当期分位同一个。"""
    days, values = metrics.window_series(daily, lookback_days=lookback_days)
    out: list[Point] = []
    for index, (day, value) in enumerate(zip(days, values, strict=False)):
        start = max(0, index - lookback_days + 1)
        percentile = metrics.percentile_rank(values[start : index + 1], value)
        if percentile is not None:
            out.append({"date": day, "value": percentile})
    return out


def _counts_through(stamps: list[str], timeline: list[str]) -> dict[str, int]:
    """每个截止日为止已有多少根序列值（升序时间线，一次线性扫描）。"""
    at: dict[str, int] = {}
    index = 0
    for day in timeline:
        while index < len(stamps) and stamps[index] <= day:
            index += 1
        at[day] = index
    return at


def _day_of(row: dict[str, Any] | None) -> str | None:
    return snapshot._day(row) if row else None


def _stock_name(*rows: dict[str, Any] | None) -> str | None:
    """逐股名称：缓存里哪个数据集带名字就用哪个，都没有就 `null`（不从外部补）。

    候选键沿用 R5 的 `NAME_KEYS`，不另建一张别名表。
    """
    for row in rows:
        if row is None:
            continue
        found = str(pick(row, snapshot.NAME_KEYS) or "").strip()
        if found:
            return found
    return None


def _max_day(*days: str | None) -> str | None:
    present = [day for day in days if day]
    return max(present) if present else None


# --------------------------------------------------------------------------- #
# 缺口 #6：规则信号
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Predicate:
    """规则里的一个条件：字段路径 + 比较符 + 阈值，加展示用的标签、单位与取值口径。

    `label` 里的 `{fund_days}` / `{valuation_lookback_days}` / `{turnover_lookback_days}`
    由快照的窗口参数填充，窗口改了文案跟着改，不会写出与数据不符的「近 5 日」。
    """

    path: str
    label: str
    comparator: str
    threshold: float
    unit: str
    fmt: str

    def render(self, windows: dict[str, Any]) -> tuple[str, str]:
        """`(标签, 阈值文本)`：整数阈值写成 `0` / `25`，小数阈值固定两位（`0.30`）。"""
        label = self.label
        for key, value in windows.items():
            label = label.replace(f"{{{key}}}", str(value))
        return label, _threshold_text(self.threshold)


@dataclass(frozen=True)
class SignalRule:
    """一条规则：全部条件都满足才出信号（`null` 条件 → 整条不出，不把缺失当 0）。"""

    id: str
    title: str
    direction: str
    severity: str
    predicates: tuple[Predicate, ...]

    def expression(self, windows: dict[str, Any]) -> str:
        parts = [
            f"{item.path} {item.comparator} {item.render(windows)[1]}" for item in self.predicates
        ]
        return " 且 ".join(parts)

    def view(self, windows: dict[str, Any]) -> dict[str, Any]:
        """SPEC §2.3 `rules[]` 的一项（四键，一个不多一个不少）。"""
        return {
            "id": self.id,
            "title": self.title,
            "direction": self.direction,
            "expression": self.expression(windows),
        }


#: 四条规则：阈值与表达式照 SPEC-INDUSTRY-PAGES §2.3 定稿，改阈值要升 SPEC 版本（DEC-028 §3）
SIGNAL_RULES: tuple[SignalRule, ...] = (
    SignalRule(
        id="capital_valuation",
        title="资金流入 + 估值低位",
        direction="bullish",
        severity="info",
        predicates=(
            Predicate(
                path="capital.constituents.main_net_inflow_nd",
                label="近 {fund_days} 日主力净流入",
                comparator=">",
                threshold=0.0,
                unit="元",
                fmt="money",
            ),
            Predicate(
                path="valuation.pe_percentile",
                label="PE 近 {valuation_lookback_days} 日分位",
                comparator="<",
                threshold=0.30,
                unit="ratio",
                fmt="ratio",
            ),
        ),
    ),
    SignalRule(
        id="prosperity_valuation",
        title="景气向上 + 估值低位",
        direction="bullish",
        severity="info",
        predicates=(
            Predicate(
                path="prosperity.revenue_yoy_median",
                label="营收同比中位数",
                comparator=">",
                threshold=0.0,
                unit="%",
                fmt="percent",
            ),
            Predicate(
                path="prosperity.profit_yoy_median",
                label="净利润同比中位数",
                comparator=">",
                threshold=0.0,
                unit="%",
                fmt="percent",
            ),
            Predicate(
                path="valuation.pe_percentile",
                label="PE 近 {valuation_lookback_days} 日分位",
                comparator="<",
                threshold=0.30,
                unit="ratio",
                fmt="ratio",
            ),
        ),
    ),
    SignalRule(
        id="momentum_crowding",
        title="动量转强 + 拥挤度不高",
        direction="bullish",
        severity="info",
        predicates=(
            Predicate(
                path="momentum.change_pct_5d",
                label="5 日等权涨跌幅",
                comparator=">",
                threshold=0.0,
                unit="%",
                fmt="percent",
            ),
            Predicate(
                path="momentum.change_pct_20d",
                label="20 日等权涨跌幅",
                comparator=">",
                threshold=0.0,
                unit="%",
                fmt="percent",
            ),
            Predicate(
                path="crowding.turnover_percentile",
                label="换手率近 {turnover_lookback_days} 日分位",
                comparator="<",
                threshold=0.70,
                unit="ratio",
                fmt="ratio",
            ),
        ),
    ),
    SignalRule(
        id="crowding_risk",
        title="拥挤度高位（风险）",
        direction="bearish",
        severity="warn",
        predicates=(
            Predicate(
                path="crowding.turnover_percentile",
                label="换手率近 {turnover_lookback_days} 日分位",
                comparator=">",
                threshold=0.80,
                unit="ratio",
                fmt="ratio",
            ),
            Predicate(
                path="crowding.fund_concentration_pct",
                label="主力净流入集中度",
                comparator=">",
                threshold=25.0,
                unit="%",
                fmt="percent",
            ),
        ),
    ),
)

#: 规则 id → 规则（`rule` 查询参数的合法值域，非法即 422）
RULES_BY_ID: dict[str, SignalRule] = {rule.id: rule for rule in SIGNAL_RULES}

#: 比较符表：写错规则立刻炸，不让一条错规则静默放行
_COMPARATORS: dict[str, Any] = {
    ">": lambda value, threshold: value > threshold,
    ">=": lambda value, threshold: value >= threshold,
    "<": lambda value, threshold: value < threshold,
    "<=": lambda value, threshold: value <= threshold,
}


def rule_ids() -> list[str]:
    return [rule.id for rule in SIGNAL_RULES]


def _threshold_text(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.2f}"


def _format_value(value: float, fmt: str) -> str:
    """摘要里的数字写法（**只改写法不改值**，证据行永远带原值）。

    `ratio` 是分位（0–1）→ 乘 100 写成百分数；`percent` 本身就是百分数 → 不再乘。
    `money` 是元 → 按亿 / 万收档位。
    """
    if fmt == "money":
        if abs(value) >= 1e8:
            return f"{value / 1e8:.2f} 亿"
        if abs(value) >= 1e4:
            return f"{value / 1e4:.2f} 万"
        return f"{value:.2f} 元"
    if fmt == "ratio":
        return f"{value * 100:.2f}%"
    return f"{value:.2f}%"


def evaluate_signals(
    payload: dict[str, Any],
    *,
    industry: str | None = None,
    rule_id: str | None = None,
) -> dict[str, Any]:
    """`industry_list(with_metrics=True)` 的载荷 → SPEC §2.3 的 `signals` 载荷。

    判定只消费 `metrics` 里已有的字段：值为 `null`、或时点 / 数据集取不到时，
    **整条信号不出**（宁缺不半），数量并进 `reason` 说明，不让前端丢证据才发现。
    """
    windows = dict(payload.get("windows") or {})
    rows = [row for row in payload.get("industries") or [] if isinstance(row, dict)]
    if industry is not None:
        rows = [row for row in rows if str(row.get("name") or "") == industry]
    selected = [rule for rule in SIGNAL_RULES if rule_id is None or rule.id == rule_id]

    signals: list[dict[str, Any]] = []
    incomplete = 0
    for rule in selected:
        for row in rows:
            evidence, passed_all, has_gap = _rule_outcome(rule, row, windows)
            if has_gap:
                incomplete += 1
                continue
            if passed_all:
                signals.append(_signal_view(rule, row, evidence))
    signals.sort(key=lambda item: (str(item["industry"]), str(item["id"])))

    by_rule: dict[str, int] = {rule.id: 0 for rule in selected}
    for item in signals:
        key = str(item["rule_id"])
        by_rule[key] = by_rule.get(key, 0) + 1
    counts = {
        "total": len(signals),
        "bullish": sum(1 for item in signals if item["direction"] == "bullish"),
        "bearish": sum(1 for item in signals if item["direction"] == "bearish"),
        "by_rule": by_rule,
    }

    as_of = (
        _max_day(*[str(item["as_of"]) for item in signals])
        if signals
        else _payload_as_of(payload, rows)
    )
    return {
        "available": bool(payload.get("available")),
        "as_of": as_of,
        "rules": [rule.view(windows) for rule in SIGNAL_RULES],
        "signals": signals,
        "counts": counts,
        "reason": _signals_reason(payload, signals, incomplete, industry, rule_id),
    }


def _rule_outcome(
    rule: SignalRule, row: dict[str, Any], windows: dict[str, Any]
) -> tuple[list[dict[str, Any]], bool, bool]:
    """一条规则对一个行业的判定结果：`(证据行, 条件全部成立, 有字段取不到)`。

    「取不到」= 值不是数、或数据时点 / 所属数据集给不出来。这种整条信号不出，
    而不是出一条缺项的证据（前端遇到缺项会丢整条，等于把判定错误推给展示层）。
    """
    values = row.get("metrics") or {}
    days = row.get("metric_as_of") or {}
    evidence: list[dict[str, Any]] = []
    passed_all = True
    gap = False
    for predicate in rule.predicates:
        value = values.get(predicate.path)
        number = _evidence_number(value)
        day = days.get(predicate.path)
        dataset = snapshot._source_for(predicate.path)
        if number is None or not day or not dataset:
            gap = True
            continue
        passed = _comparator_passes(predicate, number)
        passed_all = passed_all and passed
        evidence.append(
            {
                "path": predicate.path,
                "label": predicate.render(windows)[0],
                "value": value,
                "unit": predicate.unit,
                "as_of": str(day),
                "dataset": dataset,
                "comparator": predicate.comparator,
                "threshold": predicate.threshold,
                "passed": passed,
            }
        )
    return evidence, passed_all, gap


def _evidence_number(value: Any) -> float | None:
    """证据里的值必须是数（`bool` 不算），否则返回 None 让调用方判「取不到」。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _comparator_passes(predicate: Predicate, value: float) -> bool:
    compare = _COMPARATORS.get(predicate.comparator)
    if compare is None:
        raise ValueError(f"规则 {predicate.path} 用了未知比较符 {predicate.comparator!r}")
    return bool(compare(value, predicate.threshold))


def _signal_view(
    rule: SignalRule, row: dict[str, Any], evidence: list[dict[str, Any]]
) -> dict[str, Any]:
    name = str(row.get("name") or "")
    parts: list[str] = []
    for item in evidence:
        fmt = _fmt_of(rule, str(item["path"]))
        value = _format_value(float(item["value"]), fmt)
        threshold = _format_threshold(float(item["threshold"]), fmt)
        parts.append(f"{item['label']} {value}（{item['comparator']} {threshold}）")
    return {
        "id": f"sig-{rule.id}-{name}",
        "rule_id": rule.id,
        "rule_title": rule.title,
        "industry": name,
        "direction": rule.direction,
        "severity": rule.severity,
        "as_of": max(str(item["as_of"]) for item in evidence),
        "summary": "，".join(parts),
        "evidence": evidence,
    }


def _fmt_of(rule: SignalRule, path: str) -> str:
    for predicate in rule.predicates:
        if predicate.path == path:
            return predicate.fmt
    return "percent"


def _format_threshold(value: float, fmt: str) -> str:
    """摘要里的阈值：跟着字段口径写，`25.00%（< 0.30）` 这种「值是百分数、阈值是小数」的读法不收。

    `expression` 里仍是原始阈值（SPEC §2.3 的写法），证据行也仍是原始值——只有摘要换写法。
    """
    if fmt == "ratio":
        return f"{value * 100:.2f}%"
    if fmt == "money":
        return _format_value(value, "money")
    return f"{value:.2f}%"


def _payload_as_of(payload: dict[str, Any], rows: list[dict[str, Any]]) -> str | None:
    """没有信号时的数据时点：被评估行业里最新的字段时点，再退到清单顶层时点。"""
    days = [
        str(day) for row in rows for day in (row.get("metric_as_of") or {}).values() if day
    ]
    if not days:
        days = [str(day) for day in dict(payload.get("as_of") or {}).values() if day]
    return max(days) if days else None


def _signals_reason(
    payload: dict[str, Any],
    signals: list[dict[str, Any]],
    incomplete: int,
    industry: str | None,
    rule_id: str | None,
) -> str | None:
    if not payload.get("available"):
        return str(payload.get("reason") or "本机缓存里没有可用的行业指标")
    parts: list[str] = []
    names = [str(row.get("name") or "") for row in payload.get("industries") or []]
    if industry is not None and industry not in names:
        parts.append(f"行业 {industry!r} 不在 config/industry.yaml 清单里（不猜近似名）")
    if incomplete:
        parts.append(f"{incomplete} 次「规则 × 行业」判定因字段取不到而跳过（不把缺失当 0）")
    if not signals and not parts:
        parts.append("当前清单里没有规则全部条件都满足的行业（`null` 字段不参与判定）")
    return "；".join(parts) if parts else None


__all__ = [
    "MOMENTUM_POINTS_MAX",
    "NO_CONSTITUENT_CODES",
    "RULES_BY_ID",
    "SIGNAL_RULES",
    "CacheProjection",
    "Predicate",
    "SignalRule",
    "evaluate_signals",
    "rule_ids",
]
