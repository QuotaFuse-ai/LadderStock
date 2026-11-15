"""QUOT-616：问答取数接线的单元测试。

不联网：`run_fetch` 用假 registry 注入，验证 Provenance 形状、ChatSource 形状、
失败降级文案与闲聊零调用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from ladderstock.web import qa_fetch
from ladderstock.web.qa_fetch import (
    FetchPlan,
    build_plan,
    extract_symbol,
    metrics_from_outcome,
    run_fetch,
    select_dataset,
)


@dataclass
class _Outcome:
    payload: dict[str, Any]
    data_as_of: Any = None

    @property
    def envelope(self) -> Any:
        return self


@dataclass
class _FakeRegistry:
    outcome: Any = None
    exc: Exception | None = None
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def fetch_live(self, dataset: str, *, params: dict[str, Any], allow_cache: bool, timeout: float) -> Any:
        self.calls.append((dataset, dict(params)))
        if self.exc is not None:
            raise self.exc
        return self.outcome


# ---------------------------------------------------------------- extract_symbol
def test_extract_symbol_prefixed() -> None:
    assert extract_symbol("SH600000 现在多少钱") == "600000"
    assert extract_symbol("sz000001 跌了吗") == "000001"


def test_extract_symbol_bare_passes_whitelist() -> None:
    assert extract_symbol("600519 的最新价") == "600519"


def test_extract_symbol_rejects_non_code_numbers() -> None:
    # 123456 不在 A 股前缀白名单里，不能当标的
    assert extract_symbol("预算 123456 元怎么花") is None
    assert extract_symbol("今天大盘怎么样") is None


# ---------------------------------------------------------------- select_dataset
def test_select_dataset_market_level_words() -> None:
    assert select_dataset("今天有多少涨停", None)[0] == "limit_pool"
    assert select_dataset("炸板率高吗", None)[0] == "broken_board"
    assert select_dataset("今天市场情绪温度如何", None)[0] == "sentiment"


def test_select_dataset_symbol_missing_gives_none() -> None:
    assert select_dataset("帮我看看估值", None) == (None, {}, None)


def test_select_dataset_kline_words() -> None:
    dataset, params, _ = select_dataset("600519 最近走势怎么样", "600519")
    assert dataset == "kline_chart"
    assert params["code"] == "600519"
    assert params["limit"] == qa_fetch.DEFAULT_KLINE_LIMIT


def test_select_dataset_default_spot_quote() -> None:
    dataset, params, _ = select_dataset("600519 现在多少钱", "600519")
    assert dataset == "spot_quote"
    assert params["codes"] == "sh600519"
    assert params["max_codes"] == 1


# ---------------------------------------------------------------- build_plan
def test_build_plan_chitchat_never_fetches() -> None:
    plan = build_plan("你好", "chitchat")
    assert plan.dataset is None
    assert plan.symbol is None


def test_build_plan_data_query_without_symbol_reports_gap() -> None:
    plan = build_plan("这只票多少钱", "data_query")
    assert plan.dataset is None
    assert plan.gap == "未识别到具体标的"


def test_build_plan_uses_context_symbol() -> None:
    plan = build_plan("最新价多少", "data_query", context_symbol="600519")
    assert plan.dataset == "spot_quote"
    assert plan.params["codes"] == "sh600519"


# ---------------------------------------------------------------- metrics_from_outcome
def _quote_outcome() -> _Outcome:
    return _Outcome(
        payload={
            "records": [
                {
                    "code": "sh600519",
                    "name": "贵州茅台",
                    "price": 1520.5,
                    "change_pct": "1.20%",
                    "prev_close": 1502.4,
                    "open": 1510.0,
                    "high": 1530.0,
                    "low": 1508.0,
                    "timestamp": "2026-11-14 15:00:03",
                }
            ]
        }
    )


def test_metrics_spot_quote_provenance_shape() -> None:
    metrics = metrics_from_outcome(_quote_outcome(), "spot_quote", "600519")
    close = metrics["close"]
    assert close["value"] == 1520.5
    assert close["unit"] == "元"
    assert close["source"] == "tickhub-cn"
    assert close["as_of"] == "2026-11-14"
    assert close["endpoint"]  # tool 名不为空
    assert close["verified"] is True
    assert metrics["change_pct"]["unit"] == "%"


def test_metrics_kline_uses_last_row() -> None:
    outcome = _Outcome(
        payload={
            "records": [
                {"date": "2026-11-12", "close": 1500.0},
                {"date": "2026-11-13", "close": 1515.2, "open": 1502.0},
            ]
        }
    )
    metrics = metrics_from_outcome(outcome, "kline_chart", "600519")
    assert metrics["close"]["value"] == 1515.2
    assert metrics["close"]["as_of"] == "2026-11-13"
    assert "change_pct" not in metrics


def test_metrics_limit_pool_counts_rows() -> None:
    outcome = _Outcome(
        payload={"records": [{"trade_date": "2026-11-14", "code": "600519"}, {"trade_date": "2026-11-14", "code": "000001"}]}
    )
    metrics = metrics_from_outcome(outcome, "limit_pool", None)
    assert metrics["limit_up_count"]["value"] == 2.0
    assert metrics["limit_up_count"]["as_of"] == "2026-11-14"


def test_metrics_empty_rows_give_empty_dict() -> None:
    assert metrics_from_outcome(_Outcome(payload={"records": []}), "spot_quote", "600519") == {}


def test_as_of_normalizes_tencent_compact_timestamp() -> None:
    metrics = metrics_from_outcome(_quote_outcome(), "spot_quote", "600519")
    assert metrics["close"]["as_of"] == "2026-11-14"


def test_as_of_compact_digits_becomes_iso() -> None:
    outcome = _Outcome(
        payload={"records": [{"code": "sh600519", "price": 1.0, "timestamp": "20261113161444"}]}
    )
    metrics = metrics_from_outcome(outcome, "spot_quote", "600519")
    assert metrics["close"]["as_of"] == "2026-11-13"


# ---------------------------------------------------------------- run_fetch
def test_run_fetch_no_dataset_zero_calls() -> None:
    plan = FetchPlan()
    metrics, sources = run_fetch(plan)
    assert metrics == {} and sources == []


def test_run_fetch_success(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRegistry(outcome=_quote_outcome())
    monkeypatch.setattr(
        "ladderstock.data.provider_registry.ProviderRegistry", lambda root=None: fake
    )
    plan = build_plan("600519 现在多少钱", "data_query")
    metrics, sources = run_fetch(plan)
    assert metrics["close"]["value"] == 1520.5
    assert len(sources) == 1
    assert sources[0]["name"].startswith("tickhub")
    assert sources[0]["symbol"] == "600519"
    assert sources[0]["as_of"] == "2026-11-14"
    assert plan.source_failures == []


def test_run_fetch_upstream_failure_names_the_source(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Exc(Exception):
        message = "连接超时"
        source = "tencent_quote_batch"

    fake = _FakeRegistry(exc=_Exc())
    monkeypatch.setattr(
        "ladderstock.data.provider_registry.ProviderRegistry", lambda root=None: fake
    )
    plan = build_plan("600519 现在多少钱", "data_query")
    metrics, sources = run_fetch(plan)
    assert metrics == {} and sources == []
    assert plan.source_failures == ["tencent_quote_batch: 连接超时"]


def test_run_fetch_no_rows_records_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeRegistry(outcome=_Outcome(payload={"records": []}))
    monkeypatch.setattr(
        "ladderstock.data.provider_registry.ProviderRegistry", lambda root=None: fake
    )
    plan = build_plan("600519 现在多少钱", "data_query")
    metrics, sources = run_fetch(plan)
    assert metrics == {} and sources == []
    assert plan.source_failures == ["spot_quote: 上游没有返回可用的记录行"]
