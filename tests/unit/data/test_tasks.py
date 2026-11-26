from __future__ import annotations

import sys
import time
import types

import pytest

from ladderstock.data import coverage, tasks


class FakeCollectError(RuntimeError):
    pass


def _state(state: str, reason: str | None = None, **extra):
    base = {"state": state, "source": None, "as_of": None, "age_days": None, "reason": reason}
    base.update(extra)


    return base


def _payload(symbol: str, kinds: dict[str, dict]):
    # 判定层会按依赖把 quote 补进报价/财报请求，假件必须给全，否则会被当成"缺行情"
    expanded = dict(kinds)
    if coverage.KIND_FINANCIAL in expanded and coverage.KIND_QUOTE not in expanded:
        expanded[coverage.KIND_QUOTE] = _state(coverage.STATE_READY)
    return {
        "symbol": symbol,
        "checked_at": "2026-11-20T10:00:00+08:00",
        "kinds": expanded,
        "ready": [k for k, v in expanded.items() if v["state"] == coverage.STATE_READY],
        "missing": [k for k, v in expanded.items() if v["state"] != coverage.STATE_READY],
        "complete": all(v["state"] == coverage.STATE_READY for v in expanded.values()),
    }


def _wait(mgr: tasks.TaskManager, task_id: str, timeout: float = 8.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = mgr.get(task_id)
        if record and record["status"] in tasks.FINAL_STATUSES:
            return record
        time.sleep(0.03)
    record = mgr.get(task_id)
    assert record is not None
    return record


@pytest.fixture
def fake_backends(monkeypatch):
    """把 collector / coverage 换成可控假件，单测不碰真机 CLI。"""

    calls: dict[str, list] = {"collect": [], "wait": [], "build": []}
    errors = {"collect": None}

    def fake_collect(kind, symbols, **kwargs):
        if errors["collect"]:
            raise FakeCollectError(errors["collect"])
        calls["collect"].append((kind, tuple(symbols)))
        return {"kind": kind, "symbols": list(symbols), "fetched": 24, "inserted": 24}

    def fake_wait(symbol, kind, **kwargs):
        calls["wait"].append((symbol, kind))
        return {"symbol": symbol, "kind": kind, "state": "ready", "info": {}}

    def fake_build(symbol, **kwargs):
        calls["build"].append(symbol)
        return {"symbol": symbol, "ok": True}

    collector_mod = types.SimpleNamespace(
        collect=fake_collect,
        wait_rows=fake_wait,
        build_wide=fake_build,
        CollectError=FakeCollectError,
    )
    monkeypatch.setattr("ladderstock.data.collector", collector_mod, raising=False)
    monkeypatch.setitem(sys.modules, "ladderstock.data.collector", collector_mod)
    return calls, errors


@pytest.fixture
def patch_check(monkeypatch):
    """按调用次序返回预设的 coverage 结果，最后一个重复使用。"""

    def install(sequence: list[dict]):
        counter = {"i": 0}

        def fake_check(symbol, kinds=None):
            idx = min(counter["i"], len(sequence) - 1)
            counter["i"] += 1
            return sequence[idx]

        monkeypatch.setattr(coverage, "check_coverage", fake_check, raising=False)

    return install


def test_submit_records_queue_state(fake_backends, patch_check):
    calls, _ = fake_backends
    mgr = tasks.TaskManager(max_concurrent=1)
    patch_check([_payload("600000", {"financial": _state(coverage.STATE_READY)})])

    # 提交必须立即返回一个句柄；假件跑得比线程调度还快，所以只校验句柄形态
    record = mgr.submit("600000", ["financial"])
    assert record["task_id"].startswith("ens_600000_")
    assert record["symbol"] == "600000"
    assert record["kinds"] == ["financial", "quote"]  # 行情作为财报的硬依赖被自动带上
    assert set(record["stages"]) == {"financial", "quote"}
    _wait(mgr, record["task_id"])


def test_all_ready_skips_collect(fake_backends, patch_check):
    calls, _ = fake_backends
    mgr = tasks.TaskManager(max_concurrent=2)
    patch_check([_payload("600000", {"financial": _state(coverage.STATE_READY)})])
    mgr.submit("600000", ["financial"])
    record = mgr.submit("600000", ["financial"])
    done = _wait(mgr, record["task_id"])
    assert done["status"] == tasks.STATUS_READY
    # 两单都不该触发采集
    assert calls["collect"] == []


def test_missing_financial_triggers_collect_and_build(fake_backends, patch_check):
    calls, _ = fake_backends
    mgr = tasks.TaskManager(max_concurrent=2)
    before = _payload("600000", {"financial": _state(coverage.STATE_MISSING, coverage.REASON_NO_ROW)})
    # 采集后：明细到货但宽表还没建 → 判定层报 stale/no_wide_row，这正是要 build 的信号
    mid = _payload(
        "600000", {"financial": _state(coverage.STATE_STALE, coverage.REASON_NO_WIDE_ROW)}
    )
    after = _payload("600000", {"financial": _state(coverage.STATE_READY)})
    patch_check([before, mid, after])

    record = mgr.submit("600000", ["financial"])
    done = _wait(mgr, record["task_id"])
    assert done["status"] == tasks.STATUS_READY
    assert [c[0] for c in calls["collect"]] == ["financial"]
    assert calls["build"] == ["600000"]


def test_wide_only_skips_collect_but_builds(fake_backends, patch_check):
    calls, _ = fake_backends
    mgr = tasks.TaskManager(max_concurrent=2)
    before = _payload(
        "600000", {"financial": _state(coverage.STATE_STALE, coverage.REASON_NO_WIDE_ROW)}
    )
    after = _payload("600000", {"financial": _state(coverage.STATE_READY)})
    patch_check([before, before, after])

    record = mgr.submit("600000", ["financial"])
    done = _wait(mgr, record["task_id"])
    assert done["status"] == tasks.STATUS_READY
    # 明细已在库、只缺宽表：不该再去采集
    assert calls["collect"] == []
    assert calls["build"] == ["600000"]


def test_collect_error_marks_partial(fake_backends, patch_check):
    calls, errors = fake_backends
    errors["collect"] = "upstream timeout"
    mgr = tasks.TaskManager(max_concurrent=2)
    payload = _payload("600000", {"financial": _state(coverage.STATE_MISSING, coverage.REASON_NO_ROW)})
    # 行情已就绪、财报采集失败 → 只该 partial
    payload["kinds"][coverage.KIND_QUOTE] = _state(coverage.STATE_READY)
    patch_check([payload])
    try:
        record = mgr.submit("600000", ["financial"])
        done = _wait(mgr, record["task_id"])
    finally:
        errors["collect"] = None
    assert done["status"] in (tasks.STATUS_PARTIAL, tasks.STATUS_FAILED)
    assert any("upstream timeout" in e for e in done["result"]["errors"])
    assert done["stages"]["financial"] == "failed"


def test_cancel_on_open_task_returns_bool():
    mgr = tasks.TaskManager(max_concurrent=1)
    # 直接塞一条排队中的假记录，避免与线程时序竞争
    record = {
        "task_id": "ens_600000_deadbeef",
        "symbol": "600000",
        "kinds": ["financial"],
        "status": tasks.STATUS_QUEUED,
        "stages": {"financial": "pending"},
    }
    mgr._tasks[record["task_id"]] = dict(record)
    assert mgr.cancel(record["task_id"]) is True
    assert mgr.get(record["task_id"])["status"] == tasks.STATUS_FAILED
    # 已终结的单子再取消不该报错
    assert mgr.cancel(record["task_id"]) is False
    assert mgr.cancel("不存在的单子") is False


def test_prune_drops_old_records():
    mgr = tasks.TaskManager(max_concurrent=1)
    for i in range(tasks.MAX_KEPT + 5):
        mgr._tasks[f"t{i}"] = {"task_id": f"t{i}", "status": tasks.STATUS_READY}
        mgr._created[f"t{i}"] = time.monotonic() + i * 0.001
    mgr._prune()
    assert len(mgr._tasks) <= tasks.MAX_KEPT
    assert mgr.get("t0") is None
