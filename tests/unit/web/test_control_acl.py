"""QUOT-194 P1-3 / P2-8：状态文件与 dotenv 的 DACL 收口、共享目录拒绝、fail closed 文案。

验收口径（对应 issue QUOT-194）：

1. `peer_state.json` / `.env.local` 落盘后 DACL 只剩「当前用户 + SYSTEM +
   Administrators」，且**首次创建与覆盖写都一样**（写 tmp → 收紧 → 原子替换）；
2. 收紧失败一律 fail closed：状态文件不落成品、dotenv 不写成品、配置接口 500 且不改文件；
3. dotenv「存在但读不出来」不得退化成「没有密钥」（读配置 / AI 测试 / 日志脱敏）；
4. peer 目录落在 UNC / 映射网络盘 / Windows 公共目录 → 控制面启动即拒绝；
5. P2-8：漂移文案带一位小数与容差数值；停止失败提示不再是自指的「请先停止」。

Windows 专属断言（真实 `icacls`）在非 Windows 上 skip 并说明原因；其余用例跨平台可跑。
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from ladderstock.core import fsguard
from ladderstock.core.boundary import PeerControl
from ladderstock.core.fsguard import (
    ADMINISTRATORS_SID,
    SYSTEM_SID,
    AclError,
    SharedPeerDirectoryError,
    acl_harden_argv,
    harden_file_acl,
    is_unc_path,
    require_local_peer_directories,
    shared_directory_reason,
)
from ladderstock.web import control as control_module
from ladderstock.web.control import (
    CONTROL_STATE_FILE,
    CONTROL_STATE_SUBDIR,
    ControlPlane,
    EnvFileUnreadable,
    StatePersistError,
    read_env_file,
    secret_preview,
    write_env_file,
)
from ladderstock.web.server import create_app

LOOPBACK = ("127.0.0.1", 54321)
RECORDED_AT = "2026-10-29T00:00:00+00:00"
RECORDED_AT_DT = datetime(2026, 10, 29, 0, 0, 0, tzinfo=UTC)

IS_WINDOWS = os.name == "nt"
WINDOWS_ONLY = pytest.mark.skipif(
    not IS_WINDOWS,
    reason="DACL 只有在 Windows 上才有意义（POSIX 走 chmod 0600，另有独立用例）",
)


# --------------------------------------------------------------------------- #
# 夹具：两个临时 peer 目录 + 可注入的 harden_acl
# --------------------------------------------------------------------------- #

@pytest.fixture()
def peer_dirs(tmp_path: Path) -> dict[str, Path]:
    alpha = tmp_path / "peers" / "alpha"
    beta = tmp_path / "peers" / "beta"
    alpha.mkdir(parents=True)
    beta.mkdir(parents=True)
    return {"alpha": alpha, "beta": beta}


def _peers(peer_dirs: dict[str, Path]) -> dict[str, Any]:
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
    harden_acl: Any = None,
    process_info: Any = None,
    process_started: Any = None,
    runtime: Any = None,
) -> ControlPlane:
    calls: list[list[str]] = []

    def fake_run(args: list[str], cwd: Path, timeout: float) -> Any:
        calls.append(list(args))
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    return ControlPlane(
        data_root=tmp_path,
        peers=_peers(peer_dirs),
        sources_loader=lambda: {key: {"product": key} for key in ("alpha", "beta")},
        run=runtime or fake_run,
        port_probe=lambda port: [],
        health_probe=lambda url, timeout: (True, "HTTP 200"),
        spawn=lambda args, *, cwd, log_path: SimpleNamespace(pid=4242),
        http_post=lambda url, payload, headers, timeout: (200, json.dumps({"model": "m"})),
        process_info=process_info or (lambda pid, cwd: (None, None)),
        process_started=process_started or (lambda pid: RECORDED_AT_DT),
        resolve=lambda host: ["127.0.0.1"],
        harden_acl=harden_acl,
    )


def _state_path(tmp_path: Path) -> Path:
    return tmp_path / CONTROL_STATE_SUBDIR / CONTROL_STATE_FILE


# --------------------------------------------------------------------------- #
# 1. fsguard：参数构造 / UNC / 共享目录判定
# --------------------------------------------------------------------------- #

def test_acl_harden_argv_disables_inheritance_and_grants_three_sids(tmp_path: Path) -> None:
    """`/inheritance:r` 必须存在（否则父目录的 Users 继承 ACE 还在）；三个主体用 SID。"""
    target = tmp_path / "peer_state.json"
    argv = acl_harden_argv(target, user_sid="S-1-5-21-111-222-333-1001")
    assert argv[0] == "icacls" and argv[1] == str(target)
    assert "/inheritance:r" in argv
    granted = [argv[i + 1] for i, token in enumerate(argv) if token == "/grant:r"]
    assert granted == [
        "*S-1-5-21-111-222-333-1001:(F)",
        f"*{SYSTEM_SID}:(F)",
        f"*{ADMINISTRATORS_SID}:(F)",
    ]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (r"\\fileserver\share\ladderstock\alpha", True),
        ("//fileserver/share/ladderstock", True),
        (r"\\?\UNC\fileserver\share\alpha", True),
        (r"[local-path]\LadderStock\alpha", False),
        ("C:/Users/Administrator/peer", False),
        ("", False),
    ],
)
def test_is_unc_path(path: str, expected: bool) -> None:
    assert is_unc_path(path) is expected


def test_shared_directory_reason_flags_unc() -> None:
    reason = shared_directory_reason(r"\\fileserver\share\alpha")
    assert reason is not None and "UNC" in reason


def test_shared_directory_reason_flags_mapped_remote_drive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """映射网络盘：盘符本身是本地写法，只能靠 GetDriveTypeW 识别。"""
    monkeypatch.setattr(fsguard, "drive_type", lambda drive: fsguard.DRIVE_REMOTE)
    monkeypatch.setattr(fsguard, "public_directories", lambda: [])
    reason = shared_directory_reason("Z:\\peers\\alpha")
    assert reason is not None and "DRIVE_REMOTE" in reason


def test_shared_directory_reason_flags_public_dirs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    public = tmp_path / "Public"
    public.mkdir()
    monkeypatch.setattr(fsguard, "public_directories", lambda: [public])
    monkeypatch.setattr(fsguard, "drive_type", lambda drive: 3)
    reason = shared_directory_reason(str(public / "peers" / "alpha"))
    assert reason is not None and "公共目录" in reason


def test_shared_directory_reason_accepts_local_fixed_disk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fsguard, "drive_type", lambda drive: 3)  # DRIVE_FIXED
    monkeypatch.setattr(fsguard, "public_directories", lambda: [tmp_path / "elsewhere"])
    assert shared_directory_reason(str(tmp_path / "peers" / "alpha")) is None


def test_require_local_peer_directories_lists_every_offender(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一次把所有越界 peer 都列出来，并给出可执行的迁移建议（而不是只说「拒绝了」）。"""
    with pytest.raises(SharedPeerDirectoryError) as excinfo:
        require_local_peer_directories(
            {"alpha": r"\\fileserver\share\alpha", "beta": "//fileserver/share/beta"}
        )
    message = str(excinfo.value)
    assert "alpha" in message and "beta" in message
    assert r"\\fileserver\share\alpha" in message
    assert "处理：" in message
    assert "移到本机固定磁盘" in message
    assert "LDST_CONTROL_ENABLED=0" in message


def test_require_local_peer_directories_passes_on_private_disk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(fsguard, "drive_type", lambda drive: 3)
    monkeypatch.setattr(fsguard, "public_directories", lambda: [tmp_path / "elsewhere"])
    require_local_peer_directories({"alpha": str(tmp_path / "peers" / "alpha")})


# --------------------------------------------------------------------------- #
# 2. fsguard：harden_file_acl 的成功 / 失败（跨平台 + Windows 真实 icacls）
# --------------------------------------------------------------------------- #

def _fake_nt(monkeypatch: pytest.MonkeyPatch, *, sid: str = "S-1-5-21-1-2-3-1001") -> None:
    """把 fsguard 看成「Windows」：只替换 os.name 与 whoami/icacls 两个外部依赖。"""
    monkeypatch.setattr(fsguard, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(fsguard, "current_user_sid", lambda: sid)


def test_harden_file_acl_calls_icacls_with_sid_principals(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _fake_nt(monkeypatch)
    seen: list[list[str]] = []

    def fake_run(argv: list[str], **kwargs: Any) -> Any:
        seen.append(list(argv))
        return SimpleNamespace(returncode=0, stdout="已处理 1 个文件", stderr="")

    monkeypatch.setattr(fsguard.subprocess, "run", fake_run)
    harden_file_acl(tmp_path / "peer_state.json")
    assert len(seen) == 1
    assert "S-1-5-21-1-2-3-1001" in " ".join(seen[0])
    assert "/inheritance:r" in seen[0]


def test_harden_file_acl_fails_closed_when_icacls_rejects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _fake_nt(monkeypatch)
    monkeypatch.setattr(
        fsguard.subprocess,
        "run",
        lambda argv, **kwargs: SimpleNamespace(returncode=5, stdout="", stderr="拒绝访问。"),
    )
    with pytest.raises(AclError) as excinfo:
        harden_file_acl(tmp_path / "peer_state.json")
    assert "退出码 5" in str(excinfo.value)


def test_harden_file_acl_fails_closed_without_current_user_sid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _fake_nt(monkeypatch, sid="")
    with pytest.raises(AclError) as excinfo:
        harden_file_acl(tmp_path / "peer_state.json")
    assert "whoami" in str(excinfo.value)


@pytest.mark.skipif(IS_WINDOWS, reason="POSIX 分支：Windows 上 chmod 是空操作，走 icacls")
def test_harden_file_acl_posix_uses_chmod_0600(tmp_path: Path) -> None:
    target = tmp_path / "peer_state.json"
    target.write_text("{}", encoding="utf-8")
    target.chmod(0o666)
    harden_file_acl(target)
    assert (target.stat().st_mode & 0o777) == 0o600


def _icacls_entries(path: Path) -> list[str]:
    """`icacls <file>` 的可读输出（账户名形式），只看条目数与继承标记。"""
    proc = subprocess.run(
        ["icacls", str(path)], capture_output=True, text=True, errors="replace", timeout=15
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return [line.strip() for line in (proc.stdout or "").splitlines() if ":(F)" in line]


def _dacl_principals(path: Path) -> str:
    """用 `icacls /save` 取 SDDL：主体一律是别名/SID，不受系统语言影响。

    返回形如 `D:P(A;;FA;;;BA)(A;;FA;;;SY)(A;;FA;;;LA)` 的串（`icacls` 默认把 SID
    反解成本地化账户名，不能按 SID 断言，故走 SDDL）。
    """
    dump = path.with_name(f"{path.name}.sddl")
    proc = subprocess.run(
        ["icacls", str(path), "/save", str(dump)],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=15,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    try:
        raw = dump.read_bytes()
    finally:
        dump.unlink(missing_ok=True)
    # `icacls /save` 在部分 Windows 版本上写 UTF-16LE **不带 BOM**：Python 的
    # `utf-16` 增量解码器遇到无 BOM 会抛 "does not start with BOM"，而
    # `bytes.decode("utf-16")`（走 C 的 utf_16_decode）反而能解。所以自己判：
    # 有 BOM 就按 BOM 走，没有就按本机字节序（Windows 上恒为 LE）解。
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16")
    else:
        text = raw.decode("utf-16-le")
    lines = text.splitlines()
    assert len(lines) == 2, lines
    return lines[1].strip()


def _assert_acl_locked(path: Path) -> None:
    """成品文件：DACL 受保护（无继承），恰好三个主体 = 当前用户 + SYSTEM + Administrators。"""
    sddl = _dacl_principals(path)
    assert sddl.startswith("D:P"), sddl  # P = DACL 受保护（继承已去掉）
    body = sddl[sddl.index("(") :] if "(" in sddl else ""
    principals = [ace.rstrip(")").split(";")[-1] for ace in body.split("(") if ace.strip()]
    allowed = {"SY", "BA", "LA", fsguard.current_user_sid()}
    assert len(principals) == 3, (sddl, principals)
    assert set(principals) <= allowed, (sddl, principals)
    assert {"SY", "BA"} <= set(principals), sddl  # SYSTEM + Administrators 必须在
    # 不允许任何「所有用户/所有已认证用户」类主体
    assert not set(principals) & {"WD", "BU", "AU", "IU", "SU", "CO"}, sddl


@WINDOWS_ONLY
def test_harden_file_acl_windows_leaves_only_three_principals(tmp_path: Path) -> None:
    """真实 icacls：收紧后 DACL 只含当前用户 / SYSTEM / Administrators，且不再有继承项。"""
    target = tmp_path / "peer_state.json"
    target.write_text("{}", encoding="utf-8")
    harden_file_acl(target)
    _assert_acl_locked(target)
    readable = _icacls_entries(target)
    assert len(readable) == 3, readable  # 只有三条 (F) 授权
    assert all("(I)" not in line for line in readable), readable  # 没有继承 ACE


# --------------------------------------------------------------------------- #
# 3. ControlPlane._save_state：先收紧 tmp 再原子替换；失败 fail closed
# --------------------------------------------------------------------------- #

def test_save_state_hardens_tmp_before_replace(tmp_path: Path, peer_dirs: dict[str, Path]) -> None:
    """收紧动作必须作用在 tmp 上（成品路径上不存在「权限未收口」的窗口）。"""
    observed: list[tuple[str, str]] = []

    def fake_harden(path: Path) -> None:
        assert path.name.endswith(".tmp"), f"收紧的是成品路径而不是 tmp：{path}"
        observed.append((path.name, path.read_text(encoding="utf-8")))

    plane = _plane(tmp_path, peer_dirs, harden_acl=fake_harden)
    plane._peer_state("alpha")["pids"] = [{"pid": 4242, "exe": "x.exe", "started_at": RECORDED_AT}]
    plane._save_state()

    assert len(observed) == 1
    name, payload = observed[0]
    assert name == f"{CONTROL_STATE_FILE}.tmp"
    assert '"pid": 4242' in payload
    state = _state_path(tmp_path)
    assert state.is_file()
    assert not state.with_name(f"{CONTROL_STATE_FILE}.tmp").exists()
    assert json.loads(state.read_text(encoding="utf-8"))["peers"]["alpha"]["pids"]


def test_save_state_fails_closed_when_harden_fails(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    """收紧失败 → 状态文件不落成品、不留 tmp，抛 StatePersistError（任务以干净文案失败）。"""

    def broken_harden(path: Path) -> None:
        raise AclError("icacls 拒绝访问")

    plane = _plane(tmp_path, peer_dirs, harden_acl=broken_harden)
    plane._peer_state("alpha")["pids"] = [{"pid": 4242}]
    with pytest.raises(StatePersistError) as excinfo:
        plane._save_state()
    message = str(excinfo.value)
    assert "无法保存控制面状态文件" in message
    assert "peer_state.json" in message
    assert not _state_path(tmp_path).exists()
    assert not _state_path(tmp_path).with_name(f"{CONTROL_STATE_FILE}.tmp").exists()


def test_state_persist_error_is_a_job_failure() -> None:
    """继承 JobFailure：控制面任务面板显示原因，而不是「内部错误」。"""
    from ladderstock.web.control import JobFailure

    assert issubclass(StatePersistError, JobFailure)


@WINDOWS_ONLY
def test_state_file_dacl_after_first_create_and_overwrite(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    """真实 icacls：首次创建与覆盖写之后，成品文件的 DACL 都只剩三个受信主体。"""
    plane = _plane(tmp_path, peer_dirs)  # 不注入 → 走真实 harden_file_acl

    plane._peer_state("alpha")["pids"] = [{"pid": 1, "exe": "a.exe", "started_at": RECORDED_AT}]
    plane._save_state()
    _assert_acl_locked(_state_path(tmp_path))  # 首次创建

    plane._peer_state("beta")["pids"] = [{"pid": 2, "exe": "b.exe", "started_at": RECORDED_AT}]
    plane._save_state()  # 覆盖写
    _assert_acl_locked(_state_path(tmp_path))
    assert json.loads(_state_path(tmp_path).read_text(encoding="utf-8"))["peers"]["beta"]["pids"]


# --------------------------------------------------------------------------- #
# 4. 启动校验：peer 目录在共享 / 公共位置 → 拒绝启动
# --------------------------------------------------------------------------- #

def _peer(key: str, directory: str) -> PeerControl:
    return PeerControl(
        key=key,
        directory=directory,
        import_package=f"{key}_pkg",
        mcp_port=8100,
        web_port=8101,
        web_port_label="管理端",
        health_path="/health",
        start_commands=((key, "start"),),
        config_keys=("MCP_HTTP_PORT",),
    )


def test_control_plane_refuses_shared_peer_directory(tmp_path: Path) -> None:
    with pytest.raises(SharedPeerDirectoryError) as excinfo:
        ControlPlane(
            data_root=tmp_path,
            peers={"alpha": _peer("alpha", r"\\fileserver\share\alpha")},
            sources_loader=lambda: {},
        )
    assert "alpha" in str(excinfo.value)


def test_control_plane_refuses_public_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    public = tmp_path / "Public"
    public.mkdir()
    monkeypatch.setattr(fsguard, "public_directories", lambda: [public])
    monkeypatch.setattr(fsguard, "drive_type", lambda drive: 3)
    with pytest.raises(SharedPeerDirectoryError):
        ControlPlane(
            data_root=tmp_path,
            peers={"beta": _peer("beta", str(public / "beta"))},
            sources_loader=lambda: {},
        )


def test_control_plane_starts_with_private_directories(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    monkeypatch.setattr(fsguard, "drive_type", lambda drive: 3)
    monkeypatch.setattr(fsguard, "public_directories", lambda: [tmp_path / "elsewhere"])
    plane = _plane(tmp_path, peer_dirs)
    assert set(plane.peers) == {"alpha", "beta"}


# --------------------------------------------------------------------------- #
# 5. dotenv：写失败 fail closed / 读失败 fail closed
# --------------------------------------------------------------------------- #

def _client(tmp_path: Path, plane: ControlPlane) -> TestClient:
    app = create_app(data_root=tmp_path, dist_dir=tmp_path / "missing", control_plane=plane)
    # 控制面写请求的来源证据（QUOT-178）：服务端要求 Origin 同源或这个显式头
    return TestClient(app, client=LOOPBACK, headers={"X-LDST-Client": "1"})


def test_config_put_keeps_file_untouched_when_harden_fails(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    """权限收不口就不落成品：原文件逐字不变，也不留 .tmp。"""
    env_file = peer_dirs["alpha"] / ".env.local"
    original = "# 手写注释\nOTHER=keep-me\n"
    env_file.write_text(original, encoding="utf-8")

    def broken_harden(path: Path) -> None:
        raise AclError("icacls 拒绝访问")

    client = _client(tmp_path, _plane(tmp_path, peer_dirs, harden_acl=broken_harden))
    response = client.put(
        "/api/control/peers/alpha/config", json={"values": {"MCP_HTTP_PORT": "8003"}}
    )
    assert response.status_code == 500
    assert "已中止" in response.json()["detail"]
    assert env_file.read_text(encoding="utf-8") == original
    assert not env_file.with_name(".env.local.tmp").exists()


def test_config_get_fails_closed_when_dotenv_unreadable(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    """`exists but unreadable` ≠ `no keys`：不能报「全部未配置」诱导用户覆盖密钥。"""
    (peer_dirs["alpha"] / ".env.local").mkdir()  # 目录 → read_text 抛 IsADirectoryError
    client = _client(tmp_path, _plane(tmp_path, peer_dirs))
    response = client.get("/api/control/peers/alpha/config")
    assert response.status_code == 500
    assert "fail closed" in response.json()["detail"]


def test_config_get_fails_closed_on_bad_encoding(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    """GBK 手改的 dotenv 解码失败同样按「读不出来」处理，而不是静默返回空配置。"""
    path = peer_dirs["alpha"] / ".env.local"
    path.write_bytes("ALPHA_API_KEY=密钥\n".encode("gbk"))
    with pytest.raises(EnvFileUnreadable):
        read_env_file(path, strict=True)
    plane = _plane(tmp_path, peer_dirs)
    with pytest.raises(HTTPException) as excinfo:
        plane.read_config("alpha")
    assert excinfo.value.status_code == 500


def test_update_config_refuses_to_overwrite_unreadable_dotenv(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    path = peer_dirs["alpha"] / ".env.local"
    path.write_bytes("ALPHA_API_KEY=密钥\n".encode("gbk"))
    plane = _plane(tmp_path, peer_dirs)
    with pytest.raises(HTTPException) as excinfo:
        plane.update_config("alpha", {"MCP_HTTP_PORT": "8003"}, [])
    assert excinfo.value.status_code == 500
    assert path.read_bytes() == "ALPHA_API_KEY=密钥\n".encode("gbk")  # 原文件不动


def test_logs_fail_closed_when_dotenv_unreadable(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    """读不到密钥就脱不了敏 → 一行日志都不给，绝不漏明文。"""
    (peer_dirs["alpha"] / ".env.local").mkdir()
    log_dir = tmp_path / "logs" / "control"
    log_dir.mkdir(parents=True)
    (log_dir / "alpha.log").write_text("boot with sk-live-SECRET\n", encoding="utf-8")
    payload = _plane(tmp_path, peer_dirs).read_logs("alpha")
    assert payload["lines"] == []
    assert "error" in payload and "fail closed" in payload["error"]
    assert "sk-live-SECRET" not in json.dumps(payload, ensure_ascii=False)


def test_ai_test_fails_closed_when_dotenv_unreadable(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    """读不到已存密钥时不能退化成「无鉴权请求」。"""
    (peer_dirs["alpha"] / ".env.local").mkdir()
    plane = _plane(tmp_path, peer_dirs)
    with pytest.raises(HTTPException) as excinfo:
        plane.ai_test(peer="alpha", base_url="https://api.example.com/v1", model="m")
    assert excinfo.value.status_code == 500
    assert "无鉴权" in excinfo.value.detail


def test_write_env_file_hardens_after_writing(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    path = peer_dirs["alpha"] / ".env.local"
    hardened: list[str] = []
    write_env_file(
        path,
        {"ALPHA_API_KEY": "sk-abcd12345678"},
        harden=lambda p: hardened.append(p.name),
    )
    assert hardened == [".env.local.tmp"]
    assert "ALPHA_API_KEY=sk-abcd12345678" in path.read_text(encoding="utf-8")


def test_write_env_file_removes_tmp_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """替换失败（目标被占用）也不能把含密钥的 `.tmp` 留在磁盘上。"""
    path = tmp_path / ".env.local"
    original = "# 手写注释\nOTHER=keep-me\n"
    path.write_text(original, encoding="utf-8")

    def broken_replace(src: Any, dst: Any) -> None:
        raise OSError(32, "另一个进程正在使用该文件")

    monkeypatch.setattr(control_module.os, "replace", broken_replace)
    with pytest.raises(OSError):
        write_env_file(path, {"ALPHA_API_KEY": "sk-abcd12345678"})

    assert path.read_text(encoding="utf-8") == original
    assert not path.with_name(".env.local.tmp").exists()


@WINDOWS_ONLY
def test_env_file_dacl_after_first_create_and_overwrite(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    """真实 icacls：`.env.local` 首次创建与覆盖写之后，DACL 同样只剩三个受信主体。"""
    plane = _plane(tmp_path, peer_dirs)  # 不注入 → 走真实 harden_file_acl
    env_file = peer_dirs["alpha"] / ".env.local"

    plane.update_config("alpha", {"ALPHA_API_KEY": "sk-live-PROBE-12345678"}, [])
    _assert_acl_locked(env_file)  # 首次创建
    assert "ALPHA_API_KEY=sk-live-PROBE-12345678" in env_file.read_text(encoding="utf-8")

    plane.update_config("alpha", {"MCP_HTTP_PORT": "8003"}, [])
    _assert_acl_locked(env_file)  # 覆盖写
    assert "MCP_HTTP_PORT=8003" in env_file.read_text(encoding="utf-8")


def test_secret_preview_still_masked() -> None:
    """收口改动不能顺带把打码规则弄丢（C8 的另一半）。"""
    assert secret_preview("sk-live-QUOT121-SENTINEL") == "sk-****INEL"
    assert "QUOT121" not in secret_preview("sk-live-QUOT121-SENTINEL")


# --------------------------------------------------------------------------- #
# 6. P2-8：漂移文案一位小数 + 容差；停止失败提示不自指
# --------------------------------------------------------------------------- #

def _stop_refusal(
    tmp_path: Path,
    peer_dirs: dict[str, Path],
    *,
    record: dict[str, Any],
    process_info: Any,
    process_started: Any,
) -> str:
    plane = _plane(
        tmp_path, peer_dirs, process_info=process_info, process_started=process_started
    )
    plane._peer_state("alpha")["pids"] = [record]
    return plane._ownership_refusal(plane.peers["alpha"], record) or ""


def test_drift_message_prints_one_decimal_and_tolerance(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    """5.4s 必须印成「5.4 秒」并写明容差 5 秒；印成「5 秒」会让人以为判据自相矛盾。"""
    exe = peer_dirs["alpha"] / ".venv" / "Scripts" / "python.exe"
    exe.parent.mkdir(parents=True, exist_ok=True)
    exe.write_text("", encoding="utf-8")
    reason = _stop_refusal(
        tmp_path,
        peer_dirs,
        record={"pid": 17136, "exe": str(exe), "started_at": RECORDED_AT},
        process_info=lambda pid, cwd: (str(exe), "python.exe"),
        process_started=lambda pid: RECORDED_AT_DT + timedelta(seconds=5.4),
    )
    assert "相差 5.4 秒" in reason
    assert "容差 5 秒" in reason
    assert "相差 5 秒" not in reason


def test_stop_failure_hint_is_not_self_referential(
    tmp_path: Path, peer_dirs: dict[str, Path]
) -> None:
    """旧文案让用户「先停止后重新启动」，可失败的动作**就是**停止；改成可执行的两条路。"""
    reason = _stop_refusal(
        tmp_path,
        peer_dirs,
        record={"pid": 17136, "legacy": True},
        process_info=lambda pid, cwd: (None, None),
        process_started=lambda pid: None,
    )
    assert "请先停止后重新启动" not in reason
    assert "重新启动该 peer" in reason
    assert "data/control/peer_state.json" in reason
