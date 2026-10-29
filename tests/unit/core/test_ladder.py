"""连板天梯：高度、断层、龙头候选与字段容忍（M3 / DEC-006 闸门 5）。

全部用内存记录驱动，不读缓存、不联网；数字必须能手工复核。
"""

from __future__ import annotations

from ladderstock.core.ladder import as_float, as_int, compute_ladder, summary

DAY = "2026-10-27"

RECORDS = [
    {"code": "A", "name": "甲", "consecutive_boards": 5},
    {"code": "B", "name": "乙", "limit_up_days": "3板"},
    {"code": "C", "name": "丙", "连板数": 2},
    {"code": "D", "name": "丁", "boards": 1},
    {"code": "E", "name": "戊"},
]


def test_height_gap_and_leaders() -> None:
    ladder = compute_ladder(DAY, records=RECORDS)

    assert ladder["available"] is True
    assert ladder["height"] == 5
    assert ladder["count"] == 4
    assert ladder["gap"] is True
    assert ladder["gap_levels"] == [4]
    assert ladder["unknown_board"] == 1
    assert [item["code"] for item in ladder["leader_candidates"]] == ["A"]
    assert [item["boards"] for item in ladder["ladder"]] == [5, 3, 2, 1]


def test_tolerant_board_field_names() -> None:
    """`consecutive_boards` / `limit_up_days` / `连板数` 都能解析。"""
    assert as_int("3板") == 3
    assert as_int("2") == 2
    assert as_int(True) is None
    assert as_int(2.0) == 2
    assert as_int(2.5) is None
    ladder = compute_ladder(DAY, records=RECORDS)
    boards = {row["code"]: row["boards"] for group in ladder["ladder"] for row in group["stocks"]}
    assert boards == {"A": 5, "B": 3, "C": 2, "D": 1}


def test_empty_pool_is_unavailable_not_zero() -> None:
    ladder = compute_ladder(DAY, records=[])
    assert ladder["available"] is False
    assert ladder["height"] is None
    assert "同步" in ladder["reason"]
    assert summary(ladder) == "连板天梯：数据缺失"


def test_records_without_board_field_are_unknown() -> None:
    ladder = compute_ladder(DAY, records=[{"code": "X", "name": "某股"}])
    assert ladder["available"] is False
    assert "连板数" in ladder["reason"]
    assert ladder["unknown_board"] == 1


def test_no_gap_when_levels_continuous() -> None:
    ladder = compute_ladder(
        DAY,
        records=[
            {"code": "A", "consecutive_boards": 3},
            {"code": "B", "consecutive_boards": 2},
            {"code": "C", "consecutive_boards": 1},
        ],
    )
    assert ladder["gap"] is False
    assert ladder["gap_levels"] == []


def test_summary_is_program_generated() -> None:
    ladder = compute_ladder(DAY, records=RECORDS)
    text = summary(ladder)
    assert "最高 5 板" in text
    assert "断层 4板" in text


def test_as_float_tolerates_percent() -> None:
    assert as_float("3.5%") == 3.5
    assert as_float(7) == 7.0
    assert as_float("n/a") is None


def test_duplicate_code_counts_once() -> None:
    """D-04：同一 `code` 的重复行不得放大 count / 层级 / 龙头（保留首条）。"""
    ladder = compute_ladder(
        DAY,
        records=[
            {"code": "A", "name": "甲", "consecutive_boards": 4},
            {"code": "A", "name": "甲", "consecutive_boards": 4},
            {"code": "A", "name": "甲", "consecutive_boards": 4},
            {"code": "B", "name": "乙", "consecutive_boards": 2},
        ],
    )
    assert ladder["available"] is True
    assert ladder["count"] == 2
    assert ladder["height"] == 4
    assert [item["code"] for item in ladder["leader_candidates"]] == ["A"]
    assert [group["count"] for group in ladder["ladder"]] == [1, 1]
    # 重复条数写进 `source_detail`，可溯源
    assert "重复 code 去重 2 条" in ladder["source_detail"]


def test_records_without_code_are_kept_for_unknown_count() -> None:
    """去重只作用于有 `code` 的行；无 `code` 的行仍计入 `unknown_board`。"""
    ladder = compute_ladder(
        DAY,
        records=[
            {"code": "A", "consecutive_boards": 3},
            {"code": "A", "consecutive_boards": 3},
            {"name": "无代码"},
            {"name": "无代码"},
        ],
    )
    assert ladder["count"] == 1
    assert ladder["unknown_board"] == 2


def test_seal_time_passed_through_tolerantly() -> None:
    """D-01：封板时间按多名字段透传；缺失如实 null，不猜。"""
    ladder = compute_ladder(
        DAY,
        records=[
            {
                "code": "A",
                "consecutive_boards": 3,
                "first_seal_time": "093512",
                "last_seal_time": "142101",
            },
            {"code": "B", "连板数": 2, "首次封板时间": "095000", "封板时间": "101500"},
            {"code": "C", "boards": 1},
        ],
    )
    leaders = {row["code"]: row for row in ladder["leader_candidates"]}
    assert leaders["A"]["first_seal_time"] == "093512"
    assert leaders["A"]["seal_time"] == "142101"

    stocks = {
        row["code"]: row for group in ladder["ladder"] for row in group["stocks"]
    }
    assert stocks["B"]["first_seal_time"] == "095000"
    assert stocks["B"]["seal_time"] == "101500"
    # 上游没给时间 -> null（不填 0、不填 "--"、不猜）
    assert stocks["C"]["first_seal_time"] is None
    assert stocks["C"]["seal_time"] is None


def test_seal_time_is_never_invented_from_boards() -> None:
    """涨停池只有连板数时，封板时间必须整片为 null。"""
    ladder = compute_ladder(DAY, records=RECORDS)
    for group in ladder["ladder"]:
        for row in group["stocks"]:
            assert row["first_seal_time"] is None
            assert row["seal_time"] is None
