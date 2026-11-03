"""前端持久化契约：配置、清单、迁移版本、`ldst state` 行为必须对得上。

这些断言是「三处版本号必须一致」的唯一护栏：`config/state.yaml`、
`frontend/stores/manifest.json`、`frontend/persist/migrate.ts:SCHEMA_VERSION`。
改了任何一处而不改另外两处，这里就会红。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator
from typer.testing import CliRunner

from ladderstock.cli.main import app
from ladderstock.cli.state import build_report, index_path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config"
FRONTEND = ROOT / "frontend"
MANIFEST_PATH = FRONTEND / "stores" / "manifest.json"
MIGRATE_PATH = FRONTEND / "persist" / "migrate.ts"
PERSISTED_STORE_PATH = FRONTEND / "persist" / "persistedStore.ts"

runner = CliRunner()

#: 前端已实现分片（journal_draft 仍只在配置里预留）；顺序与 config/state.yaml 一致。
IMPLEMENTED_STORES = ("watch_snapshot", "review_draft", "validation_draft", "watch_settings")


def _state_config() -> dict[str, Any]:
    return yaml.safe_load((CONFIG / "state.yaml").read_text(encoding="utf-8"))["state"]


def _manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _migrate_schema_version() -> int:
    text = MIGRATE_PATH.read_text(encoding="utf-8")
    match = re.search(r"export const SCHEMA_VERSION\s*=\s*(\d+)", text)
    assert match, "migrate.ts 里找不到 SCHEMA_VERSION"
    return int(match.group(1))


def _index_document(stores: dict[str, Any], version: int = 2) -> dict[str, Any]:
    return {
        "backend": "indexeddb",
        "database": "ladderstock-state",
        "schema_version": version,
        "generated_at": "2026-10-22T07:00:00Z",
        "stores": stores,
    }


@pytest.fixture()
def data_dir(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把数据目录指向临时目录，保证 `state reset` 永不写进仓库。"""
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------- 配置 / 清单


def test_state_config_matches_schema() -> None:
    data = yaml.safe_load((CONFIG / "state.yaml").read_text(encoding="utf-8"))
    schema = json.loads((CONFIG / "schema" / "state.schema.json").read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(data), key=lambda e: list(e.path))
    assert not errors, [f"{list(e.path)}: {e.message}" for e in errors]


def test_manifest_stores_are_declared_in_config() -> None:
    """清单里的每个分片都必须在 state.yaml 里有同名条目，且 persist/ttl 一致。"""
    config = {store["name"]: store for store in _state_config()["stores"]}
    for store in _manifest()["stores"]:
        name = store["persistKey"]
        assert name in config, f"manifest 的 {name} 没有写进 config/state.yaml"
        assert config[name]["persist"] is True
        if "ttlSec" in store:
            assert config[name].get("ttl_sec") == store["ttlSec"], f"{name} 的 ttl 两处不一致"


def test_implemented_stores_cover_the_core_shards() -> None:
    manifest_keys = {store["persistKey"] for store in _manifest()["stores"]}
    assert set(IMPLEMENTED_STORES) <= manifest_keys


def test_extra_configured_stores_are_documented_as_planned() -> None:
    """journal_draft 只在配置里预留；哪天实现了必须同时进 manifest，否则这里会提醒。"""
    manifest_keys = {store["persistKey"] for store in _manifest()["stores"]}
    extra = {store["name"] for store in _state_config()["stores"]} - manifest_keys
    assert extra <= {"journal_draft"}, f"配置里有、前端未实现的分片超出预期：{sorted(extra)}"


def test_schema_version_aligned_in_three_places() -> None:
    versions = {
        "config/state.yaml": _state_config()["schema_version"],
        "stores/manifest.json": _manifest()["schemaVersion"],
        "persist/migrate.ts": _migrate_schema_version(),
    }
    assert len(set(versions.values())) == 1, f"分片版本三处不一致：{versions}"


def test_persistence_uses_indexeddb_not_localstorage() -> None:
    source = PERSISTED_STORE_PATH.read_text(encoding="utf-8")
    assert "createJSONStorage(() => createIdbJSONStorage())" in source
    # 不允许退回默认的 window.localStorage 后端
    assert not re.search(r"createJSONStorage\(\(\)\s*=>\s*[\w.]*localStorage", source)
    indexeddb_source = (FRONTEND / "persist" / "indexeddb.ts").read_text(encoding="utf-8")
    assert "indexedDB.open(" in indexeddb_source


def test_index_file_path_matches_manifest(data_dir: Path) -> None:
    relative = Path(_manifest()["indexFile"])
    assert data_dir / relative.relative_to("data") == index_path()


# ---------------------------------------------------------------- state inspect


def test_inspect_without_index_reports_missing_without_inventing(data_dir: Path) -> None:
    report = build_report()
    assert report["index_present"] is False
    assert report["index_generated_at"] is None
    assert report["warnings"], "索引缺失必须给出提示"
    assert [row["name"] for row in report["stores"]][: len(IMPLEMENTED_STORES)] == list(
        IMPLEMENTED_STORES
    )
    for row in report["stores"]:
        assert row["source"] == "config-only"
        assert row["updated_at"] is None
        assert row["bytes"] is None


def test_inspect_reads_index_and_reports_size_and_time(data_dir: Path) -> None:
    document = _index_document(
        {
            "review_draft": {
                "schema_version": 2,
                "updated_at": "2026-10-22T06:59:00Z",
                "bytes": 2048,
                "needs_refetch": False,
                "needs_refetch_reason": None,
            },
            "watch_snapshot": {
                "schema_version": 2,
                "updated_at": "2026-10-22T06:58:00Z",
                "bytes": 8192,
                "needs_refetch": True,
                "needs_refetch_reason": "ttl",
            },
        }
    )
    path = index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")

    report = build_report()
    assert report["index_present"] is True
    assert report["schema_version"] == 2
    rows = {row["name"]: row for row in report["stores"]}
    assert rows["review_draft"]["source"] == "index"
    assert rows["review_draft"]["bytes"] == 2048
    assert rows["review_draft"]["updated_at"] == "2026-10-22T06:59:00Z"
    assert rows["watch_snapshot"]["needs_refetch"] is True
    assert rows["watch_snapshot"]["needs_refetch_reason"] == "ttl"
    assert rows["validation_draft"]["source"] == "config-only"
    assert any("validation_draft" in warning for warning in report["warnings"])


def test_inspect_warns_on_version_drift(data_dir: Path) -> None:
    document = _index_document({"review_draft": {"schema_version": 1, "bytes": 10}}, version=1)
    path = index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")

    report = build_report()
    assert any("版本不一致" in warning for warning in report["warnings"])


def test_inspect_warns_on_index_only_store(data_dir: Path) -> None:
    document = _index_document({"redis_draft": {"schema_version": 2, "bytes": 1}})
    path = index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")

    report = build_report()
    rows = {row["name"]: row for row in report["stores"]}
    assert rows["redis_draft"]["source"] == "index-only"
    assert any("清单漂移" in warning for warning in report["warnings"])


def test_inspect_is_read_only(data_dir: Path) -> None:
    report = build_report()
    assert report["index_present"] is False
    assert not index_path().exists(), "inspect 不得创建索引文件"


def test_inspect_command_exits_zero(repo: Path, data_dir: Path) -> None:
    result = runner.invoke(app, ["state", "inspect"])
    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["backend"] == "indexeddb"
    assert payload["config"].endswith("state.yaml")


# ---------------------------------------------------------------- state reset


def test_reset_requires_confirmation(data_dir: Path) -> None:
    result = runner.invoke(app, ["state", "reset", "--store", "review_draft"])
    assert result.exit_code == 2
    assert "--yes" in result.stderr
    assert not index_path().exists()


def test_reset_unknown_store_exits_usage(data_dir: Path) -> None:
    result = runner.invoke(app, ["state", "reset", "--store", "nope", "--yes"])
    assert result.exit_code == 2
    assert "review_draft" in result.stderr
    assert not index_path().exists()


def test_reset_registers_pending_without_touching_indexeddb(data_dir: Path) -> None:
    result = runner.invoke(app, ["state", "reset", "--store", "review_draft", "--yes"])
    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["store"] == "review_draft"
    assert "下次启动" in payload["applied"]

    document = json.loads(index_path().read_text(encoding="utf-8"))
    pending = document["pending_reset"]
    assert [item["store"] for item in pending] == ["review_draft"]
    assert document["stores"] == {}


def test_reset_is_idempotent_per_store(data_dir: Path) -> None:
    for _ in range(3):
        result = runner.invoke(app, ["state", "reset", "--store", "validation_draft", "--yes"])
        assert result.exit_code == 0, result.stderr
    document = json.loads(index_path().read_text(encoding="utf-8"))
    assert [item["store"] for item in document["pending_reset"]] == ["validation_draft"]


def test_reset_keeps_other_pending_entries_and_store_snapshot(data_dir: Path) -> None:
    path = index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            _index_document(
                {"watch_snapshot": {"schema_version": 2, "bytes": 99}},
            )
        ),
        encoding="utf-8",
    )
    first = runner.invoke(app, ["state", "reset", "--store", "review_draft", "--yes"])
    second = runner.invoke(app, ["state", "reset", "--store", "validation_draft", "--yes"])
    assert first.exit_code == 0 and second.exit_code == 0

    document = json.loads(path.read_text(encoding="utf-8"))
    assert [item["store"] for item in document["pending_reset"]] == [
        "review_draft",
        "validation_draft",
    ]
    assert document["stores"]["watch_snapshot"]["bytes"] == 99
    assert document["schema_version"] == 2


def test_reset_acknowledged_entry_is_not_reported_twice(data_dir: Path) -> None:
    """前端回执后 pending_reset 会被清空；之后 inspect 不应再提示待重置。"""
    path = index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_index_document({})), encoding="utf-8")
    runner.invoke(app, ["state", "reset", "--store", "review_draft", "--yes"])
    document = json.loads(path.read_text(encoding="utf-8"))
    document["pending_reset"] = []
    path.write_text(json.dumps(document), encoding="utf-8")

    assert build_report()["pending_reset"] == []
