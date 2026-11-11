"""`ServiceManager`（QUOT-527）：dry-run 零副作用、平台闸门、失败关闭、清单。

执行器全部注入假件：测试永远不调用 schtasks / launchctl / systemctl。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ladderstock.service.manager import ServiceManager
from ladderstock.service.model import (
    LAUNCHD_LABEL,
    SERVICE_NAME,
    SYSTEMD_UNIT,
    CommandResult,
    ServiceError,
    ServiceSpec,
)
from ladderstock.service.spec import SERVICE_SUBCOMMAND


class FakeRunner:
    """按序返回预置结果；用完后一律成功。记录全部 argv。"""

    def __init__(self, results: list[CommandResult] | None = None) -> None:
        self.calls: list[tuple[str, ...]] = []
        self._results = list(results or [])

    def run(self, command: tuple[str, ...]) -> CommandResult:
        self.calls.append(tuple(command))
        if self._results:
            preset = self._results.pop(0)
            return CommandResult(
                tuple(command),
                preset.returncode,
                preset.stdout,
                preset.stderr,
                preset.error,
            )
        return CommandResult(tuple(command), 0)


def _spec(tmp_path: Path) -> ServiceSpec:
    return ServiceSpec(
        name=SERVICE_NAME,
        argv=(str(tmp_path / "LadderStock.exe"), SERVICE_SUBCOMMAND),
        workdir=tmp_path,
        log_dir=tmp_path / "logs",
        env={"LDST_DATA_DIR": str(tmp_path / "data")},
    )


def _manager(
    tmp_path: Path,
    runner: FakeRunner,
    *,
    platform: str,
    current: str,
) -> ServiceManager:
    return ServiceManager(
        platform=platform,  # type: ignore[arg-type]
        current=current,  # type: ignore[arg-type]
        home=tmp_path / "home",
        data_root=tmp_path / "data",
        runner=runner,
        uid=501,
        user="tester",
        config_home=tmp_path / "xdg",
    )


# --------------------------------------------------------------------------- #
# dry-run：零副作用
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
def test_install_dry_run_never_touches_disk_or_os(
    tmp_path: Path, platform: str
) -> None:
    runner = FakeRunner()
    manager = _manager(tmp_path, runner, platform=platform, current=platform)

    payload = manager.install(_spec(tmp_path))

    assert payload["dry_run"] is True
    assert payload["ok"] is True
    assert runner.calls == []
    assert not Path(str(payload["artifact_path"])).exists()
    assert not manager.manifest_path.exists()
    assert payload["artifact_sha256"]
    assert payload["commands"]


def test_uninstall_dry_run_never_queries_or_removes(tmp_path: Path) -> None:
    runner = FakeRunner()
    manager = _manager(tmp_path, runner, platform="linux", current="linux")

    payload = manager.uninstall(_spec(tmp_path))

    assert payload["dry_run"] is True
    assert payload["ok"] is True
    assert runner.calls == []


# --------------------------------------------------------------------------- #
# apply：产物编码、命令顺序、清单
# --------------------------------------------------------------------------- #


def test_windows_apply_writes_utf16_artifact_runs_commands_and_manifest(
    tmp_path: Path,
) -> None:
    runner = FakeRunner()
    manager = _manager(tmp_path, runner, platform="windows", current="windows")

    payload = manager.install(_spec(tmp_path), apply=True)

    artifact = Path(str(payload["artifact_path"]))
    assert artifact.is_file()
    assert artifact.read_bytes().startswith(b"\xff\xfe")  # UTF-16 BOM
    assert payload["ok"] is True
    assert runner.calls[0][:3] == ("schtasks", "/Create", "/TN")
    assert manager.manifest_path.is_file()
    manifest = json.loads(manager.manifest_path.read_text(encoding="utf-8"))
    assert manifest["artifact_sha256"] == payload["artifact_sha256"]


def test_linux_apply_writes_unit_and_reloads_then_enables(tmp_path: Path) -> None:
    runner = FakeRunner()
    manager = _manager(tmp_path, runner, platform="linux", current="linux")

    payload = manager.install(_spec(tmp_path), apply=True)

    artifact = Path(str(payload["artifact_path"]))
    assert artifact == tmp_path / "xdg" / "systemd" / "user" / SYSTEMD_UNIT
    assert artifact.read_text(encoding="utf-8").startswith("[Unit]")
    assert runner.calls == [
        ("systemctl", "--user", "daemon-reload"),
        ("systemctl", "--user", "enable", "--now", SYSTEMD_UNIT),
    ]


def test_install_fails_closed_and_does_not_write_manifest(tmp_path: Path) -> None:
    runner = FakeRunner([CommandResult((), 1, stderr="access denied")])
    manager = _manager(tmp_path, runner, platform="windows", current="windows")

    payload = manager.install(_spec(tmp_path), apply=True)

    assert payload["ok"] is False
    assert payload["artifact_written"] is True
    assert not manager.manifest_path.exists()
    # 失败关闭：第一条必需命令失败后，后续命令不再执行
    assert len(runner.calls) == 1


# --------------------------------------------------------------------------- #
# 平台闸门：只允许在目标平台 apply
# --------------------------------------------------------------------------- #


def test_apply_on_another_platform_is_refused_before_any_write(
    tmp_path: Path,
) -> None:
    runner = FakeRunner()
    manager = _manager(tmp_path, runner, platform="linux", current="windows")

    with pytest.raises(ServiceError, match="不能在这里安装"):
        manager.install(_spec(tmp_path), apply=True)

    assert runner.calls == []
    assert not (tmp_path / "xdg").exists()


# --------------------------------------------------------------------------- #
# 卸载：先查状态，失败保留产物
# --------------------------------------------------------------------------- #


def test_uninstall_skips_when_status_says_not_installed(tmp_path: Path) -> None:
    artifact = tmp_path / "xdg" / "systemd" / "user" / SYSTEMD_UNIT
    artifact.parent.mkdir(parents=True)
    artifact.write_text("[Unit]\n", encoding="utf-8")
    runner = FakeRunner(
        [
            CommandResult(
                (),
                0,
                stdout="LoadState=not-found\nActiveState=inactive\nSubState=dead\n",
            )
        ]
    )
    manager = _manager(tmp_path, runner, platform="linux", current="linux")

    payload = manager.uninstall(_spec(tmp_path), apply=True)

    assert payload["ok"] is True
    assert payload["already_absent"] is True
    assert payload["artifact_removed"] is True
    assert not artifact.exists()
    # 只跑了状态查询，没有跑 disable / daemon-reload
    assert len(runner.calls) == 1


def test_uninstall_runs_native_commands_then_removes_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / "home" / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("plist", encoding="utf-8")
    runner = FakeRunner(
        [
            CommandResult((), 0, stdout="\tstate = running\n\tpid = 42\n"),
            CommandResult((), 0),
        ]
    )
    manager = _manager(tmp_path, runner, platform="macos", current="macos")

    payload = manager.uninstall(_spec(tmp_path), apply=True)

    assert payload["ok"] is True
    assert payload["artifact_removed"] is True
    assert runner.calls[0][:2] == ("launchctl", "print")
    assert runner.calls[1][:2] == ("launchctl", "bootout")
    assert not artifact.exists()
    assert not manager.manifest_path.exists()


def test_uninstall_keeps_artifact_when_unregister_fails(tmp_path: Path) -> None:
    artifact = tmp_path / "home" / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("plist", encoding="utf-8")
    runner = FakeRunner(
        [
            CommandResult((), 0, stdout="\tstate = running\n"),
            CommandResult((), 1, stderr="Operation not permitted"),
        ]
    )
    manager = _manager(tmp_path, runner, platform="macos", current="macos")

    payload = manager.uninstall(_spec(tmp_path), apply=True)

    assert payload["ok"] is False
    assert artifact.is_file()  # 失败关闭：注册还在时绝不删产物


def test_uninstall_still_attempts_when_status_is_unknown(tmp_path: Path) -> None:
    runner = FakeRunner(
        [
            CommandResult((), -1, error="launchctl 不可用"),
            CommandResult((), 0),
        ]
    )
    manager = _manager(tmp_path, runner, platform="macos", current="macos")

    payload = manager.uninstall(_spec(tmp_path), apply=True)

    # unknown 不等于「没装」：仍尝试反注册，避免留下幽灵注册
    assert runner.calls[1][:2] == ("launchctl", "bootout")
    assert payload["ok"] is True


# --------------------------------------------------------------------------- #
# 渲染三平台 + 密钥拒绝
# --------------------------------------------------------------------------- #


def test_render_all_writes_three_native_artifacts(tmp_path: Path) -> None:
    runner = FakeRunner()
    manager = _manager(tmp_path, runner, platform="windows", current="windows")
    out_dir = tmp_path / "units"

    payload = manager.render_all(_spec(tmp_path), out_dir)

    files = {Path(str(item["path"])).name for item in payload["artifacts"]}
    assert files == {
        "LadderStock-SingleEntry.xml",
        f"{LAUNCHD_LABEL}.plist",
        SYSTEMD_UNIT,
    }
    xml = out_dir / "LadderStock-SingleEntry.xml"
    assert xml.read_bytes().startswith(b"\xff\xfe")
    assert runner.calls == []


def test_secret_env_is_rejected_before_writing(tmp_path: Path) -> None:
    runner = FakeRunner()
    manager = _manager(tmp_path, runner, platform="windows", current="windows")
    spec = ServiceSpec(
        name=SERVICE_NAME,
        argv=(str(tmp_path / "LadderStock.exe"), SERVICE_SUBCOMMAND),
        workdir=tmp_path,
        log_dir=tmp_path / "logs",
        env={"LDST_API_TOKEN": "abc"},
    )

    with pytest.raises(ServiceError):
        manager.install(spec, apply=True)

    assert runner.calls == []
    assert not (tmp_path / "data" / "service").exists()
