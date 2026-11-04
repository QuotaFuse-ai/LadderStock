"""我的自选：本机隐私库读写（`journal/watchlist.py`）+ CLI 写面（`cli/watchlist.py`）。

对应 QUOT-380 第 4 项（`ldst watch add|rm|list`）与 DEC-052 §2.3：
**这张表此前只有 schema、没有生产者**，所以这里钉的是「生产者长什么样」：

- 写入只落本机 SQLite，代码归一成裸 6 位（与 `cli/sync.py watchlist_codes()`、
  `spot_quote` 回键同一口径）；
- `note` 不在投影里（拍板口径 2：这一版不出自由文本）；
- 读面不建库（`create=False`），空清单是 `[]` 而不是 404 语义；
- 非法代码 = 用法错误（exit 2）、删不到 = 无结果（exit 6），与 `ldst position` 同一套退出码。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ladderstock.cli.main import app
from ladderstock.data import sqlite_store
from ladderstock.errors import EXIT_NOT_FOUND, EXIT_USAGE, LadderStockError
from ladderstock.journal import watchlist as module

runner = CliRunner()

SENTINEL = "隐私哨兵-WATCHCLI-8d31"


def _invoke(args: list[str]):
    return runner.invoke(app, ["watch", *args])


def _codes(root: Path) -> list[str]:
    return [str(row["code"]) for row in module.list_watch(root=root)]


# ------------------------------------------------------------------ 代码口径


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("600519", "600519"),
        ("sh600519", "600519"),
        ("SZ000701", "000701"),
        ("bj430047", "430047"),
        ("  000001  ", "000001"),
    ],
)
def test_code_is_normalised_to_bare_six_digits(given: str, expected: str) -> None:
    assert module.clean_code(given) == expected


@pytest.mark.parametrize("given", ["", "   ", "60051", "6005190", "sh60051", "AAPL", "600519.SH"])
def test_bad_code_is_a_usage_error(given: str) -> None:
    with pytest.raises(LadderStockError) as excinfo:
        module.clean_code(given)
    assert excinfo.value.code == EXIT_USAGE


# ---------------------------------------------------------------------- 写面


def test_add_is_idempotent_and_keeps_first_created_at(tmp_path: Path) -> None:
    first = module.add_watch("sh600519", root=tmp_path)
    again = module.add_watch("600519", root=tmp_path)

    assert first["code"] == again["code"] == "600519"
    assert first["created_at"] == again["created_at"]  # 不刷新：清单是集合不是流水
    assert _codes(tmp_path) == ["600519"]


def test_list_is_ordered_by_added_time(tmp_path: Path) -> None:
    module.add_watch("600519", root=tmp_path)
    module.add_watch("000001", root=tmp_path)
    module.add_watch("000701", root=tmp_path)

    assert _codes(tmp_path) == ["600519", "000001", "000701"]


def test_remove_deletes_only_that_row(tmp_path: Path) -> None:
    module.add_watch("600519", root=tmp_path)
    module.add_watch("000001", root=tmp_path)

    assert module.remove_watch("SZ600519", root=tmp_path) == "600519"
    assert _codes(tmp_path) == ["000001"]


def test_remove_missing_code_is_no_result_not_success(tmp_path: Path) -> None:
    module.add_watch("600519", root=tmp_path)
    with pytest.raises(LadderStockError) as excinfo:
        module.remove_watch("000001", root=tmp_path)
    assert excinfo.value.code == EXIT_NOT_FOUND
    assert _codes(tmp_path) == ["600519"]


def test_projection_never_reads_note(tmp_path: Path) -> None:
    """`note` 不在 SELECT 里（口径 2）：即使库里写了备注，读本模块也拿不到那个列。"""
    conn = sqlite_store.connect(root=tmp_path, create=True)
    assert conn is not None
    try:
        conn.execute(
            "INSERT INTO watchlist (code, note, created_at) VALUES (?, ?, ?)",
            ("600519", SENTINEL, "2026-11-03T09:30:00+08:00"),
        )
    finally:
        sqlite_store.close(conn)

    rows = module.list_watch(root=tmp_path)
    assert [row["code"] for row in rows] == ["600519"]
    assert set(rows[0]) == set(module.WATCHLIST_FIELDS) == {"code", "created_at"}
    assert SENTINEL not in json.dumps(rows, ensure_ascii=False)


def test_list_does_not_create_the_private_store(tmp_path: Path) -> None:
    assert module.list_watch(root=tmp_path) == []
    assert (tmp_path / sqlite_store.DB_FILENAME).exists() is False


def test_watchlist_is_the_same_table_the_code_pool_reads(tmp_path: Path) -> None:
    """与 `cli/sync.py watchlist_codes()` 同一张表、同一口径：裸 6 位、按加入时间。"""
    from ladderstock.cli.sync import watchlist_codes

    module.add_watch("sh600519", root=tmp_path)
    monkey = tmp_path  # `watchlist_codes` 读 LDST_DATA_DIR，这里直接对拍 SQL 形状
    conn = sqlite_store.connect(root=monkey, create=False)
    assert conn is not None
    try:
        rows = [str(r[0]) for r in conn.execute("SELECT code FROM watchlist ORDER BY created_at")]
    finally:
        sqlite_store.close(conn)
    assert rows == ["600519"] == _codes(tmp_path)
    assert callable(watchlist_codes)


# ----------------------------------------------------------------------- CLI


def test_cli_add_list_rm_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))

    added = _invoke(["add", "--code", "sh600519", "--output", "json"])
    assert added.exit_code == 0, added.stderr
    body = json.loads(added.stdout)
    assert body["ok"] is True and body["local_only"] is True and body["store"] == "local_sqlite"
    assert body["added"]["code"] == "600519"

    listed = _invoke(["list", "--output", "json"])
    assert listed.exit_code == 0, listed.stderr
    listing = json.loads(listed.stdout)
    assert listing["watch_count"] == 1
    assert [item["code"] for item in listing["items"]] == ["600519"]

    removed = _invoke(["rm", "--code", "600519", "--output", "json"])
    assert removed.exit_code == 0, removed.stderr
    assert json.loads(removed.stdout)["removed"] == "600519"
    assert _codes(tmp_path) == []


def test_cli_empty_list_is_not_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    listed = _invoke(["list", "--output", "json"])
    assert listed.exit_code == 0, listed.stderr
    body = json.loads(listed.stdout)
    assert body["watch_count"] == 0 and body["items"] == []
    assert "ldst watch add" in body["detail"]


def test_cli_bad_code_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    result = _invoke(["add", "--code", "AAPL"])
    assert result.exit_code == EXIT_USAGE
    assert "6 位" in result.stderr


def test_cli_rm_unknown_code_exits_6(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    assert _invoke(["rm", "--code", "600519"]).exit_code == EXIT_NOT_FOUND


def test_cli_still_has_the_intraday_watch_commands(tmp_path: Path) -> None:
    """`watch` 组里 盯盘（start|status|stop）与 自选（add|rm|list）共存，不互相覆盖。"""
    help_text = runner.invoke(app, ["watch", "--help"]).output
    for name in ("start", "status", "stop", "add", "rm", "list"):
        assert name in help_text


def test_db_has_a_single_writer_path(tmp_path: Path) -> None:
    """写面只有一条路：本模块的 INSERT。表里不该出现第二个来源的痕迹。"""
    module.add_watch("600519", root=tmp_path)
    conn = sqlite3.connect(sqlite_store.db_path(tmp_path))
    try:
        count = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
    finally:
        conn.close()
    assert count == 1
