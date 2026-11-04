"""候选池资金/题材分项必须认实机分区里的真实字段名（QUOT-360 / QUOT-362）。

实机 `fund_flow` 每行是 `main_net`、`concept_board` 每行是 `board_name`；
两处都不在旧容忍表里，`/api/pool` 的 `parts.capital` / `parts.theme`
因此一直为 null。本文件按真实 payload 形状锁住映射，防止再退回 null。

QUOT-364 补的两条纪律：
- 覆盖 / 分位的用例走**真缓存**（`duckdb_store.write_dataset`），不打桩——
  只测容忍表时「实现与金标一起漏了同一个字段名」是一起绿的；
- 同名板块在上游两条通道里**符号相反**（QUOT-364 B-4），题材分只能认 `concept_board`。
"""

from __future__ import annotations

from datetime import date as date_type
from pathlib import Path

from ladderstock.core import pool as pool_module
from ladderstock.data import duckdb_store


def test_fund_inflow_map_reads_daily_main_net() -> None:
    flows = pool_module.fund_inflow_map(
        [
            {"code": "600001", "main_net": 506847989.25},
            {"code": "600002", "net_inflow": 12.0},
            {"code": "600003", "main_net": None},
        ]
    )
    assert flows == {"600001": 506847989.25, "600002": 12.0}


def test_theme_board_index_reads_board_name() -> None:
    boards = pool_module.theme_board_index(
        [
            {"board_name": "华为汽车", "change_pct": -2.74},
            {"board_name": "白酒概念", "change_pct": 3.0},
            {"board_name": "", "change_pct": 9.0},
        ]
    )
    assert boards == {"华为汽车": -2.74, "白酒概念": 3.0}


def test_compute_pool_fills_capital_and_theme(tmp_path: Path) -> None:
    """真缓存驱动 `compute_pool()`：夹具写的是实机分区的字段名，不打桩。

    QUOT-364 的漏测教训——只测容忍表（`fund_inflow_map` / `theme_board_index`）时，
    「合并前两边一起缺 `main_net` 所以一起绿」是能发生的。只有走真实读写路径，
    字段名、分区、日期口径才一起被检验。
    """
    day = "2026-11-03"
    duckdb_store.write_dataset(
        "concept_board",
        [{"board_name": "白酒概念", "change_pct": 3.0}],
        source="live",
        default_trade_date=date_type.fromisoformat(day),
        root=tmp_path,
    )
    duckdb_store.write_dataset(
        "fund_flow",
        [{"code": "600001", "main_net": 506847989.25}],
        source="live",
        default_trade_date=date_type.fromisoformat(day),
        root=tmp_path,
    )
    # 归属表这一路整列缺失（当日没同步到）：题材走关键词降级，`concept_membership` 记进降级
    limit_pool = [
        {
            "code": "600001",
            "name": "甲",
            "streak": 3,
            "turnover_rate": 10.0,
            "hot_topic": "白酒概念",
        },
    ]

    payload = pool_module.compute_pool(day, root=tmp_path, records=limit_pool)
    candidate = payload["candidates"][0]
    assert candidate["parts"]["capital"] == 50.0  # 单样本给中位
    assert candidate["parts"]["theme"] == 60.0  # 3.0% / 5.0 记 60 分
    assert candidate["absent_parts"] == []
    assert candidate["raw"]["net_inflow"] == 506847989.25
    assert candidate["raw"]["theme_tags"] == ["白酒概念"]
    assert candidate["raw"]["theme_source"] == pool_module.THEME_SOURCE_KEYWORDS
    assert payload["counts"]["fund_flow_rows"] == 1
    assert payload["counts"]["concept_membership_rows"] == 0
    assert payload["degraded_inputs"] == ["concept_membership"]
    assert payload["missing"] == []
    assert payload["meta"]["fund_data_as_of"] == day
    assert "concept_membership: none（0 条" in payload["source_detail"]


def test_theme_ignores_membership_change_pct_when_the_two_channels_disagree(
    tmp_path: Path,
) -> None:
    """上游两条通道对**同名板块**的涨幅可以符号相反（QUOT-364 B-4 实测）。

    2026-11-03 实机：`融资融券` 在 `stock_concept_blocks` 里是 −1.27，
    在 `concept_boards` 里是 +1.2782（后者的 `price`/`change` 自洽）。
    题材分只能认 `concept_board` 的当日榜——认了归属快照那一侧，
    分数就随逐只取数的时刻漂移，同一只票两次跑能差出一个符号。
    """
    day = "2026-11-03"
    duckdb_store.write_dataset(
        "concept_board",
        [{"board_name": "融资融券", "change_pct": 1.2782422986296}],
        source="live",
        default_trade_date=date_type.fromisoformat(day),
        root=tmp_path,
    )
    duckdb_store.write_dataset(
        "concept_membership",
        [
            {
                "code": "600261",
                "board_name": "融资融券",
                "board_code": "gn_rzrq",
                "change_pct": -1.27,
            }
        ],
        source="live",
        default_trade_date=date_type.fromisoformat(day),
        root=tmp_path,
    )

    payload = pool_module.compute_pool(
        day,
        root=tmp_path,
        records=[{"code": "600261", "name": "甲", "boards": 4, "turnover_rate": 10.0}],
    )
    candidate = payload["candidates"][0]

    assert candidate["raw"]["theme_board_change_pct"] == 1.2782422986296
    assert candidate["parts"]["theme"] == round(1.2782422986296 / 5.0 * 100.0, 1)
    assert candidate["raw"]["theme_source"] == pool_module.THEME_SOURCE_MEMBERSHIP
