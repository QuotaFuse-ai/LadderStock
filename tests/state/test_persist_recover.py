"""状态恢复（草稿恢复 + 迁移 + 需重取）的跨进程契约。

前端持久化层跑在浏览器里，Python 只在**边界上**与它对齐：

1. `frontend/persist/migrate.ts` 的迁移语义（v1→v2、失败丢弃、日志上限）；
2. `frontend/persist/freshness.ts` 的「可恢复 / 需重取」判定顺序与枚举；
3. `frontend/persist/indexReporter.ts` 写出的索引字段，必须正好是
   `ladderstock.cli.state.build_report()` 读取的那一套（字段名、键口径、白名单）；
4. `ldst state reset` 的登记 → 前端回执 → `state inspect` 不再提示的闭环。

这里既做**静态契约**断言（改一边不改另一边就红），也在 node 可用时
**真跑**一次前端 vitest 套件（fake-indexeddb，无网络），避免只对着源码
正则而漏掉「实际 rehydrate 行为」。

文档依据：源文档 §6.4「schema 版本与迁移」、§6.4「可恢复 / 需重取」。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from ladderstock.cli.state import build_report, index_path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
PERSIST = FRONTEND / "persist"
STORES = FRONTEND / "stores"

MIGRATE = PERSIST / "migrate.ts"
FRESHNESS = PERSIST / "freshness.ts"
INDEX_REPORTER = PERSIST / "indexReporter.ts"
BOOTSTRAP = PERSIST / "bootstrap.ts"
WATCH_STORE = STORES / "watchSnapshot.ts"
REVIEW_STORE = STORES / "reviewDraft.ts"
VALIDATION_STORE = STORES / "validationDraft.ts"
MANIFEST_PATH = STORES / "manifest.json"
STATE_CONFIG = ROOT / "config" / "state.yaml"
VITEST_BIN = FRONTEND / "node_modules" / "vitest" / "vitest.mjs"

#: 前端 vitest 套件的下限（当前 3 个文件 23 个用例）；只增不减。
MIN_FRONTEND_CASES = 20


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _manifest() -> dict[str, Any]:
    return json.loads(_source(MANIFEST_PATH))


def _stores_by_kind(kind: str) -> list[dict[str, Any]]:
    return [store for store in _manifest()["stores"] if store["kind"] == kind]


def _migration_table() -> set[str]:
    """`MIGRATIONS` 里登记了 v1→v2 步骤的分片 id。"""
    block = re.search(r"const MIGRATIONS[^{]*\{(.*?)\n\}", _source(MIGRATE), re.S)
    assert block, "migrate.ts 里找不到 MIGRATIONS 表"
    return set(re.findall(r"(\w+):\s*\{\s*1:", block.group(1)))


def _refetch_reasons() -> set[str]:
    match = re.search(r"export type RefetchReason\s*=([^\n]+)", _source(FRESHNESS))
    assert match, "freshness.ts 里找不到 RefetchReason"
    return set(re.findall(r"'([a-z-]+)'", match.group(1)))


def _const_int(name: str, path: Path = MIGRATE) -> int:
    match = re.search(rf"const {name}\s*=\s*(\d+)", _source(path))
    assert match, f"{path.name} 里找不到 {name}"
    return int(match.group(1))


@pytest.fixture()
def data_dir(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    return tmp_path


def _write_index(document: dict[str, Any]) -> Path:
    path = index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# 1. 分片清单：草稿可恢复、快照需重取
# --------------------------------------------------------------------------- #


def test_snapshot_shards_are_not_recoverable_and_drafts_are() -> None:
    snapshots = _stores_by_kind("snapshot")
    drafts = _stores_by_kind("draft")
    assert snapshots, "manifest 至少要有一个快照分片"
    assert drafts, "manifest 至少要有一个草稿分片"
    for store in snapshots:
        assert store["recoverable"] is False, f"{store['id']} 是快照，不能标记为可恢复"
        assert store.get("ttlSec"), f"{store['id']} 是快照，必须声明 ttlSec"
    for store in drafts:
        assert store["recoverable"] is True, f"{store['id']} 是草稿，必须可恢复"
        assert "ttlSec" not in store, f"{store['id']} 是草稿，不该有 TTL"


def test_snapshot_ttl_matches_config_and_frontend_constant() -> None:
    """manifest.ttlSec ↔ config/state.yaml ttl_sec ↔ watchSnapshot.ts 常量。"""
    snapshot = _stores_by_kind("snapshot")[0]
    config = {
        item["name"]: item
        for item in yaml.safe_load(_source(STATE_CONFIG))["state"]["stores"]
    }
    assert config[snapshot["persistKey"]]["ttl_sec"] == snapshot["ttlSec"]
    assert re.search(
        rf"WATCH_SNAPSHOT_TTL_SEC\s*=\s*META\.ttlSec\s*\?\?\s*{snapshot['ttlSec']}",
        _source(WATCH_STORE),
    ), "watchSnapshot.ts 的兜底 TTL 与 manifest 不一致"


def test_stale_snapshot_rows_are_hidden_not_rendered() -> None:
    """需重取时 `selectTrustedRows` 必须返回空数组，界面不回调旧值。"""
    source = _source(WATCH_STORE)
    assert "selectFreshness(state, now).needsRefetch ? [] : state.rows" in source


# --------------------------------------------------------------------------- #
# 2. 迁移：v1 → v2 覆盖、失败丢弃、日志上限
# --------------------------------------------------------------------------- #


#: v2 之后才引入的分片：不存在 v1 数据，不写 v1→v2 迁移（watch_settings，DEC-037）。
POST_V2_STORES = {"watchSettings"}


def test_every_persisted_shard_has_a_v1_upgrade_step() -> None:
    declared = {store["id"] for store in _manifest()["stores"]}
    assert POST_V2_STORES <= declared, (
        f"豁免名单里的分片已不在 manifest：{sorted(POST_V2_STORES - declared)}"
    )
    legacy = declared - POST_V2_STORES
    assert legacy <= _migration_table(), (
        f"这些分片没有 v1→v2 迁移步骤：{sorted(legacy - _migration_table())}"
    )


def test_migration_never_propagates_errors() -> None:
    """任何失败路径都返回 fallback（初始态），不向启动流程抛错。"""
    source = _source(MIGRATE)
    body = source[source.index("export function migratePersistedState") :]
    assert body.count("return fallback") >= 3, "丢弃路径必须全部回落到初始态"
    assert "if (!step) throw new Error(" in body, "缺迁移步骤要转成丢弃，不能静默放行"


def test_review_draft_v1_keeps_only_the_single_text_block() -> None:
    """v1 只有一个大文本框，v2 必须拆成五段并把正文落到「情绪」。"""
    migrate = _source(MIGRATE)
    assert re.search(r"interface ReviewDraftV1 \{[^}]*text\?", migrate, re.S)
    assert "sentiment: text," in migrate
    for blank in ("capital: ''", "theme: ''", "dragonTiger: ''", "leader: ''"):
        assert blank in migrate, f"v2 其余四段应留空：{blank}"


def test_review_section_keys_are_aligned_across_files() -> None:
    keys = re.findall(r"REVIEW_SECTION_KEYS = \[([^\]]+)\]", _source(MIGRATE))
    assert keys, "migrate.ts 里找不到 REVIEW_SECTION_KEYS"
    migrate_keys = re.findall(r"'(\w+)'", keys[0])
    store_keys = re.findall(r"key: '(\w+)'", _source(REVIEW_STORE))
    assert migrate_keys == store_keys == ["sentiment", "capital", "theme", "dragonTiger", "leader"]


def test_validation_draft_v1_conditions_get_stable_ids_and_confirmed() -> None:
    migrate = _source(MIGRATE)
    assert "`v1-${index}`" in migrate, "v1 条件没有 id，迁移必须补稳定 id"
    assert "confirmed: condition.confirmed === true" in migrate
    assert "confirmed: boolean" in _source(VALIDATION_STORE)


def test_watch_snapshot_v1_has_no_fetched_at_and_is_treated_as_empty() -> None:
    migrate = _source(MIGRATE)
    assert re.search(r"interface WatchSnapshotV1 \{[^}]*\}", migrate, re.S)
    assert "fetchedAt: null" in migrate, "v1 无法判断 TTL，必须置空以强制重取"
    # freshness 侧：fetchedAt 为空 → reason=empty → needsRefetch
    assert "if (age === null || !snapshot.tradeDate)" in _source(FRESHNESS)


def test_migration_log_is_bounded() -> None:
    limit = _const_int("LOG_LIMIT")
    assert limit == 20, "迁移日志只保留最近 20 条，避免无限增长"
    assert ".slice(-LOG_LIMIT)" in _source(MIGRATE)


# --------------------------------------------------------------------------- #
# 3. 需重取判定：枚举、顺序、容差
# --------------------------------------------------------------------------- #


def test_refetch_reasons_are_exactly_the_documented_four() -> None:
    expected = {"empty", "ttl", "trade-date-changed", "clock-skew"}
    assert _refetch_reasons() == expected
    labels = re.search(r"FRESHNESS_LABELS[^{]*\{(.*?)\n\}", _source(FRESHNESS), re.S)
    assert labels, "freshness.ts 里找不到 FRESHNESS_LABELS"
    keys = re.findall(r"(?:^|\n)\s+'?([a-z][a-z-]*)'?:", labels.group(1))
    assert set(keys) == expected, f"提示文案与枚举不同步：{sorted(keys)}"


def test_ttl_is_checked_before_trade_date_change() -> None:
    """顺序是契约：同一份快照既过期又跨日时，先说「超过 TTL」。"""
    source = _source(FRESHNESS)
    ttl = source.index("age > ttlSec")
    trade_date = source.index("snapshot.tradeDate !== localDateIso(now)")
    assert ttl < trade_date, "TTL 判定必须先于交易日判定"


def test_clock_skew_tolerance_is_sixty_seconds() -> None:
    assert _const_int("CLOCK_SKEW_TOLERANCE_SEC", FRESHNESS) == 60
    assert "age < -CLOCK_SKEW_TOLERANCE_SEC" in _source(FRESHNESS)


def test_drafts_are_always_recoverable_without_freshness_checks() -> None:
    """索引上报只对快照算 freshness；草稿的 needs_refetch 恒为 false。"""
    source = _source(INDEX_REPORTER)
    assert "if (meta.kind === 'snapshot')" in source
    assert "let needsRefetch = false" in source


# --------------------------------------------------------------------------- #
# 4. 索引契约：前端写什么，Python 就读什么
# --------------------------------------------------------------------------- #


def test_index_is_keyed_by_persist_key_not_store_id() -> None:
    """`build_report()` 按 config/state.yaml 的 name 取行，索引必须同名落键。"""
    assert "stores[handle.meta.persistKey] = indexEntry(" in _source(INDEX_REPORTER)
    names = {
        item["name"]
        for item in yaml.safe_load(_source(STATE_CONFIG))["state"]["stores"]
    }
    assert {store["persistKey"] for store in _manifest()["stores"]} <= names


def test_index_entry_carries_the_fields_python_reads() -> None:
    source = _source(INDEX_REPORTER)
    for field in (
        "schema_version",
        "updated_at",
        "bytes",
        "needs_refetch",
        "needs_refetch_reason",
        "ttl_sec",
    ):
        assert f"{field}:" in source, f"索引条目缺少 Python 侧读取的字段：{field}"


def test_reset_whitelist_only_covers_declared_shards() -> None:
    source = _source(INDEX_REPORTER)
    assert "known.has(persistKey)" in source, "回执删除前必须过 manifest 白名单"


def test_python_reads_needs_refetch_from_index(data_dir: Path) -> None:
    snapshot = _stores_by_kind("snapshot")[0]["persistKey"]
    _write_index(
        {
            "backend": "indexeddb",
            "database": "ladderstock-state",
            "schema_version": 2,
            "generated_at": "2026-10-28T06:00:00Z",
            "stores": {
                snapshot: {
                    "store_id": _stores_by_kind("snapshot")[0]["id"],
                    "kind": "snapshot",
                    "schema_version": 2,
                    "updated_at": "2026-10-28T05:50:00Z",
                    "bytes": 4096,
                    "recoverable": False,
                    "needs_refetch": True,
                    "needs_refetch_reason": "trade-date-changed",
                    "ttl_sec": 300,
                },
                "review_draft": {
                    "store_id": "reviewDraft",
                    "kind": "draft",
                    "schema_version": 2,
                    "updated_at": "2026-10-28T05:59:00Z",
                    "bytes": 512,
                    "recoverable": True,
                    "needs_refetch": False,
                    "needs_refetch_reason": None,
                    "ttl_sec": None,
                },
            },
        }
    )

    rows = {row["name"]: row for row in build_report()["stores"]}
    assert rows[snapshot]["needs_refetch"] is True
    assert rows[snapshot]["needs_refetch_reason"] == "trade-date-changed"
    assert rows[snapshot]["source"] == "index"
    assert rows["review_draft"]["needs_refetch"] is False
    assert rows["review_draft"]["bytes"] == 512


# --------------------------------------------------------------------------- #
# 5. 草稿恢复闭环：reset 登记 → inspect 提示 → 前端回执后消失
# --------------------------------------------------------------------------- #


def test_draft_reset_round_trip_is_acknowledged_once(data_dir: Path) -> None:
    path = _write_index(
        {
            "backend": "indexeddb",
            "database": "ladderstock-state",
            "schema_version": 2,
            "generated_at": "2026-10-28T06:00:00Z",
            "stores": {
                "review_draft": {
                    "store_id": "reviewDraft",
                    "kind": "draft",
                    "schema_version": 2,
                    "updated_at": "2026-10-28T05:59:00Z",
                    "bytes": 512,
                    "recoverable": True,
                    "needs_refetch": False,
                    "needs_refetch_reason": None,
                    "ttl_sec": None,
                }
            },
        }
    )
    from typer.testing import CliRunner

    from ladderstock.cli.main import app

    runner = CliRunner()
    result = runner.invoke(app, ["state", "reset", "--store", "review_draft", "--yes"])
    assert result.exit_code == 0, result.stderr

    document = json.loads(path.read_text(encoding="utf-8"))
    assert [item["store"] for item in document["pending_reset"]] == ["review_draft"]
    assert build_report()["pending_reset"], "登记后 inspect 必须提示待重置"

    # 前端启动时 syncPendingResets() 删掉分片并回执；回执后 pending_reset 清空。
    document["pending_reset"] = []
    document["stores"]["review_draft"]["updated_at"] = None
    path.write_text(json.dumps(document), encoding="utf-8")
    assert build_report()["pending_reset"] == []


def test_bootstrap_clears_reset_registrations_before_hydrating() -> None:
    """先删被登记的分片再 rehydrate，避免「刚恢复就又被删」的竞态。"""
    source = _source(BOOTSTRAP)
    reset = source.index("syncPendingResets()")
    hydrate = source.index("store.rehydrate()")
    assert reset < hydrate, "启动顺序必须是 reset 回执 → rehydrate"
    assert "catch" in source, "任一步失败都只降级，不阻塞看板启动"


# --------------------------------------------------------------------------- #
# 6. 真跑一次前端套件（fake-indexeddb，无网络）
# --------------------------------------------------------------------------- #


def test_frontend_persistence_suite_passes() -> None:
    node = shutil.which("node")
    if node is None or not VITEST_BIN.is_file():
        pytest.skip("本机没有 node / 前端依赖，跳过前端持久化套件")

    env = dict(os.environ)
    env["CI"] = "1"
    completed = subprocess.run(
        [node, str(VITEST_BIN), "run"],
        cwd=FRONTEND,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=300,
        check=False,
    )
    output = f"{completed.stdout}\n{completed.stderr}"
    # vitest 在 CI=1 下仍可能输出颜色控制符，解析前先剥掉
    plain = re.sub(r"\x1b\[[0-9;]*m", "", output)
    assert completed.returncode == 0, f"前端持久化套件失败：\n{plain[-3000:]}"
    assert "failed" not in plain.lower(), plain[-3000:]
    match = re.search(r"Tests\s+(\d+) passed", plain)
    assert match, f"没解析到用例统计：\n{plain[-2000:]}"
    assert int(match.group(1)) >= MIN_FRONTEND_CASES
