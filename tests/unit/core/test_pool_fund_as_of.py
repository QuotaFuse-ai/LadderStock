"""资金分项的**口径日期**（QUOT-361 / QUOT-364 B-3）。

日线主力净额是当日盘后才发布的，「请求日没有分区」是常态。只读请求日分区就等于
在盘后早段把 `capital` 整列判空——这正是 QUOT-360 要解决的问题。这里的三条纪律：

- 当日分区缺失 → 回落到 `FUND_LOOKBACK_DAYS` 个自然日内最近一个可用交易日；
- 回落读出来的数**必须标成它自己的日期**（`meta.fund_data_as_of` + `source_detail`
  + `degraded_inputs`），不能冒充请求日；
- 窗口内也没有 → `capital = null` + 权重从分母剔除（现有机制不动），不补 0、不越窗口取数。

夹具按真实分区形状写（`main_net` 是日频 `stock_fund_flow` 的实际字段名），
全程离线、只写 tmp，不碰仓库 `data/`。
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from ladderstock.core import pool as pool_module
from ladderstock.data import duckdb_store

DAY = date(2026, 11, 3)
RECORDS = [
    {"code": "600261", "name": "甲", "boards": 4, "turnover_rate": 10.0},
    {"code": "002580", "name": "乙", "boards": 2, "turnover_rate": 8.0},
]


def write_fund(rows: list[dict[str, Any]], on: date, root: Path) -> None:
    """把资金记录写成 `on` 那一天的分区（`trade_date` 决定分区，与真实同步同一条路）。"""
    duckdb_store.write_dataset(
        "fund_flow",
        [{**row, "trade_date": on.isoformat()} for row in rows],
        source="golden",
        default_trade_date=on,
        root=root,
    )


def write_baseline(root: Path) -> None:
    """题材两路输入按「都有数」铺开：本文件的变量只有资金这一路，
    `degraded_inputs` 里出现的就一定是资金。"""
    duckdb_store.write_dataset(
        "concept_board",
        [{"board_name": "固态电池", "change_pct": 4.0}],
        source="golden",
        default_trade_date=DAY,
        root=root,
    )
    duckdb_store.write_dataset(
        "concept_membership",
        [{"code": code, "board_name": "固态电池"} for code in ("600261", "002580")],
        source="golden",
        default_trade_date=DAY,
        root=root,
    )


def pool_for(root: Path) -> dict[str, Any]:
    write_baseline(root)
    return pool_module.compute_pool(DAY, root=root, records=list(RECORDS))


# --------------------------------------------------------------------------- #
# 1. 当日有分区：口径日期就是请求日，不标降级
# --------------------------------------------------------------------------- #


def test_same_day_partition_reports_the_request_day(tmp_path: Path) -> None:
    write_fund(
        [{"code": "600261", "main_net": 417850659.38}, {"code": "002580", "main_net": 1e7}],
        DAY,
        tmp_path,
    )

    pool = pool_for(tmp_path)

    assert pool["meta"]["fund_data_as_of"] == DAY.isoformat()
    assert pool["meta"]["fund_is_request_day"] is True
    assert pool["degraded_inputs"] == []
    assert pool["meta"]["parts_non_null"]["capital"] == 2
    assert "口径日期" not in pool["source_detail"]


# --------------------------------------------------------------------------- #
# 2. 当日没有分区：回落到最近可用日，且如实标出日期
# --------------------------------------------------------------------------- #


def test_falls_back_to_the_latest_available_day_and_labels_it(tmp_path: Path) -> None:
    """QUOT-364 B-3 的复现形状：只放 T-1 的资金数据、不留请求日分区。"""
    as_of = DAY - timedelta(days=1)
    write_fund(
        [{"code": "600261", "main_net": 417850659.38}, {"code": "002580", "main_net": 1e7}],
        as_of,
        tmp_path,
    )

    pool = pool_for(tmp_path)

    assert pool["meta"]["fund_data_as_of"] == as_of.isoformat()
    assert pool["meta"]["fund_is_request_day"] is False
    assert pool["degraded_inputs"] == ["fund_flow"]
    assert pool["missing"] == []  # 有数，只是口径日期不是请求日
    assert pool["meta"]["parts_non_null"]["capital"] == 2
    assert all(row["parts"]["capital"] is not None for row in pool["candidates"])
    detail = pool["source_detail"]
    assert f"口径日期 {as_of.isoformat()} ≠ 请求日 {DAY.isoformat()}" in detail
    assert "回落读取" in detail


def test_lookback_picks_the_nearest_day_not_the_newest_partition(tmp_path: Path) -> None:
    """T-3 与 T-1 都有分区时取 T-1：回落要的是「最近一个可用交易日」，不是任意一天。"""
    write_fund([{"code": "600261", "main_net": 1.0}], DAY - timedelta(days=3), tmp_path)
    write_fund(
        [{"code": "600261", "main_net": 2.0}, {"code": "002580", "main_net": 1.0}],
        DAY - timedelta(days=1),
        tmp_path,
    )

    pool = pool_for(tmp_path)

    assert pool["meta"]["fund_data_as_of"] == (DAY - timedelta(days=1)).isoformat()
    assert pool["counts"]["fund_flow_rows"] == 2


def test_no_data_inside_the_window_stays_missing(tmp_path: Path) -> None:
    """窗口外（数据断了一周以上）宁缺不补：`capital=null`、权重剔出分母、口径日期为 null。"""
    write_fund(
        [{"code": "600261", "main_net": 417850659.38}],
        DAY - timedelta(days=pool_module.FUND_LOOKBACK_DAYS + 2),
        tmp_path,
    )

    pool = pool_for(tmp_path)

    assert pool["meta"]["fund_data_as_of"] is None
    assert pool["counts"]["fund_flow_rows"] == 0
    assert pool["missing"] == ["fund_flow"]
    assert pool["degraded_inputs"] == []
    assert all(row["parts"]["capital"] is None for row in pool["candidates"])
    assert all("capital" in row["absent_parts"] for row in pool["candidates"])
    # 资金取不到 → 0.20 从分母里剔除（分母 0.80），题材那 80 分照常参与：不补 0
    top = pool["candidates"][0]
    assert top["parts"] == {
        "strength": 66.7,
        "turnover": 50.0,
        "standing": 100.0,
        "capital": None,
        "theme": 80.0,
    }
    assert top["score"] == round(
        (0.30 * 66.7 + 0.10 * 50.0 + 0.25 * 100.0 + 0.15 * 80.0) / 0.80, 1
    )


# --------------------------------------------------------------------------- #
# 3. 取数函数本身的契约
# --------------------------------------------------------------------------- #


def test_fund_flow_snapshot_signature(tmp_path: Path) -> None:
    """`(records, backend, as_of, fell_back)`：`as_of` 来自记录里的 `trade_date`，
    不是请求日期——`archive.snapshot()` 会把空记录的 `as_of` 退化成请求日，这里必须挡住。"""
    records, backend, as_of, fell_back = pool_module.fund_flow_snapshot(DAY, tmp_path)

    assert records == []
    assert backend == "none"
    assert as_of is None
    assert fell_back is False

    write_fund([{"code": "600261", "main_net": 5.0}], DAY, tmp_path)
    records, backend, as_of, fell_back = pool_module.fund_flow_snapshot(DAY, tmp_path)

    assert [row["code"] for row in records] == ["600261"]
    assert backend == "parquet"
    assert as_of == DAY
    assert fell_back is False


def test_dataset_snapshot_as_of_is_silent_when_unreadable(tmp_path: Path) -> None:
    """非法日期 / 不可读缓存 → 空记录 + `as_of=None`，绝不拿请求日期顶。"""
    records, backend, as_of = pool_module.dataset_snapshot_as_of("fund_flow", "不是日期", tmp_path)

    assert records == []
    assert backend == "error"
    assert as_of is None
