"""生产阶段探针（QUOT-562 / 563）：`build_stage_probes` 数据七路 + `build_debate_probes` 辩论三路。

钉住：

| 场景 | 期望 |
|---|---|
| collect_script | sync.yaml 有数据集 → ok；缺文件 / 空数据集 → down |
| collect_process | 四 peer 全在线 → ok；部分 → warn（点名不在线者）；全无 → down |
| collect_store | 当日有记录 → ok；近 7 天无当日 → warn；更旧 / 空库 → down |
| collect_success | 新鲜 ok → ok；超 24h 或降级/空/跳过 → warn；failed 或无 runs → down |
| ingest_watermark | 水位 ≥ 今天 → ok；近 7 天 → warn；更旧 / 无水位 → down |
| ingest_persist | 有行 → ok；0 行 / 无库 → down |
| ingest_idempotent | 当日无重复 → ok；有重复 → warn；当日无数据 → warn；无库 / 无表 → down |
| DEC-006 | 探针前后库与水位文件字节不变（只读，绝不触发采集 / 不写库） |
| /state | 快照带注册表全量（数据七 + 辩论三）；/events 承载 schema 完整的 stage 事件 |

真实 DuckDB 与水位文件都落在 tmp 目录；辩论探针 home 注入 tmp（不读真实家目录）。
不起 peer 进程（判活注入），不联网。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from ladderstock.core.stage_events import (
    HEALTH_DOWN,
    HEALTH_OK,
    HEALTH_WARN,
    MODULE_CLASS_COG,
    MODULE_CLASS_DATA,
    STAGE_ORDER,
)
from ladderstock.data import duckdb_store
from ladderstock.supervisor.health_events import EVENT_STAGE
from ladderstock.supervisor.ledger import Ledger, ModuleRecord
from ladderstock.supervisor.selfheal import SelfHealEngine
from ladderstock.supervisor.stage_monitor import StageHealthMonitor
from ladderstock.web.server import create_app
from ladderstock.web.supervisor_api import build_debate_probes, build_stage_probes

TODAY = datetime.now().astimezone().date()
PEERS = ("senate", "tickhub", "tickhub_us", "wire")


def _root(tmp_path: Path, name: str) -> Path:
    data = tmp_path / name
    data.mkdir(parents=True, exist_ok=True)
    return data


def _probes(
    root: Path,
    *,
    ledger: Ledger | None = None,
    is_pid_alive: Any = None,
    sync_yaml: Path | None = None,
) -> dict[str, Any]:
    return build_stage_probes(
        root,
        ledger=ledger or Ledger(root / "supervisor" / "ledger.json"),
        is_pid_alive=is_pid_alive,
        sync_yaml=sync_yaml,
    )


def _seed_daily(root: Path, day: Any, *, code: str = "600000") -> None:
    duckdb_store.write_dataset(
        "daily_k",
        [{"code": code, "trade_date": day.isoformat(), "close": 12.5}],
        root=root,
    )


def _write_sync_yaml(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "sync.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _ledger_with_pids(tmp_path: Path, pids: dict[str, int | None]) -> Ledger:
    tmp_path.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(tmp_path / "supervisor" / "ledger.json")
    for peer in PEERS:
        ledger.update(ModuleRecord(module=peer, pid=pids.get(peer), state="running"))
    return ledger


def _write_watermark(
    root: Path, *, value: str | None = None, runs: list[dict[str, Any]] | None = None
) -> None:
    doc: dict[str, Any] = {"version": 1, "watermarks": {}, "runs": list(runs or [])}
    if value is not None:
        doc["watermarks"]["daily_k"] = {
            "dataset": "daily_k",
            "value": value,
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "rows": 1,
            "source": "tickhub_kline",
            "status": "ok",
        }
    state_dir = root / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "watermark.json").write_text(
        json.dumps(doc, ensure_ascii=False), encoding="utf-8"
    )


def _run(status: str, *, finished_at: str | None = None, detail: str = "") -> dict[str, Any]:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    return {
        "dataset": "daily_k",
        "mode": "incremental",
        "started_at": now,
        "finished_at": finished_at or now,
        "status": status,
        "rows": 10,
        "source": "tickhub_kline",
        "trail": [],
        "watermark_before": None,
        "watermark_after": None,
        "detail": detail,
    }


# ------------------------------------------------------------- collect_script


def test_collect_script_ok_when_sync_yaml_declares_datasets(tmp_path: Path) -> None:
    root = _root(tmp_path, "script-ok")
    sync_yaml = _write_sync_yaml(tmp_path, "sync:\n  datasets: [daily_k, sentiment]\n")

    sample = _probes(root, sync_yaml=sync_yaml)["collect_script"]()

    assert sample.health == HEALTH_OK
    assert "就绪" in sample.reason


def test_collect_script_down_when_config_missing_or_empty(tmp_path: Path) -> None:
    root = _root(tmp_path, "script-missing")
    probes = _probes(root, sync_yaml=tmp_path / "no-such-sync.yaml")

    missing = probes["collect_script"]()
    assert missing.health == HEALTH_DOWN
    assert "缺失" in missing.reason

    empty_yaml = _write_sync_yaml(tmp_path / "second", "sync:\n  datasets: []\n")
    probes_empty = _probes(_root(tmp_path, "script-empty"), sync_yaml=empty_yaml)
    empty = probes_empty["collect_script"]()
    assert empty.health == HEALTH_DOWN
    assert "数据集" in empty.reason


# ----------------------------------------------------------- collect_process


def test_collect_process_ok_warn_down_by_peer_liveness(tmp_path: Path) -> None:
    alive_root = _root(tmp_path, "process-ok")
    ledger = _ledger_with_pids(alive_root, {peer: 101 for peer in PEERS})
    sample = _probes(alive_root, ledger=ledger, is_pid_alive=lambda pid: pid == 101)[
        "collect_process"
    ]()
    assert sample.health == HEALTH_OK
    assert all(peer in sample.reason for peer in PEERS)

    partial_root = _root(tmp_path, "process-partial")
    ledger = _ledger_with_pids(
        partial_root, {"senate": 101, "tickhub": 101, "tickhub_us": None, "wire": None}
    )
    sample = _probes(partial_root, ledger=ledger, is_pid_alive=lambda pid: pid == 101)[
        "collect_process"
    ]()
    assert sample.health == HEALTH_WARN
    assert "tickhub_us" in sample.reason and "wire" in sample.reason
    assert "senate" not in sample.reason, "在线 peer 不该被点名"

    dead_root = _root(tmp_path, "process-dead")
    ledger = _ledger_with_pids(dead_root, {peer: None for peer in PEERS})
    sample = _probes(dead_root, ledger=ledger, is_pid_alive=lambda pid: False)[
        "collect_process"
    ]()
    assert sample.health == HEALTH_DOWN
    assert "行情" in sample.reason


# ------------------------------------------------------------ collect_store


def test_collect_store_today_ok_recent_warn_stale_down(tmp_path: Path) -> None:
    fresh_root = _root(tmp_path, "store-fresh")
    _seed_daily(fresh_root, TODAY)
    sample = _probes(fresh_root)["collect_store"]()
    assert sample.health == HEALTH_OK

    recent_root = _root(tmp_path, "store-recent")
    _seed_daily(recent_root, TODAY - timedelta(days=3))
    sample = _probes(recent_root)["collect_store"]()
    assert sample.health == HEALTH_WARN
    assert (TODAY - timedelta(days=3)).isoformat() in sample.reason

    stale_root = _root(tmp_path, "store-stale")
    _seed_daily(stale_root, TODAY - timedelta(days=30))
    sample = _probes(stale_root)["collect_store"]()
    assert sample.health == HEALTH_DOWN

    empty_root = _root(tmp_path, "store-empty")
    sample = _probes(empty_root)["collect_store"]()
    assert sample.health == HEALTH_DOWN
    assert "空" in sample.reason


# --------------------------------------------------------- collect_success


def test_collect_success_reads_latest_sync_run(tmp_path: Path) -> None:
    def probe_with(runs: list[dict[str, Any]]) -> Any:
        root = _root(tmp_path, f"success-{len(runs)}-{runs[-1]['status'] if runs else 'none'}")
        _write_watermark(root, runs=runs)
        return _probes(root)["collect_success"]()

    fresh = probe_with([_run("ok")])
    assert fresh.health == HEALTH_OK

    two_days_ago = (
        datetime.now().astimezone() - timedelta(days=2)
    ).isoformat(timespec="seconds")
    stale = probe_with([_run("ok", finished_at=two_days_ago)])
    assert stale.health == HEALTH_WARN
    assert "成功" in stale.reason

    degraded = probe_with([_run("degraded", detail="wire 备源兜底")])
    assert degraded.health == HEALTH_WARN
    assert "wire 备源兜底" in degraded.reason

    assert probe_with([_run("empty")]).health == HEALTH_WARN
    assert probe_with([_run("skipped")]).health == HEALTH_WARN

    failed = probe_with([_run("failed", detail="退出码 1")])
    assert failed.health == HEALTH_DOWN

    none = probe_with([])
    assert none.health == HEALTH_DOWN
    assert "还没有" in none.reason


# --------------------------------------------------------- ingest_watermark


def test_ingest_watermark_fresh_recent_old_missing(tmp_path: Path) -> None:
    def probe_with(value: str | None) -> Any:
        root = _root(tmp_path, f"watermark-{value or 'none'}")
        _write_watermark(root, value=value)
        return _probes(root)["ingest_watermark"]()

    assert probe_with(TODAY.isoformat()).health == HEALTH_OK
    assert probe_with((TODAY - timedelta(days=3)).isoformat()).health == HEALTH_WARN
    old = probe_with((TODAY - timedelta(days=30)).isoformat())
    assert old.health == HEALTH_DOWN
    assert (TODAY - timedelta(days=30)).isoformat() in old.reason
    assert probe_with(None).health == HEALTH_DOWN
    assert "从未" in probe_with(None).reason


# ----------------------------------------------------------- ingest_persist


def test_ingest_persist_rows_zero_and_missing_db(tmp_path: Path) -> None:
    filled = _root(tmp_path, "persist-rows")
    _seed_daily(filled, TODAY)
    sample = _probes(filled)["ingest_persist"]()
    assert sample.health == HEALTH_OK
    assert "1" in sample.reason

    created = _root(tmp_path, "persist-empty-db")
    duckdb_store.connect(duckdb_store.db_path(created)).close()
    sample = _probes(created)["ingest_persist"]()
    assert sample.health == HEALTH_DOWN

    missing = _root(tmp_path, "persist-no-db")
    sample = _probes(missing)["ingest_persist"]()
    assert sample.health == HEALTH_DOWN


# -------------------------------------------------------- ingest_idempotent


def test_ingest_idempotent_ok_duplicate_and_no_data(tmp_path: Path) -> None:
    clean = _root(tmp_path, "idem-ok")
    _seed_daily(clean, TODAY)
    sample = _probes(clean)["ingest_idempotent"]()
    assert sample.health == HEALTH_OK
    assert "幂等" in sample.reason or "没有重复行" in sample.reason

    duplicated = _root(tmp_path, "idem-dupe")
    _seed_daily(duplicated, TODAY)
    conn = duckdb_store.connect(duckdb_store.db_path(duplicated))
    try:
        conn.execute(
            f"INSERT INTO \"daily_k\" VALUES ('600000', DATE '{TODAY}', now(), 'test', '{{}}')"
        )
    finally:
        duckdb_store.close(conn)
    sample = _probes(duplicated)["ingest_idempotent"]()
    assert sample.health == HEALTH_WARN
    assert "重复" in sample.reason

    no_today = _root(tmp_path, "idem-old-only")
    _seed_daily(no_today, TODAY - timedelta(days=1))
    sample = _probes(no_today)["ingest_idempotent"]()
    assert sample.health == HEALTH_WARN
    assert "今天" in sample.reason

    no_table = _root(tmp_path, "idem-no-table")
    duckdb_store.connect(duckdb_store.db_path(no_table)).close()
    assert _probes(no_table)["ingest_idempotent"]().health == HEALTH_DOWN

    no_db = _root(tmp_path, "idem-no-db")
    assert _probes(no_db)["ingest_idempotent"]().health == HEALTH_DOWN


# ----------------------------------------------------------------- DEC-006


def test_probes_are_readonly_never_write(tmp_path: Path) -> None:
    root = _root(tmp_path, "readonly")
    _seed_daily(root, TODAY)
    _write_watermark(root, value=TODAY.isoformat(), runs=[_run("ok")])
    sync_yaml = _write_sync_yaml(tmp_path / "cfg", "sync:\n  datasets: [daily_k]\n")
    ledger = _ledger_with_pids(root / "ledger-home", {peer: 101 for peer in PEERS})

    def fingerprint(data: Path) -> dict[str, str]:
        return {
            str(item.relative_to(data)): hashlib.sha256(item.read_bytes()).hexdigest()
            for item in sorted(data.rglob("*"))
            if item.is_file()
        }

    before = fingerprint(root)
    before_ledger = fingerprint(ledger.path.parent.parent)

    probes = _probes(root, ledger=ledger, is_pid_alive=lambda pid: True, sync_yaml=sync_yaml)
    probes.update(build_debate_probes(home=root))  # 辩论三路同受只读约束
    for stage, probe in probes.items():
        assert stage in STAGE_ORDER, f"探针 {stage} 不在注册表里"
        probe()  # 每个探针都跑，绝不留下写痕

    assert fingerprint(root) == before, "探针在数据目录留下了写痕（违反 DEC-006 只读口径）"
    assert fingerprint(ledger.path.parent.parent) == before_ledger


# ----------------------------------------------------------- /state + /events


def test_state_endpoint_exposes_stages_and_events_carry_schema(tmp_path: Path) -> None:
    root = _root(tmp_path, "wiring")
    sync_yaml = _write_sync_yaml(tmp_path / "cfg", "sync:\n  datasets: [daily_k]\n")
    ledger = _ledger_with_pids(root, {peer: 101 for peer in PEERS})
    from ladderstock.supervisor.health_events import HealthEventBus

    bus = HealthEventBus()
    monitor = StageHealthMonitor(
        bus,
        probes={
            **build_stage_probes(
                root, ledger=ledger, is_pid_alive=lambda pid: pid == 101, sync_yaml=sync_yaml
            ),
            **build_debate_probes(home=root),
        },
    )
    heal = SelfHealEngine(
        ledger,
        _AliveRestarter(),
        clock=lambda: 0.0,
        sleeper=lambda seconds: None,
        event_bus=bus,
        stage_monitor=monitor,
    )
    app = create_app(data_root=root, dist_dir=root / "no-dist", supervisor_engine=heal)
    client = TestClient(app, base_url="http://127.0.0.1:5950", client=("127.0.0.1", 54321))

    body = client.get("/api/supervisor/state").json()
    assert [item["stage"] for item in body["stages"]] == list(STAGE_ORDER)
    unobserved = body["stages"][0]
    assert unobserved["health"] == HEALTH_DOWN
    assert unobserved["reason"], "未观测占位也要有白话原因，不能是空串"

    monitor.observe()  # TestClient 不触发 startup；显式跑首轮观测（发注册表全量）
    events = client.get("/api/supervisor/events").json()
    stage_events = [item for item in events["events"] if item["event"] == EVENT_STAGE]
    assert len(stage_events) == len(STAGE_ORDER), "首轮观测后 /events 应发全量 stage 事件"
    debate = [item for item in stage_events if item["module"] == "debate"]
    data = [item for item in stage_events if item["module"] != "debate"]
    assert len(data) == 7 and len(debate) == 3
    for item in data:
        assert item["module_class"] == MODULE_CLASS_DATA
    for item in debate:
        assert item["module_class"] == MODULE_CLASS_COG
    for item in stage_events:
        assert item["as_of"]


class _AliveRestarter:
    def is_alive(self, entry: Any) -> bool:
        return True

    def restart(self, module: str) -> Any:
        raise AssertionError("只读观测不得触发重启")


class _SilentNotifier:
    def failure(self, **kwargs: Any) -> None:
        return None

    def gave_up(self, **kwargs: Any) -> None:
        return None

    def recovered(self, **kwargs: Any) -> None:
        return None
