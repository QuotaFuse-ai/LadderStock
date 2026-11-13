"""看门狗（QUOT-443 / DEC-063）：三态判定、Origin 白名单、30s 节流、重启回落顺序。

不起真进程、不拨真网络：探活与重启都注入假实现；端口 / PID 查询换成常数。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.cli import watchdog
from ladderstock.cli.watchdog import (
    RESTART_THROTTLE_SEC,
    WatchdogConfig,
    create_watchdog_app,
    judge,
    kill_guard,
    offline_shell_html,
    origin_allowed,
    restart_service,
    script_issue,
)

ALLOWED = "http://127.0.0.1:5950"


def _client(
    config: WatchdogConfig,
    *,
    probe=None,
    restart=None,
) -> TestClient:
    return TestClient(create_watchdog_app(config, probe=probe, restart=restart))


# --------------------------------------------------------------------------- #
# 三态判定
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


def test_health_payload_has_contract_fields(tmp_path: Path) -> None:
    config = WatchdogConfig(data_root=tmp_path)
    client = _client(config, probe=lambda: {"status": "ok", "db_ok": True, "uptime_sec": 12})

    payload = client.get("/platform/health").json()

    for key in ("status", "uptime_sec", "version", "db_ok", "checked_at", "api", "watchdog"):
        assert key in payload, f"缺字段 {key}"
    assert payload["status"] == "ok"
    assert payload["db_ok"] is True
    # 顶层字段描述主服务（uptime 12 来自 /api/health），看门狗的 uptime 只在 watchdog 块
    assert payload["uptime_sec"] == 12
    assert payload["api"]["uptime_sec"] == 12
    assert isinstance(payload["watchdog"]["uptime_sec"], int)
    assert payload["watchdog"]["uptime_sec"] >= 0


def test_health_is_offline_when_probe_fails(tmp_path: Path) -> None:
    client = _client(WatchdogConfig(data_root=tmp_path), probe=lambda: None)
    payload = client.get("/platform/health").json()

    assert payload["status"] == "offline"
    assert payload["db_ok"] is None
    assert payload["api"] is None


def test_health_is_not_cacheable(tmp_path: Path) -> None:
    client = _client(WatchdogConfig(data_root=tmp_path), probe=lambda: None)
    response = client.get("/platform/health")
    assert response.headers["cache-control"] == "no-store"


# --------------------------------------------------------------------------- #
# Origin 白名单
# --------------------------------------------------------------------------- #


def test_origin_allowed_exact_whitelist() -> None:
    assert origin_allowed(ALLOWED)
    assert origin_allowed("http://localhost:5173")
    assert origin_allowed("http://127.0.0.1:5952")
    assert not origin_allowed(None)
    assert not origin_allowed("http://evil.example:5950")
    assert not origin_allowed("http://127.0.0.1:9999")


def test_post_without_origin_is_rejected(tmp_path: Path) -> None:
    called: list[int] = []

    def restart() -> tuple[int, dict]:
        called.append(1)
        return 200, {"ok": True}

    client = _client(WatchdogConfig(data_root=tmp_path), probe=lambda: None, restart=restart)
    response = client.post("/platform/restart")

    assert response.status_code == 403
    assert response.headers.get("access-control-allow-origin") is None
    assert called == []


def test_post_with_foreign_origin_is_rejected(tmp_path: Path) -> None:
    client = _client(WatchdogConfig(data_root=tmp_path), probe=lambda: None)
    response = client.post("/platform/restart", headers={"Origin": "http://evil.example"})
    assert response.status_code == 403


def test_post_with_allowed_origin_passes_cors(tmp_path: Path) -> None:
    client = _client(
        WatchdogConfig(data_root=tmp_path),
        probe=lambda: None,
        restart=lambda: (200, {"ok": True, "via": "script"}),
    )
    response = client.post("/platform/restart", headers={"Origin": ALLOWED})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ALLOWED
    assert response.headers["access-control-allow-origin"] != "*"


def test_preflight_allows_only_whitelisted_origin(tmp_path: Path) -> None:
    client = _client(WatchdogConfig(data_root=tmp_path), probe=lambda: None)

    ok = client.options(
        "/platform/restart",
        headers={"Origin": ALLOWED, "Access-Control-Request-Method": "POST"},
    )
    assert ok.status_code == 204
    assert "POST" in ok.headers["access-control-allow-methods"]

    bad = client.options(
        "/platform/restart",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert bad.status_code == 403


def test_get_has_no_side_effect(tmp_path: Path) -> None:
    calls: list[int] = []

    def restart() -> tuple[int, dict]:
        calls.append(1)
        return 200, {"ok": True}

    client = _client(WatchdogConfig(data_root=tmp_path), probe=lambda: None, restart=restart)
    client.get("/platform/health", headers={"Origin": ALLOWED})
    assert calls == []


# --------------------------------------------------------------------------- #
# 30s 节流
# --------------------------------------------------------------------------- #


def test_restart_is_throttled_for_30_seconds(tmp_path: Path) -> None:
    calls: list[int] = []

    def restart() -> tuple[int, dict]:
        calls.append(1)
        return 200, {"ok": True}

    client = _client(WatchdogConfig(data_root=tmp_path), probe=lambda: None, restart=restart)
    headers = {"Origin": ALLOWED}

    assert client.post("/platform/restart", headers=headers).status_code == 200
    throttled = client.post("/platform/restart", headers=headers)

    assert throttled.status_code == 429
    assert "30s" in throttled.json()["reason"] or "频繁" in throttled.json()["reason"]
    assert calls == [1]
    assert RESTART_THROTTLE_SEC == 30.0


# --------------------------------------------------------------------------- #
# 重启：顺序、回落、安全
# --------------------------------------------------------------------------- #


def _completed(returncode: int) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout="", stderr="")


def test_restart_returns_409_when_no_mechanism(tmp_path: Path) -> None:
    def runner(*_args, **_kwargs):
        raise AssertionError("没有重启方式时不该执行任何命令")

    code, payload = restart_service(
        WatchdogConfig(data_root=tmp_path, restart_task="", restart_script=""),
        run=runner,
        lookup_pid=lambda _port: None,
    )

    assert code == 409
    assert payload["ok"] is False
    assert "未配置" in payload["reason"]


def test_restart_falls_back_from_task_to_script(tmp_path: Path) -> None:
    script = tmp_path / "restart-api.ps1"
    script.write_text("exit 0", encoding="utf-8")
    calls: list[list[str]] = []

    def runner(argv, **_kwargs):
        calls.append(list(argv))
        return _completed(1 if argv[0] == "schtasks" else 0)

    code, payload = restart_service(
        WatchdogConfig(
            data_root=tmp_path,
            restart_task="LadderStock API",
            restart_script=str(script),
        ),
        run=runner,
        lookup_pid=lambda _port: None,
        wait=lambda _seconds: None,
    )

    assert code == 200
    assert payload["via"] == "script"
    assert [call[0] for call in calls] == ["schtasks", "powershell"]
    assert payload["attempts"][0]["ok"] is False
    assert payload["attempts"][1]["ok"] is True


def test_restart_prefers_spawn_cmd_over_task(tmp_path: Path) -> None:
    """被 supervisor 托管时只给 spawn-cmd：直接拉起，不再碰 schtasks（QUOT-523）。"""
    calls: list[list[str]] = []

    def runner(argv, **_kwargs):
        calls.append(list(argv))
        return _completed(0)

    code, payload = restart_service(
        WatchdogConfig(
            data_root=tmp_path,
            restart_task="LadderStock API",
            spawn_cmd='["C:\\\\tools\\\\ldst.exe", "-m", "ladderstock.cli.main", "run"]',
        ),
        run=runner,
        lookup_pid=lambda _port: None,
    )

    assert code == 200
    assert payload["via"] == "spawn"
    assert calls == [["C:\\tools\\ldst.exe", "-m", "ladderstock.cli.main", "run"]]


def test_restart_falls_back_from_spawn_cmd_to_task(tmp_path: Path) -> None:
    calls: list[str] = []

    def runner(argv, **_kwargs):
        calls.append(argv[0])
        return _completed(1 if argv[0] == "ldst.exe" else 0)

    code, payload = restart_service(
        WatchdogConfig(
            data_root=tmp_path,
            restart_task="LadderStock API",
            spawn_cmd='["ldst.exe", "run"]',
        ),
        run=runner,
        lookup_pid=lambda _port: None,
    )

    assert code == 200
    assert payload["via"] == "schtasks"
    assert calls == ["ldst.exe", "schtasks"]
    assert [attempt["via"] for attempt in payload["attempts"]] == ["spawn", "schtasks"]
    assert payload["attempts"][0]["ok"] is False


def test_restart_rejects_bad_spawn_cmd_without_running(tmp_path: Path) -> None:
    def runner(*_args, **_kwargs):
        raise AssertionError("spawn-cmd 非法时不该执行任何命令")

    code, payload = restart_service(
        WatchdogConfig(data_root=tmp_path, restart_task="", spawn_cmd="{not json"),
        run=runner,
        lookup_pid=lambda _port: None,
    )

    assert code == 503
    assert payload["attempts"][0]["via"] == "spawn"
    assert payload["attempts"][0]["ok"] is False


def test_restart_uses_task_when_it_succeeds(tmp_path: Path) -> None:
    script = tmp_path / "restart-api.ps1"
    script.write_text("exit 0", encoding="utf-8")
    calls: list[str] = []

    def runner(argv, **_kwargs):
        calls.append(argv[0])
        return _completed(0)

    code, payload = restart_service(
        WatchdogConfig(
            data_root=tmp_path,
            restart_task="LadderStock API",
            restart_script=str(script),
        ),
        run=runner,
        lookup_pid=lambda _port: None,
    )

    assert code == 200
    assert payload["via"] == "schtasks"
    assert calls == ["schtasks"]


def test_supervised_watchdog_never_falls_back_to_the_scheduled_task(tmp_path: Path) -> None:
    """托管模式（QUOT-523）：spawn-cmd 失败也只回 503，不碰 schtasks / 脚本。"""
    script = tmp_path / "restart-api.ps1"
    script.write_text("exit 0", encoding="utf-8")
    calls: list[str] = []

    def runner(argv, **_kwargs):
        calls.append(argv[0])
        return _completed(1)

    code, payload = restart_service(
        WatchdogConfig(
            data_root=tmp_path,
            restart_task="LadderStock API",
            restart_script=str(script),
            spawn_cmd='["ldst.exe", "run"]',
            supervised=True,
        ),
        run=runner,
        lookup_pid=lambda _port: None,
    )

    assert code == 503
    assert payload["ok"] is False
    assert calls == ["ldst.exe"]
    assert [attempt["via"] for attempt in payload["attempts"]] == ["spawn"]


def test_supervised_flag_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(watchdog.SUPERVISED_ENV, raising=False)
    assert watchdog.supervised() is False
    monkeypatch.setenv(watchdog.SUPERVISED_ENV, "0")
    assert watchdog.supervised() is False
    monkeypatch.setenv(watchdog.SUPERVISED_ENV, "1")
    assert watchdog.supervised() is True
    assert watchdog.supervised({}) is False


def test_restart_returns_503_when_everything_fails(tmp_path: Path) -> None:
    code, payload = restart_service(
        WatchdogConfig(data_root=tmp_path, restart_task="A", restart_script=""),
        run=lambda *_a, **_k: _completed(1),
        lookup_pid=lambda _port: None,
    )

    assert code == 503
    assert payload["ok"] is False
    assert payload["attempts"][0]["via"] == "schtasks"


def test_restart_kills_only_the_recorded_port_owner(tmp_path: Path) -> None:
    script = tmp_path / "restart-api.ps1"
    script.write_text("exit 0", encoding="utf-8")
    state = {"pid": 4321}
    terminated: list[int] = []

    def lookup(port: int) -> int | None:
        assert port == 5950
        return state["pid"]

    def terminate(pid: int) -> bool:
        terminated.append(pid)
        state["pid"] = None
        return True

    code, payload = restart_service(
        WatchdogConfig(data_root=tmp_path, restart_task="", restart_script=str(script)),
        run=lambda *_a, **_k: _completed(0),
        lookup_pid=lookup,
        name_of=lambda _pid: "python.exe",
        terminate=terminate,
        wait=lambda _seconds: None,
    )

    assert code == 200
    assert terminated == [4321]
    assert payload["killed_pid"] == 4321
    recorded = watchdog.read_state(tmp_path)["api"]
    assert recorded["pid"] == 4321 and recorded["port"] == 5950


def test_restart_kills_current_port_owner_not_stale_recorded_pid(tmp_path: Path) -> None:
    """PID 会被系统复用：只认「此刻占用 API 端口」的那个，旧记录值一律不碰。"""
    script = tmp_path / "restart-api.ps1"
    script.write_text("exit 0", encoding="utf-8")
    watchdog.write_state(
        tmp_path, {"api": {"pid": 1111, "port": 5950, "recorded_at": "2026-11-06T00:00:00+00:00"}}
    )
    terminated: list[int] = []

    def terminate(pid: int) -> bool:
        terminated.append(pid)
        return True

    code, payload = restart_service(
        WatchdogConfig(data_root=tmp_path, restart_task="", restart_script=str(script)),
        run=lambda *_a, **_k: _completed(0),
        lookup_pid=lambda _port: 999,
        name_of=lambda _pid: "python.exe",
        terminate=terminate,
        wait=lambda _seconds: None,
    )

    assert code == 200
    assert terminated == [999]
    assert payload["killed_pid"] == 999


def test_restart_does_not_kill_stale_pid_when_port_is_free(tmp_path: Path) -> None:
    script = tmp_path / "restart-api.ps1"
    script.write_text("exit 0", encoding="utf-8")
    watchdog.write_state(
        tmp_path, {"api": {"pid": 1111, "port": 5950, "recorded_at": "2026-11-06T00:00:00+00:00"}}
    )
    terminated: list[int] = []

    code, payload = restart_service(
        WatchdogConfig(data_root=tmp_path, restart_task="", restart_script=str(script)),
        run=lambda *_a, **_k: _completed(0),
        lookup_pid=lambda _port: None,  # 服务已经死了：没有可结束的对象
        name_of=lambda _pid: "python.exe",
        terminate=terminated.append,
    )

    assert code == 200
    assert terminated == []
    assert payload["killed_pid"] is None


def test_kill_guard_refuses_multica() -> None:
    reason = kill_guard(1234, 5950, name_of=lambda _pid: "multica.exe", lookup=lambda _p: 1234)
    assert "multica" in reason
    assert "拒绝" in reason


def test_kill_guard_refuses_non_owner() -> None:
    reason = kill_guard(1234, 5950, name_of=lambda _pid: "python.exe", lookup=lambda _p: 999)
    assert "监听者" in reason


def test_kill_guard_allows_api_owner() -> None:
    assert kill_guard(1234, 5950, name_of=lambda _pid: "python.exe", lookup=lambda _p: 1234) == ""


# --------------------------------------------------------------------------- #
# 脚本路径 / 壳页
# --------------------------------------------------------------------------- #


def test_script_issue_requires_absolute_existing_file(tmp_path: Path) -> None:
    assert script_issue("") is None
    assert "绝对路径" in str(script_issue("restart-api.ps1"))
    missing = tmp_path / "missing.ps1"
    assert "不存在" in str(script_issue(str(missing)))
    present = tmp_path / "ok.ps1"
    present.write_text("", encoding="utf-8")
    assert script_issue(str(present)) is None


def test_offline_shell_is_self_contained() -> None:
    html = offline_shell_html("http://127.0.0.1:5950")

    assert "重启平台" in html
    assert "/platform/health" in html
    assert "/platform/restart" in html
    assert "127.0.0.1:5950" in html
    # 无外部依赖：没有 http(s) 资源引用（跳转地址除外）
    assert "<script src" not in html
    assert "<link" not in html
    assert "cdn" not in html.lower()


def test_offline_shell_page_is_served(tmp_path: Path) -> None:
    client = _client(WatchdogConfig(data_root=tmp_path), probe=lambda: None)
    response = client.get("/")

    assert response.status_code == 200
    assert "LadderStock" in response.text
    assert response.headers["cache-control"] == "no-store"


# --------------------------------------------------------------------------- #
# 实例化看门狗（QUOT-589）：dev 实例（5951）有自己的 scoped 看门狗（5953）
# --------------------------------------------------------------------------- #


def test_dev_watchdog_config_is_scoped_to_5951(tmp_path: Path) -> None:
    config = WatchdogConfig(
        data_root=tmp_path,
        port=5953,
        api_base="http://127.0.0.1:5951",
        restart_script="",
    )
    assert config.api_port == 5951
    assert config.port == 5953


def test_dev_watchdog_origin_uses_its_own_port() -> None:
    """Origin 白名单跟端口走：5953 看门狗只认 5950/5173/5953，prod 5952 不放行。"""
    # 5951 页面（经 backend 同源转发）打到看门狗时 Origin 已被代理冒充成 5953
    assert origin_allowed("http://127.0.0.1:5953", port=5953)
    assert origin_allowed("http://localhost:5953", port=5953)
    # prod 看门狗端口 5952 不在 dev 看门狗的白名单里
    assert not origin_allowed("http://127.0.0.1:5952", port=5953)


def test_dev_watchdog_restart_only_touches_its_api_port(tmp_path: Path) -> None:
    """B 项验收：dev 看门狗（--api 5951）重启只找 / 只杀 5951 的监听者。

    `restart_service` 的端口归属判定只吃 `config.api_port`——注入 5951 后，
    prod 端口 5952 的监听者（PID 7768）永远不会进入它的查询与 kill 面。
    """
    from ladderstock.cli.watchdog import restart_service

    script = tmp_path / "restart-dev.ps1"
    script.write_text("", encoding="utf-8")
    config = WatchdogConfig(
        data_root=tmp_path,
        port=5953,
        api_base="http://127.0.0.1:5951",
        # dev 实例不回退 prod 计划任务：start-dev-watchdog.ps1 显式传空
        restart_task="",
        restart_script=str(script),
    )
    probed_ports: list[int] = []

    def lookup(port: int) -> int | None:
        probed_ports.append(port)
        return 4242 if port == 5951 else None

    class _Result:
        returncode = 0

    def fake_run(argv: list[str], **kw: Any) -> _Result:
        # 只允许走 --restart-script（scripts/restart-dev.ps1 的契约），
        # 绝不回退 prod 计划任务
        assert argv[0] == "powershell"
        assert argv[-1].endswith("restart-dev.ps1")
        return _Result()

    result = restart_service(
        config,
        run=fake_run,
        lookup_pid=lookup,
        name_of=lambda _: "python.exe",
        terminate=lambda pid: pid == 4242,
        wait=lambda _: None,
    )

    code, payload = result
    # 只查 5951（归属 + 让端口等待重查），5952 从未出现
    assert set(probed_ports) == {5951}
    assert payload["killed_pid"] == 4242
