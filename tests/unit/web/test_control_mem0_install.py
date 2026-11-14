"""QUOT-569：mem0 一键安装（预检 → 落资产 → compose up → 验活）+ 目录漂移。

全部走 mock / 临时目录：不真跑 WSL / docker / 端口探测，密钥不落任何文件。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ladderstock.core import runtime
from ladderstock.core.boundary import PeerControl, resolve_peer_control
from ladderstock.web import control
from ladderstock.web.control import (
    STATUS_DRIFTED,
    STATUS_UNCONFIGURED,
    ControlPlane,
    StepResult,
)

# --------------------------------------------------------------------------- #
# peer_root / 目录解析（install_root 漂移根治）
# --------------------------------------------------------------------------- #

def test_peer_root_env_override_wins(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(runtime.KNOWN_DEPLOY_ROOTS_ENV, raising=False)
    assert runtime.peer_root({runtime.PEER_ROOT_ENV: str(tmp_path)}) == tmp_path


def test_peer_root_falls_back_to_known_deploy_root(monkeypatch, tmp_path) -> None:
    """源码态 install_root 指不到部署根时，已知根下有 peer 痕迹即回退。"""
    monkeypatch.setattr(runtime, "KNOWN_DEPLOY_ROOTS", ())
    deploy = tmp_path / "deploy"
    (deploy / "Mem0").mkdir(parents=True)
    env = {runtime.KNOWN_DEPLOY_ROOTS_ENV: str(deploy)}
    assert runtime.peer_root(env) == deploy


def test_peer_root_unknown_deploy_root_is_ignored(monkeypatch, tmp_path) -> None:
    """已知根下没有任何 peer 痕迹 → 不盲信，回落 install_root。"""
    monkeypatch.setattr(runtime, "KNOWN_DEPLOY_ROOTS", ())
    env = {runtime.KNOWN_DEPLOY_ROOTS_ENV: str(tmp_path / "empty")}
    fallback = runtime.install_root(env)
    assert runtime.peer_root(env) == fallback


def test_resolve_peer_control_uses_peer_base(monkeypatch, tmp_path) -> None:
    deploy = tmp_path / "QuotaFuseModules"
    (deploy / "Mem0").mkdir(parents=True)
    monkeypatch.setenv(runtime.KNOWN_DEPLOY_ROOTS_ENV, str(deploy))
    monkeypatch.setenv(runtime.INSTALL_ROOT_ENV, str(tmp_path / "nowhere"))
    resolved = resolve_peer_control({})
    assert resolved["mem0"].directory == str(deploy / "Mem0")


def test_resolve_peer_control_dir_env_beats_deploy_root(monkeypatch, tmp_path) -> None:
    deploy = tmp_path / "QuotaFuseModules"
    (deploy / "Mem0").mkdir(parents=True)
    monkeypatch.setenv(runtime.KNOWN_DEPLOY_ROOTS_ENV, str(deploy))
    monkeypatch.setenv(runtime.INSTALL_ROOT_ENV, str(tmp_path / "nowhere"))
    custom = tmp_path / "custom" / "Mem0"
    resolved = resolve_peer_control({"LDST_PEER_DIR_MEM0": str(custom)})
    assert resolved["mem0"].directory == str(custom)


# --------------------------------------------------------------------------- #
# 目录漂移状态
# --------------------------------------------------------------------------- #

def _mem0_peer(directory: str) -> PeerControl:
    ctl = PeerControl(
        key="mem0",
        directory=directory,
        import_package="",
        mcp_port=9100,
        web_port=3200,
        web_port_label="Dashboard",
        health_path="/configure",
        start_commands=(),
        config_keys=(),
        compose_file="docker-compose.yml",
        has_wheel_dist=False,
    )
    ctl = type(ctl).__new__(type(ctl))
    object.__setattr__(ctl, "__dict__", dict(PeerControl.__dataclass_fields__))
    return ctl


def test_drift_status_when_listening_but_dir_missing() -> None:
    """端口在听 + 健康通过 + 目录不存在 → drift，不是 unconfigured。"""
    plane = ControlPlane.__new__(ControlPlane)
    plane.peers = {"mem0": _mem0_peer(r"[modules-path]\Mem0")}
    plane._last_job = {}
    status, detail = plane._decide_status(
        key="mem0",
        registered=True,
        dir_exists=False,
        installed=False,
        listening=True,
        health_ok=True,
        health_detail="web: HTTP 200",
    )
    assert status == STATUS_DRIFTED
    assert "服务在运行" in detail
    assert "LDST_PEER_DIR_MEM0" in detail


def test_unconfigured_when_dir_missing_and_not_listening() -> None:
    plane = ControlPlane.__new__(ControlPlane)
    plane.peers = {"mem0": _mem0_peer(r"[modules-path]\Mem0")}
    plane._last_job = {}
    status, detail = plane._decide_status(
        key="mem0",
        registered=True,
        dir_exists=False,
        installed=False,
        listening=False,
        health_ok=None,
        health_detail="",
    )
    assert status == STATUS_UNCONFIGURED


# --------------------------------------------------------------------------- #
# mem0 一键安装 job
# --------------------------------------------------------------------------- #

class FakeRuntimeScripted:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run(self, args: list[str], cwd: Path, timeout: float) -> StepResult:
        self.calls.append(list(args))
        joined = " ".join(args)
        if "wslpath" in joined:
            return StepResult(0, "/mnt/d/Mem0/docker-compose.yml\n")
        if "docker version" in joined:
            return StepResult(0, "ok\n")
        if "compose" in joined and "up" in joined:
            return StepResult(0, "started\n")
        return StepResult(0, "ok\n")


@pytest.fixture()
def mem0_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    peer_dir = tmp_path / "Mem0"
    monkeypatch.delenv(control.MEM0_API_KEY_ENV, raising=False)
    monkeypatch.setattr(runtime, "bundle_root", lambda: tmp_path / "bundle")
    assets = tmp_path / "bundle" / "ops" / "peers" / "mem0"
    assets.mkdir(parents=True)
    (assets / "docker-compose.yml").write_text("name: qf-mem0\n", encoding="utf-8")
    (assets / "init-db.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    (assets / ".env.example").write_text(
        "POSTGRES_PASSWORD=CHANGE_ME_RANDOM\nJWT_SECRET=CHANGE_ME_RANDOM\n",
        encoding="utf-8",
    )
    server = assets / "server"
    server.mkdir()
    (server / "Dockerfile").write_text("FROM python:3.12-slim\n", encoding="utf-8")
    return {"peer_dir": peer_dir, "assets": assets}


def _mem0_plane(tmp_path: Path, rt: FakeRuntimeScripted, ports: Any) -> ControlPlane:
    ctl = PeerControl(
        key="mem0",
        directory=str(tmp_path / "Mem0"),
        import_package="",
        mcp_port=9100,
        web_port=3200,
        web_port_label="Dashboard",
        health_path="/configure",
        start_commands=(),
        config_keys=("OPENAI_BASE_URL",),
        compose_file="docker-compose.yml",
        has_wheel_dist=False,
    )
    return ControlPlane(
        data_root=tmp_path / "data",
        peers={"mem0": ctl},
        components={},
        sources_loader=lambda: {"mem0": {"product": "Mem0"}},
        run=rt.run,
        port_probe=ports,
        health_probe=lambda url, timeout: (False, "connecting"),
        spawn=lambda args, *, cwd, log_path: SimpleNamespace(pid=1),
        harden_acl=lambda path: None,
        config_root=tmp_path / "config",
    )


def test_mem0_install_preflight_rejects_missing_api_key(mem0_env, tmp_path) -> None:
    """LDST_LLM_API_KEY 缺失 → 安装失败，错误信息给中文指引，密钥指引不含值。"""
    rt = FakeRuntimeScripted()
    plane = _mem0_plane(tmp_path, rt, ports=lambda port: [])
    job = plane.create_job("mem0", "install")
    done = plane.get_job(job.id)
    for _ in range(200):
        if done is not None and done.status in {"ok", "failed"}:
            break
        import time

        time.sleep(0.05)
        done = plane.get_job(job.id)
    assert done is not None and done.status == "failed"
    assert done.error is not None
    assert control.MEM0_API_KEY_ENV in done.error
    assert "setx" in done.error
    # 预检失败 → 没有落任何资产
    assert not mem0_env["peer_dir"].exists() or not any(mem0_env["peer_dir"].iterdir())


def test_mem0_install_success_path(mem0_env, tmp_path, monkeypatch) -> None:
    """预检全过 → 资产落位 + .env 密钥随机化 + compose up + 验活通过。"""
    monkeypatch.setenv(control.MEM0_API_KEY_ENV, "sk-test-fake")
    monkeypatch.setattr(control, "default_wsl_distro", lambda: "Ubuntu")
    rt = FakeRuntimeScripted()
    plane = _mem0_plane(tmp_path, rt, ports=lambda port: [])
    # 验活：第一次探测直接成功
    plane._health = lambda url, timeout: (True, "HTTP 200")  # type: ignore[method-assign]
    job = plane.create_job("mem0", "install")
    import time

    done = plane.get_job(job.id)
    for _ in range(300):
        if done is not None and done.status in {"ok", "failed"}:
            break
        time.sleep(0.05)
        done = plane.get_job(job.id)
    assert done is not None and done.status == "ok", done.log if done else None

    peer_dir: Path = mem0_env["peer_dir"]
    ctl = plane.peers["mem0"]
    assert (peer_dir / "docker-compose.yml").is_file()
    assert (peer_dir / "server" / "Dockerfile").is_file()
    env_text = (peer_dir / ctl.env_filename).read_text(encoding="utf-8")
    assert "CHANGE_ME_RANDOM" not in env_text
    assert "sk-test-fake" not in env_text  # 密钥绝不落 .env
    assert any("compose" in " ".join(c) and "up" in " ".join(c) for c in rt.calls)
