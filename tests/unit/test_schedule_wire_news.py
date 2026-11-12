"""07:30 题材资讯每日采集任务（QUOT-554，kind=`wire_news`）。

钉住四件事：

1. 注册面：schema / 结构自检 / 内置默认表 / 仓库 `schedule.yaml` 都认 `wire_news`，
   且默认表第一条就是 `07:30` 的 `wire_news_daily`（共 15 条）；
2. 采集面：触发的是 Wire peer 自己的 `qfw run`（cwd 钉在 Wire 目录，密钥由 Wire
   自己从 .env 加载，不进 LadderStock 的 argv/日志）；退出码 0/8/其余 → ok/degraded/
   failed，超时与启动失败如实记 failed，Wire 未安装 → skipped；
3. 分发面：两段状态取差者——采集失败不挡落库，但整体如实记 failed（明细保留同步
   结果）；采集后同步再 skipped 也是 failed；**每天跑（含周末）**，与 sync_close
   的仅交易日口径刻意不同；
4. 落库面（真链路，离线桩）：模拟当日调度自动跑后 theme_news 水位=当日、
   `GET /api/market/theme` available=true，全程零手动 sync；同日重复触发不膨胀；
   部分源失败如实 degraded、全失败不推水位不编数。
"""

from __future__ import annotations

import json
import subprocess
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fixtures.fake_peers import (
    SYNC_DOC,
    FakeTransport,
    make_registry,
    wire_digest_tool_result,
)

from ladderstock.cli.schedule import (
    DEFAULT_JOBS,
    WIRE_RUN_TIMEOUT_SEC,
    _collect_wire_news,
    _default_table,
    _structural_problems,
    load_schedule,
    run_job,
    validate_table,
)
from ladderstock.data import duckdb_store, watermark
from ladderstock.data.provider_registry import SourceUnavailable
from ladderstock.errors import EXIT_CONFIG, LadderStockError
from ladderstock.web.server import create_app

#: 2026-11-10 是周二；07:30 触发。2026-11-07 是周六——资讯不收盘，周末照跑。
DAY = "2026-11-10"
MOMENT = datetime(2026, 11, 10, 7, 30, 0)
WEEKEND_DAY = "2026-11-07"
WEEKEND_MOMENT = datetime(2026, 11, 7, 7, 30, 0)

WIRE_NEWS_JOB = {
    "id": "wire_news_daily",
    "name": "07:30 题材资讯采集",
    "kind": "wire_news",
    "trigger": "07:30",
    "enabled": True,
}

POOL = ["ai", "semiconductor"]


# --------------------------------------------------------------------------- #
# 离线桩：Wire 目录 / qfw 子进程 / wire_get_digest 传输
# --------------------------------------------------------------------------- #

def _wire_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, installed: bool = True) -> Path:
    """把 peer 目录解析钉在 tmp；installed=True 时造出 qfw 入口文件。"""
    wire_dir = tmp_path / "wire-peer"
    if installed:
        entry = wire_dir / ".venv" / "Scripts" / "qfw.exe"
        entry.parent.mkdir(parents=True, exist_ok=True)
        entry.write_bytes(b"")
    monkeypatch.setattr(
        "ladderstock.supervisor.module_adapters.default_peer_dir",
        lambda key, env=None: wire_dir,
    )
    return wire_dir


def _qfw_summary(returncode: int = 0, day: str = DAY) -> dict[str, Any]:
    """`qfw --output json run` 的真实摘要形状（Wire `RunResult.summary`）。"""
    return {
        "run_id": f"{day}-stub",
        "day": day,
        "status": "ok" if returncode == 0 else ("partial" if returncode == 8 else "unavailable"),
        "exit_code": returncode,
        "items": 24,
        "sources_total": 10,
        "sources_ok": 10 if returncode == 0 else 7,
        "sources_degraded": 0 if returncode == 0 else 2,
        "sources_unavailable": 0 if returncode == 0 else 1,
        "digests": 12,
    }


def _fake_qfw(
    monkeypatch: pytest.MonkeyPatch,
    *,
    returncode: int = 0,
    stdout: str | None = None,
    error: Exception | None = None,
) -> list[tuple[list[str], dict[str, Any]]]:
    """替换 `subprocess.run`，记录 argv 与 kwargs；返回调用记录。"""
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> SimpleNamespace:
        calls.append((list(argv), kwargs))
        if error is not None:
            raise error
        text = stdout if stdout is not None else json.dumps(_qfw_summary(returncode))
        return SimpleNamespace(returncode=returncode, stdout=text, stderr="")

    monkeypatch.setattr("ladderstock.cli.schedule.subprocess.run", fake_run)
    return calls


def _digest_transport(
    day: str = DAY, *, status: str = "ok", fail_topics: tuple[str, ...] = ()
) -> FakeTransport:
    """`wire_get_digest` 逐赛道回真实 FastMCP 外壳形状（structuredContent.result）。"""

    def handler(params: dict[str, Any]) -> dict[str, Any]:
        topic = str(params["topic"])
        if topic in fail_topics:
            raise SourceUnavailable(f"未知赛道: {topic}")
        return wire_digest_tool_result(topic, day, status=status)

    return FakeTransport(handlers={"wire_get_digest": handler})


@pytest.fixture()
def data_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    return tmp_path


# --------------------------------------------------------------------------- #
# 1. 注册面
# --------------------------------------------------------------------------- #

def test_schema_accepts_wire_news_kind() -> None:
    validate_table(_default_table())  # 不抛即通过（新 kind 已进 enum）


def test_schedule_schema_enum_contains_wire_news() -> None:
    schema = json.loads(
        (
            Path(__file__).resolve().parents[2] / "config" / "schema" / "schedule.schema.json"
        ).read_text(encoding="utf-8")
    )
    kinds = schema["properties"]["jobs"]["items"]["properties"]["kind"]["enum"]
    assert "wire_news" in kinds


def test_default_table_declares_wire_news_first_at_0730() -> None:
    assert len(DEFAULT_JOBS) == 15
    assert DEFAULT_JOBS[0]["id"] == "wire_news_daily"
    jobs = [job for job in _default_table()["jobs"] if job.get("kind") == "wire_news"]
    assert len(jobs) == 1
    assert jobs[0]["trigger"] == "07:30"
    assert jobs[0]["enabled"] is True


def test_repo_schedule_declares_wire_news(repo: Path) -> None:
    table, warnings, _source = load_schedule()
    assert warnings == []
    jobs = [job for job in table["jobs"] if job.get("kind") == "wire_news"]
    assert [job["trigger"] for job in jobs] == ["07:30"]
    assert jobs[0]["enabled"] is True


def test_structural_self_check_knows_wire_news() -> None:
    assert _structural_problems(_default_table()) == []


# --------------------------------------------------------------------------- #
# 2. 采集面：qfw run 子进程契约
# --------------------------------------------------------------------------- #

def test_collect_skips_when_wire_not_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_home(tmp_path, monkeypatch, installed=False)
    calls = _fake_qfw(monkeypatch)

    result = _collect_wire_news()

    assert result["status"] == "skipped"
    assert "Wire 未安装" in result["detail"]
    assert calls == []


def test_collect_invokes_qfw_run_in_wire_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wire_dir = _wire_home(tmp_path, monkeypatch)
    calls = _fake_qfw(monkeypatch)

    result = _collect_wire_news()

    assert result["status"] == "ok", result
    assert len(calls) == 1
    argv, kwargs = calls[0]
    assert argv == [str(wire_dir / ".venv" / "Scripts" / "qfw.exe"), "--output", "json", "run"]
    # cwd 钉在 Wire 目录：QF_WIRE_API_KEY 由 Wire 自己的 dotenv 加载，不经 LadderStock
    assert kwargs["cwd"] == str(wire_dir)
    assert kwargs["timeout"] == WIRE_RUN_TIMEOUT_SEC
    assert "采集 24 条资讯" in result["detail"]
    assert "信源 10/10 成功" in result["detail"]


def test_collect_never_leaks_api_key_into_argv_or_detail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = "sk-quota554-sentinel-do-not-log"
    monkeypatch.setenv("QF_WIRE_API_KEY", sentinel)
    _wire_home(tmp_path, monkeypatch)
    calls = _fake_qfw(monkeypatch)

    result = _collect_wire_news()

    assert result["status"] == "ok"
    assert sentinel not in json.dumps(calls, ensure_ascii=False, default=str)
    assert sentinel not in result["detail"]


@pytest.mark.parametrize(
    ("returncode", "expected"),
    [(0, "ok"), (8, "degraded"), (4, "failed"), (3, "failed")],
)
def test_collect_maps_wire_exit_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, returncode: int, expected: str
) -> None:
    """Wire `core/errors.py` 口径：0 全成、8 部分源失败、4 不可用、3 配置错。"""
    _wire_home(tmp_path, monkeypatch)
    _fake_qfw(monkeypatch, returncode=returncode)

    result = _collect_wire_news()

    assert result["status"] == expected, result
    if expected == "degraded":
        assert "部分源不可用" in result["detail"]


def test_collect_without_json_summary_reports_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_home(tmp_path, monkeypatch)
    _fake_qfw(monkeypatch, returncode=4, stdout="不是 JSON 的崩溃输出")

    result = _collect_wire_news()

    assert result["status"] == "failed"
    assert "退出码 4" in result["detail"]


def test_collect_timeout_is_failed_not_hang(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_home(tmp_path, monkeypatch)
    _fake_qfw(
        monkeypatch,
        error=subprocess.TimeoutExpired(cmd="qfw", timeout=WIRE_RUN_TIMEOUT_SEC),
    )

    result = _collect_wire_news()

    assert result["status"] == "failed"
    assert "超时" in result["detail"]


def test_collect_spawn_error_is_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _wire_home(tmp_path, monkeypatch)
    _fake_qfw(monkeypatch, error=OSError("拒绝访问"))

    result = _collect_wire_news()

    assert result["status"] == "failed"
    assert "启动失败" in result["detail"]


# --------------------------------------------------------------------------- #
# 3. 分发面：两段状态合成（sync_one 用桩替换，只验接线）
# --------------------------------------------------------------------------- #

def _sync_row(status: str) -> dict[str, Any]:
    """按 `sync_one` 的真实行形状造桩——字段名对不上就该让测试红。"""
    return {
        "dataset": "theme_news",
        "status": status,
        "rows": 2 if status in ("ok", "degraded") else 0,
        "watermark_after": DAY if status in ("ok", "degraded") else None,
        "detail": f"桩：{status}",
    }


def _patch_sync_one(monkeypatch: pytest.MonkeyPatch, row: dict[str, Any]) -> list[str]:
    calls: list[str] = []

    def fake_sync_one(registry: Any, store: Any, dataset: str, **kwargs: Any) -> dict[str, Any]:
        calls.append(dataset)
        return dict(row)

    monkeypatch.setattr("ladderstock.cli.sync.sync_one", fake_sync_one)
    return calls


def _run(
    data_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    collect_code: int = 0,
    sync_status: str = "ok",
    now: datetime = MOMENT,
) -> dict[str, Any]:
    _wire_home(data_home, monkeypatch)
    _fake_qfw(monkeypatch, returncode=collect_code)
    _patch_sync_one(monkeypatch, _sync_row(sync_status))
    registry = make_registry(FakeTransport(), data_home)
    return run_job(
        WIRE_NEWS_JOB, now=now, timezone="Asia/Shanghai", root=data_home, registry=registry
    )


@pytest.mark.parametrize(
    ("collect_code", "sync_status", "expected"),
    [
        (0, "ok", "ok"),
        (8, "ok", "degraded"),  # 部分 RSS 源 403 → 如实降级，不补造
        (4, "ok", "failed"),  # 采集失败不挡落库，但当日新鲜没达成，整体如实 failed
        (0, "degraded", "degraded"),
        (0, "empty", "degraded"),  # 当日无内容：水位保持，不拿旧数据顶替
        (0, "skipped", "failed"),  # 采集已跑过，同步再 skipped 就是失败
        (0, "failed", "failed"),
        (4, "skipped", "failed"),
    ],
)
def test_run_job_composes_collect_and_sync_status(
    data_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    collect_code: int,
    sync_status: str,
    expected: str,
) -> None:
    result = _run(data_home, monkeypatch, collect_code=collect_code, sync_status=sync_status)

    assert result["status"] == expected, result
    assert result["dataset"] == "theme_news"
    assert result["date"] == DAY
    assert result["collect_status"] == {0: "ok", 8: "degraded", 4: "failed"}[collect_code]
    assert result["sync_status"] == sync_status


def test_run_job_runs_on_weekend(data_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """资讯不收盘：周六 07:30 照跑，与 sync_close 的仅交易日口径刻意不同。"""
    result = _run(data_home, monkeypatch, now=WEEKEND_MOMENT)

    assert result["status"] == "ok", result
    assert result["date"] == WEEKEND_DAY


def test_run_job_passes_rows_and_watermark_through(
    data_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _run(data_home, monkeypatch)

    assert result["rows"] == 2
    assert result["watermark_after"] == DAY
    assert "同步：" in result["detail"]


def test_run_job_sync_config_error_folds_to_failed(
    data_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_home(data_home, monkeypatch)
    _fake_qfw(monkeypatch)

    def raise_boundary(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise LadderStockError("主备跨 peer", EXIT_CONFIG)

    monkeypatch.setattr("ladderstock.cli.sync.sync_one", raise_boundary)
    registry = make_registry(FakeTransport(), data_home)

    result = run_job(
        WIRE_NEWS_JOB, now=MOMENT, timezone="Asia/Shanghai", root=data_home, registry=registry
    )

    assert result["status"] == "failed"
    assert "同步中止" in result["detail"]
    assert "qfw run" in result["detail"]  # 采集段的明细不丢


def test_run_job_wire_absent_skips_without_registry(
    data_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_home(data_home, monkeypatch, installed=False)

    def boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Wire 未安装不应构造 registry / 跑同步")

    monkeypatch.setattr("ladderstock.data.provider_registry.ProviderRegistry", boom)
    monkeypatch.setattr("ladderstock.cli.sync.sync_one", boom)

    result = run_job(WIRE_NEWS_JOB, now=MOMENT, timezone="Asia/Shanghai", root=data_home)

    assert result["status"] == "skipped"
    assert "Wire 未安装" in result["detail"]
    assert not (data_home / "archive").exists()


# --------------------------------------------------------------------------- #
# 4. 落库面：真 sync_one + 离线桩，水位 / 分区 / 幂等 / 诚实降级
# --------------------------------------------------------------------------- #

def test_auto_run_lands_partition_and_advances_watermark(
    data_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_home(data_home, monkeypatch)
    _fake_qfw(monkeypatch)
    transport = _digest_transport(DAY)
    registry = make_registry(transport, data_home, sync=SYNC_DOC)

    result = run_job(
        WIRE_NEWS_JOB, now=MOMENT, timezone="Asia/Shanghai", root=data_home, registry=registry
    )

    assert result["status"] == "ok", result
    assert [call.params for call in transport.calls] == [{"topic": t} for t in POOL]
    partition = duckdb_store.partition_path("theme_news", date.fromisoformat(DAY))
    assert partition.is_file(), f"theme_news 应落 {partition}"
    assert duckdb_store.dataset_rows("theme_news", root=data_home) == 2
    assert watermark.WatermarkStore(root=data_home).value("theme_news") == DAY
    assert result["watermark_after"] == DAY


def test_auto_run_is_idempotent_same_day(data_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _wire_home(data_home, monkeypatch)
    _fake_qfw(monkeypatch)

    for _ in range(2):
        registry = make_registry(_digest_transport(DAY), data_home, sync=SYNC_DOC)
        result = run_job(
            WIRE_NEWS_JOB, now=MOMENT, timezone="Asia/Shanghai", root=data_home, registry=registry
        )
        assert result["status"] == "ok", result

    # replace 整日覆盖：同日重复触发不新增分区、不膨胀行数
    assert duckdb_store.dataset_rows("theme_news", root=data_home) == 2
    day_dir = duckdb_store.partition_path("theme_news", date.fromisoformat(DAY)).parent
    assert [p.name for p in day_dir.glob("*.parquet")] == ["theme_news.parquet"]


def test_partial_digest_degrades_but_watermark_stays_honest(
    data_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LLM 上游不可达 → Wire 回 partial 兜底摘要：degraded 落库，水位仍是 data_as_of。"""
    _wire_home(data_home, monkeypatch)
    _fake_qfw(monkeypatch, returncode=8)
    registry = make_registry(_digest_transport(DAY, status="partial"), data_home, sync=SYNC_DOC)

    result = run_job(
        WIRE_NEWS_JOB, now=MOMENT, timezone="Asia/Shanghai", root=data_home, registry=registry
    )

    assert result["status"] == "degraded", result
    assert result["sync_status"] == "degraded"
    assert watermark.WatermarkStore(root=data_home).value("theme_news") == DAY


def test_all_topics_unavailable_fails_without_watermark(
    data_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """全赛道不可达：不编数、不推水位、不落分区，如实 failed。"""
    _wire_home(data_home, monkeypatch)
    _fake_qfw(monkeypatch)
    registry = make_registry(
        _digest_transport(DAY, fail_topics=tuple(POOL)), data_home, sync=SYNC_DOC
    )

    result = run_job(
        WIRE_NEWS_JOB, now=MOMENT, timezone="Asia/Shanghai", root=data_home, registry=registry
    )

    assert result["status"] == "failed", result
    assert result["sync_status"] == "skipped"  # optional 数据集的同步口径
    assert watermark.WatermarkStore(root=data_home).value("theme_news") is None
    assert not duckdb_store.partition_path("theme_news", date.fromisoformat(DAY)).exists()


def test_weekend_auto_run_lands_weekend_digest(
    data_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _wire_home(data_home, monkeypatch)
    _fake_qfw(monkeypatch)
    registry = make_registry(_digest_transport(WEEKEND_DAY), data_home, sync=SYNC_DOC)

    result = run_job(
        WIRE_NEWS_JOB,
        now=WEEKEND_MOMENT,
        timezone="Asia/Shanghai",
        root=data_home,
        registry=registry,
    )

    assert result["status"] == "ok", result
    assert watermark.WatermarkStore(root=data_home).value("theme_news") == WEEKEND_DAY


# --------------------------------------------------------------------------- #
# 5. 验收模拟：调度自动跑 → 水位=当日 → /api/market/theme available=true
# --------------------------------------------------------------------------- #

def test_acceptance_scheduled_run_feeds_theme_api_without_manual_sync(
    data_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """QUOT-554 验收口径（模拟当日）：只跑一次调度任务，不手动 `ldst sync`。

    registry 走生产同款构造路径（`ProviderRegistry(root=...)` 被桩替换），
    采集走 qfw 桩、取数走离线 transport——链路其余部分全是真代码。
    """
    _wire_home(data_home, monkeypatch)
    _fake_qfw(monkeypatch)
    registry = make_registry(_digest_transport(DAY), data_home, sync=SYNC_DOC)
    monkeypatch.setattr(
        "ladderstock.data.provider_registry.ProviderRegistry", lambda root=None: registry
    )

    result = run_job(WIRE_NEWS_JOB, now=MOMENT, timezone="Asia/Shanghai", root=data_home)

    assert result["status"] == "ok", result
    assert watermark.WatermarkStore(root=data_home).value("theme_news") == DAY

    client = TestClient(
        create_app(data_root=data_home, dist_dir=data_home / "no-dist"),
        client=("127.0.0.1", 54321),
    )
    response = client.get("/api/market/theme", params={"date": DAY})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["available"] is True
    assert body["payload"]["available"] is True
    assert sorted(row["theme"] for row in body["payload"]["themes"]) == sorted(POOL)
    # 强度类字段取不到就是 null（Wire 摘要没有强度口径），不冒 0
    for row in body["payload"]["themes"]:
        assert row["strength"] is None
        assert row["limit_up_count"] is None