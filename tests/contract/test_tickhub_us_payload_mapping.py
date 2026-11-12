"""TickHub-US 真实返回 → `LadderEnvelope` 的契约（QUOT-471 / QUOT-560）。

锁三件事，对应 `data/adapters/tickhub_us.py` 的三段说明：

1. **形状不同**：US 回的是自己的 `DataEnvelope`（`payload` 对象），没有 CN 的
   `ok/data/meta` 壳，也没有「记录数组」——整包落一行。
2. **合规字段必须落地**：`compliance_tier` 与 `warnings` 原样进信封 `payload`
   （`sina` = Tier C），`source_detail` 带出上游与分级。这是 C-2 第 3 条的修复。
3. **归属闸门**：`dataset` 不是 `tickhub_us` 归属的直接抛（DEC-006 闸门 1/2）。

行数据用 `fixtures.fake_peers.us_quote_payload`（对齐 2026-11-06 实测形状）。
"""

from __future__ import annotations

import json

import pytest
from fixtures.fake_peers import us_quote_payload

from ladderstock.core.boundary import owner_of
from ladderstock.data.adapters import from_us_tool_response
from ladderstock.errors import LadderStockError

DATASET = "index_quote_us"


def test_us_quote_lands_compliance_tier_and_warnings() -> None:
    envelope = from_us_tool_response(DATASET, us_quote_payload("DJI"))
    assert envelope.source == "tickhub_us"
    assert envelope.payload["compliance_tier"] == "C"
    assert envelope.payload["warnings"], "合规警告句不能吞"
    assert "compliance_tier=C" in envelope.source_detail
    assert "th_us_get_quote@sina" in envelope.source_detail


def test_us_quote_projects_one_row_without_conversion() -> None:
    envelope = from_us_tool_response(DATASET, us_quote_payload("IXIC", price=24707.8101))
    records = envelope.payload["records"]
    assert len(records) == 1
    row = records[0]
    assert row["symbol"] == "IXIC"
    assert row["market"] == "US"
    assert row["price"] == 24707.8101
    assert row["currency"] == "USD"


def test_us_quote_unwraps_structured_content_shell() -> None:
    """真实 FastMCP 把 `model_dump_json()` 放进 `structuredContent.result`。"""
    body = us_quote_payload("DJI")
    shell = {"structuredContent": {"result": json.dumps(body, ensure_ascii=False)}}
    envelope = from_us_tool_response(DATASET, shell)
    assert envelope.payload["records"][0]["price"] == body["payload"]["price"]


def test_us_confidence_follows_compliance_tier() -> None:
    envelope = from_us_tool_response(DATASET, us_quote_payload("DJI", compliance_tier="B"))
    assert envelope.confidence == "medium"


def test_us_adapter_rejects_non_us_dataset() -> None:
    """归属闸门：CN 数据集不能按 US 返回解析（DEC-006 闸门 1/2）。"""
    with pytest.raises(LadderStockError):
        from_us_tool_response("index_quote", us_quote_payload("DJI"))


def test_us_adapter_rejects_error_body() -> None:
    with pytest.raises(LadderStockError):
        from_us_tool_response(DATASET, {"error": {"code": "E_COMPLIANCE", "message": "拒回"}})


def test_us_dataset_is_owned_by_tickhub_us() -> None:
    assert owner_of(DATASET).peer == "tickhub_us"
    assert owner_of(DATASET).tool == "th_us_get_quote"


# --------------------------------------------------------------------------- #
# 港股指数（QUOT-560）：同一个适配器，market / 币种 / 名称按回包落地
# --------------------------------------------------------------------------- #

DATASET_HK = "index_quote_hk"


def _hk_body(symbol: str = "HSI.HK") -> dict:
    return us_quote_payload(
        symbol,
        price=24315.92, prev_close=24557.04, change=-241.12, change_pct=-0.98,
        source="tencent", market="HK", currency="HKD",
        name="恒生指数", timestamp="2026-11-11 18:31:03",
        warnings=["腾讯行情 属 Tier C：商用需事先书面许可，当前为非商用调用。"],
    )


def test_hk_dataset_is_owned_by_tickhub_us() -> None:
    assert owner_of(DATASET_HK).peer == "tickhub_us"
    assert owner_of(DATASET_HK).tool == "th_us_get_quote"


def test_hk_quote_lands_market_currency_and_name() -> None:
    envelope = from_us_tool_response(DATASET_HK, _hk_body())
    row = envelope.payload["records"][0]
    assert row["symbol"] == "HSI.HK"
    assert row["market"] == "HK"  # 来自回包 payload.market，不是缺省的 US
    assert row["currency"] == "HKD"
    assert row["name"] == "恒生指数"
    assert row["price"] == 24315.92
    assert row["prev_close"] == 24557.04
    assert row["change"] == -241.12
    assert row["change_pct"] == -0.98
    assert row["timestamp"] == "2026-11-11 18:31:03"


def test_hk_quote_lands_compliance_tier_and_warnings() -> None:
    envelope = from_us_tool_response(DATASET_HK, _hk_body("HSTECH.HK"))
    assert envelope.payload["compliance_tier"] == "C"
    assert envelope.payload["warnings"], "合规警告句不能吞"
    assert "compliance_tier=C" in envelope.source_detail
    assert "th_us_get_quote@tencent" in envelope.source_detail
    assert envelope.confidence == "low"  # Tier C → low，与美股同一映射
