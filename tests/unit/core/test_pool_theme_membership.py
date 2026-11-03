"""题材分项的取数链（QUOT-362）：板块名解析、概念过滤、两条路的标注。

`test_pool_golden.py` 管的是「分数算得对不对」，这里管的是「数是从哪条路来的」：

- 板块名解析（根因一）：`concept_boards` 的真实行用 `board_name`，
  而 `ladder.NAME_KEYS` 里从来没有这个键——板块索引一条都建不起来；
- 概念过滤（根因二）：`stock_concept_blocks` 的 boards 是 industry/concept/region
  **混在一起**的，板块码（`BKxxxx`）与板块榜主键（`gn_*`）不同构，只能按板块名 join，
  「在当日概念榜里」同时就是过滤规则；
- 两条路的标注：归属命中 = `stock_concept_blocks`，退回涨停原因 = `reason_keywords`，
  都没有 = `null`（权重从分母剔除，不补 0）。

全程离线、只写 tmp，不碰仓库 `data/`。
"""

from __future__ import annotations

from datetime import date as date_type
from pathlib import Path
from typing import Any

from ladderstock.core import ladder as ladder_module
from ladderstock.core import limit_pool as limit_pool_module
from ladderstock.core import pool as pool_module
from ladderstock.data import duckdb_store

DAY = "2026-10-27"


def write_cache(datasets: dict[str, list[dict[str, Any]]], root: Path) -> None:
    """与 `test_pool_golden.py` 同一做法：`ldst pool` 读的是真缓存路径，不是打桩。"""
    for dataset, rows in datasets.items():
        if rows:
            duckdb_store.write_dataset(
                dataset,
                rows,
                source="theme",
                default_trade_date=date_type.fromisoformat(DAY),
                root=root,
            )


# --------------------------------------------------------------------------- #
# 根因一：板块名解析
# --------------------------------------------------------------------------- #


def test_board_index_resolves_board_name_and_keeps_legacy_name() -> None:
    """`board_name` 是现网键，`name` 是历史夹具键——两个都要认，缺一个就建不起索引。"""
    boards = pool_module.theme_board_index(
        [
            {"board_name": "固态电池", "change_pct": 4.0},
            {"name": "光模块", "change_pct": 1.0},
            {"板块名称": "港口", "涨跌幅": "2.5"},
            {"board_name": "  ", "change_pct": 9.9},
            {"board_name": "缺涨幅"},
        ]
    )

    assert boards == {"固态电池": 4.0, "光模块": 1.0, "港口": 2.5}


def test_board_name_keys_do_not_touch_the_stock_name_table() -> None:
    """`ladder.NAME_KEYS` 的「股票名」语义不许被板块表污染（两份清单，各管各的）。"""
    assert "board_name" not in ladder_module.NAME_KEYS
    assert "板块名称" not in ladder_module.NAME_KEYS
    assert "board_name" in pool_module.BOARD_NAME_KEYS


# --------------------------------------------------------------------------- #
# 根因二：概念过滤 + 归属索引
# --------------------------------------------------------------------------- #


def test_membership_index_keeps_only_boards_present_in_the_days_concept_list() -> None:
    """行业与地域归属进不了题材分；不在当日榜里的名字如实丢弃。"""
    boards = {"固态电池": 4.0, "光模块": 1.0}
    members = pool_module.membership_index(
        [
            {"code": "600010", "board_name": "固态电池"},
            {"code": "600010", "board_name": "农林牧渔"},  # 行业段
            {"code": "600010", "board_name": "上海板块"},  # 地域段
            {"code": "600010", "board_name": "固态电池"},  # 重复行不重复计
            {"code": "600011", "board_name": "光模块"},
            {"board_name": "光模块"},  # 没有股票代码的行
        ],
        boards,
    )

    assert members == {"600010": ["固态电池"], "600011": ["光模块"]}


def test_membership_rows_carry_the_stock_code_not_the_board_code() -> None:
    """归属表的代码列是股票代码（板块码在 `board_code` 里），join 才接得上候选池。"""
    boards = {"水产饲料": 3.08}
    members = pool_module.membership_index(
        [{"code": "002696", "board_name": "水产饲料", "board_code": "BK1509"}], boards
    )

    assert list(members) == ["002696"]
    assert "BK1509" not in members


# --------------------------------------------------------------------------- #
# 关键词降级：`+` 分隔与涨停原因
# --------------------------------------------------------------------------- #


def test_reason_keys_are_reused_not_copied() -> None:
    """题材容忍表末尾接的是 `core/limit_pool.py` 的 `REASON_KEYS`（同一份清单，一个口径）。"""
    assert set(limit_pool_module.REASON_KEYS) <= set(pool_module.THEME_KEYS)


def test_theme_tokens_split_on_plus_separator() -> None:
    """实测涨停原因 `水产加工+鱼粉鱼油+“羊”字辈`：`+`（含全角 `＋`）是主分隔符。"""
    assert pool_module.theme_tokens({"reason": "水产加工+鱼粉鱼油+“羊”字辈"}) == [
        "水产加工",
        "鱼粉鱼油",
        "“羊”字辈",
    ]
    assert pool_module.theme_tokens({"reason": "饲料＋港口，机器人"}) == ["饲料", "港口", "机器人"]
    assert pool_module.theme_tokens({"code": "600000"}) == []


# --------------------------------------------------------------------------- #
# 两条路的标注（meta + raw.theme_source）
# --------------------------------------------------------------------------- #


def test_theme_source_labels_the_membership_path(tmp_path: Path) -> None:
    write_cache(
        {
            "concept_board": [{"board_name": "固态电池", "change_pct": 4.0}],
            "concept_membership": [{"code": "600010", "board_name": "固态电池"}],
            "fund_flow": [{"code": "600010", "net_inflow": 1.0}],
        },
        tmp_path,
    )
    pool = pool_module.compute_pool(
        DAY,
        root=tmp_path,
        records=[{"code": "600010", "name": "甲", "boards": 4, "turnover_rate": 10.0}],
    )

    row = pool["candidates"][0]
    assert row["raw"]["theme_source"] == pool_module.THEME_SOURCE_MEMBERSHIP
    assert row["raw"]["theme_tags"] == ["固态电池"]
    assert row["parts"]["theme"] == 80.0
    assert pool["meta"]["theme_source"] == pool_module.THEME_SOURCE_MEMBERSHIP
    assert pool["meta"]["theme_membership_codes"] == 1
    assert pool["meta"]["theme_source_counts"][pool_module.THEME_SOURCE_MEMBERSHIP] == 1


def test_theme_source_labels_the_keyword_fallback(tmp_path: Path) -> None:
    """归属表存在但没覆盖这只 → 退回关键词，标注必须是 `reason_keywords`，
    否则读的人会以为是成分归属。"""
    write_cache(
        {
            "concept_board": [{"board_name": "饲料", "change_pct": 5.0}],
            "concept_membership": [{"code": "600099", "board_name": "饲料"}],
            "fund_flow": [{"code": "600010", "net_inflow": 1.0}],
        },
        tmp_path,
    )
    pool = pool_module.compute_pool(
        DAY,
        root=tmp_path,
        records=[
            {
                "code": "600010",
                "name": "甲",
                "boards": 4,
                "turnover_rate": 10.0,
                "reason": "水产加工+饲料",
            }
        ],
    )

    row = pool["candidates"][0]
    assert row["raw"]["theme_source"] == pool_module.THEME_SOURCE_KEYWORDS
    assert row["raw"]["theme_tags"] == ["水产加工", "饲料"]
    assert row["parts"]["theme"] == 100.0
    assert pool["meta"]["theme_source_counts"] == {
        pool_module.THEME_SOURCE_MEMBERSHIP: 0,
        pool_module.THEME_SOURCE_KEYWORDS: 1,
    }


def test_theme_null_when_both_paths_miss(tmp_path: Path) -> None:
    """两条路都没有 → `theme=null` + 权重剔出分母，且 `concept_membership` 不进 `missing`
    （它是可选的第四路输入，缺它只是降级，不是候选池缺数据）。"""
    write_cache(
        {
            "concept_board": [{"board_name": "港口", "change_pct": 2.0}],
            "fund_flow": [{"code": "600010", "net_inflow": 1.0}],
        },
        tmp_path,
    )
    pool = pool_module.compute_pool(
        DAY,
        root=tmp_path,
        records=[{"code": "600010", "name": "甲", "boards": 4, "turnover_rate": 10.0}],
    )

    row = pool["candidates"][0]
    assert row["parts"]["theme"] is None
    assert row["raw"]["theme_source"] is None
    assert row["absent_parts"] == ["theme"]
    assert row["score"] == round(
        (0.30 * 66.7 + 0.10 * 50.0 + 0.25 * 100.0 + 0.20 * 50.0) / 0.85, 1
    )  # 分母 0.85：题材的 0.15 从分母里剔除，不是补 0
    assert pool["missing"] == []
    assert pool["meta"]["theme_membership_codes"] == 0
    assert pool["meta"]["theme_membership_landed_codes"] == 0


def test_source_detail_reports_the_membership_backend(tmp_path: Path) -> None:
    """四路输入都在 `source_detail` 里报后端，读的人不用猜归属是取的还是缺的。"""
    write_cache(
        {
            "concept_board": [{"board_name": "固态电池", "change_pct": 4.0}],
            "concept_membership": [{"code": "600010", "board_name": "固态电池"}],
        },
        tmp_path,
    )
    pool = pool_module.compute_pool(
        DAY,
        root=tmp_path,
        records=[{"code": "600010", "name": "甲", "boards": 4}],
    )

    assert "concept_membership: " in pool["source_detail"]
    assert "1 只落库" in pool["source_detail"]
    assert "1 只命中当日概念榜" in pool["source_detail"]


def test_membership_landed_but_off_the_board_list_is_reported_separately(
    tmp_path: Path,
) -> None:
    """2026-11-03 现网实测的那种票：归属行取到了（`000995` 等 8 只），但它挂的板块名
    不在当日概念榜（榜单只有 100 行）里 → 题材仍为 `null`。
    meta 必须把「几只落库」和「几只命中榜」分开报，否则读的人会把覆盖率的锅算到解析头上。"""
    write_cache(
        {
            "concept_board": [{"board_name": "港口", "change_pct": 2.0}],
            "concept_membership": [
                {"code": "600010", "board_name": "稀土"},
                {"code": "600010", "board_name": "陶瓷"},
            ],
            "fund_flow": [{"code": "600010", "net_inflow": 1.0}],
        },
        tmp_path,
    )
    pool = pool_module.compute_pool(
        DAY,
        root=tmp_path,
        records=[
            {
                "code": "600010",
                "name": "甲",
                "boards": 4,
                "turnover_rate": 10.0,
            }
        ],
    )

    row = pool["candidates"][0]
    assert row["raw"]["theme_tags"] == []
    assert row["raw"]["theme_source"] is None
    assert row["parts"]["theme"] is None
    assert pool["meta"]["theme_membership_landed_codes"] == 1
    assert pool["meta"]["theme_membership_codes"] == 0
