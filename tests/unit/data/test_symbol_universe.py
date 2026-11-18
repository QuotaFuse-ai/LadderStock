"""QUOT-6xx：全市场标的字典（`symbol_basic`）的取数与落库口径测试。

钉住四条：

1. **候选号码**：代码段穷举出来的是 6 位十进制字符串、去重、按段顺序——它是本机
   事实，不是从哪个上游抄来的表；
2. **不编名称**：`_row_of` 对缺 code / 缺 name 的行直接丢，绝不用代码顶替名称；
3. **局部刷新不缩表**：只刷几只时，已有行必须并进来（整张字典不能被覆盖成一小撮）；
4. **单批失败不崩**：一批挂了只记 `failed_batches`，其余批照常合并。

全程不打上游：`fetch_live` 用桩，落库落 `tmp_path`。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from ladderstock.data import duckdb_store, symbol_universe
from ladderstock.data.provider_registry import SourceUnavailable

DAY = date(2026, 11, 10)
#: refresh 把整表写在「今天」这一天的分区里（`replace` 口径按日替换），
#: 所以「已有行」必须种在同一天，才是在比较同一张表而不是两个日期快照的并集。
TODAY = date.today()


class _Envelope:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.payload = {"records": records}


class _Outcome:
    def __init__(self, records: list[dict[str, Any]]) -> None:
        self.envelope = _Envelope(records)


class _StubRegistry:
    """按 codes 入参回固定记录；`fail` 里列出的那批（按首个代码识别）直接抛异常。"""

    def __init__(self, table: dict[str, dict[str, Any]], fail: set[str] | None = None) -> None:
        self.table = table
        self.fail = fail or set()
        self.calls: list[list[str]] = []

    def fetch_live(self, dataset: str, *, trade_date=None, params=None, **_kw):
        codes = [c for c in str((params or {}).get("codes", "")).split(",") if c]
        self.calls.append(codes)
        if codes and codes[0] in self.fail:
            raise SourceUnavailable("stub: 该批不可用")
        return _Outcome([dict(self.table[c]) for c in codes if c in self.table])


def _names(root: Path) -> dict[str, str]:
    return {row["code"]: row["name"] for row in duckdb_store.query_dataset("symbol_basic", root=root)}


def test_candidate_codes_are_six_digit_and_deduped() -> None:
    codes = symbol_universe.candidate_codes([(600000, 600003), (1, 2), (600000, 600002)])
    # 左闭右开：600000/600001/600002 + 000001；重复段不产生重复号码
    assert codes == ["600000", "600001", "600002", "000001"]
    assert all(len(code) == 6 and code.isdigit() for code in codes)


def test_candidate_codes_full_universe_size() -> None:
    """默认段的规模钉一下：3.5 万个候选 ≈ 700 批，这个量决定了「只能按需刷新」。"""
    assert len(symbol_universe.candidate_codes()) == 35099


def test_row_of_drops_rows_without_name() -> None:
    assert symbol_universe._row_of({"code": "600519"}) is None
    assert symbol_universe._row_of({"name": "贵州茅台"}) is None
    row = symbol_universe._row_of(
        {"code": "600519", "name": "贵州茅台", "market": "sh", "volume": 100}
    )
    assert row is not None
    assert row["market"] == "SH"
    assert row["active"] is True
    # 没有成交量的行仍然进字典（名称要能查），但 active=False——不拿它当「今天有交易」
    assert symbol_universe._row_of({"code": "000002", "name": "万科"})["active"] is False


def test_refresh_keeps_existing_rows_when_only_some_codes(tmp_path: Path) -> None:
    duckdb_store.write_dataset(
        "symbol_basic",
        [
            {"code": "600519", "name": "旧名字", "trade_date": TODAY.isoformat()},
            {"code": "000001", "name": "平安银行", "trade_date": TODAY.isoformat()},
        ],
        root=tmp_path,
    )
    registry = _StubRegistry({"600519": {"code": "600519", "name": "贵州茅台", "market": "SH"}})
    result = symbol_universe.refresh(registry, codes=["600519"], chunk=50, workers=1, root=tmp_path)

    assert result.rows == 1  # 本次取到的
    assert result.total_rows == 2  # 并上已有行之后的总量：没有被缩成 1 行
    names = _names(tmp_path)
    assert names["600519"] == "贵州茅台"  # 新值覆盖旧值
    assert names["000001"] == "平安银行"  # 没刷到的行还在


def test_refresh_full_scan_does_not_merge_existing(tmp_path: Path) -> None:
    duckdb_store.write_dataset(
        "symbol_basic",
        [{"code": "600519", "name": "旧名字", "trade_date": TODAY.isoformat()}],
        root=tmp_path,
    )
    registry = _StubRegistry({"000001": {"code": "000001", "name": "平安银行"}})
    # 显式关掉合并 → 全量口径：旧行不保留（退市 / 改名的残留靠这一轮清掉）
    result = symbol_universe.refresh(
        registry,
        codes=["000001"],
        chunk=50,
        workers=1,
        root=tmp_path,
        merge_existing=False,
    )
    assert result.total_rows == 1
    assert _names(tmp_path) == {"000001": "平安银行"}


def test_refresh_survives_one_failed_batch(tmp_path: Path) -> None:
    registry = _StubRegistry(
        {
            "600519": {"code": "600519", "name": "贵州茅台"},
            "000001": {"code": "000001", "name": "平安银行"},
        },
        fail={"600519"},
    )
    result = symbol_universe.refresh(
        registry,
        codes=["600519", "000001"],
        chunk=1,
        workers=1,
        root=tmp_path,
        merge_existing=False,
    )
    assert result.rows == 1
    assert len(result.failed_batches) == 1
    assert result.errors and "SourceUnavailable" in result.errors[0]
    assert result.total_rows == 1


def test_refresh_all_batches_failed_writes_nothing(tmp_path: Path) -> None:
    duckdb_store.write_dataset(
        "symbol_basic",
        [{"code": "600519", "name": "贵州茅台", "trade_date": TODAY.isoformat()}],
        root=tmp_path,
    )
    registry = _StubRegistry({}, fail={"600519"})
    result = symbol_universe.refresh(registry, codes=["600519"], chunk=50, workers=1, root=tmp_path)

    assert result.rows == 0
    assert result.total_rows == 0
    # 空轮次不覆盖已有字典
    assert _names(tmp_path) == {"600519": "贵州茅台"}


def test_latest_trade_date_and_count(tmp_path: Path) -> None:
    duckdb_store.write_dataset(
        "symbol_basic",
        [{"code": "600519", "name": "贵州茅台", "trade_date": DAY.isoformat()}],
        root=tmp_path,
    )
    assert symbol_universe.latest_trade_date(root=tmp_path) == DAY
    assert symbol_universe.count_rows(root=tmp_path) == 1


def test_latest_trade_date_on_empty_root(tmp_path: Path) -> None:
    assert symbol_universe.latest_trade_date(root=tmp_path) is None
    assert symbol_universe.count_rows(root=tmp_path) == 0
