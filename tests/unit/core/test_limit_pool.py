"""涨停样本投影：字段容忍、两条不补空值红线、与天梯同源的去重与排序。

全部用内存记录驱动，不读缓存、不联网。
"""

from __future__ import annotations

from typing import Any

from ladderstock.core.limit_pool import compute_limit_pool, project_limit_pool

DAY = "2026-10-30"

RECORDS: list[dict[str, Any]] = [
    {
        "code": "600000",
        "name": "浦发银行",
        "consecutive_boards": 2,
        "first_seal_time": "093015",
        "last_seal_time": "142500",
        "reason": "银行",
        "trade_date": "2026-10-30",
    },
    # 备工具 ths_limit_up_pool 的写法：中文键 + `连板数`
    {"sec_code": "000001", "名称": "平安银行", "连板数": "5板", "trade_date": "2026-10-30"},
    # 重复 code：去重保留首条
    {"code": "600000", "name": "浦发银行", "boards": 9, "trade_date": "2026-10-30"},
    # 无连板数：不进 rows，也不补 1
    {"code": "300001", "name": "缺连板数", "trade_date": "2026-10-30"},
    # 无 code：不进 rows
    {"code": "", "name": "无代码", "boards": 3},
]


def _payload(records: list[dict[str, Any]]) -> dict[str, Any]:
    return project_limit_pool(records, day=DAY, backend="fixture")


def test_rows_shape_sort_and_dedupe() -> None:
    result = _payload(RECORDS)

    assert result["available"] is True
    assert result["reason"] is None
    assert result["date"] == DAY
    assert result["count"] == 2
    assert [row["code"] for row in result["rows"]] == ["000001", "600000"]
    # streak 降序；600000 保留首条（boards=9 是重复行，不参与）
    assert [row["streak"] for row in result["rows"]] == [5, 2]
    assert result["rows"][1] == {
        "code": "600000",
        "name": "浦发银行",
        "streak": 2,
        "first_seal_time": "093015",
        "last_seal_time": "142500",
        "reason": "银行",
        "trade_date": "2026-10-30",
    }


def test_source_detail_counts_dedupe_and_unknown() -> None:
    detail = _payload(RECORDS)["source_detail"]
    assert detail == "limit_pool: fixture（5 条，重复 code 去重 1 条，无连板数或无代码 2 条）"


def test_missing_fields_stay_null_not_defaulted() -> None:
    """封板时间 / 原因 / 行内交易日取不到就是 null，不补 1、不用请求日期顶替。"""
    row = _payload([{"code": "600001", "name": "某股", "boards": 3}])["rows"][0]

    assert row["streak"] == 3
    assert row["first_seal_time"] is None
    assert row["last_seal_time"] is None
    assert row["reason"] is None
    assert row["trade_date"] is None


def test_seal_time_passes_through_verbatim() -> None:
    """HHMMSS 原样透传，不做时区换算、不补冒号。"""
    row = _payload(
        [{"code": "600002", "name": "乙", "boards": 1, "first_seal_time": "092500",
          "seal_time": "150000", "trade_date": "2026-10-29"}]
    )["rows"][0]

    assert row["first_seal_time"] == "092500"
    assert row["last_seal_time"] == "150000"
    # 行内交易日与请求日期不同也照实返回
    assert row["trade_date"] == "2026-10-29"


def test_empty_cache_is_unavailable_not_an_error() -> None:
    result = _payload([])

    assert result["available"] is False
    assert "同步" in result["reason"]
    assert result["count"] == 0
    assert result["rows"] == []
    assert result["source_detail"] == "limit_pool: fixture（0 条）"


def test_pool_without_board_rows_says_so() -> None:
    result = _payload([{"code": "600003", "name": "丙"}])

    assert result["available"] is False
    assert "连板数" in result["reason"]
    assert result["rows"] == []


def test_compute_falls_back_to_unavailable_when_cache_missing(monkeypatch) -> None:
    """缓存读不到（backend=none）时如实 `available=False`，不造行。"""
    from ladderstock.core import limit_pool as module

    monkeypatch.setattr(module, "dataset_snapshot", lambda *_a, **_k: ([], "none"))
    result = compute_limit_pool(DAY)

    assert result["available"] is False
    assert result["count"] == 0
    assert result["source_detail"] == "limit_pool: none（0 条）"


def test_compute_reads_limit_pool_dataset(monkeypatch) -> None:
    from ladderstock.core import limit_pool as module

    seen: dict[str, Any] = {}

    def fake_snapshot(dataset: str, day: Any, root: Any = None) -> tuple[list[dict], str]:
        seen["dataset"] = dataset
        seen["day"] = day
        seen["root"] = root
        return [{"code": "600004", "name": "丁", "boards": 2}], "duckdb"

    monkeypatch.setattr(module, "dataset_snapshot", fake_snapshot)
    result = compute_limit_pool(DAY, root="/tmp/whatever")

    assert seen["dataset"] == "limit_pool"
    assert seen["day"] == DAY
    assert result["available"] is True
    assert result["source_detail"] == "limit_pool: duckdb（1 条）"
