"""代码池改写的单测：锚点保留、校验、拒绝坏输入。

改 yaml 不像改数据库，错了没有事务兜底，所以每条都在验：
写完之后别人（其它数据集、下次采集）拿到的还是同一份池子。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from ladderstock.errors import LadderStockError
from ladderstock.web import pool_api

SAMPLE = """# 头注释，不能被吃掉
sync:
  datasets:
    daily_k:
      primary: tickhub_kline
      # 这一段是给人和工具共用的基础代码池
      codes: &base_pool
        - "600000"  # 浦发银行
        - "600519"  # 贵州茅台

        - "000001"  # 平安银行
      watermark_field: trade_date
    fund_flow:
      primary: tickhub_fund_flow
      codes: *base_pool
    industry_fund_flow:
      primary: tickhub_industry
"""


def _project(tmp_path: Path) -> tuple[Path, Path]:
    project = tmp_path / "LadderStock"
    (project / "config").mkdir(parents=True)
    (project / "config" / "sync.yaml").write_text(SAMPLE, encoding="utf-8")
    data = project / "data"
    data.mkdir()
    return project, data


def _symbol_db(data: Path, rows: list[tuple[str, str, bool, float]]) -> None:
    import duckdb

    conn = duckdb.connect(str(data / "market.duckdb"))
    conn.execute(
        "create table symbol_basic (code varchar, trade_date date, ingested_at timestamp,"
        " source varchar, payload json)"
    )
    for code, name, active, volume in rows:
        payload = json.dumps({"code": code, "name": name, "active": active, "price": 1.0, "volume": volume})
        conn.execute(
            "insert into symbol_basic values (?, date '2026-11-20', now(), 'test', ?)",
            [code, payload],
        )
    conn.close()


def test_rewrite_keeps_anchor_and_drops_old_items() -> None:
    text = pool_api.rewrite_pool_text(SAMPLE, [("600000", "浦发银行"), ("000002", "万科A")])
    datasets = yaml.safe_load(text)["sync"]["datasets"]
    assert datasets["daily_k"]["codes"] == ["600000", "000002"]
    assert datasets["fund_flow"]["codes"] == ["600000", "000002"]  # 锚点仍共享同一份
    assert datasets["daily_k"]["watermark_field"] == "trade_date"  # 兄弟键没被吃掉
    assert "头注释，不能被吃掉" in text
    assert "  # 万科A" in text
    assert "600519" not in text  # 旧列表项与中间空行都清掉了


def test_anchor_names_ignores_independent_pools() -> None:
    """index_daily 有自己的 1 只，不该被当成基础池的共用户。"""
    text = SAMPLE.replace(
        "    industry_fund_flow:\n      primary: tickhub_industry\n",
        "    index_daily:\n      primary: tickhub_index\n      codes:\n        - \"000300\"\n",
    )
    assert pool_api._anchor_names(text) == ["daily_k", "fund_flow"]
    new_text = pool_api.rewrite_pool_text(text, [("600000", "")])
    pool_api._verify(new_text, ["600000"], ["daily_k", "fund_flow"])
    assert yaml.safe_load(new_text)["sync"]["datasets"]["index_daily"]["codes"] == ["000300"]


def test_rewrite_refuses_when_anchor_missing() -> None:
    with pytest.raises(LadderStockError) as info:
        pool_api.rewrite_pool_text("sync:\n  datasets: {}\n", [("600000", "")])
    assert "找不到" in str(info.value)


def test_apply_pool_size_uses_ranked_active(tmp_path: Path) -> None:
    project, data = _project(tmp_path)
    _symbol_db(
        data,
        [
            ("600519", "贵州茅台", True, 900.0),
            ("600000", "浦发银行", True, 500.0),
            ("000001", "平安银行", True, 300.0),
            ("600001", "退市票", False, 9999.0),
        ],
    )
    result = pool_api.apply_pool(project, data, {"mode": "size", "size": 2})
    assert result["applied"] is True
    assert result["size"] == 2
    assert result["codes_written"] == 2
    doc = yaml.safe_load((project / "config" / "sync.yaml").read_text(encoding="utf-8"))
    # 按成交额降序取活票；不活跃的票再大也不进池
    assert doc["sync"]["datasets"]["daily_k"]["codes"] == ["600519", "600000"]
    assert list((project / "config").glob("sync.yaml.bak-*"))


def test_apply_pool_rejects_bad_input(tmp_path: Path) -> None:
    project, data = _project(tmp_path)
    for payload in (
        {"mode": "size", "size": 0},
        {"mode": "codes", "codes": []},
        {"mode": "magic"},
        {"mode": "size", "size": "abc"},
    ):
        with pytest.raises(LadderStockError):
            pool_api.apply_pool(project, data, payload)
    # 拒绝的请求不该动文件
    assert (project / "config" / "sync.yaml").read_text(encoding="utf-8") == SAMPLE
    assert not list((project / "config").glob("sync.yaml.bak-*"))


def test_apply_pool_explicit_codes(tmp_path: Path) -> None:
    project, data = _project(tmp_path)
    result = pool_api.apply_pool(project, data, {"mode": "codes", "codes": ["600036", "000858"]})
    assert result["size"] == 2
    codes = yaml.safe_load((project / "config" / "sync.yaml").read_text(encoding="utf-8"))["sync"]["datasets"][
        "daily_k"
    ]["codes"]
    assert codes == ["600036", "000858"]


def test_warning_scales_with_pool() -> None:
    assert pool_api._warning(85) is None
    assert "100" in (pool_api._warning(300) or "")
    assert "盘后包" in (pool_api._warning(1500) or "")
