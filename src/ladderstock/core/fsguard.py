"""本地文件权限与 peer 目录位置的收口判据（QUOT-194 P1-3）。

控制面会写两类**高价值**文件：

- `data/control/peer_state.json`：控制面「启动过谁」的唯一证据。它一旦能被其他本地
  用户改写，QUOT-123 P0-2（盲杀非本控制面进程）就重新打开了；
- 各 peer 的 `.env` / `.env.local`：AI 中继密钥的唯一落点（QUOT-123 C8）。

Windows 上 POSIX 的 `chmod 0600` 是**空操作**，必须显式收紧 DACL。本模块提供三件事：

1. `harden_file_acl()` —— 用 `icacls` 把 DACL 收到「当前用户 + SYSTEM +
   Administrators」并关闭继承（等价 0600）；非 Windows 走 `chmod 0600`。
   失败一律抛 `AclError`：**fail closed**，不留下半松权限的成品文件；
2. `shared_directory_reason()` / `require_local_peer_directories()` —— peer 目录
   不得落在 UNC / 映射网络盘 / Windows 公共目录。那里别的机器、别的账户都能替换
   peer 的代码与 `.venv`，等于把控制面的「执行白名单」交给外人；
3. `drive_type()` —— 上面判据用到的 `GetDriveTypeW` 包装。

判据只有这一份：`web/control.py` 写状态文件、写 `.env*` 时都调这里，不各写一半。
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from pathlib import Path

#: 允许留在 DACL 上的三个主体：当前用户、SYSTEM、Administrators
#: 用 SID 而不是名称：`Administrators` 等内置组名在部分语言的 Windows 上会被本地化，
#: 用名称做 `/grant` 会因为解析失败而静默漏授权（QUOT-194 P1-3）。
SYSTEM_SID = "S-1-5-18"
ADMINISTRATORS_SID = "S-1-5-32-544"

#: 授予的权限：完全控制（等价 0600 的读写在 Windows 上对应 (F)/(M)；状态文件要能被
#: 控制面原子替换，故给 (F)，且只有三个受信主体持有）
ACL_RIGHTS = "(F)"

#: `icacls` / `whoami` 的兜底超时
ACL_COMMAND_TIMEOUT_SEC = 15.0

#: `GetDriveTypeW` 的返回值：4 = DRIVE_REMOTE（映射的网络盘）
DRIVE_REMOTE = 4


class AclError(RuntimeError):
    """DACL 收紧失败。调用方必须 fail closed（中止，而不是带着松权限继续）。"""


class SharedPeerDirectoryError(OSError):
    """peer 目录落在共享/公共位置。继承 `OSError`：CLI 按「配置错误」退出码处理。"""


# --------------------------------------------------------------------------- #
# DACL 收紧
# --------------------------------------------------------------------------- #

def current_user_sid() -> str:
    """当前进程用户的 SID（`whoami /user`）；取不到返回空串。"""
    try:
        proc = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            shell=False,
            timeout=ACL_COMMAND_TIMEOUT_SEC,
            capture_output=True,
            text=True,
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        return ""
    for line in (proc.stdout or "").splitlines():
        cells = [cell.strip().strip('"') for cell in line.split(",")]
        for cell in reversed(cells):
            if cell.upper().startswith("S-1-") and cell.count("-") >= 2:
                return cell
    return ""


def acl_harden_argv(path: Path, *, user_sid: str) -> list[str]:
    """构造收紧 DACL 的参数数组（不拼 shell）。

    `icacls <file> /inheritance:r /grant:r <sid>:(F) ...`：
    `/inheritance:r` 去掉全部继承 ACE（否则父目录的 Users/Everyone 还能进来），
    `/grant:r` 替换同名主体的显式 ACE，重复调用幂等。
    """
    argv = ["icacls", str(path), "/inheritance:r"]
    for principal in (user_sid, SYSTEM_SID, ADMINISTRATORS_SID):
        argv += ["/grant:r", f"*{principal}:{ACL_RIGHTS}"]
    return argv


def harden_file_acl(path: Path) -> None:
    """把单个文件的权限收到「仅当前用户 + SYSTEM + Administrators」（等价 0600）。

    Windows 走 `icacls`；其它平台走 `chmod 0600`。失败抛 `AclError`——
    调用方据此 fail closed（宁可不写，也不留一个别人能读改的成品文件）。
    """
    target = Path(path)
    if os.name != "nt":
        try:
            os.chmod(target, 0o600)
        except OSError as exc:  # pragma: no cover - 非 Windows 分支由 POSIX 用例覆盖
            raise AclError(f"收紧 {target.name} 的权限失败（chmod 0600）：{exc}") from exc
        return

    user_sid = current_user_sid()
    if not user_sid:
        raise AclError(
            f"无法取得当前用户 SID（whoami /user 失败），不能收紧 {target.name} 的 DACL；"
            "请确认 %SystemRoot%\\System32\\whoami.exe 可用后重试"
        )
    argv = acl_harden_argv(target, user_sid=user_sid)
    try:
        proc = subprocess.run(
            argv,
            shell=False,
            timeout=ACL_COMMAND_TIMEOUT_SEC,
            capture_output=True,
            text=True,
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AclError(f"调用 icacls 收紧 {target.name} 的 DACL 失败：{exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        reason = detail[-1] if detail else "无输出"
        raise AclError(
            f"icacls 收紧 {target.name} 的 DACL 失败（退出码 {proc.returncode}）：{reason}"
        )


# --------------------------------------------------------------------------- #
# peer 目录位置
# --------------------------------------------------------------------------- #

def drive_type(drive: str) -> int | None:
    """Windows 盘符类型（`GetDriveTypeW`）；非 Windows、空盘符或查询失败返回 None。"""
    if os.name != "nt" or not drive:
        return None
    import ctypes

    try:
        # typeshed 只在 Windows 平台给 ctypes 声明 WinDLL；用 getattr 取，Linux 上 mypy 也能过
        windll = getattr(ctypes, "WinDLL", None)
        if windll is None:  # pragma: no cover - 非 Windows 在函数开头就返回了
            return None
        kernel32 = windll("kernel32")
        root = drive if drive.endswith("\\") else f"{drive}\\"
        return int(kernel32.GetDriveTypeW(root))
    except (AttributeError, OSError, ValueError):  # pragma: no cover - 本机永远可查
        return None


def is_unc_path(text: str) -> bool:
    """UNC 路径判定（`\\\\server\\share`、`\\\\?\\UNC\\...`、`//server/share`）。"""
    raw = (text or "").strip()
    if raw.lower().startswith(("//", "\\\\?\\unc\\", "\\\\.\\unc\\")):
        return True
    return raw.startswith("\\\\")


def public_directories() -> list[Path]:
    """Windows 公共目录（`%PUBLIC%` 与 `<SystemDrive>\\Users\\Public`），去重后返回。"""
    roots: list[Path] = []
    raw = os.environ.get("PUBLIC", "").strip()
    if raw:
        roots.append(Path(raw))
    system_drive = os.environ.get("SystemDrive", "C:").strip() or "C:"
    roots.append(Path(f"{system_drive}\\Users\\Public"))

    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = os.path.normcase(os.path.abspath(str(root)))
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return unique


def _normalized(text: str) -> str:
    """只做本地字符串归一，**不 resolve**：UNC 路径上 resolve 会真的去连网络。"""
    return os.path.normcase(os.path.normpath(os.path.abspath(text)))


def _is_within(child: str, parent: str) -> bool:
    child_norm = _normalized(child)
    parent_norm = _normalized(parent).rstrip("\\/") or os.sep
    if child_norm == parent_norm:
        return True
    return child_norm.startswith(parent_norm + os.sep)


def shared_directory_reason(directory: str) -> str | None:
    """目录落在共享/公共位置就返回原因（中文，可直接展示）；本机私有磁盘返回 None。"""
    raw = (directory or "").strip()
    if not raw:
        return "目录为空"
    if is_unc_path(raw):
        return "UNC 网络路径（\\\\server\\share，别的机器可读写）"

    drive = Path(raw).drive
    if drive and drive_type(drive) == DRIVE_REMOTE:
        return f"映射网络盘（{drive} 的实际类型是 DRIVE_REMOTE）"

    for root in public_directories():
        if _is_within(raw, str(root)):
            return f"Windows 公共目录（{root}，本机所有账户都可读写）"
    return None


def require_local_peer_directories(directories: Mapping[str, str]) -> None:
    """控制面启动前的硬校验：任一 peer 目录命中共享路径就整体拒绝启动。

    拒绝而不是「只告警」：peer 目录里的 `.venv/Scripts/python.exe` 就是控制面
    要执行的东西，放在共享盘上等于把执行权交给别人；启动后才发现就已经晚了。
    """
    problems: list[str] = []
    for key, directory in directories.items():
        reason = shared_directory_reason(directory)
        if reason:
            problems.append(f"  - {key}：{directory} —— {reason}")
    if not problems:
        return
    raise SharedPeerDirectoryError(
        "控制面拒绝启动：以下 peer 目录不在本机私有磁盘上，"
        "peer 的代码与 .venv 可被其他机器/账户替换：\n"
        + "\n".join(problems)
        + "\n处理：把 peer 移到本机固定磁盘（例如 [local-path]\\<peer>）后重新启动；"
        "只需数据面时可用 LDST_CONTROL_ENABLED=0 暂时关闭控制面。"
    )


__all__ = [
    "ACL_RIGHTS",
    "ADMINISTRATORS_SID",
    "DRIVE_REMOTE",
    "SYSTEM_SID",
    "AclError",
    "SharedPeerDirectoryError",
    "acl_harden_argv",
    "current_user_sid",
    "drive_type",
    "harden_file_acl",
    "is_unc_path",
    "public_directories",
    "require_local_peer_directories",
    "shared_directory_reason",
]
