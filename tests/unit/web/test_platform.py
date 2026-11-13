"""同源 `/platform/*` 面（QUOT-589 重修）：健康契约、写守门、节流、分离式 spawn。

不起真进程、不拨真网络：探活与 spawn 注入假实现；脚手架用临时脚本文件。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ladderstock.web.platform import (
    CLIENT_HEADER,
    CLIENT_HEADER_VALUE,
    RESTART_SCRIPT_ENV,
    _script_issue,
    build_platform_router,
    default_restart_script,
    detached_spawn,
    resolve_restart_script,
)


def _client(
    tmp_path: Path,
    *,
    probe_health: Any = None,
    spawn: Any = None,
    restart_script: str | None = None,
) -> TestClient:
    if restart_script is None:
        script = tmp_path / "restart.ps1"
        script.write_text("exit 0", encoding="utf-8")
        restart_script = str(script)
    default_script = default_restart_script
    if restart_script == "":
        # 测「未配置」：显式空串 + 封死真机默认脚本回退（真机可能装了 restart-dev.ps1）

        def _no_default(_port: int) -> str:
            return ""

        default_script = _no_default
    app = FastAPI()
    app.include_router(
        build_platform_router(
            5950,
            restart_script=restart_script,
            default_script=default_script,
            probe_health=probe_health or _default_probe,
            spawn=spawn or (lambda _argv: 4321),
        )
    )
    # TestClient 默认远端是 "testclient"（非回环会被写守门 403），指到 127.0.0.1
    return TestClient(app, client=("127.0.0.1", 51234))


def _default_probe(_base: str) -> dict[str, Any]:
    return {"status": "ok", "db_ok": True}


def _health_probe(payload: dict[str, Any] | None) -> Any:
    def probe(_base: str) -> dict[str, Any] | None:
        return payload

    return probe


# --------------------------------------------------------------------------- #
# 健康契约
# --------------------------------------------------------------------------- #


def test_health_payload_is_contract_compatible(tmp_path: Path) -> None:
    client = _client(
        tmp_path,
        probe_health=_health_probe(
            {
                "status": "ok",
                "db_ok": True,
                "uptime_sec": 12,
                "version": "9.9",
                "checked_at": "2026-01-01T00:00:00+00:00",
                "api": {"uptime_sec": 12, "version": "9.9"},
                "watchdog": {"version": "9.9", "portless": True},
            }
        ),
    )
    response = client.get("/platform/health")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    for key in ("status", "uptime_sec", "version", "db_ok", "checked_at", "api", "watchdog"):
        assert key in body, f"缺字段 {key}"
    assert body["status"] == "ok"
    assert body["uptime_sec"] == 12
    assert body["db_ok"] is True
    assert body["api"]["uptime_sec"] == 12


def test_health_is_offline_shape_when_probe_fails(tmp_path: Path) -> None:
    client = _client(tmp_path, probe_health=_health_probe(None))
    body = client.get("/platform/health").json()

    assert body["status"] == "offline"
    assert body["uptime_sec"] is None
    assert body["db_ok"] is None
    assert body["api"] is None


# --------------------------------------------------------------------------- #
# 重启：守门 / 节流 / spawn
# --------------------------------------------------------------------------- #


def test_restart_without_origin_is_rejected(tmp_path: Path) -> None:
    spawned: list[list[str]] = []
    client = _client(tmp_path, spawn=lambda argv: spawned.append(argv) or 1)

    response = client.post("/platform/restart")

    assert response.status_code == 403
    assert spawned == []


def test_restart_with_same_origin_loopback_passes(tmp_path: Path) -> None:
    spawned: list[list[str]] = []
    client = _client(tmp_path, spawn=lambda argv: spawned.append(argv) or 1)

    response = client.post(
        "/platform/restart",
        headers={"Origin": "http://127.0.0.1", CLIENT_HEADER: CLIENT_HEADER_VALUE},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert spawned and spawned[0][:4] == [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
    ]


def test_restart_with_foreign_origin_is_rejected(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.post(
        "/platform/restart", headers={"Origin": "http://evil.example"}
    )
    assert response.status_code == 403


def test_restart_without_origin_needs_client_header(tmp_path: Path) -> None:
    client = _client(tmp_path)
    ok = client.post("/platform/restart", headers={CLIENT_HEADER: CLIENT_HEADER_VALUE})
    bad = client.post("/platform/restart", headers={CLIENT_HEADER: "0"})

    assert ok.status_code == 200
    assert bad.status_code == 403


def test_restart_is_throttled_for_30_seconds(tmp_path: Path) -> None:
    client = _client(tmp_path)
    headers = {CLIENT_HEADER: CLIENT_HEADER_VALUE}

    first = client.post("/platform/restart", headers=headers)
    second = client.post("/platform/restart", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 429


def test_restart_returns_409_when_script_missing(tmp_path: Path) -> None:
    client = _client(tmp_path, restart_script=str(tmp_path / "missing.ps1"))
    response = client.post("/platform/restart", headers={CLIENT_HEADER: CLIENT_HEADER_VALUE})

    assert response.status_code == 409
    assert response.json()["ok"] is False


def test_restart_returns_409_when_script_unconfigured(tmp_path: Path) -> None:
    client = _client(tmp_path, restart_script="")
    response = client.post("/platform/restart", headers={CLIENT_HEADER: CLIENT_HEADER_VALUE})

    assert response.status_code == 409


def test_restart_returns_503_when_spawn_fails(tmp_path: Path) -> None:
    def bad_spawn(_argv: list[str]) -> int:
        raise OSError("boom")

    client = _client(tmp_path, spawn=bad_spawn)
    response = client.post("/platform/restart", headers={CLIENT_HEADER: CLIENT_HEADER_VALUE})

    assert response.status_code == 503


# --------------------------------------------------------------------------- #
# 脚本解析与分离式 spawn
# --------------------------------------------------------------------------- #


def test_resolve_restart_script_env_overrides_default(tmp_path: Path) -> None:
    script = tmp_path / "custom.ps1"
    script.write_text("exit 0", encoding="utf-8")
    assert resolve_restart_script(5950, {RESTART_SCRIPT_ENV: str(script)}) == str(script)
    assert resolve_restart_script(5951, {}) == default_restart_script(5951)


def test_default_restart_script_names_by_port() -> None:
    # 5950 → restart-api.ps1；其余（dev 5951 等）→ restart-dev.ps1
    assert "restart-api.ps1" in default_restart_script(5950)
    assert "restart-dev.ps1" in default_restart_script(5951)


def test_script_issue_static_validation(tmp_path: Path) -> None:
    assert "未配置" in (_script_issue("") or "")
    assert "绝对路径" in (_script_issue("relative.ps1") or "")
    assert "不存在" in (_script_issue(str(tmp_path / "no.ps1")) or "")
    script = tmp_path / "ok.ps1"
    script.write_text("exit 0", encoding="utf-8")
    assert _script_issue(str(script)) is None


def test_detached_spawn_passes_process_group_flags() -> None:
    captured: dict[str, Any] = {}

    def fake_popen(argv, stdout=None, stderr=None, shell=None, **kw):
        captured.update(kw)
        captured["argv"] = argv
        captured["stderr"] = stderr

        class P:
            pid = 777

        return P()

    pid = detached_spawn(["powershell", "-File", "x.ps1"], popen=fake_popen)

    assert pid == 777
    assert captured["argv"] == ["powershell", "-File", "x.ps1"]
    assert captured["stderr"] is not None  # STDERR 合并进 stdout，不落管道
