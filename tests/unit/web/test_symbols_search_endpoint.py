"""QUOT-405：`GET /api/symbols/search`（标的字典）的契约测试。

钉住的口径：

1. 代码命中：`q=600000` → `matches[0]` 是 `600000 浦发银行`，字段对齐参考库
   （`symbol / name / market / kind / board / pinyin`）；
2. 中文名称命中：`q=茅台` → 出 `600519 贵州茅台`，名称来自本机缓存，不编造；
3. 空结果：`q=999999` → 200 + `matches=[]`，**不许出现编造的名称**；
4. 闸门：非回环 403、`Host` 不在白名单 403（照 `/api/research/*` 的形状）。

数据源盘点结论（`web/symbols.py` 模块注释）：TickHub-CN 没有代码表 / 标的检索类
工具，所以这里反查的是本机 DuckDB 缓存（测试用 `duckdb_store.write_dataset` 落
带名称字段的行）。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.data import duckdb_store
from ladderstock.web.server import create_app

LOOPBACK = ("127.0.0.1", 54321)
REMOTE = ("10.1.2.3", 54321)
DAY = date(2026, 11, 3)
STAMP = DAY.isoformat()

MATCH_KEYS = {"symbol", "name", "market", "kind", "board", "pinyin"}


def _client(root: Path, client: tuple[str, int] = LOOPBACK) -> TestClient:
    return TestClient(create_app(data_root=root, dist_dir=root / "no-dist"), client=client)


def _seed(root: Path, records: list[dict[str, Any]]) -> None:
    """往 `fund_flow` 落几行带名称的缓存（该数据集是六个名称源里覆盖最宽的）。"""
    duckdb_store.write_dataset("fund_flow", records, root=root)


def _search(root: Path, q: str, **params: Any) -> Any:
    response = _client(root).get("/api/symbols/search", params={"q": q, **params})
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# 1. 代码命中
# --------------------------------------------------------------------------- #

def test_code_query_returns_real_name(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        [{"code": "600000", "name": "浦发银行", "market": "SH", "trade_date": STAMP}],
    )
    body = _search(tmp_path, "600000")

    payload = body["payload"]
    assert payload["data_source_status"] == "ok"
    assert payload["count"] == 1
    match = payload["matches"][0]
    assert set(match) == MATCH_KEYS
    assert match["symbol"] == "600000"
    assert match["name"] == "浦发银行"
    assert match["market"] == "SH"
    assert match["kind"] == "stock"
    assert match["board"] == "主板"
    assert match["pinyin"] is None  # pyproject 没有 pypinyin：如实 null，不手写映射表
    # 信封照 data 面：data_as_of 取缓存里最新的交易日
    assert body["data_as_of"] == STAMP


def test_code_query_with_market_affix_normalizes(tmp_path: Path) -> None:
    _seed(tmp_path, [{"code": "600030", "name": "中信证券", "trade_date": STAMP}])
    body = _search(tmp_path, "600030.SH")
    assert body["payload"]["matches"][0]["symbol"] == "600030"
    # payload 没带 market 时按代码前缀推导，不编一个上游没给的值
    assert body["payload"]["matches"][0]["market"] == "SH"


# --------------------------------------------------------------------------- #
# 2. 中文名称命中
# --------------------------------------------------------------------------- #

def test_chinese_name_query_hits(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        [
            {"code": "600519", "name": "贵州茅台", "market": "SH", "trade_date": STAMP},
            {"code": "000858", "name": "五粮液", "market": "SZ", "trade_date": STAMP},
        ],
    )
    body = _search(tmp_path, "茅台")

    matches = body["payload"]["matches"]
    assert [(item["symbol"], item["name"]) for item in matches] == [("600519", "贵州茅台")]
    assert matches[0]["board"] == "主板"


def test_name_query_is_case_insensitive_and_prefix_ranks_first(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        [
            {"code": "600519", "name": "贵州茅台", "trade_date": STAMP},
            {"code": "600809", "name": "山西汾酒", "trade_date": STAMP},
        ],
    )
    body = _search(tmp_path, "山西")
    assert body["payload"]["matches"][0]["symbol"] == "600809"


# --------------------------------------------------------------------------- #
# 3. 空结果：查无此代码不许编名称
# --------------------------------------------------------------------------- #

def test_unknown_code_returns_empty_matches(tmp_path: Path) -> None:
    _seed(tmp_path, [{"code": "600000", "name": "浦发银行", "trade_date": STAMP}])
    body = _search(tmp_path, "999999")

    assert body["payload"]["matches"] == []
    assert body["payload"]["count"] == 0
    # 字典本身有数据（source ok），只是这个查询没命中——不是数据源缺失
    assert body["payload"]["data_source_status"] == "ok"


def test_empty_cache_reports_missing_source(tmp_path: Path) -> None:
    body = _search(tmp_path, "600000")
    assert body["payload"]["matches"] == []
    assert body["payload"]["data_source_status"] == "missing"
    assert body["data_as_of"] is None


# --------------------------------------------------------------------------- #
# 4. 参数与闸门
# --------------------------------------------------------------------------- #

def test_blank_query_is_422(tmp_path: Path) -> None:
    assert _client(tmp_path).get("/api/symbols/search", params={"q": "  "}).status_code == 422
    assert _client(tmp_path).get("/api/symbols/search").status_code == 422


def test_non_loopback_and_bad_host_are_403(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed(tmp_path, [{"code": "600000", "name": "浦发银行", "trade_date": STAMP}])
    monkeypatch.setenv("LDST_WEB_ALLOW_REMOTE", "1")
    monkeypatch.setenv("LDST_WEB_TOKEN", "0123456789abcdef0123")

    response = _client(tmp_path, client=REMOTE).get("/api/symbols/search", params={"q": "600000"})
    assert response.status_code == 403
    assert "浦发银行" not in response.text

    rebinding = _client(tmp_path).get(
        "/api/symbols/search", params={"q": "600000"}, headers={"host": "evil.example.com"}
    )
    assert rebinding.status_code == 403
    assert (
        _client(tmp_path).get("/api/symbols/search", params={"q": "600000"}).status_code == 200
    )
