"""本机缓存工具「按个股代码取数」的回归（资金流答错事故）。

事故原样：用户问「茅台资金流向」，回答正文说「这一项没有取到」，下面却挂出
000002 / 000333 / 000651 等**别的股票**三个月前的行。

两层根因，这里逐条钉死：

1. `_cached_tool` 调 `_read` 时不传 `code`，工具参数表里也没有 code —— 模型
   根本无法按股票查，`kind=stock` 只是选了一张全市场表；
2. `_latest_day` 取的是**整张表**的最新交易日。别的票当天更新了、目标票没更新，
   按全表最新日过滤后目标票一行不剩，于是把别人的行当成了答案。

所以本文件的核心断言不是「能查到」，而是**「查不到时绝不能返回别人的行」**。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from ladderstock.ai.agent_runtime import build_tools
from ladderstock.data import duckdb_store


@pytest.fixture()
def tools(tmp_path: Path) -> dict[str, Any]:
    return {spec.name: spec for spec in build_tools(tmp_path)}


def _seed_split_days(tmp_path: Path) -> None:
    """构造事故现场：600519 停在 11-18，600000 更新到 11-19。

    全表最新日 = 11-19，其中**只有 600000**。旧实现在这一天里找不到 600519，
    就把 600000（或前 N 行的任意票）当成了茅台的资金流。
    """
    duckdb_store.write_dataset(
        "fund_flow",
        [
            {
                "code": "600519",
                "name": "贵州茅台",
                "trade_date": "2026-11-18",
                "main_net": 71497106.81,
                "main_pct": 1.7459,
                "change_pct": -0.734,
            },
            {
                "code": "600000",
                "name": "浦发银行",
                "trade_date": "2026-11-19",
                "main_net": 123.0,
                "main_pct": 0.5,
                "change_pct": 0.1,
            },
        ],
        source="test:fund_flow",
        root=tmp_path,
    )


def test_asking_one_stock_never_returns_another(tmp_path: Path, tools: dict[str, Any]) -> None:
    """事故回归：全表最新日不含目标票时，必须回目标票自己的最新日，不含他人。"""
    _seed_split_days(tmp_path)
    result = tools["fund_flow"].invoke({"kind": "stock", "code": "600519"})

    payload = json.loads(result.text)
    assert payload["code"] == "600519"
    assert payload["as_of"] == "2026-11-18"
    codes = {row["code"] for row in payload["rows"]}
    assert codes == {"600519"}, f"返回了别人的行：{codes}"
    assert payload["rows"][0]["main_net"] == pytest.approx(71497106.81)

    table_codes = {row[0] for row in result.metrics["table"]["rows"]}
    assert table_codes == {"600519"}


def test_missing_stock_says_so_and_shows_no_table(tmp_path: Path, tools: dict[str, Any]) -> None:
    """缓存里没有这只票 → 明说没有，且**不挂任何表**（不得回退展示他人）。"""
    _seed_split_days(tmp_path)
    result = tools["fund_flow"].invoke({"kind": "stock", "code": "000001"})

    assert "未取到" in result.text
    assert "000001" in result.text
    assert result.metrics is None
    assert result.sources is None


def test_without_code_keeps_ranking_semantics(tmp_path: Path, tools: dict[str, Any]) -> None:
    """不传 code 仍是「全市场排行」口径，按全表最新日取——旧行为不能回归。"""
    _seed_split_days(tmp_path)
    result = tools["fund_flow"].invoke({"kind": "stock"})

    payload = json.loads(result.text)
    assert payload["as_of"] == "2026-11-19"
    assert {row["code"] for row in payload["rows"]} == {"600000"}
    assert "code" not in payload


def test_industry_kind_ignores_code(tmp_path: Path, tools: dict[str, Any]) -> None:
    """行业资金流表不是个股维度：给了 code 也不按它过滤，避免误报「查不到」。"""
    duckdb_store.write_dataset(
        "industry_fund_flow",
        [
            {
                "code": "new_dlhy",
                "name": "电力行业",
                "trade_date": "2026-11-19",
                "main_net": 1.0,
                "main_pct": 0.2,
                "change_pct": 0.3,
            }
        ],
        source="test:industry_fund_flow",
        root=tmp_path,
    )
    result = tools["fund_flow"].invoke({"kind": "industry", "code": "600519"})

    payload = json.loads(result.text)
    assert {row["code"] for row in payload["rows"]} == {"new_dlhy"}
    assert "code" not in payload


@pytest.mark.parametrize("raw", ["600519", "600519.SH", "sh600519", " 600519 "])
def test_code_normalization(tmp_path: Path, tools: dict[str, Any], raw: str) -> None:
    """带交易所后缀 / 前缀 / 空格都要落到同一个 6 位代码。"""
    _seed_split_days(tmp_path)
    result = tools["fund_flow"].invoke({"kind": "stock", "code": raw})
    assert json.loads(result.text)["code"] == "600519"


def test_illegal_code_is_rejected_without_query(tmp_path: Path, tools: dict[str, Any]) -> None:
    """非法代码直接报错，不静默当成「没传」而返回全市场排行。"""
    _seed_split_days(tmp_path)
    result = tools["fund_flow"].invoke({"kind": "stock", "code": "茅台"})

    assert "不是合法的 6 位证券代码" in result.text
    assert result.metrics is None


def test_stale_cache_is_flagged_in_caption(tmp_path: Path, tools: dict[str, Any]) -> None:
    """数据日明显滞后时，表头必须写出来，不能与实时价混排而不作说明。"""
    duckdb_store.write_dataset(
        "fund_flow",
        [{"code": "600519", "trade_date": "2026-08-19", "main_net": 1.0}],
        source="test:fund_flow",
        root=tmp_path,
    )
    result = tools["fund_flow"].invoke({"kind": "stock", "code": "600519"})

    caption = result.metrics["table"]["caption"]
    assert "2026-08-19" in caption
    assert "已滞后" in caption
    assert json.loads(result.text)["stale_days"] > 3


def test_fresh_cache_has_no_stale_warning(tmp_path: Path, tools: dict[str, Any]) -> None:
    """正常休市范围内的数据日不加滞后噪声。"""
    today = date.today().isoformat()
    duckdb_store.write_dataset(
        "fund_flow",
        [{"code": "600519", "trade_date": today, "main_net": 1.0}],
        source="test:fund_flow",
        root=tmp_path,
    )
    result = tools["fund_flow"].invoke({"kind": "stock", "code": "600519"})
    assert "已滞后" not in result.metrics["table"]["caption"]


@pytest.mark.parametrize("name", ["fund_flow", "dragon_tiger", "hot_rank"])
def test_stock_tools_expose_code_param(tools: dict[str, Any], name: str) -> None:
    """个股维度的工具必须把 code 暴露给模型，否则它无从按票查。"""
    assert "code" in tools[name].params_schema["properties"]


@pytest.mark.parametrize("name", ["board_rank", "news_flash"])
def test_non_stock_tools_do_not_expose_code(tools: dict[str, Any], name: str) -> None:
    """板块 / 快讯不是个股维度，不该凭空多出一个 code 入参。"""
    assert "code" not in tools[name].params_schema["properties"]


def test_hot_rank_by_code(tmp_path: Path, tools: dict[str, Any]) -> None:
    """热度榜同样支持按票查，且不得串票。"""
    duckdb_store.write_dataset(
        "hot_rank",
        [
            {"code": "600519", "name": "贵州茅台", "trade_date": "2026-11-18", "rank": 9},
            {"code": "600000", "name": "浦发银行", "trade_date": "2026-11-19", "rank": 1},
        ],
        source="test:hot_rank",
        root=tmp_path,
    )
    result = tools["hot_rank"].invoke({"code": "600519"})
    payload = json.loads(result.text)
    assert payload["as_of"] == "2026-11-18"
    assert {row["code"] for row in payload["rows"]} == {"600519"}


# ---------------------------------------------------------------- 名称补全
# 2026-11-19 实测：`fund_flow` 的 `name` 列上游给的是 null（600519 一行 name 为空），
# 表格「名称」整列显示「—」。名字只能从本机标的字典（`SymbolPlane`，6193 条）补。


def _seed_name_dictionary(tmp_path: Path) -> None:
    """造一份本机标的字典；真实环境里它来自 `symbol_basic` 等带名称的数据集。"""
    duckdb_store.write_dataset(
        "symbol_basic",
        [{"code": "600519", "name": "贵州茅台", "market": "SH", "trade_date": "2026-11-19"}],
        source="test:symbol_basic",
        root=tmp_path,
    )


def test_name_is_backfilled_from_local_symbol_dictionary(
    tmp_path: Path, tools: dict[str, Any]
) -> None:
    """上游 name 为空时，表格里的「名称」要从本机字典补上，不能整列空。"""
    duckdb_store.write_dataset(
        "fund_flow",
        [
            {
                "code": "600519",
                "name": None,
                "trade_date": "2026-11-19",
                "main_net": 71497106.81,
                "main_pct": 1.7459,
                "change_pct": -0.734,
            }
        ],
        source="test:fund_flow",
        root=tmp_path,
    )
    _seed_name_dictionary(tmp_path)

    result = tools["fund_flow"].invoke({"kind": "stock", "code": "600519"})

    row = result.metrics["table"]["rows"][0]
    assert row[0] == "600519"
    assert row[1] == "贵州茅台", f"名称没补上：{row}"


def test_upstream_name_is_not_overwritten_by_dictionary(
    tmp_path: Path, tools: dict[str, Any]
) -> None:
    """上游自己给了名字就以它为准：字典里可能是简称或旧名。"""
    duckdb_store.write_dataset(
        "fund_flow",
        [
            {
                "code": "600519",
                "name": "上游口径名",
                "trade_date": "2026-11-19",
                "main_net": 1.0,
            }
        ],
        source="test:fund_flow",
        root=tmp_path,
    )
    _seed_name_dictionary(tmp_path)

    result = tools["fund_flow"].invoke({"kind": "stock", "code": "600519"})
    assert result.metrics["table"]["rows"][0][1] == "上游口径名"


def test_name_stays_empty_when_dictionary_has_no_entry(
    tmp_path: Path, tools: dict[str, Any]
) -> None:
    """字典里没有这只票就留空（前端显示「—」），不许凭空编名字。"""
    duckdb_store.write_dataset(
        "fund_flow",
        [{"code": "601999", "name": None, "trade_date": "2026-11-19", "main_net": 1.0}],
        source="test:fund_flow",
        root=tmp_path,
    )
    _seed_name_dictionary(tmp_path)

    result = tools["fund_flow"].invoke({"kind": "stock", "code": "601999"})
    assert result.metrics["table"]["rows"][0][1] is None
