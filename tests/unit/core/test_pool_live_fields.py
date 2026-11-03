"""候选池资金/题材分项必须认实机分区里的真实字段名（QUOT-360 / QUOT-362）。

实机 `fund_flow` 每行是 `main_net`、`concept_board` 每行是 `board_name`；
两处都不在旧容忍表里，`/api/pool` 的 `parts.capital` / `parts.theme`
因此一直为 null。本文件按真实 payload 形状锁住映射，防止再退回 null。
"""

from __future__ import annotations

from typing import Any

from ladderstock.core import pool as pool_module


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


def test_compute_pool_fills_capital_and_theme(monkeypatch) -> None:
    limit_pool = [
        {
            "code": "600001",
            "name": "甲",
            "streak": 3,
            "turnover_rate": 10.0,
            "hot_topic": "白酒概念",
        },
    ]
    snapshots: dict[str, tuple[list[dict[str, Any]], str]] = {
        "concept_board": ([{"board_name": "白酒概念", "change_pct": 3.0}], "parquet"),
        "fund_flow": ([{"code": "600001", "main_net": 506847989.25}], "parquet"),
    }

    def fake_snapshot(dataset: str, day: Any, root: Any = None):
        return snapshots[dataset]

    monkeypatch.setattr(pool_module, "dataset_snapshot", fake_snapshot)

    payload = pool_module.compute_pool("2026-11-03", records=limit_pool)
    candidate = payload["candidates"][0]
    assert candidate["parts"]["capital"] == 50.0  # 单样本给中位
    assert candidate["parts"]["theme"] == 60.0  # 3.0% / 5.0 记 60 分
    assert candidate["absent_parts"] == []
    assert candidate["raw"]["net_inflow"] == 506847989.25
    assert candidate["raw"]["theme_tags"] == ["白酒概念"]
