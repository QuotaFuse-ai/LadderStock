"""无端口看门狗（QUOT-589 重修）：自愈循环、重启回落顺序、kill 守门。

不起真进程、不拨真网络：探活与重启都注入假实现；端口 / PID 查询换成常数。
看门狗不再监听任何端口（全机只留 5950/5951），HTTP 面由主服务 web/platform.py
自己提供（见 tests/unit/web/test_platform.py）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from ladderstock.cli import watchdog
from ladderstock.cli.watchdog import (
    FAILURES_BEFORE_RED,
    RESTART_THROTTLE_SEC,
    WatchdogConfig,
    judge,
    kill_guard,
    probe_api,
    restart_service,
    run_self_heal,
    script_issue,
    spawn_command_issue,
    state_path,
    supervised,
    write_state,
)


class _Completed:
    """`subprocess.CompletedProcess` 的最小替身（_run_command 只读 returncode）。"""

    def __init__(self, code: int) -> None:
        self.returncode = code

def _config(tmp_path: Path, **kw: Any) -> WatchdogConfig:
    kw.setdefault("data_root", tmp_path)
    return WatchdogConfig(**kw)


class _Loop:
    """可控节拍：`sleep` 不真睡，`monotonic` 按 tick 前进。"""

    def __init__(self, max_ticks: int) -> None:
        self.now = 0.0
        self.max_ticks = max_ticks
        self.ticks = 0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, sec: float) -> None:
        self.now += sec
        self.ticks += 1
        if self.ticks > self.max_ticks:
            raise KeyboardInterrupt  # run_self_heal 不捕获 KeyboardInterrupt → 退出


def _stop_after(ticks: int) -> Any:
    state = {"n": 0}

    def check() -> bool:
        state["n"] += 1
        return state["n"] > ticks

    return check


# --------------------------------------------------------------------------- #
# 三态判定 / 探活
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"status": "ok", "db_ok": True, "ok": True}, "ok"),
        ({"status": "degraded", "db_ok": True, "ok": True}, "degraded"),
        ({"status": "ok", "db_ok": False, "ok": True}, "degraded"),
        (None, "offline"),
        # 兼容未升级的主服务：只有 `ok` 字段
        ({"ok": True}, "ok"),
        ({"ok": False}, "degraded"),
    ],
)
def test_judge_three_states(payload, expected) -> None:
    assert judge(payload) == expected


def test_probe_api_returns_none_on_connection_error() -> None:
    # 真函数、必败端口：主服务死了 probe 必须「安静地」失败，绝不抛
    assert probe_api("http://127.0.0.1:1", timeout=0.2) is None


# --------------------------------------------------------------------------- #
# 自愈循环（无端口核心）
# --------------------------------------------------------------------------- #


def test_single_blip_does_not_restart(tmp_path: Path) -> None:
    """单次探活失败 ≠ 服务死了：不重启，下一拍恢复即清零。"""
    results: list[dict[str, Any] | None] = [None, {"status": "ok", "db_ok": True}]
    restarts: list[str] = []
    loop = _Loop(max_ticks=2)

    run_self_heal(
        _config(tmp_path),
        probe=lambda _base: results.pop(0) if results else {"status": "ok"},
        restart=lambda _c: (restarts.append("x"), (200, {"ok": True}))[1],
        sleep=loop.sleep,
        monotonic=loop.monotonic,
        stop_requested=_stop_after(2),
    )

    assert restarts == []


def test_confirmed_outage_triggers_restart_once(tmp_path: Path) -> None:
    """连续 FAILURES_BEFORE_RED 次失败 → 确认中断 → 自愈重启一次。"""
    restarts: list[WatchdogConfig] = []
    loop = _Loop(max_ticks=5)
    calls = {"n": 0}

    def probe(_base: str) -> dict[str, Any] | None:
        calls["n"] += 1
        return {"status": "ok", "db_ok": True} if calls["n"] > 3 else None

    run_self_heal(
        _config(tmp_path),
        probe=probe,
        restart=lambda c: (restarts.append(c), (200, {"ok": True}))[1],
        sleep=loop.sleep,
        monotonic=loop.monotonic,
        stop_requested=_stop_after(5),
    )

    assert len(restarts) == 1


def test_restart_is_throttled_within_cooldown(tmp_path: Path) -> None:
    """冷却期（RESTART_THROTTLE_SEC）内不再触发第二次自愈。"""
    restarts: list[int] = []
    loop = _Loop(max_ticks=6)

    run_self_heal(
        _config(tmp_path),
        probe=lambda _base: None,
        restart=lambda _c: (restarts.append(1), (503, {"ok": False}))[1],
        sleep=loop.sleep,
        monotonic=loop.monotonic,
        stop_requested=_stop_after(6),
    )

    # probe_interval=2s，6 拍 = 12s < 30s 冷却：只允许 1 次重启
    assert len(restarts) == 1


def test_recovery_clears_failure_count(tmp_path: Path) -> None:
    """失败 1 次后恢复、再失败 1 次：始终没到确认线，不重启。"""
    seq = [None, {"status": "ok", "db_ok": True}, None, {"status": "ok", "db_ok": True}]
    restarts: list[int] = []
    loop = _Loop(max_ticks=4)

    run_self_heal(
        _config(tmp_path),
        probe=lambda _base: seq.pop(0) if seq else {"status": "ok"},
        restart=lambda _c: (restarts.append(1), (200, {"ok": True}))[1],
        sleep=loop.sleep,
        monotonic=loop.monotonic,
        stop_requested=_stop_after(4),
    )

    assert restarts == []


def test_stop_marker_ends_the_loop(tmp_path: Path) -> None:
    restarts: list[int] = []
    loop = _Loop(max_ticks=10)

    run_self_heal(
        _config(tmp_path),
        probe=lambda _base: None,
        restart=lambda _c: (restarts.append(1), (200, {"ok": True}))[1],
        sleep=loop.sleep,
        monotonic=loop.monotonic,
        stop_requested=_stop_after(0),
    )

    assert restarts == []


def test_restart_exception_is_caught_and_logged(tmp_path: Path) -> None:
    """自愈重启抛异常不能带崩监控循环。"""
    restarts: list[int] = []
    loop = _Loop(max_ticks=5)

    def bad_restart(_c: WatchdogConfig) -> tuple[int, dict[str, Any]]:
        restarts.append(1)
        raise RuntimeError("boom")

    run_self_heal(
        _config(tmp_path),
        probe=lambda _base: None,
        restart=bad_restart,
        sleep=loop.sleep,
        monotonic=loop.monotonic,
        stop_requested=_stop_after(5),
    )

    assert len(restarts) == 1


# --------------------------------------------------------------------------- #
# 重启顺序与守门（保留原契约）
# --------------------------------------------------------------------------- #


def test_restart_returns_409_when_no_mechanism(tmp_path: Path) -> None:
    code, payload = restart_service(
        _config(tmp_path, restart_task="", restart_script="", spawn_cmd="")
    )
    assert code == 409
    assert payload["ok"] is False


def test_restart_prefers_spawn_cmd_over_task(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def run(argv, **kw):
        calls.append([str(part) for part in argv])
        return _Completed(0)

    config = _config(
        tmp_path,
        restart_task="Some Task",
        spawn_cmd=json.dumps(["python", "-m", "ladderstock"]),
    )
    code, payload = restart_service(config, run=run)

    assert code == 200
    assert payload["via"] == "spawn"
    assert calls[0] == ["python", "-m", "ladderstock"]
    assert all("schtasks" not in argv[0] for argv in calls[1:])


def test_supervised_watchdog_never_falls_back_to_the_scheduled_task(tmp_path: Path) -> None:
    """supervised 模式：spawn-cmd 失败后不再 schtasks / 脚本兜底。"""
    calls: list[list[str]] = []

    def run(argv, **kw):
        calls.append([str(part) for part in argv])
        return _Completed(1)

    config = _config(
        tmp_path,
        restart_task="Some Task",
        restart_script=str(tmp_path / "restart.ps1"),
        spawn_cmd=json.dumps(["python", "-m", "ladderstock"]),
        supervised=True,
    )
    (tmp_path / "restart.ps1").write_text("exit 0", encoding="utf-8")
    code, payload = restart_service(config, run=run)

    assert code == 503
    assert payload["ok"] is False
    # 只尝试过 spawn 一种方式
    assert [a["via"] for a in payload["attempts"]] == ["spawn"]


def test_restart_falls_back_to_script(tmp_path: Path) -> None:
    script = tmp_path / "restart-api.ps1"
    script.write_text("exit 0", encoding="utf-8")
    calls: list[list[str]] = []

    def run(argv, **kw):
        calls.append([str(part) for part in argv])
        return _Completed(0)

    code, payload = restart_service(
        _config(tmp_path, restart_task="", restart_script=str(script)), run=run
    )

    assert code == 200
    assert payload["via"] == "script"
    assert calls[0][:4] == ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass"]


def test_restart_returns_503_when_everything_fails(tmp_path: Path) -> None:
    code, payload = restart_service(
        _config(tmp_path, restart_task="Missing Task"), run=lambda argv, **kw: _Completed(1)
    )
    assert code == 503
    assert [a["via"] for a in payload["attempts"]] == ["schtasks"]


def test_restart_kills_current_port_owner_not_stale_recorded_pid(tmp_path: Path) -> None:
    """kill 面只认「此刻监听 API 端口」的 PID，绝不按旧记录杀。"""
    killed: list[int] = []

    def lookup(port: int) -> int | None:
        return 4242 if port == 5950 else None

    def terminate(pid: int) -> bool:
        killed.append(pid)
        return True

    # lookup 始终回 None（端口已让出），循环第一拍就 break，wait 只被调一次
    def wait(_sec: float) -> None:
        return None

    code, payload = restart_service(
        _config(tmp_path, restart_script="", spawn_cmd=json.dumps(["python"])),
        lookup_pid=lookup,
        name_of=lambda _pid: "python.exe",
        terminate=terminate,
        wait=wait,
        run=lambda argv, **kw: _Completed(0),
    )

    assert code == 200
    assert killed == [4242]
    assert payload["killed_pid"] == 4242
    recorded = watchdog.read_state(tmp_path)["api"]
    assert recorded["pid"] == 4242 and recorded["port"] == 5950


def test_restart_skips_killing_when_guard_refuses(tmp_path: Path) -> None:
    """kill_guard 拒绝（multica）时绝不 terminate。"""
    killed: list[int] = []

    code, payload = restart_service(
        _config(tmp_path, restart_script="", spawn_cmd=json.dumps(["python"])),
        lookup_pid=lambda port: 123,
        name_of=lambda _pid: "multica.exe",
        terminate=lambda pid: killed.append(pid) or True,
        wait=lambda _s: None,
        run=lambda argv, **kw: _Completed(0),
    )

    assert code == 200
    assert killed == []
    assert "拒绝" in payload["skipped"]


def test_restart_does_not_kill_when_port_is_free(tmp_path: Path) -> None:
    killed: list[int] = []
    code, payload = restart_service(
        _config(tmp_path, restart_script="", spawn_cmd=json.dumps(["python"])),
        lookup_pid=lambda port: None,
        terminate=lambda pid: killed.append(pid) or True,
        wait=lambda _s: None,
        run=lambda argv, **kw: _Completed(0),
    )
    assert code == 200
    assert killed == []
    assert payload["killed_pid"] is None


# --------------------------------------------------------------------------- #
# kill_guard / 静态校验
# --------------------------------------------------------------------------- #


def test_kill_guard_refuses_multica() -> None:
    reason = kill_guard(123, 5950, name_of=lambda _p: "multica.exe", lookup=lambda _p: 123)
    assert "multica" in reason


def test_kill_guard_refuses_non_owner() -> None:
    reason = kill_guard(123, 5950, name_of=lambda _p: "python.exe", lookup=lambda _p: 456)
    assert "5950" in reason


def test_kill_guard_allows_api_owner() -> None:
    assert kill_guard(123, 5950, name_of=lambda _p: "python.exe", lookup=lambda _p: 123) == ""


def test_kill_guard_refuses_self() -> None:
    reason = kill_guard(
        os.getpid(), 5950, name_of=lambda _p: "python.exe", lookup=lambda _p: 0
    )
    assert "自身" in reason


def test_script_issue_requires_absolute_existing_file(tmp_path: Path) -> None:
    assert script_issue("") is None
    assert script_issue("relative.ps1") == "必须是绝对路径"
    assert script_issue(str(tmp_path / "missing.ps1")) == "文件不存在"
    ok = tmp_path / "restart.ps1"
    ok.write_text("exit 0", encoding="utf-8")
    assert script_issue(str(ok)) is None


def test_spawn_command_issue_validates_json_array() -> None:
    assert spawn_command_issue("") is None
    assert spawn_command_issue("not json") == "不是合法 JSON 数组"
    assert spawn_command_issue('["ok"]') is None
    assert spawn_command_issue("[1, 2]") == "必须是字符串数组"
    assert spawn_command_issue('{"a": 1}') == "必须是字符串数组"


# --------------------------------------------------------------------------- #
# supervised 标记与状态文件
# --------------------------------------------------------------------------- #


def test_supervised_flag_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(watchdog.SUPERVISED_ENV, raising=False)
    assert supervised() is False
    monkeypatch.setenv(watchdog.SUPERVISED_ENV, "1")
    assert supervised() is True
    monkeypatch.setenv(watchdog.SUPERVISED_ENV, "0")
    assert supervised() is False


def test_state_records_portless_watchdog(tmp_path: Path) -> None:
    """QUOT-589：看门狗无端口化——state 的 watchdog.port 必须是 null。"""
    write_state(tmp_path, {"watchdog": {"pid": 1, "port": None, "version": "x"}})
    data = watchdog.read_state(tmp_path)
    assert data["watchdog"]["port"] is None
    assert state_path(tmp_path).is_file()


def test_watchdog_config_scopes_to_its_api_port(tmp_path: Path) -> None:
    """scope 只跟 api_base 的端口走：dev 5951 实例的看门狗只查 5951。"""
    dev = _config(tmp_path, api_base="http://127.0.0.1:5951")
    prod = _config(tmp_path, api_base="http://127.0.0.1:5950")
    assert dev.api_port == 5951
    assert prod.api_port == 5950
    assert FAILURES_BEFORE_RED == 2
    assert RESTART_THROTTLE_SEC == 30.0
