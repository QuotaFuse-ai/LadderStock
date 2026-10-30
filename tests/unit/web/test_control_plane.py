"""QUOT-121 控制面（DEC-014）：`/api/control/*` 的可执行验收口径。

这里把 PRD `PRD-SETTINGS-CONTROL-PLANE.md` 的验收标准逐条钉成测试，
**全部走 mock / 临时目录**：不装包、不起 peer、不杀进程、不碰端口占用者。

覆盖：
1. 状态判定 5 级（未配置 / 未安装 / 已安装 / 启动中 / 运行中 / 已停止 / 异常）；
2. 端口冲突：占用的端口只报告、不换端口、不终止；
3. 配置白名单：非白名单键 400；密钥 GET 只回打码预览；空串不修改；clear 才删；
4. 安装任务成功 / 失败路径（失败必须带最后 20 行日志）；
5. `LDST_CONTROL_ENABLED=0` → 整族 404 且数据面不受影响；
6. 非回环来源 → 403。
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from ladderstock.core.boundary import PEER_CONTROL
from ladderstock.web import control
from ladderstock.web.control import (
    ControlPlane,
    Job,
    PortOwner,
    StepResult,
    mask_secrets,
    secret_preview,
)
from ladderstock.web.server import create_app

LOOPBACK = ("127.0.0.1", 54321)
REMOTE = ("10.1.2.3", 54321)

#: 控制面写请求的来源证据（QUOT-178）：服务端要求 Origin 同源或这个显式头
WRITE_HEADERS = {"X-LDST-Client": "1"}

SENTINEL_KEY = "sk-live-QUOT121-SENTINEL-donotprint"

#: 停止类用例的统一时间基准：状态文件里的 `started_at` 与假「进程创建时间」都用它。
#: 真实路径（`_now_iso()` 落盘）与进程创建时间天然有子秒级差，5s 容差内即视为同一进程。
RECORDED_AT = "2026-10-29T00:00:00+00:00"
RECORDED_AT_DT = datetime(2026, 10, 29, 0, 0, 0, tzinfo=UTC)


# --------------------------------------------------------------------------- #
# 夹具：临时 peer 目录 + 可控的探测原语
# --------------------------------------------------------------------------- #

class FakeRuntime:
    """记录被调用的命令，并按脚本返回结果（绝不真的执行任何东西）。"""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.responses: dict[str, StepResult] = {}
        self.default = StepResult(0, "ok")

    def run(self, args: list[str], cwd: Path, timeout: float) -> StepResult:
        self.calls.append(list(args))
        joined = " ".join(args)
        for needle, result in self.responses.items():
            if needle in joined:
                return result
        if "import " in joined and "-c" in args:
            return self.responses.get("__import__", StepResult(0, "1.2.3\n"))
        return self.default


@pytest.fixture()
def peer_dirs(tmp_path: Path) -> dict[str, Path]:
    alpha = tmp_path / "peers" / "alpha"
    beta = tmp_path / "peers" / "beta"
    alpha.mkdir(parents=True)
    beta.mkdir(parents=True)
    return {"alpha": alpha, "beta": beta}


def _peers(peer_dirs: dict[str, Path]) -> dict[str, Any]:
    from ladderstock.core.boundary import PeerControl

    return {
        "alpha": PeerControl(
            key="alpha",
            directory=str(peer_dirs["alpha"]),
            import_package="alpha_pkg",
            mcp_port=8002,
            web_port=8789,
            web_port_label="管理端",
            health_path="/health",
            start_commands=(("alpha", "start"),),
            config_keys=("MCP_HTTP_PORT", "ALPHA_API_KEY"),
            secret_keys=("ALPHA_API_KEY",),
        ),
        "beta": PeerControl(
            key="beta",
            directory=str(peer_dirs["beta"]),
            import_package="beta_pkg",
            mcp_port=8791,
            web_port=8790,
            web_port_label="HTTP 服务",
            health_path="/api/health",
            start_commands=(("beta", "serve"),),
            config_keys=("QF_WIRE_PORT",),
            has_wheel_dist=False,
        ),
    }


def _plane(
    tmp_path: Path,
    peer_dirs: dict[str, Path],
    *,
    runtime: FakeRuntime | None = None,
    ports: Any = None,
    health: Any = None,
    spawn: Any = None,
    registered: tuple[str, ...] = ("alpha", "beta"),
    http_post: Any = None,
    process_info: Any = None,
    process_started: Any = None,
    resolve: Any = None,
) -> ControlPlane:
    runtime = runtime or FakeRuntime()
    return ControlPlane(
        data_root=tmp_path,
        peers=_peers(peer_dirs),
        sources_loader=lambda: {key: {"product": key} for key in registered},
        run=runtime.run,
        port_probe=ports or (lambda port: []),
        health_probe=health or (lambda url, timeout: (True, "HTTP 200")),
        spawn=spawn or (lambda args, *, cwd, log_path: SimpleNamespace(pid=4242)),
        http_post=http_post
        or (lambda url, payload, headers, timeout: (200, json.dumps({"model": "m"}))),
        # 默认查不到可执行文件路径 → 停止类用例天然走「拒绝」分支，不碰真实进程
        process_info=process_info or (lambda pid, cwd: (None, None)),
        # 默认给一个与 RECORDED_AT 完全对齐的假创建时间（QUOT-177 P1-2），不查真实进程
        process_started=process_started or (lambda pid: RECORDED_AT_DT),
        resolve=resolve
        or (
            lambda host: ["127.0.0.1"]
            if host in {"127.0.0.1", "localhost", "::1"}
            else ["93.184.216.34"]
        ),
    )


def _venv_bin(peer_dir: Path) -> Path:
    """venv 的脚本目录：Windows 是 `Scripts/`，POSIX 是 `bin/`（与控制面探测路径一致）。"""
    return peer_dir / ".venv" / ("Scripts" if os.name == "nt" else "bin")


def _venv(peer_dir: Path) -> Path:
    scripts = _venv_bin(peer_dir)
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / ("python.exe" if os.name == "nt" else "python")).write_text(
        "", encoding="utf-8"
    )
    return peer_dir / ".venv"


def _venv_script(peer_dir: Path, name: str) -> Path:
    return _venv_bin(peer_dir) / f"{name}{'.exe' if os.name == 'nt' else ''}"


def _wait(plane: ControlPlane, job_id: str, timeout: float = 5.0) -> Job:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = plane.get_job(job_id)
        if job is not None and job.status in {"ok", "failed"}:
            return job
        time.sleep(0.01)
    raise AssertionError(f"任务 {job_id} 未在 {timeout}s 内结束")


# --------------------------------------------------------------------------- #
# 1. 状态判定 5 级
# --------------------------------------------------------------------------- #

def test_status_unconfigured_when_directory_missing(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    plane = _plane(tmp_path, peer_dirs)
    # alpha 目录存在且未装 venv → 未安装
    assert plane.snapshot("alpha")["status"] == control.STATUS_NOT_INSTALLED
    # beta 目录删掉 → 未配置
    shutil.rmtree(peer_dirs["beta"])
    assert plane.snapshot("beta")["status"] == control.STATUS_UNCONFIGURED
    assert "目录不存在" in plane.snapshot("beta")["detail"]


def test_status_unconfigured_when_not_registered(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    plane = _plane(tmp_path, peer_dirs, registered=("beta",))
    snapshot = plane.snapshot("alpha")
    assert snapshot["status"] == control.STATUS_UNCONFIGURED
    assert snapshot["registered"] is False
    assert "sources.yaml" in snapshot["detail"]


def test_status_not_installed_without_venv(tmp_path: Path, peer_dirs: dict[str, Path]) -> None:
    plane = _plane(tmp_path, peer_dirs)
    snapshot = plane.snapshot("alpha")
    assert snapshot["status"] == control.STATUS_NOT_INSTALLED
    assert snapshot["installed"] is False
    assert ".venv 不存在" in snapshot["detail"]
    assert snapshot["actions"]["can_install"] is True


def test_status_installed_when_import_succeeds(tmp_path: Path, peer_dirs: dict[str, Path]) -> None:
    _venv(peer_dirs["alpha"])
    plane = _plane(tmp_path, peer_dirs)
    snapshot = plane.snapshot("alpha")
    assert snapshot["status"] == control.STATUS_INSTALLED
    assert snapshot["version"] == "1.2.3"
    assert snapshot["actions"]["can_start"] is True


def test_status_running_requires_port_and_health(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    _venv(peer_dirs["alpha"])
    def ports(port: int) -> list[PortOwner]:
        return [PortOwner(pid=999, process="python.exe")] if port == 8002 else []

    plane = _plane(tmp_path, peer_dirs, ports=ports)
    snapshot = plane.snapshot("alpha")
    assert snapshot["status"] == control.STATUS_RUNNING
    assert snapshot["open_url"] == "http://127.0.0.1:8789"


def test_status_failed_when_health_down(tmp_path: Path, peer_dirs: dict[str, Path]) -> None:
    _venv(peer_dirs["alpha"])
    def ports(port: int) -> list[PortOwner]:
        return [PortOwner(pid=999, process="python.exe")] if port == 8002 else []

    plane = _plane(
        tmp_path,
        peer_dirs,
        ports=ports,
        health=lambda url, timeout: (False, "无法连接"),
    )
    snapshot = plane.snapshot("alpha")
    assert snapshot["status"] == control.STATUS_FAILED
    assert "健康检查失败" in snapshot["detail"]


def test_status_starting_while_start_job_in_flight(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    _venv(peer_dirs["alpha"])
    plane = _plane(tmp_path, peer_dirs)
    job = Job(id="j1", peer="alpha", action="start", status="running", step="等待端口监听")
    plane._last_job["alpha"] = job  # 模拟执行中的启动任务
    snapshot = plane.snapshot("alpha")
    assert snapshot["status"] == control.STATUS_STARTING
    assert "等待端口监听" in snapshot["detail"]


def test_status_stopped_after_recorded_process_exits(
    tmp_path: Path, peer_dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _venv(peer_dirs["alpha"])
    plane = _plane(tmp_path, peer_dirs)
    plane._peer_state("alpha")["pids"] = [123456]
    monkeypatch.setattr(control, "_pid_alive", lambda pid: False)
    snapshot = plane.snapshot("alpha")
    assert snapshot["status"] == control.STATUS_STOPPED


# --------------------------------------------------------------------------- #
# 2. 端口冲突：只报告，不换端口、不终止
# --------------------------------------------------------------------------- #

def test_port_conflicts_reported_for_known_avoided_ports(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    ports = {
        8001: [PortOwner(pid=777, process="wslhost.exe")],
        8787: [PortOwner(pid=777, process="wslhost.exe")],
    }
    plane = _plane(tmp_path, peer_dirs, ports=lambda port: ports.get(port, []))
    conflicts = plane.snapshot("alpha")["port_conflicts"]
    assert conflicts == []  # alpha 不是 tickhub_us，没有历史坑位
    # 真实元数据（tickhub_us）才带 8001 / 8787
    real = ControlPlane(
        data_root=tmp_path / "real",
        peers={"tickhub_us": PEER_CONTROL["tickhub_us"]},
        sources_loader=lambda: {"tickhub_us": {}},
        run=FakeRuntime().run,
        port_probe=lambda port: ports.get(port, []),
        health_probe=lambda url, timeout: (True, "HTTP 200"),
    )
    snapshot = real.snapshot("tickhub_us")
    reported = {item["port"]: item for item in snapshot["port_conflicts"]}
    assert reported[8001]["pid"] == 777
    assert reported[8787]["process"] == "wslhost.exe"
    assert "8002" in reported[8001]["note"]


def test_start_refuses_when_port_occupied(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    _venv(peer_dirs["alpha"])
    spawned: list[Any] = []
    plane = _plane(
        tmp_path,
        peer_dirs,
        ports=lambda port: [PortOwner(pid=555, process="wslhost.exe")] if port == 8002 else [],
        spawn=lambda args, *, cwd, log_path: spawned.append(args),
    )
    job = plane.create_job("alpha", "start")
    finished = _wait(plane, job.id)
    assert finished.status == "failed"
    assert "端口 8002 已被占用" in (finished.error or "")
    assert "PID 555" in (finished.error or "")
    assert "不自动换端口" in (finished.error or "")
    assert spawned == []  # 预检失败 → 绝不起进程


def test_foreign_occupier_marks_failed_without_killing(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    """未安装的 peer 端口被别人听着：如实说「被占用」，状态失败，绝不杀占用者。"""
    runtime = FakeRuntime()
    plane = _plane(
        tmp_path,
        peer_dirs,
        runtime=runtime,
        ports=lambda port: [PortOwner(pid=321, process="other.exe")] if port == 8789 else [],
        health=lambda url, timeout: (False, "无法连接：[WinError 10061] 拒绝连接"),
    )
    snapshot = plane.snapshot("alpha")  # alpha 的 web 端口就是 8789
    assert snapshot["status"] == control.STATUS_FAILED
    assert "8789" in snapshot["detail"]
    assert "其他进程占用" in snapshot["detail"]
    assert "不会终止占用者" in snapshot["detail"]
    conflict = snapshot["port_conflicts"][0]
    assert conflict["pid"] == 321 and conflict["foreign"] is True
    assert runtime.calls == []  # 看一眼而已，没执行任何子进程


def test_stop_refuses_to_kill_foreign_process(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    runtime = FakeRuntime()
    plane = _plane(
        tmp_path,
        peer_dirs,
        runtime=runtime,
        ports=lambda port: [PortOwner(pid=999, process="other.exe")] if port == 8002 else [],
    )
    job = plane.create_job("alpha", "stop")
    finished = _wait(plane, job.id)
    assert finished.status == "failed"
    assert "不终止非本控制面进程" in (finished.error or "")
    assert not any("taskkill" in " ".join(call) for call in runtime.calls)


# --------------------------------------------------------------------------- #
# 3. 安装任务：成功 / 失败（mock 子进程）
# --------------------------------------------------------------------------- #

def test_install_job_success_uses_local_wheel(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    (peer_dirs["alpha"] / "dist").mkdir()
    wheel = peer_dirs["alpha"] / "dist" / "alpha-1.0.0-py3-none-any.whl"
    wheel.write_text("", encoding="utf-8")
    runtime = FakeRuntime()
    plane = _plane(tmp_path, peer_dirs, runtime=runtime)
    job = plane.create_job("alpha", "install")
    finished = _wait(plane, job.id)

    assert finished.status == "ok", finished.error
    commands = [" ".join(call) for call in runtime.calls]
    assert any("-m venv" in cmd for cmd in commands)
    assert any("pip install -U pip" in cmd for cmd in commands)
    assert any(wheel.name in cmd for cmd in commands)  # 装本地产物，不联网取包
    assert any("import alpha_pkg" in cmd for cmd in commands)
    assert finished.view()["log_tail"], "任务日志不能为空"


def test_install_job_degrades_to_editable_when_no_wheel(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    runtime = FakeRuntime()
    plane = _plane(tmp_path, peer_dirs, runtime=runtime)
    job = plane.create_job("beta", "install")
    assert _wait(plane, job.id).status == "ok"
    assert any("pip install -e ." in " ".join(call) for call in runtime.calls)


def test_install_job_failure_keeps_last_lines_and_no_retry_storm(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    runtime = FakeRuntime()
    runtime.responses["pip install -U pip"] = StepResult(1, "ERROR: no network\nline2")
    plane = _plane(tmp_path, peer_dirs, runtime=runtime)
    job = plane.create_job("alpha", "install")
    finished = _wait(plane, job.id)

    assert finished.status == "failed"
    assert "升级 pip 失败" in (finished.error or "")
    view = finished.view()
    assert 0 < len(view["log_tail"]) <= control.JOB_TAIL_LINES
    assert any("no network" in line for line in view["log_tail"])
    # 重试 1 次（共 2 次调用），不无限重试
    pip_calls = [c for c in runtime.calls if "pip install -U pip" in " ".join(c)]
    assert len(pip_calls) == 2


def test_job_store_exposes_queue_running_ok(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    plane = _plane(tmp_path, peer_dirs)
    job = plane.create_job("alpha", "install")
    view = plane.get_job(job.id).view()
    assert set(view) == {
        "id",
        "peer",
        "action",
        "status",
        "step",
        "progress",
        "started_at",
        "ended_at",
        "log_tail",
        "error",
    }
    _wait(plane, job.id)
    assert plane.snapshot("alpha")["last_job"]["status"] == "ok"


# --------------------------------------------------------------------------- #
# 4. 配置：白名单 / 脱敏 / 空串不修改 / clear 才删
# --------------------------------------------------------------------------- #

def test_config_get_masks_secrets_and_never_returns_plaintext(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    (peer_dirs["alpha"] / ".env.local").write_text(
        f"MCP_HTTP_PORT=8002\nALPHA_API_KEY={SENTINEL_KEY}\n", encoding="utf-8"
    )
    plane = _plane(tmp_path, peer_dirs)
    payload = plane.read_config("alpha")
    assert payload["values"]["MCP_HTTP_PORT"] == {
        "set": True,
        "secret": False,
        "value": "8002",
    }
    secret_row = payload["values"]["ALPHA_API_KEY"]
    assert secret_row["set"] is True
    assert secret_row["secret"] is True
    assert "preview" in secret_row and "value" not in secret_row
    assert SENTINEL_KEY not in json.dumps(payload, ensure_ascii=False)


def test_config_put_rejects_non_whitelisted_key(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    app = create_app(
        data_root=tmp_path,
        dist_dir=tmp_path / "missing",
        control_plane=_plane(tmp_path, peer_dirs),
    )
    client = TestClient(app, client=LOOPBACK, headers=WRITE_HEADERS)
    response = client.put(
        "/api/control/peers/alpha/config",
        json={"values": {"PATH": "/evil", "MCP_HTTP_PORT": "8002"}},
    )
    assert response.status_code == 400
    assert "PATH" in response.json()["detail"]
    assert not (peer_dirs["alpha"] / ".env.local").exists()


def test_config_put_writes_private_file_and_keeps_other_lines(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    env_file = peer_dirs["alpha"] / ".env.local"
    env_file.write_text("# 手写注释\nOTHER=keep-me\n", encoding="utf-8")
    plane = _plane(tmp_path, peer_dirs)
    payload = plane.update_config("alpha", {"MCP_HTTP_PORT": "8003"}, [])
    assert payload["updated"] == ["MCP_HTTP_PORT"]
    text = env_file.read_text(encoding="utf-8")
    assert "OTHER=keep-me" in text and "MCP_HTTP_PORT=8003" in text
    if os.name == "posix":  # Windows 上 chmod 语义有限，仅在 POSIX 严格断言
        mode = stat.S_IMODE(env_file.stat().st_mode)
        assert mode == 0o600
    assert payload["values"]["MCP_HTTP_PORT"]["value"] == "8003"


def test_config_put_empty_string_means_unchanged_and_clear_deletes(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    env_file = peer_dirs["alpha"] / ".env.local"
    env_file.write_text("MCP_HTTP_PORT=8002\nALPHA_API_KEY=sk-abcd12345678\n", encoding="utf-8")
    plane = _plane(tmp_path, peer_dirs)
    plane.update_config("alpha", {"MCP_HTTP_PORT": ""}, [])
    assert "MCP_HTTP_PORT=8002" in env_file.read_text(encoding="utf-8")
    plane.update_config("alpha", {}, ["ALPHA_API_KEY"])
    text = env_file.read_text(encoding="utf-8")
    assert "ALPHA_API_KEY" not in text
    assert plane.read_config("alpha")["values"]["ALPHA_API_KEY"]["set"] is False


def test_config_put_rejects_newlines(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    from fastapi import HTTPException

    plane = _plane(tmp_path, peer_dirs)
    with pytest.raises(HTTPException) as excinfo:
        plane.update_config("alpha", {"MCP_HTTP_PORT": "8002\nEVIL=1"}, [])
    assert excinfo.value.status_code == 400


# --------------------------------------------------------------------------- #
# 5. 开关 / 来源 / 未知资源
# --------------------------------------------------------------------------- #

def test_control_disabled_returns_404_and_data_plane_unaffected(
    tmp_path: Path, peer_dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    app = create_app(
        data_root=tmp_path, dist_dir=tmp_path / "missing", control_plane=_plane(tmp_path, peer_dirs)
    )
    client = TestClient(app, client=LOOPBACK)
    assert client.get("/api/control/peers").status_code == 200

    monkeypatch.setenv(control.CONTROL_ENABLED_ENV, "0")
    for path in ("/api/control/peers", "/api/control/peers/alpha"):
        assert client.get(path).status_code == 404
    assert client.get("/api/control/peers/alpha/config").status_code == 404

    assert client.get("/api/health").status_code == 200
    assert client.get("/api/health").json()["read_only"] is True
    assert client.get("/api/state/config").status_code == 200


def test_non_loopback_source_is_rejected(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    app = create_app(
        data_root=tmp_path, dist_dir=tmp_path / "missing", control_plane=_plane(tmp_path, peer_dirs)
    )
    remote = TestClient(app, client=REMOTE)
    response = remote.get("/api/control/peers")
    assert response.status_code == 403
    # 数据面只读接口不因控制面策略而拒绝
    assert remote.get("/api/health").status_code == 200


def test_unknown_peer_and_job_are_404(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    app = create_app(
        data_root=tmp_path, dist_dir=tmp_path / "missing", control_plane=_plane(tmp_path, peer_dirs)
    )
    client = TestClient(app, client=LOOPBACK, headers=WRITE_HEADERS)
    assert client.get("/api/control/peers/nope").status_code == 404
    assert client.post("/api/control/peers/nope/install").status_code == 404
    assert client.get("/api/control/jobs/nope").status_code == 404


def test_unknown_api_path_still_404_not_swallowed_by_control(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    app = create_app(
        data_root=tmp_path, dist_dir=tmp_path / "missing", control_plane=_plane(tmp_path, peer_dirs)
    )
    client = TestClient(app, client=LOOPBACK)
    assert client.get("/api/controlx").status_code == 404


def test_endpoints_return_expected_shapes(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    app = create_app(
        data_root=tmp_path, dist_dir=tmp_path / "missing", control_plane=_plane(tmp_path, peer_dirs)
    )
    client = TestClient(app, client=LOOPBACK, headers=WRITE_HEADERS)
    board = client.get("/api/control/peers").json()
    assert board["ok"] is True
    assert [row["id"] for row in board["peers"]] == ["alpha", "beta"]
    assert board["ports"]["board"] == 5950

    install = client.post("/api/control/peers/alpha/install", json={"dev": False})
    assert install.status_code == 200
    job_id = install.json()["job_id"]
    job = client.get(f"/api/control/jobs/{job_id}")
    assert job.status_code == 200
    assert job.json()["action"] == "install"

    logs = client.get("/api/control/peers/alpha/logs?tail=50")
    assert logs.status_code == 200
    assert "lines" in logs.json()

    ai = client.post(
        "/api/control/ai/test",
        json={"peer": "alpha", "base_url": "http://127.0.0.1:1/v1", "model": "m"},
    )
    assert ai.status_code == 200
    assert ai.json()["ok"] is True


def test_ai_test_rejects_bad_input(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    app = create_app(
        data_root=tmp_path, dist_dir=tmp_path / "missing", control_plane=_plane(tmp_path, peer_dirs)
    )
    client = TestClient(app, client=LOOPBACK, headers=WRITE_HEADERS)
    assert client.post(
        "/api/control/ai/test",
        json={"peer": "alpha", "base_url": "ftp://x", "model": "m"},
    ).status_code == 400
    assert client.post(
        "/api/control/ai/test",
        json={"peer": "nope", "base_url": "http://127.0.0.1", "model": "m"},
    ).status_code == 404


# --------------------------------------------------------------------------- #
# 6. 脱敏 & 元数据
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("sk-live-abcdefgh1234", "sk-****1234"),
        ("short", "****"),
        ("", ""),
    ],
)
def test_secret_preview(raw: str, expected: str) -> None:
    assert secret_preview(raw) == expected


def test_mask_secrets_covers_values_and_patterns() -> None:
    text = f"key={SENTINEL_KEY}\nAuthorization: Bearer tok_abcdefgh12345\nplain ok"
    masked = mask_secrets(text)
    assert SENTINEL_KEY not in masked
    assert "tok_abcdefgh12345" not in masked
    assert "plain ok" in masked


def test_probe_health_ignores_system_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """回环探测必须绕开系统代理：本机开代理时 health 不能假装 peer 挂了。"""
    import http.server
    import threading

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server 约定
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

        def log_message(self, *args: Any) -> None:  # noqa: D401 - 静音
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    # 把所有代理都指向一个必然连不上的地址
    for name in ("http_proxy", "https_proxy", "all_proxy", "no_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9" if name != "no_proxy" else "")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    try:
        ok, detail = control.probe_health(f"http://127.0.0.1:{port}/healthz", timeout=2.0)
    finally:
        server.shutdown()
    assert ok is True, detail
    assert detail == "HTTP 200"


def test_logs_endpoint_masks_stored_secret(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    (peer_dirs["alpha"] / ".env.local").write_text(
        f"ALPHA_API_KEY={SENTINEL_KEY}\n", encoding="utf-8"
    )
    log_dir = tmp_path / "logs" / "control"
    log_dir.mkdir(parents=True)
    (log_dir / "alpha.log").write_text(f"boot with {SENTINEL_KEY}\n", encoding="utf-8")
    plane = _plane(tmp_path, peer_dirs)
    payload = plane.read_logs("alpha")
    assert SENTINEL_KEY not in json.dumps(payload, ensure_ascii=False)


def test_tickhub_us_metadata_matches_dec013() -> None:
    us = PEER_CONTROL["tickhub_us"]
    assert us.mcp_port == 8002
    assert us.web_port == 8789
    assert us.has_wheel_dist is False  # 无 dist/*.whl，只能 -e
    assert us.import_package == "tickhub_us"
    assert {"MCP_HTTP_PORT", "ADMIN_HTTP_PORT", "POLYGON_API_KEY"} <= set(us.config_keys)
    assert "POLYGON_API_KEY" in us.secret_keys
    assert PEER_CONTROL["tickhub"].mcp_port == 8000
    assert PEER_CONTROL["wire"].mcp_port == 8791
    assert PEER_CONTROL["senate"].mcp_port == 8765


def test_peer_metadata_points_at_the_dotenv_each_peer_really_reads() -> None:
    """控制面写配置必须落在 peer 真正加载的文件上（实测得出的结论）。"""
    assert PEER_CONTROL["tickhub"].env_filename == ".env"
    assert PEER_CONTROL["tickhub_us"].env_filename == ".env"
    assert PEER_CONTROL["senate"].env_filename == ".env"
    assert PEER_CONTROL["wire"].env_filename == ".env.local"  # Wire 优先读 .env.local
    for ctl in PEER_CONTROL.values():
        assert ctl.env_filename in {".env", ".env.local"}  # 只有这两种，不允许乱写


def test_peer_health_paths_are_the_ones_the_services_serve() -> None:
    """健康检查路径必须真实存在，否则快照会一直显示异常。"""
    for key in ("tickhub", "tickhub_us", "senate"):
        assert PEER_CONTROL[key].health_path == "/healthz"
    assert PEER_CONTROL["wire"].health_path == "/api/health"


def test_config_notes_flag_keys_that_do_not_take_effect() -> None:
    """PRD §5 的键有的当前不生效，控制面必须把话说清楚（DEC-014 不静默降级）。"""
    assert any("MCP_HTTP_PORT" in note for note in PEER_CONTROL["tickhub"].config_notes)
    assert any("admin" in note for note in PEER_CONTROL["tickhub_us"].config_notes)
    assert any("llm_config.toml" in note for note in PEER_CONTROL["senate"].config_notes)


def test_read_config_reports_env_file_and_notes(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    (peer_dirs["alpha"] / ".env.local").write_text("MCP_HTTP_PORT=8002\n", encoding="utf-8")
    plane = _plane(tmp_path, peer_dirs)
    payload = plane.read_config("alpha")
    assert payload["env_file"].endswith(".env.local")
    assert payload["exists"] is True
    assert isinstance(payload["notes"], list)


# --------------------------------------------------------------------------- #
# 7. QUOT-123 安全回归：Host / Origin / PID 归属 / SSRF / 开关全 404
# --------------------------------------------------------------------------- #

def _loopback_client(tmp_path: Path, peer_dirs: dict[str, Path], **kwargs: Any) -> TestClient:
    """监听地址也是回环的客户端（`scope["server"]=("127.0.0.1", 5950)`）。"""
    app = create_app(
        data_root=tmp_path,
        dist_dir=tmp_path / "missing",
        control_plane=_plane(tmp_path, peer_dirs, **kwargs),
    )
    return TestClient(app, base_url="http://127.0.0.1:5950", client=LOOPBACK)


def test_host_header_outside_whitelist_is_403(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    """B5：来源是回环但 Host 是攻击者域名（DNS rebinding）→ 403。"""
    client = _loopback_client(tmp_path, peer_dirs)
    host = {"Host": "attacker.test:5950"}
    assert client.get("/api/control/peers", headers=host).status_code == 403
    # 回环主机但端口与本机监听端口不一致 → 也拒绝
    assert client.get("/api/control/peers", headers={"Host": "127.0.0.1:17800"}).status_code == 403
    # 白名单内的 Host 放行
    assert client.get("/api/control/peers", headers={"Host": "127.0.0.1:5950"}).status_code == 200
    assert client.get("/api/control/peers", headers={"Host": "localhost:5950"}).status_code == 200


def test_cross_site_write_is_rejected_before_any_work(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    """B6：跨站简单请求带 Origin / Sec-Fetch-Site → 403，且不产生任何子进程。"""
    runtime = FakeRuntime()
    spawned: list[Any] = []
    client = _loopback_client(
        tmp_path,
        peer_dirs,
        runtime=runtime,
        spawn=lambda args, *, cwd, log_path: spawned.append(args),
    )
    evil = {"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}
    assert client.post("/api/control/peers/alpha/install", headers=evil).status_code == 403
    assert client.post("/api/control/peers/alpha/start", headers=evil).status_code == 403
    # 只带 Sec-Fetch-Site（没有任何正向证据）也拒绝
    assert (
        client.post(
            "/api/control/peers/alpha/start", headers={"Sec-Fetch-Site": "same-site"}
        ).status_code
        == 403
    )
    assert runtime.calls == []
    assert spawned == []
    # 本机页面发起的同源写请求放行
    same = {"Origin": "http://127.0.0.1:5950", "Sec-Fetch-Site": "same-origin"}
    assert client.post("/api/control/peers/alpha/start", headers=same).status_code == 200


def test_write_requires_positive_evidence(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    """QUOT-178：写方法必须「Origin 同源」或「X-LDST-Client: 1」，双缺席 403。"""
    runtime = FakeRuntime()
    spawned: list[Any] = []
    client = _loopback_client(
        tmp_path,
        peer_dirs,
        runtime=runtime,
        spawn=lambda args, *, cwd, log_path: spawned.append(args),
    )

    # ① 两个证据都缺席 → 403，且不建 job、不起进程
    assert client.post("/api/control/peers/alpha/install").status_code == 403
    assert runtime.calls == []
    assert spawned == []

    # ② 只带自定义头 → 200（curl / CLI / 脚本路径）
    handled = client.post("/api/control/peers/alpha/install", headers=WRITE_HEADERS)
    assert handled.status_code == 200
    assert handled.json()["job_id"]

    # ③ 只带同源 Origin → 200（浏览器路径）
    assert (
        client.post(
            "/api/control/peers/beta/start", headers={"Origin": "http://127.0.0.1:5950"}
        ).status_code
        == 200
    )

    # ④ 跨站 Origin + 自定义头 → 403：Origin 优先，不被自定义头救回来
    both = {"Origin": "https://evil.example", "X-LDST-Client": "1"}
    assert client.post("/api/control/peers/alpha/install", headers=both).status_code == 403

    # ⑤ Origin: null（沙箱 iframe / data: 页面）+ 自定义头 → 403
    assert (
        client.post(
            "/api/control/peers/alpha/install",
            headers={"Origin": "null", "X-LDST-Client": "1"},
        ).status_code
        == 403
    )


def test_start_records_pid_metadata(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    """P0-2：`_start` 落盘 `{pid, exe, started_at}`，不再只写裸 PID。"""
    _venv(peer_dirs["alpha"])
    _venv_script(peer_dirs["alpha"], "alpha").write_text("", encoding="utf-8")
    spawned: list[Any] = []

    def _spawn(args: list[str], *, cwd: Path, log_path: Path) -> Any:
        spawned.append(args)
        return SimpleNamespace(pid=4242)

    plane = _plane(
        tmp_path,
        peer_dirs,
        spawn=_spawn,
        ports=lambda port: [PortOwner(pid=4242, process="python.exe")] if spawned else [],
    )
    job = plane.create_job("alpha", "start")
    finished = _wait(plane, job.id)
    assert finished.status == "ok", finished.error
    records = plane._peer_state("alpha")["pids"]
    assert isinstance(records, list) and len(records) == 1
    assert records[0]["pid"] == 4242
    assert Path(records[0]["exe"]).is_absolute()
    assert records[0]["started_at"]


def test_stop_refuses_legacy_pid_state(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    """D11/D12：旧格式（裸 PID）一律拒绝，不清状态、不执行 taskkill。"""
    runtime = FakeRuntime()
    plane = _plane(tmp_path, peer_dirs, runtime=runtime)
    plane._peer_state("alpha")["pids"] = [17136]
    job = plane.create_job("alpha", "stop")
    finished = _wait(plane, job.id)
    assert finished.status == "failed"
    assert "无法确认该 PID 属于本 peer" in (finished.error or "")
    assert "旧格式" in (finished.error or "")
    assert not any("taskkill" in " ".join(call) for call in runtime.calls)
    entry = plane._peer_state("alpha")
    assert entry["pids"] == [17136]
    assert "last_stop_at" not in entry


@pytest.mark.parametrize(
    ("process_info", "needle"),
    [
        (lambda pid, cwd: (None, None), "查不到 PID"),
        (lambda pid, cwd: ("C:\\Windows\\System32\\cmd.exe", "cmd.exe"), "不在本 peer 目录内"),
        (lambda pid, cwd: ("C:\\tools\\multica.exe", "multica.exe"), "受保护"),
    ],
)
def test_stop_refuses_pid_that_cannot_be_proven_ours(
    tmp_path: Path,
    peer_dirs: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    process_info: Any,
    needle: str,
) -> None:
    monkeypatch.setattr(control, "_pid_alive", lambda pid: True)
    runtime = FakeRuntime()
    plane = _plane(tmp_path, peer_dirs, runtime=runtime, process_info=process_info)
    plane._peer_state("alpha")["pids"] = [
        {
            "pid": 4321,
            "exe": str(peer_dirs["alpha"] / "alpha.exe"),
            "started_at": RECORDED_AT,
        }
    ]
    job = plane.create_job("alpha", "stop")
    finished = _wait(plane, job.id)
    assert finished.status == "failed"
    assert needle in (finished.error or "")
    assert not any("taskkill" in " ".join(call) for call in runtime.calls)
    assert plane._peer_state("alpha")["pids"]  # 状态保留，证据不丢


def test_stop_kills_only_when_ownership_confirmed(
    tmp_path: Path, peer_dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(control, "_pid_alive", lambda pid: True)
    runtime = FakeRuntime()
    exe = peer_dirs["alpha"] / ".venv" / "Scripts" / "python.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("", encoding="utf-8")
    plane = _plane(
        tmp_path,
        peer_dirs,
        runtime=runtime,
        process_info=lambda pid, cwd: (str(exe), "python.exe"),
    )
    plane._peer_state("alpha")["pids"] = [
        {"pid": 5555, "exe": str(exe), "started_at": RECORDED_AT}
    ]
    job = plane.create_job("alpha", "stop")
    finished = _wait(plane, job.id)
    assert finished.status == "ok", finished.error
    # 终止命令跟平台走：Windows 是 taskkill，POSIX 是 kill -TERM（control.py 里同一分支）
    expected_stop = (
        ["taskkill", "/PID", "5555", "/T", "/F"]
        if os.name == "nt"
        else ["kill", "-TERM", "5555"]
    )
    assert expected_stop in runtime.calls
    entry = plane._peer_state("alpha")
    assert entry["pids"] == []
    assert entry["last_stop_at"]


# --------------------------------------------------------------------------- #
# QUOT-177 P1-2：同目录不同 exe、PID 复用（创建时间不符）都必须拒绝
# --------------------------------------------------------------------------- #

def _stop_with(
    tmp_path: Path,
    peer_dirs: dict[str, Path],
    *,
    record: dict[str, Any],
    process_info: Any,
    process_started: Any = None,
) -> tuple[Job, FakeRuntime, ControlPlane]:
    runtime = FakeRuntime()
    plane = _plane(
        tmp_path,
        peer_dirs,
        runtime=runtime,
        process_info=process_info,
        process_started=process_started,
    )
    plane._peer_state("alpha")["pids"] = [record]
    job = plane.create_job("alpha", "stop")
    return _wait(plane, job.id), runtime, plane


def test_stop_refuses_same_directory_different_exe(
    tmp_path: Path, peer_dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1-2 主场景：同目录 + 同 PID，但可执行文件不是启动记录里那个 → 一条命令都不执行。"""
    monkeypatch.setattr(control, "_pid_alive", lambda pid: True)
    record_exe = peer_dirs["alpha"] / ".venv" / "Scripts" / "qfw.exe"
    actual_exe = peer_dirs["alpha"] / ".venv" / "Scripts" / "python.exe"
    actual_exe.parent.mkdir(parents=True, exist_ok=True)
    for fake in (record_exe, actual_exe):
        fake.write_text("", encoding="utf-8")
    finished, runtime, plane = _stop_with(
        tmp_path,
        peer_dirs,
        record={"pid": 17136, "exe": str(record_exe), "started_at": RECORDED_AT},
        process_info=lambda pid, cwd: (str(actual_exe), "python.exe"),
    )
    assert finished.status == "failed"
    assert "可执行文件与启动记录不符" in (finished.error or "")
    assert str(actual_exe) in (finished.error or "")
    assert runtime.calls == []  # 实际执行的命令为空
    assert plane._peer_state("alpha")["pids"]  # 证据保留
    assert "last_stop_at" not in plane._peer_state("alpha")


def test_stop_refuses_pid_reused_after_sixty_seconds(
    tmp_path: Path, peer_dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1-2：exe 完全相同，但进程创建时间比记录晚 60s → 是 PID 复用，拒绝。"""
    monkeypatch.setattr(control, "_pid_alive", lambda pid: True)
    exe = peer_dirs["alpha"] / ".venv" / "Scripts" / "python.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("", encoding="utf-8")
    finished, runtime, plane = _stop_with(
        tmp_path,
        peer_dirs,
        record={"pid": 17136, "exe": str(exe), "started_at": RECORDED_AT},
        process_info=lambda pid, cwd: (str(exe), "python.exe"),
        process_started=lambda pid: RECORDED_AT_DT + timedelta(seconds=60),
    )
    assert finished.status == "failed"
    assert "疑似 PID 复用" in (finished.error or "")
    assert runtime.calls == []
    assert plane._peer_state("alpha")["pids"]


def test_stop_allows_when_creation_time_within_tolerance(
    tmp_path: Path, peer_dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1-2 正常路径：exe 一致、创建时间相差 2s（5s 容差内）→ 照常 taskkill 并清状态。"""
    monkeypatch.setattr(control, "_pid_alive", lambda pid: True)
    exe = peer_dirs["alpha"] / ".venv" / "Scripts" / "python.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("", encoding="utf-8")
    finished, runtime, plane = _stop_with(
        tmp_path,
        peer_dirs,
        record={"pid": 17136, "exe": str(exe), "started_at": RECORDED_AT},
        process_info=lambda pid, cwd: (str(exe), "python.exe"),
        process_started=lambda pid: RECORDED_AT_DT - timedelta(seconds=2),
    )
    assert finished.status == "ok", finished.error
    expected_stop = (
        ["taskkill", "/PID", "17136", "/T", "/F"]
        if os.name == "nt"
        else ["kill", "-TERM", "17136"]
    )
    assert expected_stop in runtime.calls
    entry = plane._peer_state("alpha")
    assert entry["pids"] == []
    assert entry["last_stop_at"]


def test_stop_refuses_when_creation_time_unavailable(
    tmp_path: Path, peer_dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1-2 失败关闭：查不到 CreationDate 就是拒绝，不因「查不到」放行。"""
    monkeypatch.setattr(control, "_pid_alive", lambda pid: True)
    exe = peer_dirs["alpha"] / ".venv" / "Scripts" / "python.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("", encoding="utf-8")
    finished, runtime, plane = _stop_with(
        tmp_path,
        peer_dirs,
        record={"pid": 17136, "exe": str(exe), "started_at": RECORDED_AT},
        process_info=lambda pid, cwd: (str(exe), "python.exe"),
        process_started=lambda pid: None,
    )
    assert finished.status == "failed"
    assert "查不到 PID 17136 的进程创建时间" in (finished.error or "")
    assert runtime.calls == []
    assert plane._peer_state("alpha")["pids"]


@pytest.mark.parametrize(
    ("record", "needle"),
    [
        (
            {"pid": 17136, "exe": "", "started_at": RECORDED_AT},
            "没有该 PID 的可执行文件路径",
        ),
        (
            {"pid": 17136, "exe": None, "started_at": RECORDED_AT},
            "没有该 PID 的可执行文件路径",
        ),
        (
            # 缺 started_at：exe 对得上也不够，仍然无法排除 PID 复用
            {"pid": 17136, "exe": "EXE", "started_at": None},
            "启动时间不可用",
        ),
        (
            {"pid": 17136, "exe": "EXE", "started_at": "not-a-time"},
            "启动时间不可用",
        ),
    ],
)
def test_stop_refuses_when_record_evidence_is_missing(
    tmp_path: Path,
    peer_dirs: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    record: dict[str, Any],
    needle: str,
) -> None:
    """P1-2 失败关闭：记录缺 `exe` / 缺 `started_at`（或时间不可解析）→ 一律拒绝。"""
    monkeypatch.setattr(control, "_pid_alive", lambda pid: True)
    exe = peer_dirs["alpha"] / ".venv" / "Scripts" / "python.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("", encoding="utf-8")
    record = {**record, "exe": str(exe) if record.get("exe") == "EXE" else record.get("exe")}
    finished, runtime, plane = _stop_with(
        tmp_path,
        peer_dirs,
        record=record,
        process_info=lambda pid, cwd: (str(exe), "python.exe"),
    )
    assert finished.status == "failed"
    assert needle in (finished.error or "")
    assert runtime.calls == []
    assert plane._peer_state("alpha")["pids"]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("20261029013112.123456+480", datetime(2026, 10, 28, 17, 31, 12, 123456, tzinfo=UTC)),
        ("20261029013112.123456-480", datetime(2026, 10, 29, 9, 31, 12, 123456, tzinfo=UTC)),
        ("20261029013112.000000+000", datetime(2026, 10, 29, 1, 31, 12, tzinfo=UTC)),
        ("2026-10-29T01:31:12.1234567Z", datetime(2026, 10, 29, 1, 31, 12, 123456, tzinfo=UTC)),
        ("2026-10-29T01:31:12+08:00", datetime(2026, 10, 28, 17, 31, 12, tzinfo=UTC)),
        ("", None),
        ("garbage", None),
    ],
)
def test_parse_process_time_handles_cim_and_iso(raw: str, expected: datetime | None) -> None:
    """`CIM_DATETIME`（偏移是分钟）与 ISO 两种形态都要解析对，坏数据返回 None。"""
    assert control._parse_process_time(raw) == expected


def test_parse_creation_output_strips_wmic_key() -> None:
    """wmic `/format:list` 的 `CreationDate=` 前缀要先剥掉再解析。"""
    assert control._parse_creation_output(
        "\r\r\nCreationDate=20261029013112.000000+480\r\r\n"
    ) == datetime(2026, 10, 28, 17, 31, 12, tzinfo=UTC)
    assert control._parse_creation_output("\r\r\n\r\r\n") is None


def test_process_created_at_falls_back_to_wmic(monkeypatch: pytest.MonkeyPatch) -> None:
    """CIM 查不到时回落 wmic；两条路都失败 → None（调用方据此拒绝）。"""
    if os.name != "nt":  # pragma: no cover - 本项目以 Windows 为主
        assert control.process_created_at(4242) is None
        return

    calls: list[list[str]] = []

    def fake_run(args: list[str], cwd: Path, timeout: float) -> StepResult:
        calls.append(list(args))
        assert timeout == control.PROCESS_QUERY_TIMEOUT_SEC
        if "wmic" in args[0]:
            return StepResult(0, "\r\r\nCreationDate=20261029013112.000000+480\r\r\n")
        return StepResult(1, "powershell 不可用")

    monkeypatch.setattr(control, "run_step", fake_run)
    assert control.process_created_at(4242) == datetime(2026, 10, 28, 17, 31, 12, tzinfo=UTC)
    assert "powershell.exe" in calls[0][0] and "wmic" in calls[1][0]

    monkeypatch.setattr(control, "run_step", lambda args, cwd, timeout: StepResult(1, ""))
    assert control.process_created_at(4242) is None  # 兜底也取不到 → None，不是「当作对得上」


def test_ai_test_rejects_internal_targets_without_sending(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    """C10：内网 / 云元数据 / 回环带密钥 → 400，且一个请求都不发。"""
    sent: list[str] = []

    def _post(url, payload, headers, timeout):
        sent.append(url)
        return 200, json.dumps({"model": "m"})

    plane = _plane(tmp_path, peer_dirs, http_post=_post, resolve=lambda host: [host])
    for target in (
        "http://169.254.169.254/latest",
        "http://10.1.2.3/v1",
        "http://192.168.1.10/v1",
        "http://100.64.0.1/v1",
    ):
        with pytest.raises(HTTPException) as excinfo:
            plane.ai_test(peer="alpha", base_url=target, model="m", api_key="k" * 16)
        assert excinfo.value.status_code == 400, target
    with pytest.raises(HTTPException) as excinfo:
        plane.ai_test(peer="alpha", base_url="http://127.0.0.1:22", model="m", api_key="k" * 16)
    assert excinfo.value.status_code == 400
    assert sent == []  # 拒绝发生在发请求之前


def test_ai_test_stored_key_never_leaves_allowlist(
    tmp_path: Path, peer_dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """C10：已存密钥只回落到 `LDST_AI_ALLOWED_HOSTS`，否则 400。"""
    (peer_dirs["alpha"] / ".env.local").write_text(
        f"ALPHA_API_KEY={SENTINEL_KEY}\n", encoding="utf-8"
    )
    sent: list[dict[str, Any]] = []

    def _post(url, payload, headers, timeout):
        sent.append({"url": url, "headers": dict(headers)})
        return 200, json.dumps({"model": "m"})

    plane = _plane(tmp_path, peer_dirs, http_post=_post)
    with pytest.raises(HTTPException) as excinfo:
        plane.ai_test(peer="alpha", base_url="https://attacker.example/v1", model="m")
    assert excinfo.value.status_code == 400
    assert "密钥可用白名单" in excinfo.value.detail
    assert SENTINEL_KEY not in excinfo.value.detail
    assert sent == []

    # 把该主机显式加进白名单后，才允许带上已存密钥
    monkeypatch.setenv(control.AI_ALLOWED_HOSTS_ENV, "attacker.example")
    out = plane.ai_test(peer="alpha", base_url="https://attacker.example/v1", model="m")
    assert out["ok"] is True
    assert sent and sent[0]["headers"]["Authorization"] == f"Bearer {SENTINEL_KEY}"


def test_ai_test_allows_loopback_only_without_credentials(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    """本机 provider（Ollama）无凭据探测照旧可用；带密钥则拒绝。"""
    sent: list[str] = []

    def _post(url, payload, headers, timeout):
        sent.append(url)
        return 200, json.dumps({"model": "m"})

    plane = _plane(tmp_path, peer_dirs, http_post=_post)
    out = plane.ai_test(peer="alpha", base_url="http://127.0.0.1:11434/v1", model="m")
    assert out["ok"] is True
    assert sent == ["http://127.0.0.1:11434/v1/chat/completions"]


def _drain_body(handler: Any) -> None:
    """读完请求体再回应答。

    不读完就直接关连接，Windows 会给对端回 RST（发送缓冲区里还有未读数据）。
    客户端此时可能还没读到状态行，`recv` 就报 `WinError 10053`
    `ConnectionAbortedError`——这条用例曾因此在全量跑时偶发失败（QUOT-206）。
    """
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except (TypeError, ValueError):
        length = 0
    if length > 0:
        handler.rfile.read(length)


def test_post_json_does_not_follow_redirects() -> None:
    """C10：3xx 原样返回（算失败），绝不跟到新地址把密钥带出去。

    稳定性（QUOT-206）：两个桩服务都先把请求体读完、显式声明 `Content-Length: 0`
    并 `Connection: close`，退出时 `server_close()` 释放监听套接字。原实现把请求体
    留在内核缓冲区就关连接，Windows 会回 RST，导致客户端读状态行时偶发
    `ConnectionAbortedError(10053)`——不是业务逻辑问题，是桩服务的 TCP 收尾不当。
    """
    import http.server
    import threading

    hits: list[str] = []

    class _Sink(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - http.server 约定
            _drain_body(self)
            hits.append(self.path)
            body = b'{"model": "stolen"}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:  # noqa: D401 - 静音
            pass

    sink = http.server.HTTPServer(("127.0.0.1", 0), _Sink)
    sink_port = sink.server_address[1]
    threading.Thread(target=sink.serve_forever, daemon=True).start()

    class _Redirect(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - http.server 约定
            _drain_body(self)
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{sink_port}/leak")
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()

        def log_message(self, *args: Any) -> None:  # noqa: D401 - 静音
            pass

    redirect = http.server.HTTPServer(("127.0.0.1", 0), _Redirect)
    redirect_port = redirect.server_address[1]
    threading.Thread(target=redirect.serve_forever, daemon=True).start()
    try:
        status, _body = control._post_json(
            f"http://127.0.0.1:{redirect_port}/chat/completions",
            {"model": "m"},
            {"Authorization": "Bearer sk-should-not-leave"},
            2.0,
        )
    finally:
        redirect.shutdown()
        sink.shutdown()
        redirect.server_close()
        sink.server_close()
    assert status == 302
    assert hits == []


def test_control_disabled_all_methods_are_spa_shaped_404(
    tmp_path: Path, peer_dirs: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """P1-1：关闭后所有方法/形态都 404，且不出 `Allow` 头；数据面不受影响。"""
    client = _loopback_client(tmp_path, peer_dirs)
    assert client.get("/api/control/peers").status_code == 200
    monkeypatch.setenv(control.CONTROL_ENABLED_ENV, "0")
    paths = (
        "/api/control",
        "/api/control/",
        "/api/control/peers",
        "/api/control/peers/",
        "/api/control/peers/alpha/start",
        "/api/control/nope/deeper",
    )
    for method in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
        for path in paths:
            response = client.request(method, path)
            assert response.status_code == 404, (method, path, response.status_code)
            assert "allow" not in {key.lower() for key in response.headers}, (method, path)
            if method != "HEAD":
                assert response.json()["detail"] == "Not Found"
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/state/config").status_code == 200


# --------------------------------------------------------------------------- #
# 8. AI 生成 / 重试任务（QUOT-236）：复用 Job 模型，不新增 peer
# --------------------------------------------------------------------------- #

def test_ai_generate_job_runs_review_cli(tmp_path: Path, peer_dirs: dict[str, Path]) -> None:
    runtime = FakeRuntime()
    runtime.default = StepResult(0, '{"ok": true, "degraded": false}\n')
    plane = _plane(tmp_path, peer_dirs, runtime=runtime)

    job = plane.create_ai_job("generate", "2026-10-30", force=True)
    done = _wait(plane, job.id)

    assert done.status == "ok"
    assert done.peer == control.AI_JOB_PEER
    args = next(call for call in runtime.calls if "review" in call)
    assert args[-4:] == ["review", "--date", "2026-10-30", "--force"]
    # peer 任务的 PRD 字段集不变（AI 参数不泄漏进 view）
    assert set(done.view()) == {
        "id",
        "peer",
        "action",
        "status",
        "step",
        "progress",
        "started_at",
        "ended_at",
        "log_tail",
        "error",
    }
    assert any("review" in line for line in done.tail())


def test_ai_retry_job_scopes_section(tmp_path: Path, peer_dirs: dict[str, Path]) -> None:
    runtime = FakeRuntime()
    runtime.default = StepResult(0, "ok\n")
    plane = _plane(tmp_path, peer_dirs, runtime=runtime)

    done = _wait(plane, plane.create_ai_job("retry", "2026-10-30", section="theme").id)

    assert done.status == "ok"
    args = next(call for call in runtime.calls if "retry" in call)
    assert args[-6:] == ["ai", "retry", "--date", "2026-10-30", "--section", "theme"]


def test_ai_job_exit_9_is_degraded_but_ok(tmp_path: Path, peer_dirs: dict[str, Path]) -> None:
    runtime = FakeRuntime()
    runtime.default = StepResult(9, "AI 降级完成\n")
    plane = _plane(tmp_path, peer_dirs, runtime=runtime)

    done = _wait(plane, plane.create_ai_job("generate", "2026-10-30").id)

    assert done.status == "ok"
    assert done.error is None
    assert any("降级" in line for line in done.tail())


def test_ai_job_other_nonzero_is_failed(tmp_path: Path, peer_dirs: dict[str, Path]) -> None:
    runtime = FakeRuntime()
    runtime.default = StepResult(2, "非法参数\n")
    plane = _plane(tmp_path, peer_dirs, runtime=runtime)

    done = _wait(plane, plane.create_ai_job("generate", "2026-10-30").id)

    assert done.status == "failed"
    assert "退出码 2" in (done.error or "")


def test_ai_generate_route_validates_date_and_section(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    client = TestClient(
        create_app(
            data_root=tmp_path,
            dist_dir=tmp_path / "missing",
            control_plane=_plane(tmp_path, peer_dirs),
        ),
        client=LOOPBACK,
        headers=WRITE_HEADERS,
    )

    assert client.post("/api/control/ai/generate", json={"date": "2026/10/30"}).status_code == 400
    bad_section = client.post(
        "/api/control/ai/retry", json={"date": "2026-10-30", "section": "nope"}
    )
    assert bad_section.status_code == 400
    assert client.post("/api/control/ai/retry", json={"date": "2026-10-30"}).status_code == 200


def test_ai_job_is_exclusive(tmp_path: Path, peer_dirs: dict[str, Path]) -> None:
    import threading

    gate = threading.Event()
    runtime = FakeRuntime()

    def blocked(args: list[str], cwd: Path, timeout: float) -> StepResult:
        gate.wait(5)
        return StepResult(0, "ok\n")

    runtime.run = blocked  # type: ignore[method-assign]
    plane = _plane(tmp_path, peer_dirs, runtime=runtime)

    first = plane.create_ai_job("generate", "2026-10-30")
    try:
        with pytest.raises(RuntimeError, match="已有 AI 生成任务"):
            plane.create_ai_job("generate", "2026-10-30")
    finally:
        gate.set()
    assert _wait(plane, first.id).status == "ok"
