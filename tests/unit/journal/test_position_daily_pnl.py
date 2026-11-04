"""持仓当日盈亏的投影规则（QUOT-380 拍板口径 4 / 5，DEC-030 只投影不算）。

`compute_metrics()` 拿到的是 `core.quotes.daily_change_map()` 算好的「每股差价」，
这里只乘股数：**不在这里再算一遍 close − prev_close**，也算不出来的就是 `null` + 原因。
"""

from __future__ import annotations

from typing import Any

from ladderstock.journal import position as position_module


def _row(code: str, cost: float, qty: float) -> dict[str, Any]:
    return {
        "id": 1,
        "code": code,
        "cost": cost,
        "qty": qty,
        "entry_date": "2026-10-20",
        "industry": "",
        "note": "",
        "updated_at": "2026-10-29T09:00:00+08:00",
    }


def _ok(close: float, prev: float) -> dict[str, Any]:
    return {
        "status": "ok",
        "close": close,
        "prev_close": prev,
        "change": round(close - prev, 6),
        "reason": None,
    }


ROWS = [_row("600000", 10.0, 1000.0), _row("000001", 11.0, 500.0)]


def test_daily_pnl_is_change_times_qty_for_every_position() -> None:
    metrics = position_module.compute_metrics(
        ROWS,
        {"600000": 12.0, "000001": 10.0},
        daily={"600000": _ok(12.0, 11.5), "000001": _ok(10.0, 10.4)},
    )

    by_code = {item["code"]: item for item in metrics["items"]}
    assert by_code["600000"]["daily_pnl"] == 500.0
    assert by_code["600000"]["daily_pnl_status"] == "ok"
    assert by_code["600000"]["daily_pnl_reason"] is None
    assert by_code["000001"]["daily_pnl"] == -200.0

    valuation = metrics["valuation"]
    assert valuation["daily_pnl"] == 300.0
    assert valuation["daily_pnl_status"] == "ok"
    assert valuation["daily_pnl_missing"] == []
    # 口径 5：只做人民币单一汇总，不建「代码 → 市场 → 币种 → 汇率」那套模型
    assert valuation["currency"] == "CNY"


def test_projection_only_never_recomputes_the_spread() -> None:
    """DEC-030：`change` 用入参给的那一份，不在这儿拿 close − prev_close 重算。"""
    metrics = position_module.compute_metrics(
        [_row("600000", 10.0, 100.0)],
        {"600000": 12.0},
        daily={"600000": {"status": "ok", "close": 12.0, "prev_close": 11.5, "change": 9.9}},
    )

    assert metrics["items"][0]["daily_pnl"] == 990.0


def test_zero_change_is_a_real_number_not_a_fallback() -> None:
    metrics = position_module.compute_metrics(
        [_row("600000", 10.0, 100.0)],
        {"600000": 12.0},
        daily={"600000": _ok(12.0, 12.0)},
    )

    item = metrics["items"][0]
    assert item["daily_pnl"] == 0.0
    assert item["daily_pnl_status"] == "ok"
    assert metrics["valuation"]["daily_pnl"] == 0.0


def test_suspended_position_is_missing_and_blocks_the_total() -> None:
    """停牌：缓存里没有 T 日行 → `missing` + 原因；半截总数**不给**，缺席逐条列出来。"""
    daily = {
        "600000": _ok(12.0, 11.5),
        "000001": {
            "status": "missing",
            "close": None,
            "prev_close": 10.4,
            "change": None,
            "reason": "daily_k 在 2026-10-26 没有 000001 的行（停牌或未同步）",
        },
    }
    metrics = position_module.compute_metrics(ROWS, {"600000": 12.0}, daily=daily)

    by_code = {item["code"]: item for item in metrics["items"]}
    assert by_code["000001"]["daily_pnl"] is None
    assert by_code["000001"]["daily_pnl_status"] == "missing"
    assert "000001" in by_code["000001"]["daily_pnl_reason"]

    valuation = metrics["valuation"]
    assert valuation["daily_pnl"] is None
    assert valuation["daily_pnl_status"] == "partial"
    assert valuation["daily_pnl_missing"] == [
        {"code": "000001", "reason": daily["000001"]["reason"]}
    ]


def test_missing_entry_without_a_reason_still_says_why() -> None:
    metrics = position_module.compute_metrics(
        [_row("600000", 10.0, 100.0)],
        {"600000": 12.0},
        daily={"600000": {"status": "missing", "change": None}},
    )

    item = metrics["items"][0]
    assert item["daily_pnl"] is None
    assert item["daily_pnl_status"] == "missing"
    assert item["daily_pnl_reason"]


def test_code_absent_from_the_map_is_missing_not_not_computed() -> None:
    metrics = position_module.compute_metrics(
        ROWS, {}, daily={"600000": _ok(12.0, 11.5)}
    )

    by_code = {item["code"]: item for item in metrics["items"]}
    assert by_code["600000"]["daily_pnl_status"] == "ok"
    assert by_code["000001"]["daily_pnl_status"] == "missing"
    assert metrics["valuation"]["daily_pnl_status"] == "partial"


def test_not_computed_is_a_different_thing_from_missing() -> None:
    """`daily=None` = 这次没算（老调用方 / 单测），与「算了但缓存没有」不能共用一个词。"""
    metrics = position_module.compute_metrics(ROWS, {"600000": 12.0, "000001": 10.0})

    assert {item["daily_pnl_status"] for item in metrics["items"]} == {"not_computed"}
    assert all(item["daily_pnl"] is None for item in metrics["items"])
    assert metrics["valuation"]["daily_pnl"] is None
    assert metrics["valuation"]["daily_pnl_status"] == "not_computed"
    assert metrics["valuation"]["daily_pnl_missing"] == []


def test_empty_map_is_still_computed() -> None:
    """`daily={}` = 算过了、一只都没算出来：状态是 `missing`，不是 `not_computed`。"""
    metrics = position_module.compute_metrics(ROWS, {}, daily={})

    assert {item["daily_pnl_status"] for item in metrics["items"]} == {"missing"}
    assert metrics["valuation"]["daily_pnl_status"] == "missing"


def test_additive_fields_do_not_change_the_existing_shape() -> None:
    """只加字段：既有键与既有数字一个都不动（DEC-049 §2.3 / QUOT-380 约束）。"""
    baseline = position_module.compute_metrics(ROWS, {"600000": 12.0, "000001": 10.0})
    with_daily = position_module.compute_metrics(
        ROWS,
        {"600000": 12.0, "000001": 10.0},
        daily={"600000": _ok(12.0, 11.5), "000001": _ok(10.0, 10.4)},
    )

    def _strip(items: list[dict]) -> dict:
        return {
            item["code"]: {k: v for k, v in item.items() if not k.startswith("daily_")}
            for item in items
        }

    assert _strip(with_daily["items"]) == _strip(baseline["items"])

    valuation = dict(with_daily["valuation"])
    baseline_valuation = dict(baseline["valuation"])
    for key in ("daily_pnl", "daily_pnl_status", "currency", "daily_pnl_missing"):
        valuation.pop(key)
        baseline_valuation.pop(key)
    assert valuation == baseline_valuation
    assert with_daily["price_source"] == baseline["price_source"] == "daily_k"
