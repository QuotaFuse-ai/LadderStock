"""`ldst config migrate` 的迁移契约：只读、先备份、幂等、拒绝高版本。

契约来自源文档 §6.5 与审计 R-6：

- 迁移不改写、不删除旧目录里的文件，只把内容复制进新目录；
- 目标文件（`config/state.yaml`、`data/journal.sqlite`）在改动前先落带时间戳的备份，
  任一步失败按备份回滚；
- `schema_version` 已达当前版本时不再写任何东西（幂等）；
- `schema_version` 高于当前程序支持值时拒绝（退出码 3），不猜未来结构。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from ladderstock.cli.main import app
from ladderstock.config import migrate as migrate_module
from ladderstock.config.migrate import (
    SUPPORTED_SCHEMA_VERSION,
    build_plan,
    run_migration,
    upgrade_state_payload,
)
from ladderstock.data import sqlite_store
from ladderstock.errors import EXIT_CONFIG, LadderStockError

runner = CliRunner()

V1_STATE = """\
state:
  backend: indexeddb
  schema_version: 1
  stores:
    watch_snapshot:
      persist: true
    review_draft:
      persist: true
"""


def _workspace(tmp_path: Path) -> tuple[Path, Path]:
    """造一个 v1 配置 + 空数据目录（彼此隔离，不碰仓库真实目录）。"""
    config = tmp_path / "config"
    config.mkdir()
    (config / "state.yaml").write_text(V1_STATE, encoding="utf-8")
    data = tmp_path / "data"
    data.mkdir()
    return config, data


def test_dry_run_only_plans_and_writes_nothing(tmp_path: Path) -> None:
    config, data = _workspace(tmp_path)
    before = (config / "state.yaml").read_text(encoding="utf-8")

    report = run_migration(dry_run=True, config_root=config, data_root=data)

    assert report["dry_run"] is True
    assert report["status"] == "planned"
    assert [step["id"] for step in report["steps"]] == ["config:state.yaml"]
    assert (config / "state.yaml").read_text(encoding="utf-8") == before
    assert not (data / "backups").exists(), "dry-run 不得落备份目录"
    assert not (data / "state" / "migration.json").exists(), "dry-run 不得写迁移标记"


def test_apply_migrates_v1_to_v2_with_timestamped_backup(tmp_path: Path) -> None:
    config, data = _workspace(tmp_path)

    report = run_migration(config_root=config, data_root=data)

    assert report["status"] == "applied"
    state = yaml.safe_load((config / "state.yaml").read_text(encoding="utf-8"))["state"]
    assert state["schema_version"] == SUPPORTED_SCHEMA_VERSION
    names = [store["name"] for store in state["stores"]]
    assert names[:4] == [
        "watch_snapshot",
        "review_draft",
        "validation_draft",
        "journal_draft",
    ], "v2 四类分片必须齐全（v1 缺的补齐）"
    assert state["stores"][0]["ttl_sec"] == 300, "watch_snapshot 必须有合法 TTL"

    backups = list((data / "backups").glob("*/state.yaml"))
    assert len(backups) == 1
    assert "schema_version: 1" in backups[0].read_text(encoding="utf-8")
    assert Path(report["backups"][0]) == backups[0]

    marker = json.loads((data / "state" / "migration.json").read_text(encoding="utf-8"))
    assert marker["schema_version"] == SUPPORTED_SCHEMA_VERSION
    assert marker["steps"] == ["config:state.yaml"]


def test_second_run_is_idempotent(tmp_path: Path) -> None:
    config, data = _workspace(tmp_path)
    run_migration(config_root=config, data_root=data)
    backups_before = sorted(path.name for path in (data / "backups").glob("*"))
    text_before = (config / "state.yaml").read_text(encoding="utf-8")

    report = run_migration(config_root=config, data_root=data)

    assert report["status"] == "up_to_date"
    assert report["steps"] == []
    assert report["backups"] == []
    assert (config / "state.yaml").read_text(encoding="utf-8") == text_before
    assert sorted(path.name for path in (data / "backups").glob("*")) == backups_before


def test_higher_schema_version_is_refused(tmp_path: Path) -> None:
    config, data = _workspace(tmp_path)
    text = "state:\n  backend: indexeddb\n  schema_version: 9\n  stores: []\n"
    (config / "state.yaml").write_text(text, encoding="utf-8")

    with pytest.raises(LadderStockError) as excinfo:
        build_plan(config_root=config, data_root=data)

    assert excinfo.value.code == EXIT_CONFIG
    assert (config / "state.yaml").read_text(encoding="utf-8") == text, "拒绝时不得改写配置"


def test_mapping_form_stores_are_normalised() -> None:
    payload = {
        "state": {
            "schema_version": 1,
            "stores": {"watch_snapshot": {"persist": True, "ttl_sec": 0}},
        }
    }

    state = upgrade_state_payload(payload)["state"]

    assert state["schema_version"] == SUPPORTED_SCHEMA_VERSION
    assert state["backend"] == "indexeddb"
    assert [store["name"] for store in state["stores"]] == list(
        migrate_module.CANONICAL_STORES
    )
    assert state["stores"][0]["ttl_sec"] == 300


def test_legacy_data_is_copied_and_left_read_only(tmp_path: Path, monkeypatch) -> None:
    config, data = _workspace(tmp_path)
    legacy = tmp_path / "old"
    (legacy / "review").mkdir(parents=True)
    (legacy / "data.js").write_text("var legacy = 1;\n", encoding="utf-8")
    (legacy / "review" / "2026-10-01.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("LDST_LEGACY_DATA_DIR", str(legacy))

    report = run_migration(config_root=config, data_root=data)

    assert "data:legacy" in [step["id"] for step in report["steps"]]
    imported = list((data / "legacy").glob("import-*"))
    assert len(imported) == 1
    assert (imported[0] / "data.js").is_file()
    assert (imported[0] / "review" / "2026-10-01.json").is_file()
    assert (legacy / "data.js").is_file(), "旧目录只读：不移动、不删除"


#: 老库（上游实现留下的结构）：有表、有数据、但没写 `user_version` /
#: `schema_meta`，也没有 v1 的索引。迁移要能就地补齐而不是重建。
LEGACY_DB_DDL = """
CREATE TABLE trades (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_date TEXT NOT NULL,
    code       TEXT NOT NULL,
    side       TEXT NOT NULL,
    price      REAL NOT NULL,
    qty        REAL NOT NULL,
    note       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
INSERT INTO trades (trade_date, code, side, price, qty, note, created_at)
VALUES ('2026-10-01', '600000', 'buy', 10.0, 100, 'keep-me', '2026-10-01T09:30:00+08:00');
"""


def test_sqlite_is_backed_up_before_upgrade(tmp_path: Path) -> None:
    config, data = _workspace(tmp_path)
    db = data / "journal.sqlite"
    conn = sqlite3.connect(db)
    for statement in LEGACY_DB_DDL.split(";"):
        if statement.strip():
            conn.execute(statement)
    conn.commit()
    conn.close()

    report = run_migration(config_root=config, data_root=data)

    assert "sqlite:journal.sqlite" in [step["id"] for step in report["steps"]]
    backups = list(data.glob("journal.sqlite.bak-v*"))
    assert len(backups) == 1, "迁移前必须先落一份带时间戳的备份（R-6b）"

    check = sqlite3.connect(db)
    assert check.execute("PRAGMA user_version").fetchone()[0] == 1
    assert check.execute("SELECT note FROM trades").fetchone()[0] == "keep-me"
    tables = sqlite_store.table_names(check)
    check.close()
    assert {"trades", "positions", "watchlist", "schema_meta"} <= set(tables)

    saved = sqlite3.connect(backups[0])
    assert saved.execute("SELECT note FROM trades").fetchone()[0] == "keep-me"
    assert saved.execute("PRAGMA user_version").fetchone()[0] == 0, "备份必须是迁移前的样子"
    saved.close()


def test_failure_rolls_back_the_state_file(tmp_path: Path, monkeypatch) -> None:
    config, data = _workspace(tmp_path)
    original = (config / "state.yaml").read_text(encoding="utf-8")

    def boom(plan: object, backups: object) -> None:
        raise RuntimeError("模拟写迁移标记失败")

    monkeypatch.setattr(migrate_module, "_write_marker", boom)

    with pytest.raises(LadderStockError) as excinfo:
        run_migration(config_root=config, data_root=data)

    assert excinfo.value.code == EXIT_CONFIG
    assert (config / "state.yaml").read_text(encoding="utf-8") == original, "失败必须按备份回滚"


# --------------------------------------------------------------------------- #
# CLI 薄封装：只负责参数与结构化输出
# --------------------------------------------------------------------------- #


def test_cli_dry_run_outputs_json_plan(tmp_path: Path, monkeypatch) -> None:
    config, data = _workspace(tmp_path)
    monkeypatch.setenv("LDST_CONFIG_DIR", str(config))
    monkeypatch.setenv("LDST_DATA_DIR", str(data))

    result = runner.invoke(app, ["config", "migrate", "--dry-run", "--output", "json"])

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["schema"]["to"] == SUPPORTED_SCHEMA_VERSION


def test_cli_refuses_higher_version_with_config_error(tmp_path: Path, monkeypatch) -> None:
    config, data = _workspace(tmp_path)
    (config / "state.yaml").write_text(
        "state:\n  schema_version: 7\n  stores: []\n", encoding="utf-8"
    )
    monkeypatch.setenv("LDST_CONFIG_DIR", str(config))
    monkeypatch.setenv("LDST_DATA_DIR", str(data))

    result = runner.invoke(app, ["config", "migrate"])

    assert result.exit_code == EXIT_CONFIG
    assert "schema_version" in result.stderr
