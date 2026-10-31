"""TickHub 真实返回 → 内部记录的契约（QUOT-233）。

fixture 是 `th-cn tool call` 的真实响应（脱敏 + 裁剪，见
`tests/fixtures/peer_responses/README.md`）。这里锁三件事：

1. **形状展开**：对象 `data` 里的记录数组要变成行，不能整包压成一行；
2. **口径透传**：`code/period/adjust` 这类头部字段要落到每一行；
3. **`data_as_of` 说真话**：数据属于哪天以 payload 自己声明的为准，
   不能拿 `meta.fetched_at` 冒充（QUOT-233 根因之二）。

以及出参口径：`code` 必填的工具不发空参（实测空参回 `QFSD-9001`）。
"""

from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from ladderstock.core.boundary import (
    DATASET_CATALOG,
    NEWS_CALENDAR_DATASETS,
    NEWS_STREAM_DATASETS,
    BoundaryViolation,
    owner_of,
)
from ladderstock.data.adapters.tickhub import (
    CODE_POOL_DATASETS,
    FIXED_PARAMS,
    SINCE_PARAM,
    from_tool_response,
    needs_code,
    normalize_records,
    shape_for,
    tool_params,
)
from ladderstock.data.duckdb_store import record_code, record_day
from ladderstock.errors import EXIT_SOURCE, LadderStockError

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "peer_responses"

#: 记录型数据集的 fixture：文件名 → (数据集, 工具, 行数, 数据所属日, 置信度)
RECORD_CASES: list[tuple[str, str, str, int, date, str]] = [
    ("tencent_kline.json", "daily_k", "tencent_kline", 3, date(2026, 10, 27), "high"),
    ("tdx_daily_package.json", "daily_k", "tdx_daily_package", 3, date(2026, 10, 30), "high"),
    ("limit_up_pool.json", "limit_pool", "limit_up_pool", 3, date(2026, 10, 30), "medium"),
    ("ths_limit_up_pool.json", "limit_pool", "ths_limit_up_pool", 3, date(2026, 10, 30), "low"),
    (
        "broken_board_pool.json",
        "broken_board",
        "broken_board_pool",
        3,
        date(2026, 10, 30),
        "medium",
    ),
    (
        "yesterday_limit_up_pool.json",
        "yesterday_limit_up",
        "yesterday_limit_up_pool",
        3,
        date(2026, 10, 30),
        "medium",
    ),
    ("industry_boards.json", "industry_board", "industry_boards", 3, date(2026, 10, 30), "medium"),
    ("concept_boards.json", "concept_board", "concept_boards", 3, date(2026, 10, 30), "medium"),
    ("stock_fund_flow.json", "fund_flow", "stock_fund_flow", 3, date(2026, 10, 30), "medium"),
    (
        "dragon_tiger_seats.json",
        "dragon_tiger_seats",
        "dragon_tiger_seats",
        3,
        date(2026, 10, 30),
        "medium",
    ),
    ("dragon_tiger.json", "dragon_tiger", "dragon_tiger", 3, date(2026, 10, 30), "medium"),
    ("ths_hot_rank.json", "hot_rank", "ths_hot_rank", 3, date(2026, 10, 30), "low"),
    ("eastmoney_hot_rank.json", "hot_rank", "eastmoney_hot_rank", 3, date(2026, 10, 30), "medium"),
    # ---- R5 产业研究（QUOT-143）：四只新工具的实测返回 ----
    # 申万归属的行日期是 `start_date`（分类计入日，不是抓取日），所以 `data_as_of`
    # 落在最新一次分类变更上；财务指标的行日期是 `report_date`。
    (
        "sw_industry_history.json",
        "sw_industry_map",
        "sw_industry_history",
        2,
        date(2021, 7, 30),
        "low",
    ),
    (
        "eastmoney_finance_indicators.json",
        "finance_indicators",
        "eastmoney_finance_indicators",
        3,
        date(2026, 9, 30),
        "medium",
    ),
    (
        "baostock_valuation_history.json",
        "valuation_history",
        "baostock_valuation_history",
        3,
        date(2026, 10, 30),
        "medium",
    ),
    # 板块榜行内没有日期（真实返回如此），数据日由抓取时刻承担——与板块榜同源
    (
        "board_fund_flow.json",
        "industry_fund_flow",
        "board_fund_flow",
        3,
        date(2026, 10, 31),
        "medium",
    ),
    # 盘中快照（QUOT-286）：`quotes` 是 {code: row} 对象，展开成行；
    # 行内没有交易日字段，数据日取 `fetched_at` 的日期（快照本身另有 timestamp 校验）
    (
        "tencent_quote_batch.json",
        "spot_quote",
        "tencent_quote_batch",
        3,
        date(2026, 10, 30),
        "high",
    ),
]

#: 快照型：整包一行，不硬凑记录
SNAPSHOT_CASES = [("market_sentiment.json", "sentiment", "market_sentiment", date(2026, 10, 30))]

#: 记录型三个用例共用的参数名（同一批 fixture，各测一个侧面）
_RECORD_PARAMS = "tool_file,dataset,tool,rows,as_of,confidence"
_SNAPSHOT_PARAMS = "tool_file,dataset,tool,as_of"
# 资讯类数据集走自己的解码器注册表（`news/normalize.py`，由 tests/unit/news/test_normalize.py
# 与目录逐项比对），不经下面这套「按工具形状展开」的通用映射，因此不要求 peer_responses 样本。
TICKHUB_DATASETS = sorted(
    name
    for name, b in DATASET_CATALOG.items()
    if b.peer == "tickhub" and name not in NEWS_STREAM_DATASETS + NEWS_CALENDAR_DATASETS
)


def load(tool_file: str) -> dict[str, Any]:
    return json.loads((FIXTURES / tool_file).read_text(encoding="utf-8"))


def case_ids(cases: list[Any]) -> list[str]:
    return [f"{row[1]}/{row[2]}" for row in cases]


# --------------------------------------------------------------------------- #
# fixture 覆盖率
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("dataset", TICKHUB_DATASETS)
def test_every_tickhub_dataset_has_a_real_fixture(dataset: str) -> None:
    """每个 tickhub 数据集至少有主工具的真实返回。

    形状问题只能由真实样本防住——没有 fixture 的工具等于「凭印象写的映射」。
    """
    binding = owner_of(dataset)
    candidates = [FIXTURES / f"{binding.tool}.json"] + [
        FIXTURES / f"{tool}.json" for tool in binding.fallback_tools
    ]
    assert any(path.is_file() for path in candidates), f"{dataset} 缺真实响应 fixture"


# --------------------------------------------------------------------------- #
# 记录型：展开 + 透传 + 归属日
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(_RECORD_PARAMS, RECORD_CASES, ids=case_ids(RECORD_CASES))
def test_real_response_maps_to_rows(
    tool_file: str, dataset: str, tool: str, rows: int, as_of: date, confidence: str
) -> None:
    envelope = from_tool_response(dataset, load(tool_file), tool=tool)

    records = envelope.payload["records"]
    assert isinstance(records, list)
    assert len(records) == rows
    assert envelope.data_as_of == as_of
    assert envelope.confidence == confidence
    assert envelope.source == "tickhub_cn"
    assert envelope.source_detail.startswith(f"{tool}@")
    assert envelope.source_status == "ok"
    assert envelope.topic == dataset


@pytest.mark.parametrize(_RECORD_PARAMS, RECORD_CASES, ids=case_ids(RECORD_CASES))
def test_every_row_lands_on_a_trading_day(
    tool_file: str, dataset: str, tool: str, rows: int, as_of: date, confidence: str
) -> None:
    """每一行都要能确定交易日：行内自带 > 对象头部补 > 信封的 `data_as_of` 兜住。

    记录列表型（`ths_limit_up_pool` / 热榜）上游本来不在行里给日期，日期在 `meta`，
    由 `data_as_of` 承担；落库时 `duckdb_store` 用的正是同一个兜底口径。
    """
    records = from_tool_response(dataset, load(tool_file), tool=tool).payload["records"]

    for row in records:
        day = record_day(row, as_of)
        assert day is not None, f"{tool} 的行定不出交易日：{row}"
        if record_day(row) is not None:
            assert day <= as_of, f"{tool} 的行日期 {day} 晚于数据日 {as_of}"


@pytest.mark.parametrize(_RECORD_PARAMS, RECORD_CASES, ids=case_ids(RECORD_CASES))
def test_rows_have_no_leading_empty_codes(
    tool_file: str, dataset: str, tool: str, rows: int, as_of: date, confidence: str
) -> None:
    """与 QUOT-231 L6 同口径：个股维度的行必须有代码，否则不入库。"""
    records = from_tool_response(dataset, load(tool_file), tool=tool).payload["records"]

    shape = shape_for(dataset, tool)
    for row in records:
        if shape is not None and shape.require_code:
            assert record_code(row), f"{dataset} 的 {tool} 出现无代码行：{row}"


# --------------------------------------------------------------------------- #
# daily_k：根因的两条（K 线全丢 / 日期取成抓取时间）
# --------------------------------------------------------------------------- #

def test_daily_k_expands_klines_and_passes_caliber_fields() -> None:
    """根因一：真实 `data` 是对象，`klines` 里的 N 根 K 线必须变成 N 行。

    修复前整包被压成 1 行、`code` 为空、日期取成 `fetched_at`（2026-10-30）。
    """
    envelope = from_tool_response("daily_k", load("tencent_kline.json"))
    records = envelope.payload["records"]
    klines = load("tencent_kline.json")["data"]["klines"]

    assert len(records) == len(klines)
    for row, kline in zip(records, klines, strict=True):
        assert row["trade_date"] == kline["date"]
        assert row["code"] == "600000"
        assert row["period"] == "daily"
        assert row["adjust"] == "qfq"
        assert row["close"] == kline["close"]
    # 根因二：数据日取 K 线里最后一天（2026-10-27），不是抓取日（2026-10-30）
    assert envelope.data_as_of == date(2026, 10, 27)
    assert envelope.fetched_at.date() == date(2026, 10, 30)


def test_daily_k_fallback_tool_uses_its_own_shape() -> None:
    """备源 `tdx_daily_package` 回 `rows`，不能按主源的 `klines` 解析。

    形状曾按登记的主工具查，备源一来就报「缺记录数组 klines」——
    降级链路等于没有（QUOT-233 复核时发现的真实缺陷）。
    """
    envelope = from_tool_response(
        "daily_k", load("tdx_daily_package.json"), tool="tdx_daily_package"
    )
    records = envelope.payload["records"]

    assert len(records) == 3
    assert {row["code"] for row in records} == {"204001", "204007", "511360"}
    assert all(row["trade_date"] == "2026-10-30" for row in records)
    assert envelope.source_detail.startswith("tdx_daily_package@")


def test_rows_without_code_are_dropped_not_stored() -> None:
    """头部 `code` 为空的日 K：行级代码补不出来，整批丢弃，不入库。"""
    doc = copy.deepcopy(load("tencent_kline.json"))
    doc["data"]["code"] = ""

    records, _ = normalize_records("daily_k", "tencent_kline", doc["data"])

    assert records == []


# --------------------------------------------------------------------------- #
# 快照型
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(_SNAPSHOT_PARAMS, SNAPSHOT_CASES, ids=case_ids(SNAPSHOT_CASES))
def test_snapshot_dataset_stays_one_row(
    tool_file: str, dataset: str, tool: str, as_of: date
) -> None:
    """`sentiment` 没有「行」的概念：整包一个对象落一行，嵌套 leaders 不展开。"""
    envelope = from_tool_response(dataset, load(tool_file), tool=tool)

    assert envelope.data_as_of == as_of
    assert isinstance(envelope.payload["records"], dict)
    assert envelope.payload["records"]["limit_up"] == 47


# --------------------------------------------------------------------------- #
# 失败形状：真实 peer 把错误放在 error.{code,message}
# --------------------------------------------------------------------------- #

def test_missing_required_code_raises_with_upstream_words() -> None:
    """空参调 `tencent_kline`：上游的 `QFSD-9001 missing a required argument: 'code'` 要留在报错里。

    压成「未知错误」等于让人去猜是网络问题还是入参问题。
    """
    with pytest.raises(LadderStockError) as excinfo:
        from_tool_response("daily_k", load("tencent_kline_missing_code.json"))

    assert excinfo.value.code == EXIT_SOURCE
    assert "QFSD-9001" in str(excinfo.value)
    assert "missing a required argument" in str(excinfo.value)


def test_unregistered_dataset_is_rejected() -> None:
    with pytest.raises(BoundaryViolation):
        from_tool_response("no_such_dataset", load("tencent_kline.json"))


# --------------------------------------------------------------------------- #
# 出参口径（QUOT-233 第 2 步）
# --------------------------------------------------------------------------- #

def test_code_pool_datasets_are_the_ones_requiring_code() -> None:
    assert CODE_POOL_DATASETS == frozenset(
        {
            "daily_k",
            "fund_flow",
            "dragon_tiger_seats",
            # R5 的三只同样是逐股工具（QUOT-143 实测：空参被 MCP 入参校验拒回
            # `code Field required`，不是 QFSD 码，所以照样不能发空参）
            "sw_industry_map",
            "finance_indicators",
            "valuation_history",
        }
    )
    assert set(CODE_POOL_DATASETS) == {d for d in DATASET_CATALOG if needs_code(d)}


def test_industry_board_flow_is_a_whole_board_request() -> None:
    """板块资金流是**板块维度**：不带代码，但 `board_type=industry` 必须显式给。

    该工具默认也是行业榜，可概念板块与行业板块是两套榜，DEC-018 的口径标注
    不允许靠上游默认值（QUOT-143）。
    """
    assert not needs_code("industry_fund_flow")
    assert tool_params("industry_fund_flow") == {
        "board_type": "industry",
        "page": 1,
        "page_size": 300,
    }


@pytest.mark.parametrize(
    ("dataset", "expected"),
    [
        ("daily_k", {"period": "daily", "adjust": "qfq", "code": "600000"}),
        ("fund_flow", {"code": "600000"}),
        ("dragon_tiger_seats", {"code": "600000"}),
    ],
)
def test_tool_params_carry_code_and_declared_caliber(
    dataset: str, expected: dict[str, Any]
) -> None:
    assert tool_params(dataset, code="600000") == expected
    assert FIXED_PARAMS.get(dataset, {}) == {k: v for k, v in expected.items() if k != "code"}


def test_incremental_start_uses_the_tools_real_argument_name() -> None:
    """`tencent_kline` 没有 `since`，增量起点是 `start`（含当日，靠 upsert 折叠）。"""
    assert SINCE_PARAM == {"daily_k": "start"}

    assert tool_params("daily_k", code="600000", since="2026-10-27") == {
        "period": "daily",
        "adjust": "qfq",
        "code": "600000",
        "start": "2026-10-27",
    }


def test_full_mode_sends_no_start() -> None:
    assert "start" not in tool_params("daily_k", code="600000", since=None)


def test_datasets_without_code_pool_need_no_code() -> None:
    assert not needs_code("limit_pool")
    assert tool_params("limit_pool") == {}
