"""三平台服务后端（QUOT-527）：原生定义内容、编码、注册命令与状态解析。

全部只渲染文本，不执行任何系统命令、不碰 OS 注册表。
"""

from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

from ladderstock.service.backends import (
    LaunchdBackend,
    SystemdUserBackend,
    WindowsTaskBackend,
    systemd_quote,
)
from ladderstock.service.model import (
    LAUNCHD_LABEL,
    SERVICE_NAME,
    SYSTEMD_UNIT,
    WINDOWS_TASK,
    CommandResult,
    ServiceError,
    ServiceSpec,
)
from ladderstock.service.spec import SERVICE_SUBCOMMAND


def _spec(tmp_path: Path, **overrides: object) -> ServiceSpec:
    values: dict[str, object] = {
        "name": SERVICE_NAME,
        "argv": (
            str(tmp_path / "LadderStock.exe"),
            SERVICE_SUBCOMMAND,
            "--port",
            "5950",
        ),
        "workdir": tmp_path,
        "log_dir": tmp_path / "logs",
        "env": {"LDST_DATA_DIR": str(tmp_path / "data")},
    }
    values.update(overrides)
    return ServiceSpec(**values)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Windows：任务计划程序
# --------------------------------------------------------------------------- #


def test_windows_task_is_utf16_xml_with_native_settings(tmp_path: Path) -> None:
    backend = WindowsTaskBackend(user="tester")
    spec = _spec(tmp_path)
    content = backend.render(spec)

    assert backend.artifact_encoding == "utf-16"
    assert content.startswith('<?xml version="1.0" encoding="UTF-16"?>')
    # Task Scheduler 注册要求 UTF-16（含 BOM），不能用 UTF-8 冒充
    assert content.encode("utf-16").startswith(b"\xff\xfe")
    for fragment in (
        "<LogonTrigger>",
        "<Delay>PT15S</Delay>",
        "<LogonType>InteractiveToken</LogonType>",
        "<RunLevel>LeastPrivilege</RunLevel>",
        "<MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>",
        "<ExecutionTimeLimit>PT0S</ExecutionTimeLimit>",
        "<RestartOnFailure>",
    ):
        assert fragment in content
    assert spec.argv[0] in content
    assert str(tmp_path) in content


def test_windows_install_commands_use_schtasks_and_own_task_name(
    tmp_path: Path,
) -> None:
    backend = WindowsTaskBackend(user="tester")
    artifact = tmp_path / "LadderStock-SingleEntry.xml"
    commands = backend.install_commands(_spec(tmp_path), artifact)

    assert commands[0].argv == (
        "schtasks",
        "/Create",
        "/TN",
        WINDOWS_TASK,
        "/XML",
        str(artifact),
        "/F",
    )
    assert commands[0].optional is False
    # 已运行时 /Run 返回非 0 不算安装失败
    assert commands[1].argv == ("schtasks", "/Run", "/TN", WINDOWS_TASK)
    assert commands[1].optional is True

    uninstall = backend.uninstall_commands(_spec(tmp_path))
    assert uninstall[-1].argv == ("schtasks", "/Delete", "/TN", WINDOWS_TASK, "/F")
    assert uninstall[-1].optional is False


def test_windows_status_parses_task_json_or_absent(tmp_path: Path) -> None:
    backend = WindowsTaskBackend(user="tester")

    installed = backend.parse_status(
        [
            CommandResult(
                (),
                0,
                stdout='{"installed": true, "state": "Running", "last_result": 0}',
            )
        ]
    )
    assert installed["installed"] is True
    assert installed["running"] is True

    absent = backend.parse_status(
        [CommandResult((), 0, stdout='{"installed": false}')]
    )
    assert absent["installed"] is False
    assert absent["running"] is False

    unavailable = backend.parse_status(
        [CommandResult((), -1, error="找不到命令：powershell")]
    )
    assert unavailable["state"] == "unknown"
    assert "powershell" in str(unavailable["detail"])


# --------------------------------------------------------------------------- #
# macOS：launchd LaunchAgent
# --------------------------------------------------------------------------- #


def test_launchd_plist_runs_at_login_but_is_not_kept_alive(tmp_path: Path) -> None:
    backend = LaunchdBackend(uid=501)
    spec = _spec(tmp_path)
    payload = plistlib.loads(backend.render(spec).encode("utf-8"))

    assert payload["Label"] == LAUNCHD_LABEL
    assert payload["ProgramArguments"] == list(spec.argv)
    assert payload["WorkingDirectory"] == str(tmp_path)
    assert payload["RunAtLoad"] is True
    # 托盘退出 = 全停：launchd 不许把它再拉起来
    assert payload["KeepAlive"] is False
    assert payload["EnvironmentVariables"] == dict(spec.env)
    assert payload["StandardOutPath"] == str(tmp_path / "logs" / "service.out.log")


def test_launchd_commands_bootstrap_into_the_user_gui_domain(tmp_path: Path) -> None:
    backend = LaunchdBackend(uid=501)
    artifact = tmp_path / "com.quotafuse.ladderstock.plist"
    commands = backend.install_commands(_spec(tmp_path), artifact)

    assert commands[0].argv == (
        "launchctl",
        "bootout",
        f"gui/501/{LAUNCHD_LABEL}",
    )
    assert commands[0].optional is True
    assert commands[1].argv == (
        "launchctl",
        "bootstrap",
        "gui/501",
        str(artifact),
    )
    assert backend.status_commands(_spec(tmp_path))[0].argv == (
        "launchctl",
        "print",
        f"gui/501/{LAUNCHD_LABEL}",
    )


def test_launchd_status_shows_running_job_and_not_loaded(tmp_path: Path) -> None:
    backend = LaunchdBackend(uid=501)
    running = backend.parse_status(
        [CommandResult((), 0, stdout="\tstate = running\n\tpid = 4242\n")]
    )
    assert running["installed"] is True
    assert running["running"] is True
    assert running["pid"] == 4242

    absent = backend.parse_status(
        [CommandResult((), 1, stderr="Could not find service")]
    )
    assert absent["installed"] is False
    assert absent["state"] == "not-loaded"


# --------------------------------------------------------------------------- #
# Linux：systemd --user
# --------------------------------------------------------------------------- #


def test_systemd_unit_execstart_and_restart_policy(tmp_path: Path) -> None:
    backend = SystemdUserBackend(config_home=tmp_path / "xdg")
    spec = _spec(tmp_path)
    unit = backend.render(spec)

    assert unit.startswith("[Unit]\n")
    assert f"ExecStart={systemd_quote(spec.argv[0])} __service --port 5950" in unit
    assert f"WorkingDirectory={systemd_quote(str(tmp_path))}" in unit
    assert "Environment=" + systemd_quote(f"LDST_DATA_DIR={spec.env['LDST_DATA_DIR']}") in unit
    assert "Restart=on-failure" in unit
    assert "KillMode=control-group" in unit
    assert "WantedBy=default.target" in unit
    # 用户级 unit 安装到 XDG_CONFIG_HOME，不是 /etc
    assert backend.install_artifact_path(tmp_path / "home") == (
        tmp_path / "xdg" / "systemd" / "user" / SYSTEMD_UNIT
    )


def test_systemd_install_and_uninstall_commands_are_user_scoped(
    tmp_path: Path,
) -> None:
    backend = SystemdUserBackend()
    install = backend.install_commands(_spec(tmp_path), tmp_path / SYSTEMD_UNIT)
    assert [item.argv for item in install] == [
        ("systemctl", "--user", "daemon-reload"),
        ("systemctl", "--user", "enable", "--now", SYSTEMD_UNIT),
    ]

    uninstall = backend.uninstall_commands(_spec(tmp_path))
    assert uninstall[0].argv == (
        "systemctl",
        "--user",
        "disable",
        "--now",
        SYSTEMD_UNIT,
    )
    assert uninstall[0].optional is True
    assert backend.post_uninstall_commands(_spec(tmp_path))[0].argv == (
        "systemctl",
        "--user",
        "daemon-reload",
    )


def test_systemd_quote_escapes_specifiers_and_spaces() -> None:
    assert systemd_quote("100%") == "100%%"
    assert systemd_quote("a b") == '"a b"'
    assert systemd_quote('say "hi"') == '"say \\"hi\\""'
    assert systemd_quote("C:\\Ladder Stock") == '"C:\\\\Ladder Stock"'
    assert systemd_quote("") == '""'


def test_systemd_status_parses_properties(tmp_path: Path) -> None:
    backend = SystemdUserBackend()
    active = backend.parse_status(
        [
            CommandResult(
                (),
                0,
                stdout=(
                    "LoadState=loaded\n"
                    "UnitFileState=enabled\n"
                    "ActiveState=active\n"
                    "SubState=running\n"
                ),
            )
        ]
    )
    assert active["installed"] is True
    assert active["running"] is True
    assert active["unit_file_state"] == "enabled"

    absent = backend.parse_status(
        [
            CommandResult(
                (),
                0,
                stdout=(
                    "LoadState=not-found\n"
                    "UnitFileState=\n"
                    "ActiveState=inactive\n"
                    "SubState=dead\n"
                ),
            )
        ]
    )
    assert absent["installed"] is False
    assert absent["state"] == "inactive"


# --------------------------------------------------------------------------- #
# 密钥守卫与参数校验
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "name", ["GITHUB_TOKEN", "LDST_DB_PASSWORD", "OPENAI_API_KEY", "SOME_SECRET"]
)
def test_secret_like_env_names_are_rejected(tmp_path: Path, name: str) -> None:
    spec = _spec(tmp_path, env={name: "super-secret"})

    with pytest.raises(ServiceError) as excinfo:
        spec.validate()
    assert name in str(excinfo.value)
    assert "密钥" in str(excinfo.value)


def test_relative_executable_is_rejected(tmp_path: Path) -> None:
    spec = _spec(tmp_path, argv=("ldst", SERVICE_SUBCOMMAND))
    with pytest.raises(ServiceError, match="绝对路径"):
        spec.validate()


def test_env_value_must_not_contain_newlines(tmp_path: Path) -> None:
    spec = _spec(tmp_path, env={"LDST_NOTE": "line1\nline2"})
    with pytest.raises(ServiceError, match="换行"):
        spec.validate()


def test_spec_dict_never_expands_secret_fields(tmp_path: Path) -> None:
    payload = _spec(tmp_path).to_dict()
    assert payload["env"] == {"LDST_DATA_DIR": str(tmp_path / "data")}
