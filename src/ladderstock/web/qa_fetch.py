"""QUOT-616：问答取数接线——按意图抽标的 → 选 source → 调 peer → 填 metrics。

只做「路由 → 数据源」这段线，数据源全部来自 `config/sources.yaml` 已登记项，
经 `ProviderRegistry.fetch_live` 调用（主备切换 / 限速 / 熔断 / 缓存回放都是
registry 内建治理，本模块**不绕过 `_PeerGate`**，不新增 URL、不新增数据源）。

取不到数不编数：每个进 `metrics` 的值都是 `Provenance` 出处记录（含来源名、
`as_of`、原始字段名）；失败如实返回缺口（哪个源、什么原因），由 notice 说清楚。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from ladderstock.core.boundary import Provenance, owner_of
from ladderstock.web.research import code_exists

logger = logging.getLogger(__name__)

#: 单次提问的总取数预算：超时即降级，不再扇出（任务书步骤 3）
TOTAL_FETCH_BUDGET_SEC = 15.0

#: 行情类问题的默认日线根数
DEFAULT_KLINE_LIMIT = 60

_CODE_WITH_PREFIX_RE = re.compile(
    r"(?:SH|SZ|BJ)[:. ]?\s*(\d{6})", re.IGNORECASE
)
_BARE_CODE_RE = re.compile(r"\b(\d{6})\b")

#: 意图 → dataset 选择。key 是数据需求关键词组；标的行情感（涨跌/涨停/炸板/情绪）
#: 优先于通用行情。全部取自 sources.yaml 已登记 dataset，未登记的需求不接。
_TAPE_WORDS = ("涨停", "跌停", "连板", "炸板", "封板", "打板", "情绪")
_KLINE_WORDS = ("k线", "K线", "走势", "趋势", "历史", "涨跌", "涨了多少", "跌了多少", "行情")

#: kline 行情问题里允许的指标键（收盘/最高/最低/成交量），缺省收盘价
_KLINE_FIELD_WORDS: tuple[tuple[str, str], ...] = (
    ("最高", "high"),
    ("最低", "low"),
    ("开盘", "open"),
    ("成交量", "volume"),
    ("成交额", "amount"),
)


@dataclass
class FetchPlan:
    """一次问答的取数计划：标的 + 数据集 + 参数 + 失败时的如实说明。"""

    symbol: str | None = None
    dataset: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    #: 未识别标的 / 数据集未登记时的说明文案（诚实缺口，不是笼统「没有数据」）
    gap: str | None = None
    #: 取数失败时点名具体源（`{spec_id}`：原因）
    source_failures: list[str] = field(default_factory=list)
    #: 执行结果（`run_fetch` 之后才有值）
    fetched_metrics: dict[str, Any] = field(default_factory=dict)
    fetched_sources: list[dict[str, Any]] = field(default_factory=list)


def extract_symbol(question: str) -> str | None:
    """从问题里抽 A 股 6 位代码；抽不出返回 None（不猜名称、不走搜索）。

    优先带前缀形态（`sh600000` / `SZ000001`），其次裸 6 位。裸 6 位须过
    `code_exists` 的 A 股前缀白名单，防止把日期、金额里的数字当代码。
    """
    text = question or ""
    for match in _CODE_WITH_PREFIX_RE.finditer(text):
        code = match.group(1)
        if code_exists(code):
            return code
    for match in _BARE_CODE_RE.finditer(text):
        code = match.group(1)
        if code_exists(code):
            return code
    return None


def _prefixed_code(code: str) -> str:
    """`tencent_quote_batch` 的入参要带 `sh`/`sz` 前缀（裸 6 位会被上游误解析）。"""
    bare = code.partition(".")[0]
    if bare.startswith(("6",)):
        return f"sh{bare}"
    if bare.startswith(("0", "3")):
        return f"sz{bare}"
    return f"bj{bare}"


def select_dataset(
    question: str, symbol: str | None
) -> tuple[str | None, dict[str, Any], str | None]:
    """按问题里的需求词选已登记 dataset；返回 `(dataset, params, gap)`。

    - 涨停/炸板/情绪类：`limit_pool` / `broken_board` / `sentiment`（市场级，不依赖标的）
    - K 线 / 走势 / 价格类（有标的）：`spot_quote`（实时）或 `kline_chart`（历史）
    - 有 data_query 意图但既无标的又无市场级需求：如实给 gap，不硬猜
    """
    text = question or ""
    if "炸板" in text:
        return "broken_board", {}, None
    if any(word in text for word in ("涨停", "连板", "封板", "打板")):
        return "limit_pool", {}, None
    if "情绪" in text or "温度" in text:
        return "sentiment", {}, None
    if symbol is None:
        return None, {}, None
    field_key = next(
        (key for word, key in _KLINE_FIELD_WORDS if word in text), None
    )
    if any(word in text for word in _KLINE_WORDS):
        params: dict[str, Any] = {
            "code": symbol.partition(".")[0],
            "period": "daily",
            "adjust": "qfq",
            "limit": DEFAULT_KLINE_LIMIT,
        }
        return "kline_chart", params, None
    # 默认：实时报价
    return "spot_quote", {"codes": _prefixed_code(symbol), "max_codes": 1}, field_key


def build_plan(
    question: str,
    intent: str,
    context_symbol: str | None = None,
) -> FetchPlan:
    """意图 + 问题 → 取数计划。只有 `data_query` / `comparison` 意图才取数。

    `context_symbol`：research 路由路径里的 `{code}`，可补问题里没写的标的。
    """
    if intent not in ("data_query", "comparison"):
        return FetchPlan()
    symbol = extract_symbol(question or "") or (
        context_symbol.partition(".")[0] if context_symbol else None
    )
    dataset, params, field_key = select_dataset(question, symbol)
    if dataset is None:
        if any(word in (question or "") for word in _TAPE_WORDS):
            # 市场级需求已被上面接走，理论上到不了；防御性兜底
            return FetchPlan(symbol=None, dataset=None, gap="未识别到具体标的")
        return FetchPlan(
            symbol=symbol,
            dataset=None,
            gap="未识别到具体标的" if symbol is None else None,
        )
    if field_key:
        params = {**params, "field": field_key}
    return FetchPlan(symbol=symbol, dataset=dataset, params=params)


# --------------------------------------------------------------------------- #
# 执行：fetch_live → Provenance metrics + ChatSource sources
# --------------------------------------------------------------------------- #

_PEER_PROVENANCE_SOURCE: dict[str, str] = {
    "tickhub": "tickhub-cn",
    "tickhub_us": "tickhub-us",
    "wire": "wire",
    "senate": "senate",
}


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().rstrip("%％").replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def _rows(outcome: Any) -> list[dict[str, Any]]:
    payload = getattr(outcome.envelope, "payload", {}) or {}
    records = payload.get("records") if isinstance(payload, Mapping) else None
    return [row for row in records or [] if isinstance(row, dict)]


def _as_of_of(outcome: Any, row: Mapping[str, Any]) -> str:
    for key in ("trade_date", "timestamp", "date"):
        value = str(row.get(key) or "").strip()
        if value:
            digits = value[:14]
            if digits.isdigit() and len(digits) >= 8:
                # 上游 `20261113` / `20261113161444` → `2026-11-13`
                return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
            return value[:10] if len(value) >= 10 and value[4:5] == "-" else value
    envelope_as_of = getattr(outcome.envelope, "data_as_of", None)
    if isinstance(envelope_as_of, date):
        return envelope_as_of.isoformat()
    return ""


def _row_value(row: Mapping[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        if key in row:
            number = _num(row[key])
            if number is not None:
                return number
    return None


_QUOTE_SPECS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("close", "最新价", ("price", "close", "收盘")),
    ("change_pct", "涨跌幅", ("change_pct", "pct_chg", "涨跌幅")),
    ("prev_close", "昨收", ("prev_close", "昨收")),
    ("open", "开盘价", ("open", "开盘")),
    ("high", "最高价", ("high", "最高")),
    ("low", "最低价", ("low", "最低")),
)
_KLINE_SPECS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("close", "收盘价", ("close", "收盘")),
    ("open", "开盘价", ("open", "开盘")),
    ("high", "最高价", ("high", "最高")),
    ("low", "最低价", ("low", "最低")),
)


def metrics_from_outcome(outcome: Any, dataset: str, symbol: str | None) -> dict[str, Any]:
    """取数结果 → `build_blocks` 认的 `metrics`（每个值都是 `Provenance` dict）。

    只有白名单键会进 metric 块（`answer_blocks.METRIC_LABELS`），这里按数据集
    产出对应键；出处 `source` 用 peer 的登记名（`tickhub-cn` 等），`endpoint`
    点名工具，`as_of` 逐行带——QUOT-612 的教训：有数没 `as_of` 等于没数。
    """
    rows = _rows(outcome)
    if not rows:
        return {}
    binding = owner_of(dataset)
    source_name = _PEER_PROVENANCE_SOURCE.get(binding.peer)
    if source_name is None:
        return {}
    fetched_at = _now_iso()
    metrics: dict[str, Any] = {}
    if dataset == "spot_quote":
        bare_symbol = (symbol or "").partition(".")[0]
        row = next(
            (r for r in rows if str(r.get("code") or "").partition(".")[0] == bare_symbol),
            rows[0],
        )
        as_of = _as_of_of(outcome, row)
        for key, _label, keys in _QUOTE_SPECS:
            number = _row_value(row, keys)
            if number is None:
                continue
            unit = "%" if key == "change_pct" else "元"
            metrics[key] = Provenance(
                value=number,
                unit=unit,
                source=source_name,
                fetched_at=fetched_at,
                as_of=as_of,
                endpoint=binding.tool,
                url=None,
                verified=True,
                metric=key,
            ).as_dict()
        name = str(row.get("name") or "").strip()
        if name:
            metrics["stock_name"] = name
    elif dataset == "kline_chart":
        row = rows[-1]
        as_of = _as_of_of(outcome, row)
        for key, _label, keys in _KLINE_SPECS:
            number = _row_value(row, keys)
            if number is None:
                continue
            metrics[key] = Provenance(
                value=number,
                unit="元",
                source=source_name,
                fetched_at=fetched_at,
                as_of=as_of,
                endpoint=binding.tool,
                url=None,
                verified=True,
                metric=key,
            ).as_dict()
    elif dataset in ("limit_pool", "broken_board", "sentiment"):
        as_of = _as_of_of(outcome, rows[0])
        if dataset == "sentiment":
            for key in ("temperature", "promotion_rate", "seal_rate", "limit_up_count"):
                number = _row_value(rows[0], (key,))
                if number is None:
                    continue
                unit = "%" if key in ("promotion_rate", "seal_rate") else ""
                metrics[key] = Provenance(
                    value=number,
                    unit=unit,
                    source=source_name,
                    fetched_at=fetched_at,
                    as_of=as_of,
                    endpoint=binding.tool,
                    verified=True,
                    metric=key,
                ).as_dict()
        else:
            key = "limit_up_count" if dataset == "limit_pool" else "broken_count"
            metrics[key] = Provenance(
                value=float(len(rows)),
                unit="家",
                source=source_name,
                fetched_at=fetched_at,
                as_of=as_of,
                endpoint=binding.tool,
                verified=True,
                metric=key,
            ).as_dict()
    return metrics


def source_from_outcome(outcome: Any, dataset: str, symbol: str | None) -> dict[str, Any]:
    """取数结果 → `ChatSource` 形态（`name / fetched_at / url / symbol / as_of`）。"""
    binding = owner_of(dataset)
    peer = binding.peer
    rows = _rows(outcome)
    as_of = _as_of_of(outcome, rows[0]) if rows else ""
    return {
        "name": f"{peer} · {binding.tool}",
        "fetched_at": _now_iso(),
        "url": None,
        "symbol": symbol,
        "as_of": as_of or None,
    }


def run_fetch(
    plan: FetchPlan, *, data_root: Any = None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """执行取数计划，返回 `(metrics, sources)`。失败如实降级，不编数、不抛 500。

    单次提问总预算 `TOTAL_FETCH_BUDGET_SEC`；registry 内部按 `sources.yaml` 的
    `fallback / retries / cooldown_sec` 治理，这里只把单次 `timeout` 压到预算内。
    """
    metrics: dict[str, Any] = {}
    sources: list[dict[str, Any]] = []
    if plan.dataset is None:
        return metrics, sources
    from ladderstock.data.provider_registry import ProviderRegistry

    try:
        registry = ProviderRegistry(root=data_root)
        outcome = registry.fetch_live(
            plan.dataset,
            params=dict(plan.params),
            allow_cache=False,
            timeout=min(TOTAL_FETCH_BUDGET_SEC, 10.0),
        )
    except Exception as exc:  # noqa: BLE001 - 取数失败是缺口，不是路由 500
        detail = str(getattr(exc, "message", None) or exc) or type(exc).__name__
        source_id = getattr(exc, "source", None) or plan.dataset
        plan.source_failures.append(f"{source_id}: {detail}")
        logger.warning("问答取数失败 dataset=%s：%s", plan.dataset, detail)
        return metrics, sources
    metrics = metrics_from_outcome(outcome, plan.dataset, plan.symbol)
    if metrics:
        sources.append(source_from_outcome(outcome, plan.dataset, plan.symbol))
    else:
        plan.source_failures.append(
            f"{plan.dataset}: 上游没有返回可用的记录行"
        )
    return metrics, sources
