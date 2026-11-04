"""TickHub-CN 返回 → `LadderEnvelope`。

TickHub-CN 的原始返回（README §envelope）不是 `DataEnvelope`，而是：

```json
{"ok": true, "data": ..., "meta": {"source": "tencent", "fetched_at": "...",
 "cache_hit": false, "confidence": "A", "trace_id": "..."}}
```

`data` 有**三种真实形状**（实测样本见 `tests/fixtures/peer_responses/`，QUOT-233）：

- **记录列表** —— `dragon_tiger` / `ths_limit_up_pool` / `yesterday_limit_up_pool` /
  `eastmoney_hot_rank`：原样成行；
- **对象 + 记录数组** —— 记录藏在 `data` 的某个键里（`tencent_kline` 的 `klines`、
  涨停池 / 炸板池 / 行业·概念板块的 `items`、`stock_fund_flow` 的 `flows`、
  `dragon_tiger_seats` 的 `records`、备源 `tdx_daily_package` 的 `rows`）：
  按 `ROW_SHAPES` 展开成行；
- **对象快照（无行概念）** —— `market_sentiment`：整包落**一行**，不硬凑。

所以这里做五件事：把 `ok/data/meta` 翻成统一信封、把 A/B/C 置信度映射为
`high/medium/low`、把 `cache_hit` 映射为 `realtime/cached`、**按工具声明的形状把对象
`data` 展开成行（`date → trade_date`，`code/period/adjust` 透传）**、
**把 `ok: false` 里的限流（`QFSD-7001`）单独识别成可退避的 `RateLimited`，其它失败照旧**——
不做任何字段填充式补救，也不用「猜哪个键是列表」：形状表里没有的工具一旦返回对象
就照快照落一行，表里声明的键缺失则直接报错（形状变了要知道，不要静默吞掉）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from ladderstock.core.boundary import owner_of
from ladderstock.core.models import Confidence, Freshness, LadderEnvelope
from ladderstock.data.duckdb_store import record_code
from ladderstock.errors import EXIT_SOURCE, LadderStockError

# TickHub 的置信度是 A/B/C，语义为数据源可靠度
CONFIDENCE_MAP: dict[str, Confidence] = {"A": "high", "B": "medium", "C": "low"}

#: 记录行里可能承载交易日的键（真实 peer 用 `date`，历史桩用 `trade_date`）
ROW_DATE_KEYS: tuple[str, ...] = ("trade_date", "date")

#: `data` 对象顶层承载「这批数据属于哪天」的键
HEADER_DATE_KEYS: tuple[str, ...] = ("date", "trade_date", "latest_date", "as_of")


# --------------------------------------------------------------------------- #
# 限流（QFSD-7001）
# --------------------------------------------------------------------------- #

#: 上游命中自身限流/熔断时的码。`retryable=true`，语义是「等一会儿再来拿同一批数据」，
#: 不是「换个工具」——同 peer 的备工具在同一个限流窗口里，换过去只是把一次等待变成两次失败。
RATE_LIMIT_CODE = "QFSD-7001"
RATE_LIMIT_NAME = "rate_limited"
#: `QF-xxxx` 是上游早期草稿的别名（`core/errors.py` 的 ALIASES），两种写法都认
_RATE_LIMIT_CODES: frozenset[str] = frozenset({RATE_LIMIT_CODE, "QF-7001"})
#: 建议等待时长的键名（真实 peer 把它放在 `error.detail` 里，单位秒）
RETRY_AFTER_KEYS: tuple[str, ...] = ("retry_after_s", "retry_after")
#: 建议时长的合理区间上限：超过它按「上游没好好说」处理，不拿它去睡
RETRY_AFTER_PLAUSIBLE_SEC = 86_400.0


class RateLimited(LadderStockError):
    """上游限流，带 peer 自己建议的等待秒数（没给就是 `None`，不猜）。

    单独成一个类型是为了让链路能**先退避再降级**：等待时长由 `provider_registry`
    按预算决定（DEC-048）。这里只负责「识别 + 把上游的建议原样带出来」。
    """

    def __init__(self, message: str, retry_after_s: float | None = None) -> None:
        super().__init__(message, EXIT_SOURCE)
        self.retry_after_s = retry_after_s


@dataclass(frozen=True)
class RowShape:
    """一个数据集在 TickHub 侧的真实记录形状。

    - `rows_key`：`data` 里承载记录的键；
    - `passthrough`：`data` 顶层要透传到每一行的字段（`code/period/adjust` 这类口径字段）；
    - `require_code`：行级必须有代码。日 K / 资金流 / 席位是**个股维度**，
      没有代码的行既无法幂等（`(code, trade_date)` 是追加模式的幂等键），
      也会污染下游天梯的 `leader_candidates`——照 QUOT-231 L6 的口径丢弃，不入库。
      板块榜（行业/概念）是**板块维度**，代码在 `board_code` 里，不适用这条规则。
    - `date_keys`：行内日期键的别名。财务指标行只有 `report_date`、申万分类行只有
      `start_date`，落库仍统一用 `trade_date` 当幂等键与水位（R5，QUOT-143）；
      不新增别名之外的兜底，取不到日期就回落到本次同步日期。
    - `rename`：**记录行里**的键改名（`(旧名, 新名)` 序列）。用在「上游把同名的
      键放了两次」的情况：`stock_concept_blocks` 的 `data.code` 是股票代码、
      `boards[].code` 是板块代码，透传 + 原样合并会让板块码盖掉股票代码
      （行就不再是个股维度的行了）。改名发生在合并之前，只作用于记录行。
    """

    rows_key: str
    passthrough: tuple[str, ...] = ()
    require_code: bool = True
    date_keys: tuple[str, ...] = ()
    rename: tuple[tuple[str, str], ...] = ()


ROW_SHAPES: dict[str, RowShape] = {
    "daily_k": RowShape("klines", passthrough=("code", "period", "adjust")),
    "limit_pool": RowShape("items"),
    "broken_board": RowShape("items"),
    "industry_board": RowShape("items", require_code=False),
    "concept_board": RowShape("items", require_code=False),
    "fund_flow": RowShape("flows", passthrough=("code", "name")),
    # 当日实时资金流（批量）：`stock_fund_flow_batch` 的 `data.flows` 是 {code: row}，
    # `normalize_records` 已按 `tencent_quote_batch` 的先例把 Mapping 摊成行（QUOT-371）。
    "fund_flow_realtime": RowShape("flows", passthrough=("code", "name")),
    "dragon_tiger_seats": RowShape("records", passthrough=("code",)),
    # R5 产业研究（QUOT-143）：三只逐股 + 一只板块资金流，形状均为实测（见
    # docs/specs/SPEC-INDUSTRY-METRICS.md §8 探针记录）。
    "sw_industry_map": RowShape("history", passthrough=("code",), date_keys=("start_date",)),
    "finance_indicators": RowShape("indicators", passthrough=("code",), date_keys=("report_date",)),
    "valuation_history": RowShape("rows", passthrough=("code",)),
    "industry_fund_flow": RowShape("items", require_code=False),
    # 备源：盘后全市场包，一天一个市场，行内自带 code/date
    "_tdx_daily_package": RowShape("rows"),
    # 盘中快照（QUOT-286）：`tencent_quote_batch` 的 `quotes` 是按 code 索引的对象，
    # 取值即记录行（见 `normalize_records` 对 Mapping 的处理）；行内只有 `timestamp`。
    "spot_quote": RowShape("quotes"),
    # 逐只归属（QUOT-362）：一行 = 「这只股票挂在这个板块」，股票维度，`require_code`。
    # `boards[].name` 改成 `board_name` 是与板块榜（`concept_board`）同一套列名，
    # `boards[].code`（东财板块码）改成 `board_code` 是给 `data.code`（股票代码）让位。
    "concept_membership": RowShape(
        "boards",
        passthrough=("code",),
        rename=(("name", "board_name"), ("code", "board_code")),
    ),
}

#: `tdx_daily_package` 是 daily_k 的备源工具，形状按工具名登记（同数据集两种形状）
_SHAPES_BY_TOOL: dict[str, RowShape] = {"tdx_daily_package": ROW_SHAPES["_tdx_daily_package"]}

# --------------------------------------------------------------------------- #
# 出参：工具入参口径（QUOT-233 第 2 步）
# --------------------------------------------------------------------------- #

#: `code` 必填的数据集（真实 schema：`tencent_kline` / `stock_fund_flow` /
#: `dragon_tiger_seats` 的 `code` 是必填项，空参实测回 `QFSD-9001 invalid_argument`）。
#: 这些数据集必须带代码池入参，不能发空参。
#: R5 的三只（申万归属 / 财务指标 / 估值历史）同样是**逐股**工具，`code` 必填
#: （QUOT-143 实测：空参被 MCP 入参校验直接拒回 `code Field required`，不是 QFSD 码）。
#: 集合内容由 `tests/contract/test_tickhub_payload_mapping.py` 逐条点名，增删必改测试。
CODE_POOL_DATASETS: frozenset[str] = frozenset(
    {
        "daily_k",
        "fund_flow",
        "dragon_tiger_seats",
        "sw_industry_map",
        "finance_indicators",
        "valuation_history",
        # QUOT-362：`stock_concept_blocks` 的 `code` 必填，且是**当日候选池那批代码**
        # 的逐只快照（上游 TTL 60 秒），不扩全市场。
        "concept_membership",
    }
)

#: 固定口径入参。`daily_k` 的口径是「日线 + 前复权」（DATASET_CATALOG 的登记），
#: 显式带上是为了让请求里的口径和落库口径是同一句话，不靠 peer 默认值。
#: `industry_fund_flow` 必须显式带 `board_type=industry`：该工具默认虽然也是行业，
#: 但概念板块与行业板块是两套榜（DEC-018 的口径标注不允许靠默认值）。
FIXED_PARAMS: dict[str, dict[str, Any]] = {
    "daily_k": {"period": "daily", "adjust": "qfq"},
    "industry_fund_flow": {"board_type": "industry", "page": 1, "page_size": 300},
}

#: 增量水位在各工具里的入参名。`tencent_kline` 没有 `since`，只有 `start`（含当日，
#: 重叠那天由 `(code, trade_date)` upsert 折叠，不会重复增长）。
#: 其余按日整批替换的数据集（涨停池 / 板块 / 情绪）本来就只回当天，没有增量入参。
SINCE_PARAM: dict[str, str] = {"daily_k": "start"}


def needs_code(dataset: str) -> bool:
    """该数据集的取数工具是否要求 `code` 入参。"""
    return dataset in CODE_POOL_DATASETS


#: 一次调用就能取多只的数据集（工具入参是 `codes` 而不是 `code`），**不**逐只扇出。
#: 目前只有当日实时资金流：`stock_fund_flow_batch` 一个请求覆盖 ≤150 只，
#: 打榜池 150 只就是 1 次上游往返，而不是 150 次（QUOT-367 验收 #1 / QUOT-371）。
BATCH_DATASETS: frozenset[str] = frozenset({"fund_flow_realtime"})


def needs_codes_batch(dataset: str) -> bool:
    """该数据集是否走「一次调用取多只」的批量路径。"""
    return dataset in BATCH_DATASETS


def tool_params(
    dataset: str,
    *,
    code: str | None = None,
    codes: Any = None,
    since: str | None = None,
) -> dict[str, Any]:
    """按工具拼真实入参：口径字段 + 单只代码 / 批量代码 + 增量起点。

    `codes` 是批量数据集（`BATCH_DATASETS`）的入参：`stock_fund_flow_batch` 收的是
    逗号分隔字符串，传列表也能被接受，但**入参形态要跟工具一致**，所以这里统一拼串。
    单只与批量互斥：同一请求里带上两者，peer 侧语义未定义，不做这种组合。
    """
    params: dict[str, Any] = dict(FIXED_PARAMS.get(dataset, {}))
    if code:
        params["code"] = code
    elif codes:
        params["codes"] = codes if isinstance(codes, str) else ",".join(str(c) for c in codes)
    since_key = SINCE_PARAM.get(dataset)
    if since and since_key:
        params[since_key] = since
    return params


def shape_for(dataset: str, tool: str) -> RowShape | None:
    """先按工具名取形状（同数据集多工具），再按数据集取。"""
    return _SHAPES_BY_TOOL.get(tool) or ROW_SHAPES.get(dataset)


def from_tool_response(
    dataset: str,
    response: dict[str, Any],
    *,
    data_as_of: date | None = None,
    topic: str | None = None,
    tool: str | None = None,
) -> LadderEnvelope:
    """把 `th-cn tool call <tool>` 的返回包成信封。

    `dataset` 必须是 `DATASET_CATALOG` 里登记过的名字——未登记的来源不允许进入。
    `tool` 是**这次真正调用的工具**（备源与主源形状不同，例如 daily_k 的
    `tencent_kline` 回 `klines`、备源 `tdx_daily_package` 回 `rows`）；
    不传就按登记的主工具解析。
    """
    binding = owner_of(dataset)
    if binding.peer != "tickhub":
        raise LadderStockError(
            f"数据集 {dataset} 归属 {binding.peer}，不能按 TickHub 返回解析", EXIT_SOURCE
        )
    actual = tool or binding.tool

    if not response.get("ok", False):
        meta_probe = response.get("meta") or {}
        data_probe = response.get("data")
        # 部分成功要能带数据过河：`stock_fund_flow_batch` 在某一批失败时会回
        # `ok=false` + `meta.partial=true`，但 `data.flows` 里仍然有另一批的结果。
        # 一律按整体失败抛，等于把取到的那半也丢了（QUOT-371 第 4 条）。
        # 只在「明确标了 partial 且确实带回了记录」时放行，其余仍然抛，不吞真失败。
        recoverable = meta_probe.get("partial") is True and isinstance(data_probe, dict) and any(
            data_probe.get(key) for key in ("records", "flows", "items", "quotes")
        )
        if not recoverable:
            raise classify_failure(actual, response)

    meta: dict[str, Any] = response.get("meta") or {}
    data = response.get("data")
    if data is None:
        raise LadderStockError(f"TickHub-CN {actual} 返回缺 data 字段", EXIT_SOURCE)

    records, payload_date = normalize_records(dataset, actual, data)

    upstream = str(meta.get("source") or "unknown")
    confidence = CONFIDENCE_MAP.get(str(meta.get("confidence") or "").upper(), "medium")
    freshness: Freshness = "cached" if meta.get("cache_hit") else "realtime"
    partial = meta.get("partial") is True
    failed_codes = meta.get("failed") or (data.get("failed") if isinstance(data, dict) else None)
    partial_note = (
        f"partial@{failed_codes}只未取到" if partial else ""
    )
    # 批量工具的「这一轮上游往返了几次」由工具自报（`data.batches` 成功 +
    # `data.failures` 失败），是老板验收「一次刷新 ≤2 个请求」唯一的取数口。
    # 不是所有工具都有这批键，没有就留 None，不猜（QUOT-371 / QUOT-374）。
    batch_requests: int | None = None
    if isinstance(data, dict) and ("batches" in data or "failures" in data):
        batch_requests = len(data.get("batches") or []) + len(data.get("failures") or [])

    fetched_at = _parse_dt(meta.get("fetched_at"))
    # 数据属于哪天以 payload 自己说的为准：入参 > 对象顶层日期 > meta 声明的日期 >
    # 行内最大日期 > 抓取时间。抓取时间只能证明「什么时候取的」，不能冒充「数据是哪天的」
    # ——列表型的涨停池（`ths_limit_up_pool`）行内没有日期，但 `meta.date` 有（YYYYMMDD）。
    as_of = (
        data_as_of
        or payload_date
        or _first_date(meta, HEADER_DATE_KEYS)
        or _max_record_date(records if isinstance(records, list) else [])
        or fetched_at.date()
    )

    return LadderEnvelope(
        domain="review",
        topic=topic or binding.dataset,
        event_id=meta.get("trace_id"),
        data_as_of=as_of,
        payload={"dataset": dataset, "tool": actual, "records": records,
                 "batch_requests": batch_requests},
        source="tickhub_cn",
        source_detail=f"{actual}@{upstream}{'/' + partial_note if partial_note else ''}",
        fetched_at=fetched_at,
        freshness=freshness,
        confidence=confidence,
        # 部分成功要显式降级，不能标成 ok —— 否则池子会以为自己拿到了完整代码池。
        source_status="degraded" if partial else "ok",
    )


def normalize_records(
    dataset: str, tool: str, data: Any
) -> tuple[Any, date | None]:
    """真实 `data` → 待落库记录（列表或快照对象）+ payload 自己声明的日期。

    - 列表：原样返回（旧行为，桩测试不受影响）；
    - 对象且登记了形状：展开 `rows_key`，透传口径字段（先按 `rename` 改记录行的键，
      避免同名字段盖掉透传值），`date → trade_date`，
      丢弃没有代码的个股行；
    - 对象但没登记形状（`market_sentiment` 这类快照）：整包返回，由 `sync` 落一行。
    """
    header_date = _first_date(data, HEADER_DATE_KEYS) if isinstance(data, dict) else None
    if isinstance(data, list):
        return data, header_date
    if not isinstance(data, dict):
        raise LadderStockError(
            f"{dataset}：TickHub 返回的 data 既不是列表也不是对象（{type(data).__name__}）",
            EXIT_SOURCE,
        )

    shape = shape_for(dataset, tool)
    if shape is None:
        return data, header_date

    raw_rows = data.get(shape.rows_key)
    if isinstance(raw_rows, Mapping):
        # `tencent_quote_batch` 的 `quotes` 是 {code: row}：键只是索引，行本身带 code。
        raw_rows = list(raw_rows.values())
    if not isinstance(raw_rows, list):
        raise LadderStockError(
            f"{dataset}：{tool} 的 data 里缺记录数组 {shape.rows_key}"
            f"（收到 {type(raw_rows).__name__}）",
            EXIT_SOURCE,
        )

    defaults = {key: data[key] for key in shape.passthrough if key in data}
    rows: list[dict[str, Any]] = []
    for item in raw_rows:
        if not isinstance(item, dict):
            continue
        row = {**defaults, **_renamed(item, shape.rename)}
        date_keys = shape.date_keys or ROW_DATE_KEYS
        if (day := _row_date(row, date_keys)) is not None:
            row["trade_date"] = day.isoformat()
        elif header_date is not None:
            row["trade_date"] = header_date.isoformat()
        if shape.require_code and not record_code(row):
            continue
        rows.append(row)
    return rows, header_date


# --------------------------------------------------------------------------- #
# 内部
# --------------------------------------------------------------------------- #

def classify_failure(tool: str, response: dict[str, Any]) -> LadderStockError:
    """`ok: false` → 异常类型。

    限流（`QFSD-7001` / `rate_limited`）单独识别成 `RateLimited`，把上游建议的等待时长
    原样带出去（QUOT-349）；其它失败保持原来的「源返回失败」一句话，不改变既有口径。
    """
    text = _failure_text(response)
    if _is_rate_limited(response):
        seconds = retry_after_s(response)
        # 原样带上上游的错误码：日志里要能认出是 QFSD-7001，而不是笼统一句「限流」
        code = str(_error_block(response).get("code") or response.get("code") or RATE_LIMIT_CODE)
        advice = "" if seconds is None else f"，上游建议 {seconds:g} 秒后再试"
        return RateLimited(
            f"TickHub-CN {tool} 限流（{code} {RATE_LIMIT_NAME}）：{text}{advice}", seconds
        )
    return LadderStockError(f"TickHub-CN {tool} 返回失败：{text}", EXIT_SOURCE)


def _error_block(response: Mapping[str, Any]) -> Mapping[str, Any]:
    error = response.get("error")
    return error if isinstance(error, Mapping) else {}


def _is_rate_limited(response: Mapping[str, Any]) -> bool:
    error = _error_block(response)
    code = str(error.get("code") or response.get("code") or "").strip().upper()
    name = str(error.get("name") or response.get("name") or "").strip().lower()
    return code in _RATE_LIMIT_CODES or name == RATE_LIMIT_NAME


def retry_after_s(response: Mapping[str, Any]) -> float | None:
    """上游建议的等待秒数：`error.detail.retry_after_s` 优先，其次 `error` 顶层。

    取不到、不是数字、为负、或大于一天的都回 `None`——「上游没好好说」和「上游说等 0 秒」
    是两件事，前者不该被当成立刻重试，后者由调用方按 `0.0` 处理。
    """
    error = _error_block(response)
    candidates: list[Any] = []
    detail = error.get("detail")
    if isinstance(detail, Mapping):
        candidates.extend(detail.get(key) for key in RETRY_AFTER_KEYS)
    candidates.extend(error.get(key) for key in RETRY_AFTER_KEYS)
    candidates.extend(response.get(key) for key in RETRY_AFTER_KEYS)
    for value in candidates:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        seconds = float(value)
        if 0.0 <= seconds <= RETRY_AFTER_PLAUSIBLE_SEC:
            return seconds
    return None


def _failure_text(response: dict[str, Any]) -> str:
    """失败信息：真实 peer 把它放在 `error.{code,name,message}`，不是顶层 `message`。

    顶层取不到会退化成「未知错误」——`QFSD-9001 missing a required argument: 'code'`
    这种一眼能定位的话就这么丢了（QUOT-233 实测）。
    """
    error = response.get("error")
    if isinstance(error, dict):
        code = str(error.get("code") or "").strip()
        message = str(error.get("message") or error.get("name") or "").strip()
        parts = [x for x in (code, message) if x]
        if parts:
            return " ".join(parts)
    return str(response.get("message") or response.get("code") or "未知错误")


def _renamed(item: dict[str, Any], rename: tuple[tuple[str, str], ...]) -> dict[str, Any]:
    """记录行的键改名（`rename` 为空时原样返回，不复制出新字典）。"""
    if not rename:
        return item
    renamed = dict(item)
    for old, new in rename:
        if old in renamed:
            renamed[new] = renamed.pop(old)
    return renamed


def _row_date(row: dict[str, Any], keys: tuple[str, ...] = ROW_DATE_KEYS) -> date | None:
    for key in keys:
        parsed = _parse_date(row.get(key))
        if parsed is not None:
            return parsed
    return None


def _first_date(data: dict[str, Any], keys: tuple[str, ...]) -> date | None:
    for key in keys:
        parsed = _parse_date(data.get(key))
        if parsed is not None:
            return parsed
    return None


def _max_record_date(records: list[dict[str, Any]]) -> date | None:
    days = [_row_date(row) for row in records if isinstance(row, dict)]
    real = [day for day in days if day is not None]
    return max(real) if real else None


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        text = value.strip()
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            if len(text) == 8 and text.isdigit():  # 真实 peer 的 YYYYMMDD
                try:
                    return date(int(text[:4]), int(text[4:6]), int(text[6:]))
                except ValueError:
                    return None
    return None


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            pass
    # 时间戳缺失时不编造，直接用当前时间并保留 fidelity 由 confidence 表达
    return datetime.now().astimezone()
