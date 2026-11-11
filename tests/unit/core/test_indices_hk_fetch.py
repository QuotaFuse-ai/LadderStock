"""`core/indices.py` 港股逐 symbol 取数的单元测试（QUOT-560）。

与 `tests/unit/web/test_indices_endpoint.py` 的分工：那边钉 HTTP 契约与真实
`ProviderRegistry` 治理（含共享源冷却）；这里用桩 registry 直接钉投影层逻辑——
**单格失败只影响那一格**，不受治理层冷却干扰，块间互不连坐。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from ladderstock.core.indices import fetch_indices

CN_ROWS = [
    {
        "code": code, "market": market, "is_index": True,
        "price": 3608.1, "change": -29.86, "change_pct": -0.82, "prev_close": 3637.96,
        "timestamp": "20261103161500",
    }
    for code, market in (
        ("000001", "SH"), ("000300", "SH"), ("399001", "SZ"), ("399006", "SZ"),
    )
]
US_ROWS = [
    {
        "symbol": symbol, "price": 51570.09, "change": 47.03,
        "change_pct": 0.09, "prev_close": 51523.06, "timestamp": "2026-11-06T22:10:00+08:00",
    }
    for symbol in ("DJI", "IXIC")
]
HK_ROWS = {
    "HSI.HK": {
        "symbol": "HSI.HK", "market": "HK", "name": "恒生指数",
        "price": 24315.92, "change": -241.12, "change_pct": -0.98,
        "prev_close": 24557.04, "currency": "HKD", "timestamp": "2026-11-11 18:31:03",
    },
    "HSTECH.HK": {
        "symbol": "HSTECH.HK", "market": "HK", "name": "恒生科技指数",
        "price": 4027.06, "change": -57.39, "change_pct": -1.41,
        "prev_close": 4084.45, "currency": "HKD", "timestamp": "2026-11-11 16:08:28",
    },
}
WARNINGS = ["腾讯行情 属 Tier C：商用需事先书面许可，当前为非商用调用。"]


class _StubRegistry:
    """按数据集分派；港股可指定哪些 symbol 抛错（模拟单格失败）。"""

    def __init__(self, hk_failing: tuple[str, ...] = ()) -> None:
        self.hk_failing = set(hk_failing)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def fetch_live(self, dataset: str, *, params: dict[str, Any] | None = None, **_: Any):
        params = params or {}
        self.calls.append((dataset, params))
        if dataset == "index_quote":
            payload: dict[str, Any] = {"records": CN_ROWS}
        elif dataset == "index_quote_us":
            symbol = str(params.get("symbol") or "")
            payload = {
                "records": [row for row in US_ROWS if row["symbol"] == symbol],
                "compliance_tier": "C",
                "warnings": WARNINGS,
            }
        elif dataset == "index_quote_hk":
            symbol = str(params.get("symbol") or "")
            if symbol in self.hk_failing:
                raise RuntimeError(f"腾讯无 {symbol} 行情")
            payload = {
                "records": [HK_ROWS[symbol]] if symbol in HK_ROWS else [],
                "compliance_tier": "C",
                "warnings": WARNINGS,
            }
        else:  # pragma: no cover - 白名单外的数据集不该被请求
            raise AssertionError(f"未预期的数据集：{dataset}")
        return SimpleNamespace(envelope=SimpleNamespace(payload=payload))


def _cells(strip) -> dict[str, dict[str, Any]]:
    return {cell["code"]: cell for cell in strip.payload["cells"]}


def test_hk_single_cell_failure_is_isolated() -> None:
    registry = _StubRegistry(hk_failing=("HSI.HK",))
    strip = fetch_indices(registry)
    cells = _cells(strip)
    # 失败的那格：available=false + 带 symbol 的原因，不补 0
    assert cells["HSI"]["available"] is False
    assert cells["HSI"]["price"] is None
    assert "index_quote_hk 取数失败（HSI.HK）" in cells["HSI"]["reason"]
    # 同块另一格照常取到（投影层逐格捕获，不连坐）
    assert cells["HSTECH"]["available"] is True
    assert cells["HSTECH"]["price"] == 4027.06
    # 其余块不受影响
    assert cells["sh000001"]["available"] is True
    assert cells["DJI"]["available"] is True
    assert strip.status == "partial"


def test_hk_both_cells_fail_block_reason_joins_both() -> None:
    registry = _StubRegistry(hk_failing=("HSI.HK", "HSTECH.HK"))
    strip = fetch_indices(registry)
    cells = _cells(strip)
    assert cells["HSI"]["available"] is False
    assert cells["HSTECH"]["available"] is False
    assert cells["DJI"]["available"] is True
    # 整块原因并进 source_detail，两块失败都看得见
    assert "HSI.HK" in strip.source_detail
    assert "HSTECH.HK" in strip.source_detail


def test_hk_params_use_canonical_symbols_and_non_commercial() -> None:
    registry = _StubRegistry()
    fetch_indices(registry)
    hk_calls = [params for dataset, params in registry.calls if dataset == "index_quote_hk"]
    assert hk_calls == [
        {"symbol": "HSI.HK", "commercial_use": False},
        {"symbol": "HSTECH.HK", "commercial_use": False},
    ]


def test_hk_row_matches_by_market_and_canonical_symbol() -> None:
    registry = _StubRegistry()
    cells = _cells(fetch_indices(registry))
    assert cells["HSI"]["available"] is True
    assert cells["HSI"]["price"] == 24315.92
    assert cells["HSI"]["prev_close"] == 24557.04
    assert cells["HSI"]["as_of"] == "2026-11-11T18:31:03"
    assert cells["HSTECH"]["available"] is True


def test_hk_row_with_wrong_market_is_not_taken() -> None:
    """market 不符就不认：HK 格不能把美股行当自己的读数（匹配键含 market）。"""
    registry = _StubRegistry()
    original = registry.fetch_live

    def patched(dataset: str, *, params: dict[str, Any] | None = None, **kwargs: Any):
        outcome = original(dataset, params=params, **kwargs)
        if dataset == "index_quote_hk":
            outcome.envelope.payload["records"] = [
                dict(row, market="US") for row in outcome.envelope.payload["records"]
            ]
        return outcome

    registry.fetch_live = patched  # type: ignore[method-assign]
    cells = _cells(fetch_indices(registry))
    assert cells["HSI"]["available"] is False
    assert cells["HSTECH"]["available"] is False


def test_compliance_detail_deduplicated_across_us_and_hk() -> None:
    """美股 / 港股同一工具同一分级：source_detail 里详述只出现一次，不重复刷屏。"""
    registry = _StubRegistry()
    strip = fetch_indices(registry)
    assert strip.source_detail.count("th_us_get_quote（compliance_tier=C") == 1
    assert strip.status == "ok"
    assert strip.payload["datasets"] == ["index_quote", "index_quote_us", "index_quote_hk"]