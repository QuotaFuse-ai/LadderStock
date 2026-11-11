"""`ldst service`（QUOT-527）：dry-run 默认、平台参数、托盘退出契约的 CLI 面。

manager / supervisor 引擎全部换成假件：不写 OS 注册表、不起进程。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from ladderstock.cli import service as service_cli
from ladderstock.errors import EXIT_CONFIG, EXIT_OK, EXIT_PARTIAL
from ladderstock.service import ServiceError, TrayUnavailable


class FakeManager:
    def __init__(
        self,
        *,
        error: Exception | None = None,
        payload: dict[str, Any] | None = None,
        render_payload: dict[str, Any] | None = None,
    ) -> None:
        self.error = error
        self.payload = payload or {"ok": True, "platform": "windows"}
        self.render_payload = render_payload or {"ok": True, "artifacts": []}
        self.calls: list[tuple[str, Any]] = []
        self.platforms: tuple[str, ...] | None = None

    def _maybe_raise(self) -> None:
        if self.error is not None:
            raise self.error

    def install(self, spec: Any, *, apply: bool = False) -> dict[str, Any]:
        self.calls.append(("install", apply))
        self._maybe_raise()
        return dict(self.payload, dry_run=not apply)

    def uninstall(self, spec: Any, *, apply: bool = False) -> dict[str, Any]:
        self.calls.append(("uninstall", apply))
        self._maybe_raise()
        return dict(self.payload, dry_run=not apply)

    def status(self, spec: Any) -> dict[str, Any]:
        self.calls.append(("status", None))
        self._maybe_raise()
        return dict(self.payload)

    def render_all(
        self, spec: Any, out_dir: Path, *, platforms: tuple[str, ...]
    ) -> dict[str, Any]:
        self.calls.append(("render", str(out_dir)))
        self.platforms = tuple(platforms)
        self._maybe_raise()
        return dict(self.render_payload)


def _app() -> typer.Typer:
    app = typer.Typer()
    service_cli.register(app)
    return app


def _run(args: list[str]) -> Any:
    return CliRunner().invoke(_app(), ["service", *args])


@pytest.fixture()
def manager(monkeypatch: pytest.MonkeyPatch) -> FakeManager:
    fake = FakeManager()
    monkeypatch.setattr(service_cli, "_manager", lambda _platform=None: fake)
    monkeypatch.setattr(service_cli, "_spec", lambda: object())
    return fake


# --------------------------------------------------------------------------- #
# install / uninstall / status
# --------------------------------------------------------------------------- #


def test_install_defaults_to_dry_run(manager: FakeManager) -> None:
    result = _run(["install", "--output", "json"])

    assert result.exit_code == EXIT_OK
    assert json.loads(result.stdout)["dry_run"] is True
    assert manager.calls == [("install", False)]


def test_install_apply_passes_the_flag(manager: FakeManager) -> None:
    result = _run(["install", "--apply", "--output", "json"])

    assert result.exit_code == EXIT_OK
    assert manager.calls == [("install", True)]


def test_install_partial_failure_exits_partial(manager: FakeManager) -> None:
    manager.payload = {"ok": False, "error": "schtasks 失败"}

    result = _run(["install", "--apply", "--output", "json"])

    assert result.exit_code == EXIT_PARTIAL
    assert json.loads(result.stdout)["ok"] is False


def test_uninstall_defaults_to_dry_run(manager: FakeManager) -> None:
    result = _run(["uninstall", "--output", "json"])

    assert result.exit_code == EXIT_OK
    assert json.loads(result.stdout)["dry_run"] is True
    assert manager.calls == [("uninstall", False)]


def test_status_is_read_only(manager: FakeManager) -> None:
    manager.payload = {"ok": True, "installed": True, "running": True}

    result = _run(["status", "--output", "json"])

    assert result.exit_code == EXIT_OK
    assert json.loads(result.stdout)["running"] is True
    assert manager.calls == [("status", None)]


def test_service_error_maps_to_config_exit(manager: FakeManager) -> None:
    manager.error = ServiceError("当前平台不能安装该平台服务")

    result = _run(["install", "--apply", "--output", "json"])

    assert result.exit_code == EXIT_CONFIG


# --------------------------------------------------------------------------- #
# render
# --------------------------------------------------------------------------- #


def test_render_defaults_to_all_three_platforms(manager: FakeManager, tmp_path: Path) -> None:
    result = _run(["render", "--out", str(tmp_path), "--output", "json"])

    assert result.exit_code == EXIT_OK
    assert manager.platforms == ("windows", "macos", "linux")


def test_render_accepts_repeated_platform_filter(
    manager: FakeManager, tmp_path: Path
) -> None:
    result = _run(
        [
            "render",
            "--platform",
            "linux",
            "--platform",
            "macos",
            "--out",
            str(tmp_path),
            "--output",
            "json",
        ]
    )

    assert result.exit_code == EXIT_OK
    assert manager.platforms == ("linux", "macos")


def test_render_rejects_unknown_platform(manager: FakeManager, tmp_path: Path) -> None:
    result = _run(["render", "--platform", "beos", "--out", str(tmp_path)])

    assert result.exit_code == EXIT_CONFIG
    assert manager.calls == []


# --------------------------------------------------------------------------- #
# tray
# --------------------------------------------------------------------------- #


class FakeProcesses:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def stop_owned(self) -> dict[str, Any]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("停止自有子进程失败")
        return {"ok": True, "modules": {}, "kept_adopted": ["tickhub"]}


@pytest.fixture()
def fake_engine(monkeypatch: pytest.MonkeyPatch) -> FakeProcesses:
    processes = FakeProcesses()

    def build(_root: str, *, panel_url: str | None = None) -> Any:
        return SimpleNamespace(processes=processes, panel_url=panel_url)

    monkeypatch.setattr(
        "ladderstock.web.supervisor_api.build_supervisor_engines", build
    )
    return processes


def test_tray_check_reports_availability_without_starting_icon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service_cli, "tray_available", lambda: (False, "无图形会话"))

    result = _run(["tray", "--check", "--output", "json"])

    assert result.exit_code == EXIT_OK
    payload = json.loads(result.stdout)
    assert payload["available"] is False
    assert payload["backend"] == "unavailable"
    assert "无图形会话" in payload["detail"]


def test_headless_tray_quit_stops_only_owned_processes(fake_engine: FakeProcesses) -> None:
    result = _run(["tray", "--headless", "--quit-after", "0", "--output", "json"])

    assert result.exit_code == EXIT_OK
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["quit"] is True
    assert payload["backend"] == "headless"
    assert payload["kept_adopted"] == ["tickhub"]
    assert fake_engine.calls == 1


def test_headless_tray_reports_stop_failure_as_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processes = FakeProcesses(fail=True)
    monkeypatch.setattr(
        "ladderstock.web.supervisor_api.build_supervisor_engines",
        lambda _root, *, panel_url=None: SimpleNamespace(processes=processes),
    )

    result = _run(["tray", "--headless", "--quit-after", "0", "--output", "json"])

    assert result.exit_code == EXIT_PARTIAL
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert "停止自有子进程失败" in payload["error"]


def test_tray_without_backend_exits_config(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(**_kwargs: Any) -> Any:
        raise TrayUnavailable("pip install 'ladderstock[tray]'")

    monkeypatch.setattr(service_cli, "build_tray_backend", unavailable)

    result = _run(["tray"])

    assert result.exit_code == EXIT_CONFIG
