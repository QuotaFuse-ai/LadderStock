"""`/api/schedule`：定时任务的页面入口（第④步）。

只测三件会被用户直接看到的事实：

1. 清单里不混进快照 / 同步这类「不该在页面上开关」的任务；
2. 开关只改目标那一行的 `enabled`，注释与其它任务原样保留，改完还要过校验；
3. 立即跑一次是异步的——未知任务或不支持的类型要明确报错，不静默排队。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import HTTPException

from ladderstock.cli import schedule as sched
from ladderstock.web import schedule_api


SAMPLE = """\
scheduler:
  timezone: Asia/Shanghai
  misfire_grace_seconds: 300
jobs:
  # 早盘盯盘：这条注释必须活下来（改动只碰 enabled 那一行）
  - id: watch_alert_morning
    name: 08:45 盯盘提醒
    kind: watch_alert
    alert_pct: 5
    trigger: "08:45"
    enabled: false
    channel: desktop
  - id: watch_live_intraday
    name: 盘中 30s 实时盯盘
    kind: watch_live
    alert_pct: 3
    move_pct: 1
    min_gap_min: 30
    trigger: "30s"
    enabled: true
    channel: desktop
  - id: agent_research_close
    name: 16:40 定时研究
    kind: agent_research
    question: 今天自选有什么值得注意的？
    symbols: []
    trigger: "16:40"
    enabled: true
    channel: desktop
  - id: intraday_fast
    name: 盘中 30s 关键快照
    kind: snapshot_fast
    trigger: "30s"
    enabled: true
    channel: console
  - id: plan_tick_close
    name: 16:55 跨轮任务推进
    kind: plan_tick
    trigger: "16:55"
    enabled: true
    allow_plan_create: false
    channel: desktop
"""


@pytest.fixture()
def cfg(tmp_path: Path) -> Path:
    path = tmp_path / "schedule.yaml"
    path.write_text(SAMPLE, encoding="utf-8")
    return path


def test_jobs_payload_only_lists_ui_kinds(
    cfg: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sched, "schedule_config_path", lambda: cfg)
    payload = schedule_api.jobs_payload(data_root=tmp_path)
    ids = [job["id"] for job in payload["jobs"]]
    # 顺序由 trigger 字符串决定（08:45 < 16:40 < 16:55 < 30s），这里只断言集合与个数
    assert set(ids) == {
        "watch_alert_morning",
        "watch_live_intraday",
        "agent_research_close",
        "plan_tick_close",
    }
    assert "intraday_fast" not in ids  # 快照任务不该出现在这个工具条里
    assert paylaod_kinds(payload) == {"watch_alert", "watch_live", "agent_research", "plan_tick"}


def test_jobs_payload_lists_background_readonly(
    cfg: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """快照 / 同步这类高频任务要摆到页面上（只读），并标明是否只在交易时段跑。"""
    monkeypatch.setattr(sched, "schedule_config_path", lambda: cfg)
    payload = schedule_api.jobs_payload(data_root=tmp_path)
    background = payload["background"]
    assert [item["id"] for item in background] == ["intraday_fast"]
    item = background[0]
    assert item["kind"] == "snapshot_fast"
    assert item["session_bound"] is True
    # 只读：不带开关所需的写面语义，也没有 allow_plan_create
    assert item["allow_plan_create"] is None


def paylaod_kinds(payload: dict[str, Any]) -> set[str]:
    return {job["kind"] for job in payload["jobs"]}


def test_jobs_payload_reports_digest_absence(cfg: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sched, "schedule_config_path", lambda: cfg)
    payload = schedule_api.jobs_payload(data_root=tmp_path)
    research = next(job for job in payload["jobs"] if job["id"] == "agent_research_close")
    assert research["latest_digest"] is None  # 没跑过就是没有，不编「刚刚跑过」
    assert research["question"].startswith("今天自选")


def test_set_enabled_preserves_comments_and_other_jobs(cfg: Path) -> None:
    out = schedule_api.set_enabled("watch_alert_morning", True, path=cfg)
    assert out["enabled"] is True
    text = cfg.read_text(encoding="utf-8")
    assert "# 早盘盯盘：这条注释必须活下来" in text
    doc = yaml.safe_load(text)
    flags = {job["id"]: job["enabled"] for job in doc["jobs"]}
    assert flags["watch_alert_morning"] is True
    assert flags["watch_live_intraday"] is True  # 其它任务不受影响
    assert flags["intraday_fast"] is True


def test_set_enabled_rejects_unknown_job(cfg: Path) -> None:
    from ladderstock.errors import LadderStockError

    with pytest.raises(LadderStockError):
        schedule_api.set_enabled("nope", True, path=cfg)


def test_run_once_rejects_snapshot_outside_session(
    cfg: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """快照可以出现在版面上，但非交易时段不许在页面上点火（会和同步抢上游）。"""
    monkeypatch.setattr(sched, "schedule_config_path", lambda: cfg)
    from ladderstock.core import intraday

    monkeypatch.setattr(intraday, "in_trading_session", lambda moment=None: False)
    with pytest.raises(HTTPException) as excinfo:
        schedule_api.run_once("intraday_fast", data_root=tmp_path)
    assert excinfo.value.status_code == 400
    assert "交易时段" in str(excinfo.value.detail)


def test_unknown_kind_is_still_rejected(
    cfg: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """既不是 UI_KINDS 也不是 BACKGROUND_KINDS 的，仍然不许手动跑。"""
    monkeypatch.setattr(sched, "schedule_config_path", lambda: cfg)
    monkeypatch.setattr(
        sched, "load_schedule", lambda *a, **k: ({"jobs": [{"id": "weird", "kind": "nope"}]}, [], "x")
    )
    with pytest.raises(HTTPException) as excinfo:
        schedule_api.run_once("weird", data_root=tmp_path)
    assert excinfo.value.status_code == 400


def test_run_once_unknown_job_is_404(cfg: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sched, "schedule_config_path", lambda: cfg)
    with pytest.raises(HTTPException) as excinfo:
        schedule_api.run_once("nope", data_root=tmp_path)
    assert excinfo.value.status_code == 404


def test_run_once_starts_in_background(cfg: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """接口本身必须立刻返回；真正的耗时在后台线程里。"""
    monkeypatch.setattr(sched, "schedule_config_path", lambda: cfg)
    monkeypatch.setattr(sched, "run_job", lambda job, **kwargs: {"status": "ok", "detail": "stub"})
    out = schedule_api.run_once("watch_alert_morning", data_root=tmp_path)
    assert out["started"] is True
    assert out["id"] == "watch_alert_morning"


def _wait_state(root: Path, job_id: str, timeout: float = 5.0) -> dict[str, Any]:
    import time

    path = root / "schedule_runs" / f"{job_id}.json"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file():
            import json

            return json.loads(path.read_text(encoding="utf-8"))
        time.sleep(0.05)
    raise AssertionError(f"{path} 没有落盘")


def test_run_once_persists_outcome_for_the_page(
    cfg: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """点过「执行」之后，页面要能看见成没成——结局必须落盘并回到 /jobs。"""
    monkeypatch.setattr(sched, "schedule_config_path", lambda: cfg)
    monkeypatch.setattr(
        sched, "run_job", lambda job, **kwargs: {"status": "ok", "detail": "3 只越阈"}
    )
    schedule_api.run_once("watch_alert_morning", data_root=tmp_path)
    state = _wait_state(tmp_path, "watch_alert_morning")
    assert state["status"] == "ok"
    assert state["detail"] == "3 只越阈"

    payload = schedule_api.jobs_payload(data_root=tmp_path)
    job = next(item for item in payload["jobs"] if item["id"] == "watch_alert_morning")
    assert job["last_run"]["status"] == "ok"


def test_run_once_refuses_while_previous_still_running(
    cfg: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """上一轮没跑完时再点一次，不能并发叠两个。"""
    from datetime import datetime

    monkeypatch.setattr(sched, "schedule_config_path", lambda: cfg)
    schedule_api._write_run_state(
        tmp_path,
        "agent_research_close",
        {"status": "running", "started_at": datetime.now().isoformat(timespec="seconds")},
    )
    called: list[str] = []
    monkeypatch.setattr(sched, "run_job", lambda job, **kwargs: called.append("ran") or {"status": "ok"})
    out = schedule_api.run_once("agent_research_close", data_root=tmp_path)
    assert out["started"] is False
    assert "还在跑" in str(out["detail"])
    assert called == []


# --------------------------------------------------------------------------- #
# 跨轮任务：熔断停下的必须留在版面上，且能手动放行
# --------------------------------------------------------------------------- #


def _seed_plan(tmp_path: Path, *, fails: int = 0):
    from ladderstock.ai import plans

    plan = plans.maybe_create("帮我盯住 600036 一周", data_root=tmp_path)
    assert plan is not None
    for _ in range(fails):
        plans.record_run(plan, status="failed", summary="通道断了", data_root=tmp_path)
    return plan


def test_plans_view_keeps_paused_visible(tmp_path: Path) -> None:
    """熔断停下的任务不能从版面上消失——否则「它不跑了」用户永远看不到。"""
    from ladderstock.ai import plans

    _seed_plan(tmp_path, fails=plans.MAX_FAILURES)
    view = schedule_api._plans_view(tmp_path)
    assert view["ok"] is True
    assert len(view["items"]) == 1
    item = view["items"][0]
    assert item["status"] == plans.STATUS_PAUSED
    assert item["failures"] == plans.MAX_FAILURES
    assert "通道断了" in item["last_error"]
    assert item["review"][-1]["ok"] is False
    assert view["max_failures"] == plans.MAX_FAILURES


def test_plan_status_route_resumes_and_clears_failures(tmp_path: Path) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from ladderstock.ai import plans

    plan = _seed_plan(tmp_path, fails=plans.MAX_FAILURES)
    app = FastAPI()
    app.include_router(schedule_api.build_router(data_root=tmp_path))
    client = TestClient(app, base_url="http://127.0.0.1:5950", client=("127.0.0.1", 1234))

    response = client.post(
        "/api/schedule/plan/status",
        json={"id": plan.id, "status": plans.STATUS_ACTIVE},
        headers={"X-LDST-Client": "1", "Origin": "http://127.0.0.1:5950"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["plans"]["items"][0]["status"] == plans.STATUS_ACTIVE
    assert body["plans"]["items"][0]["failures"] == 0


def test_plan_status_rejects_unknown_state(tmp_path: Path) -> None:
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(schedule_api.build_router(data_root=tmp_path))
    client = TestClient(app, base_url="http://127.0.0.1:5950", client=("127.0.0.1", 1234))
    response = client.post(
        "/api/schedule/plan/status",
        json={"id": "plan-x", "status": "nope"},
        headers={"X-LDST-Client": "1", "Origin": "http://127.0.0.1:5950"},
    )
    assert response.status_code == 400


# --------------------------------------------------------------------------- #
# 「推进时允许新建任务」的显式开关：只能改 plan_tick，默认关，能来回切
# --------------------------------------------------------------------------- #


def test_set_allow_plan_create_flips_only_that_line(cfg: Path) -> None:
    out = schedule_api.set_allow_plan_create("plan_tick_close", True, path=cfg)
    assert out["allow_plan_create"] is True
    text = cfg.read_text(encoding="utf-8")
    assert "# 早盘盯盘：这条注释必须活下来" in text  # 注释不被重序列化吃掉
    doc = yaml.safe_load(text)
    job = next(item for item in doc["jobs"] if item["id"] == "plan_tick_close")
    assert job["allow_plan_create"] is True
    other = next(item for item in doc["jobs"] if item["id"] == "watch_live_intraday")
    assert other["enabled"] is True  # 其它任务不受影响


def test_set_allow_plan_create_rejects_non_plan_kind(cfg: Path) -> None:
    from ladderstock.errors import LadderStockError

    with pytest.raises(LadderStockError) as excinfo:
        schedule_api.set_allow_plan_create("watch_alert_morning", True, path=cfg)
    assert "plan_tick" in str(excinfo.value)


def test_set_allow_plan_create_inserts_field_when_absent(tmp_path: Path) -> None:
    """老调度表里没有这一行时，要能补上与其它字段同缩进的一行。"""
    from ladderstock.errors import LadderStockError

    path = tmp_path / "schedule.yaml"
    path.write_text(
        "scheduler:\n  timezone: Asia/Shanghai\n  misfire_grace_seconds: 300\n"
        "jobs:\n  - id: plan_tick_close\n    name: 16:55 跨轮任务推进\n"
        "    kind: plan_tick\n    trigger: \"16:55\"\n    enabled: true\n    channel: desktop\n",
        encoding="utf-8",
    )
    out = schedule_api.set_allow_plan_create("plan_tick_close", False, path=path)
    assert out["allow_plan_create"] is False
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert doc["jobs"][0]["allow_plan_create"] is False
    # 与 enabled 同缩进，仍属于同一个 job 块
    assert "\n    allow_plan_create: false" in path.read_text(encoding="utf-8")
    # 不存在的任务照旧报错
    with pytest.raises(LadderStockError):
        schedule_api.set_allow_plan_create("nope", True, path=path)


def test_create_flag_route_round_trips(
    cfg: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from ladderstock.web import schedule_runner

    monkeypatch.setattr(sched, "schedule_config_path", lambda: cfg)
    monkeypatch.setattr(schedule_runner, "reload_runner", lambda: None)
    app = FastAPI()
    app.include_router(schedule_api.build_router(data_root=tmp_path))
    client = TestClient(app, base_url="http://127.0.0.1:5950", client=("127.0.0.1", 1234))
    headers = {"X-LDST-Client": "1", "Origin": "http://127.0.0.1:5950"}

    on = client.post(
        "/api/schedule/plan/create-flag",
        json={"id": "plan_tick_close", "allow": True},
        headers=headers,
    )
    assert on.status_code == 200
    assert on.json()["allow_plan_create"] is True
    payload = schedule_api.jobs_payload(data_root=tmp_path)
    job = next(item for item in payload["jobs"] if item["id"] == "plan_tick_close")
    assert job["allow_plan_create"] is True

    off = client.post(
        "/api/schedule/plan/create-flag",
        json={"id": "plan_tick_close", "allow": False},
        headers=headers,
    )
    assert off.status_code == 200
    assert off.json()["allow_plan_create"] is False

    bad = client.post(
        "/api/schedule/plan/create-flag",
        json={"id": "watch_alert_morning", "allow": True},
        headers=headers,
    )
    assert bad.status_code == 400
