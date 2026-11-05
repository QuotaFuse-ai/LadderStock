"""QUOT-410：`kline_chart` 数据集的 TickHub-CN 返回映射契约。

与 daily_k 同一个工具（`tencent_kline`）、同一返回形状，唯一区别是
`period / adjust` 不再写死：它们是**运行时参数**，上游按请求把口径写在
`data.period / data.adjust` 与每行里，本层原样透传（DEC-055）。

形状登记在 `adapters/tickhub.ROW_SHAPES`，归属登记在 `core.boundary.DATASET_CATALOG`
——这里按真实 fixture 断言「改动登记后返回照样能被解成行」。
"""

from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path
from typing import Any

from ladderstock.core.boundary import owner_of
from ladderstock.data.adapters.tickhub import from_tool_response

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "peer_responses"


def load(tool_file: str) -> dict[str, Any]:
    return json.loads((FIXTURES / tool_file).read_text(encoding="utf-8"))


def test_kline_chart_is_registered_on_the_same_tool_as_tencent_kline() -> None:
    binding = owner_of("kline_chart")

    assert binding.peer == "tickhub"
    assert binding.tool == "tencent_kline"
    assert binding.fallback_tools == (), "备源留空：日线全市场包不收 period / adjust"


def test_kline_chart_expands_klines_and_passes_caliber_fields() -> None:
    """`data.klines` 里的 N 根 K 线必须变成 N 行，`code/period/adjust` 透传进每行。"""
    envelope = from_tool_response("kline_chart", load("tencent_kline.json"))
    records = envelope.payload["records"]
    klines = load("tencent_kline.json")["data"]["klines"]

    assert len(records) == len(klines) == 3
    for row, kline in zip(records, klines, strict=True):
        assert row["trade_date"] == kline["date"], "行内 date 改键为 trade_date"
        assert row["code"] == "600000"
        assert row["period"] == "daily"
        assert row["adjust"] == "qfq"
        assert row["open"] == kline["open"] and row["close"] == kline["close"]
        assert row["amount"] is None, "上游 amount 为 null 就保持 null，不补 0"


def test_kline_chart_row_caliber_follows_upstream_not_a_constant() -> None:
    """口径随请求变：上游标 weekly / hfq 时透传的就是它，不是本层写死的 daily / qfq。

    这是 `kline_chart` 与 `daily_k` 分开登记的理由（DEC-055）——同工具、同形状，
    但口径字段是运行时数据，不是登记常量。
    """
    doc = copy.deepcopy(load("tencent_kline.json"))
    doc["data"]["period"] = "weekly"
    doc["data"]["adjust"] = "hfq"

    records = from_tool_response("kline_chart", doc).payload["records"]

    assert {row["period"] for row in records} == {"weekly"}
    assert {row["adjust"] for row in records} == {"hfq"}


def test_kline_chart_envelope_fields() -> None:
    envelope = from_tool_response("kline_chart", load("tencent_kline.json"))

    assert envelope.topic == "kline_chart"
    assert envelope.source == "tickhub_cn"
    assert envelope.source_detail.startswith("tencent_kline@")
    assert envelope.data_as_of == date(2026, 10, 27), "数据日取 K 线里最后一天，不是抓取日"
    assert envelope.fetched_at.date() == date(2026, 10, 30)
    assert envelope.freshness == "realtime"
    assert envelope.confidence == "high"
