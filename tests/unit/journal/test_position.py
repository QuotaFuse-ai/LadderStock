"""我的持仓：隐私库字段口径、增删改查、本地计算的手算对照。

三段对应 QUOT-145 §要求-4 与验收：

1. `positions` 表在 v2 里的列口径（含**旧库升级不丢数据**——真实用户库里已有数据）；
2. CRUD 的校验与退出码（2026-10-29 口径：改不到就 6，用法错就 2）；
3. `compute_metrics` 的盈亏 / 集中度 / 行业分布全部给手算对照，
   数字来自 `docs/specs/SPEC-MY-POSITION.md`，不靠「跑出来的值反过来写测试」。

价格一律由调用方注入（本模块不读行情），所以这里的用例直接把 `prices` 当入参传。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from ladderstock.cli.main import app
from ladderstock.data import sqlite_store
from ladderstock.errors import EXIT_NOT_FOUND, EXIT_USAGE, LadderStockError
from ladderstock.journal import position as position_module

runner = CliRunner()

COLUMNS = ["id", "code", "qty", "cost", "updated_at", "entry_date", "industry", "note"]


@pytest.fixture(autouse=True)
def _private_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo: Path) -> Path:
    """隐私库钉在 tmp：单测绝不在仓库 `data/` 里留下任何持仓记录。"""
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    return tmp_path


def _row(
    code: str,
    cost: float,
    qty: float,
    *,
    entry_date: str | None = None,
    industry: str = "",
    note: str = "",
) -> dict[str, Any]:
    """构造一条 `list_positions` 形状的记录（`compute_metrics` 的入参）。"""
    return {
        "id": 1,
        "code": code,
        "cost": cost,
        "qty": qty,
        "entry_date": entry_date,
        "industry": industry,
        "note": note,
        "updated_at": "2026-10-29T09:00:00+08:00",
    }


# --------------------------------------------------------------------------- #
# 1. schema v2：列口径与旧库升级
# --------------------------------------------------------------------------- #

def test_fresh_database_has_v2_position_columns(tmp_path: Path) -> None:
    conn = sqlite_store.connect(root=tmp_path, create=True)
    assert conn is not None
    try:
        assert sqlite_store.schema_version(conn) == sqlite_store.SCHEMA_VERSION == 2
        rows = conn.execute("PRAGMA table_info (positions)").fetchall()
        assert [str(row[1]) for row in rows] == COLUMNS
        indexes = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            ).fetchall()
        }
        assert {"idx_positions_updated_at", "idx_positions_entry_date"} <= indexes
    finally:
        sqlite_store.close(conn)


def test_v1_database_upgrades_without_losing_rows(tmp_path: Path) -> None:
    """v1 → v2：`avg_price` 改名 `cost`，新列补上；老行的建仓日如实留 NULL。"""
    path = tmp_path / sqlite_store.DB_FILENAME
    raw = sqlite3.connect(str(path))
    raw.execute("PRAGMA user_version = 1")
    raw.execute(
        """
        CREATE TABLE positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            qty REAL NOT NULL CHECK (qty >= 0),
            avg_price REAL NOT NULL CHECK (avg_price >= 0),
            updated_at TEXT NOT NULL
        )
        """
    )
    raw.execute(
        "INSERT INTO positions (code, qty, avg_price, updated_at)"
        " VALUES ('600000', 1000, 10.0, '2026-01-01T00:00:00+08:00')"
    )
    raw.commit()
    raw.close()

    conn = sqlite_store.connect(root=tmp_path, create=True)  # 打开即迁移
    assert conn is not None
    try:
        assert sqlite_store.schema_version(conn) == 2
        assert [str(row[1]) for row in conn.execute("PRAGMA table_info (positions)")] == COLUMNS
        row = conn.execute("SELECT * FROM positions").fetchone()
        assert row is not None
        saved = dict(row)
        assert saved["code"] == "600000"
        assert saved["qty"] == 1000.0
        assert saved["cost"] == 10.0  # 原 avg_price 的值，没被清零也没被猜
        assert saved["entry_date"] is None
        assert saved["industry"] == ""
        assert saved["note"] == ""
    finally:
        sqlite_store.close(conn)

    # 改结构前必须先有备份（审计 R-6b 同口径）
    assert list(tmp_path.glob(f"{sqlite_store.DB_FILENAME}.bak-v1-*"))


def test_second_open_does_not_rebuild_or_rebackup(tmp_path: Path) -> None:
    position_module.add_position("600000", 10.0, 1000.0, entry_date="2026-10-20", root=tmp_path)
    before = sorted(p.name for p in tmp_path.iterdir())

    conn = sqlite_store.connect(root=tmp_path, create=True)
    assert conn is not None
    try:
        assert sqlite_store.migrate(conn) == 2
        assert sqlite_store.migrate(conn) == 2
    finally:
        sqlite_store.close(conn)

    assert sorted(p.name for p in tmp_path.iterdir()) == before


# --------------------------------------------------------------------------- #
# 2. CRUD：校验与退出码
# --------------------------------------------------------------------------- #

def test_add_normalizes_code_and_returns_saved_row(tmp_path: Path) -> None:
    row = position_module.add_position(
        " 600000 ",
        10.5,
        1000,
        entry_date="2026-10-20",
        industry="银行",
        note="哨兵-A",
        root=tmp_path,
    )
    assert row["code"] == "600000"
    assert row["cost"] == 10.5
    assert row["qty"] == 1000.0
    assert row["entry_date"] == "2026-10-20"
    assert row["industry"] == "银行"
    assert row["note"] == "哨兵-A"
    assert set(row) == set(position_module.POSITION_FIELDS)


def test_add_defaults_entry_date_to_today(tmp_path: Path) -> None:
    from datetime import date

    row = position_module.add_position("600000", 10.0, 100.0, root=tmp_path)
    assert row["entry_date"] == date.today().isoformat()


@pytest.mark.parametrize(
    ("kwargs", "message"),
    (
        ({"code": "  "}, "--code 不能为空"),
        ({"code": "600000", "cost": 0}, "cost"),
        ({"code": "600000", "cost": -1}, "cost"),
        ({"code": "600000", "cost": 10.0, "qty": 0}, "qty"),
    ),
)
def test_add_rejects_bad_input(tmp_path: Path, kwargs: dict[str, Any], message: str) -> None:
    cost = kwargs.get("cost", 10.0)
    qty = kwargs.get("qty", 100.0)
    with pytest.raises(LadderStockError) as excinfo:
        position_module.add_position(kwargs["code"], cost, qty, root=tmp_path)
    assert excinfo.value.code == EXIT_USAGE
    assert message in excinfo.value.message
    # 校验失败的记录不许进库
    assert position_module.list_positions(root=tmp_path) == []


def test_add_twice_points_at_update(tmp_path: Path) -> None:
    position_module.add_position("600000", 10.0, 100.0, root=tmp_path)
    with pytest.raises(LadderStockError) as excinfo:
        position_module.add_position("600000", 11.0, 200.0, root=tmp_path)
    assert excinfo.value.code == EXIT_USAGE
    assert "position update" in excinfo.value.message


def test_list_is_ordered_by_entry_date_then_code(tmp_path: Path) -> None:
    for code, day in (("600002", "2026-10-20"), ("600001", "2026-10-20"), ("600000", "2026-10-09")):
        position_module.add_position(code, 10.0, 100.0, entry_date=day, root=tmp_path)

    rows = position_module.list_positions(root=tmp_path)
    assert [(row["code"], row["entry_date"]) for row in rows] == [
        ("600000", "2026-10-09"),
        ("600001", "2026-10-20"),
        ("600002", "2026-10-20"),
    ]
    filtered = position_module.list_positions(code="600001", root=tmp_path)
    assert [row["code"] for row in filtered] == ["600001"]


def test_list_never_creates_the_store(tmp_path: Path) -> None:
    assert position_module.list_positions(root=tmp_path) == []
    assert not (tmp_path / sqlite_store.DB_FILENAME).exists()


def test_update_touches_only_given_fields(tmp_path: Path) -> None:
    position_module.add_position(
        "600000",
        10.0,
        100.0,
        entry_date="2026-10-20",
        industry="银行",
        note="哨兵-B",
        root=tmp_path,
    )
    row = position_module.update_position("600000", qty=500.0, root=tmp_path)
    assert row["qty"] == 500.0
    assert row["cost"] == 10.0
    assert row["entry_date"] == "2026-10-20"
    assert row["industry"] == "银行"
    assert row["note"] == "哨兵-B"

    cleared = position_module.update_position("600000", industry="", note="", root=tmp_path)
    assert cleared["industry"] == ""
    assert cleared["note"] == ""


def _update_missing(root: Path) -> None:
    position_module.update_position("600999", qty=1.0, root=root)


def _update_no_fields(root: Path) -> None:
    position_module.update_position("600000", root=root)


def _update_bad_date(root: Path) -> None:
    position_module.update_position("600000", entry_date="2026-13-01", root=root)


def _update_bad_cost(root: Path) -> None:
    position_module.update_position("600000", cost=0, root=root)


def _remove_missing(root: Path) -> None:
    position_module.remove_position("600999", root=root)


@pytest.mark.parametrize(
    ("call", "code", "message"),
    (
        (_update_missing, EXIT_NOT_FOUND, "本机持仓里没有"),
        (_update_no_fields, EXIT_USAGE, "没有任何要改的字段"),
        (_update_bad_date, EXIT_USAGE, "YYYY-MM-DD"),
        (_update_bad_cost, EXIT_USAGE, "cost"),
        (_remove_missing, EXIT_NOT_FOUND, "本机持仓里没有"),
    ),
)
def test_update_and_remove_error_codes(
    tmp_path: Path,
    call: Any,
    code: int,
    message: str,
) -> None:
    position_module.add_position("600000", 10.0, 100.0, root=tmp_path)
    with pytest.raises(LadderStockError) as excinfo:
        call(tmp_path)
    assert excinfo.value.code == code
    assert message in excinfo.value.message


def test_remove_deletes_and_reports_basis(tmp_path: Path) -> None:
    position_module.add_position("600000", 10.0, 100.0, root=tmp_path)
    outcome = position_module.remove_position("600000", root=tmp_path)
    assert outcome == {"removed": True, "code": "600000", "qty": 100.0, "cost": 10.0}
    assert position_module.list_positions(root=tmp_path) == []


# --------------------------------------------------------------------------- #
# 3. compute_metrics：手算对照
# --------------------------------------------------------------------------- #

def _rows_partial() -> list[dict[str, Any]]:
    """三只持仓，其中 `000001` 取不到价（成本 22000、有价 20000）。"""
    return [
        _row("600000", 10.0, 1000.0, entry_date="2026-10-09", industry="银行"),
        _row("600519", 100.0, 100.0, entry_date="2026-10-15", industry="白酒"),
        _row("000001", 400.0, 5.0, entry_date="2026-10-20", industry="银行"),
    ]


def test_metrics_partial_price_coverage_keeps_cost_basis() -> None:
    """有价 20000 / 总成本 22000：覆盖 90.9%，市值口径不成立就退回成本口径。

    集中度（成本口径 10000 / 10000 / 2000，合计 22000）：
      top1   = 10000/22000 = 45.45… → 45.5
      top3   = (10000+10000+2000)/22000 = 1.0 → 100.0
             （逐行已舍入的 45.5 + 45.5 + 9.1 = 100.1，所以合计必须按原始比例只舍一次）
      hhi    = 2·(5/11)² + (1/11)² = 51/121 = 0.4215
    行业：银行 (10000+2000)/22000 = 54.5、白酒 10000/22000 = 45.5。
    """
    metrics = position_module.compute_metrics(
        _rows_partial(), {"600000": 11.0, "600519": 90.0}, as_of="2026-10-20"
    )

    assert metrics["local_only"] is True
    assert metrics["store"] == "local_sqlite"
    assert metrics["data_source_status"] == "ok"
    assert metrics["price_source"] == "daily_k"
    assert metrics["as_of"] == "2026-10-20"
    assert metrics["position_count"] == 3

    valuation = metrics["valuation"]
    assert valuation["total_cost"] == 22000.0
    assert valuation["market_value"] is None  # 有持仓取不到价 → 不给「混合市值」
    assert valuation["priced_cost"] == 20000.0
    assert valuation["unpriced_cost"] == 2000.0
    assert valuation["coverage_pct"] == 90.9
    assert valuation["unrealized_pnl"] == 0.0  # +1000 与 -1000 相抵
    assert valuation["unrealized_pnl_pct"] == 0.0

    by_code = {item["code"]: item for item in metrics["items"]}
    assert by_code["600000"]["market_value"] == 11000.0
    assert by_code["600000"]["unrealized_pnl"] == 1000.0
    assert by_code["600000"]["unrealized_pnl_pct"] == 10.0
    assert by_code["600519"]["unrealized_pnl"] == -1000.0
    assert by_code["600519"]["unrealized_pnl_pct"] == -10.0
    assert by_code["000001"] == {
        "code": "000001",
        "cost": 400.0,
        "qty": 5.0,
        "entry_date": "2026-10-20",
        "industry": "银行",
        "note": "",
        "latest_price": None,
        "market_value": None,
        "unrealized_pnl": None,
        "unrealized_pnl_pct": None,
    }

    concentration = metrics["concentration"]
    assert concentration["available"] is True
    assert concentration["basis"] == "cost"
    assert concentration["top1_pct"] == 45.5
    assert concentration["top3_pct"] == 100.0
    assert concentration["hhi"] == 0.4215
    assert concentration["holdings"] == [
        {"code": "600000", "weight_pct": 45.5, "cost": 10000.0},
        {"code": "600519", "weight_pct": 45.5, "cost": 10000.0},
        {"code": "000001", "weight_pct": 9.1, "cost": 2000.0},
    ]

    sector = metrics["sector_distribution"]
    assert sector["available"] is True
    assert sector["basis"] == "cost"
    assert sector["labeled_count"] == 3
    assert sector["unlabeled_count"] == 0
    assert sector["industries"] == [
        {"industry": "银行", "weight_pct": 54.5, "cost": 12000.0},
        {"industry": "白酒", "weight_pct": 45.5, "cost": 10000.0},
    ]


def test_metrics_fully_priced_switches_to_market_value() -> None:
    """两只都有现价：口径切到市值。

    A 成本 10×100=1000、现价 12 → 市值 1200；B 成本 20×100=2000、现价 21 → 市值 2100。
      总市值 3300、总成本 3000、浮盈 (12-10)·100 + (21-20)·100 = 300 → 300/3000 = 10.0%
      top1 = 2100/3300 = 7/11 = 63.6、A = 1200/3300 = 36.4、top3 = 100.0
      hhi  = (7/11)² + (4/11)² = 65/121 = 0.5372
    """
    rows = [
        _row("600000", 10.0, 100.0, entry_date="2026-10-09"),
        _row("600519", 20.0, 100.0, entry_date="2026-10-15"),
    ]
    metrics = position_module.compute_metrics(rows, {"600000": 12.0, "600519": 21.0})

    assert metrics["valuation"]["total_cost"] == 3000.0
    assert metrics["valuation"]["market_value"] == 3300.0
    assert metrics["valuation"]["coverage_pct"] == 100.0
    assert metrics["valuation"]["unrealized_pnl"] == 300.0
    assert metrics["valuation"]["unrealized_pnl_pct"] == 10.0

    concentration = metrics["concentration"]
    assert concentration["basis"] == "market_value"
    assert concentration["top1_pct"] == 63.6
    assert concentration["top3_pct"] == 100.0
    assert concentration["hhi"] == 0.5372
    assert concentration["holdings"] == [
        {"code": "600519", "weight_pct": 63.6, "cost": 2000.0},
        {"code": "600000", "weight_pct": 36.4, "cost": 1000.0},
    ]


def test_metrics_top3_is_the_three_heaviest_positions() -> None:
    """前三按权重取，不是按记录顺序：最轻的一只排在第一行也不该进前三合计。"""
    rows = [
        _row("000001", 1.0, 100.0, entry_date="2026-10-01"),  # 100，最轻
        _row("600000", 10.0, 100.0, entry_date="2026-10-02"),  # 1000
        _row("600519", 10.0, 100.0, entry_date="2026-10-03"),  # 1000
        _row("300750", 10.0, 100.0, entry_date="2026-10-04"),  # 1000
    ]
    metrics = position_module.compute_metrics(rows, {})
    # 合计 3100：前三 = 3000/3100 = 96.77 → 96.8（含 100 就变 100.0，那是「全部」不是「前三」）
    assert metrics["concentration"]["top1_pct"] == 32.3
    assert metrics["concentration"]["top3_pct"] == 96.8
    assert metrics["concentration"]["hhi"] == 0.3132  # 3·(10/31)² + (1/31)² = 301/961
    assert [item["code"] for item in metrics["concentration"]["holdings"][:3]] == [
        "300750",
        "600000",
        "600519",
    ]


def test_metrics_without_positions_is_honest_empty() -> None:
    metrics = position_module.compute_metrics([], {})
    assert metrics["position_count"] == 0
    assert metrics["data_source_status"] == "missing"
    assert metrics["valuation"]["total_cost"] == 0.0
    assert metrics["valuation"]["coverage_pct"] is None
    assert metrics["valuation"]["unrealized_pnl"] is None
    assert metrics["concentration"]["available"] is False
    assert metrics["concentration"]["reason"] == "本机没有持仓"
    assert metrics["sector_distribution"]["available"] is False
    assert "不猜行业" in str(metrics["sector_distribution"]["reason"])


def test_metrics_industry_bucket_falls_back_to_unlabeled() -> None:
    """标注是可选的：有一条标了就只统计行业分布，未标注的单列一个桶，不硬猜。"""
    rows = [
        _row("600000", 20.0, 100.0, industry="银行"),  # 2000
        _row("600519", 10.0, 100.0, industry=""),  # 1000
    ]
    metrics = position_module.compute_metrics(rows, {})
    sector = metrics["sector_distribution"]
    assert sector["available"] is True
    assert sector["labeled_count"] == 1
    assert sector["unlabeled_count"] == 1
    # 3000 合计：银行 66.7、未标注 33.3（未标注不进任何猜测出来的行业）
    assert sector["industries"] == [
        {"industry": "银行", "weight_pct": 66.7, "cost": 2000.0},
        {"industry": position_module.UNLABELED_INDUSTRY, "weight_pct": 33.3, "cost": 1000.0},
    ]


def test_metrics_is_independent_of_price_lookup() -> None:
    """本模块不读行情：`prices` 里没有的代码就是 null，不会去猜别处的价。"""
    metrics = position_module.compute_metrics(_rows_partial(), {})
    assert metrics["valuation"]["priced_cost"] == 0.0
    assert metrics["valuation"]["unpriced_cost"] == 22000.0
    assert metrics["valuation"]["coverage_pct"] == 0.0
    assert metrics["valuation"]["unrealized_pnl"] is None
    assert metrics["concentration"]["basis"] == "cost"


# --------------------------------------------------------------------------- #
# 4. CLI 闭环（只写本机）
# --------------------------------------------------------------------------- #

def _invoke(argv: list[str]) -> Any:
    return runner.invoke(app, ["position", *argv])


def test_cli_roundtrip_reports_local_only_each_time() -> None:
    added = _invoke(
        [
            "add", "--code", "600000", "--cost", "10.5", "--qty", "1000",
            "--date", "2026-10-20", "--industry", "银行", "--note", "哨兵-C",
            "--output", "json",
        ]
    )
    assert added.exit_code == 0, added.stderr
    payload = json.loads(added.stdout)
    assert payload["local_only"] is True and payload["store"] == "local_sqlite"
    assert payload["added"]["note"] == "哨兵-C"

    listed = _invoke(["list", "--output", "json"])
    assert listed.exit_code == 0, listed.stderr
    body = json.loads(listed.stdout)
    assert body["position_count"] == 1
    assert body["items"][0]["code"] == "600000"
    # 缓存里没有 daily_k：如实说明取不到价，不联网、不拿成本价冒充现价
    assert body["price_lookup"]["dataset"] == "daily_k"
    assert body["price_lookup"]["date"] is None
    assert "daily_k" in str(body["price_lookup"]["reason"])
    assert body["items"][0]["latest_price"] is None
    assert "持仓现价不可用" in listed.stderr

    updated = _invoke(["update", "--code", "600000", "--qty", "500", "--output", "json"])
    assert updated.exit_code == 0, updated.stderr
    assert json.loads(updated.stdout)["updated"]["qty"] == 500.0

    removed = _invoke(["remove", "--code", "600000", "--output", "json"])
    assert removed.exit_code == 0, removed.stderr
    assert json.loads(removed.stdout)["removed"] is True
    assert json.loads(_invoke(["list", "--output", "json"]).stdout)["position_count"] == 0


def test_cli_exit_codes_match_documented_semantics(tmp_path: Path) -> None:
    position_module.add_position("600000", 10.0, 100.0, root=tmp_path)

    assert _invoke(["add", "--code", "600000", "--cost", "10", "--qty", "100"]).exit_code == 2
    assert _invoke(["update", "--code", "600999", "--qty", "100"]).exit_code == 6
    assert _invoke(["remove", "--code", "600999"]).exit_code == 6
    bad_date = _invoke(
        ["add", "--code", "600001", "--cost", "10", "--qty", "100", "--date", "10-20"]
    )
    assert bad_date.exit_code == 2
    assert _invoke(["list", "--date", "2026-13-01"]).exit_code == 2
    assert position_module.list_positions(root=tmp_path)[0]["qty"] == 100.0


def test_cli_table_output_is_printable(tmp_path: Path) -> None:
    position_module.add_position("600000", 10.0, 100.0, root=tmp_path, note="哨兵-D")
    result = _invoke(["list", "--output", "table"])
    assert result.exit_code == 0, result.stderr
    assert "600000" in result.stdout
    assert "哨兵-D" in result.stdout
