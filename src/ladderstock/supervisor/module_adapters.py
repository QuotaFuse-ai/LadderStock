"""托管模块适配器：启动 / 停止 / 探活 / 进程识别。

QUOT-482-B 落地六个顺序模块的适配器，QUOT-506 追加按需模块 `proxypool`：

- `start() -> SpawnedProcess`：拉起并立即返回；端口等待由引擎轮询 `probe()`；
- `stop()`：按模块形态停（命令行模块发信号 + 整树终止，WSL 走 compose stop，
  计划任务模块走 `schtasks /change + /end`）；适配器内部带一次有界升级，
  保证多面模块（wire 8790+8791）不会只停一半；
- `kill()`：整树强杀兜底（引擎在停超时后调用）；
- `probe()`：**端口在监听且命令行命中**（本单约定的识别口径）。
  命令行读不到 / 不匹配时失败关闭（返回 False），绝不凭端口猜归属。

`proxypool` 不在 `MODULE_ORDER` 内（`ON_DEMAND_MODULES`）：`start_all` 不碰它，
只能显式启动；它的 `probe()` 是双向判据（端口在听 **且** 真实 TCP 建连成功 **且**
命令行命中本模块转发进程）。

识别规则（每个模块两条独立证据链）：

1. 端口 → `Get-NetTCPConnection` 的 `OwningProcess` → 父链上溯
   （`Get-CimInstance Win32_Process` 的 `ParentProcessId`）直到可识别的 cmdline；
2. 监听进程的 cmdline 必须同时命中该服务面的关键字（如 `th-cn` + `start-all`、
   `qfw` + `serve`、`pfctl.py` + `run`、`proxypool_forwarder` + `--id` + 映射 Id）；
   financecoffer 的第二条证据在 WSL 内（compose project label + service=api 的运行
   容器），因为 Windows 侧的持有者只是 `wslhost.exe` 转发器，不能当作归属证据。

所有外部接触面（端口探测 / 进程查询 / 终止 / 拉起 / 命令执行）都收在 `HostPivot`
后面，测试注入假实现，不占真端口。
"""

from __future__ import annotations

import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

# psutil 是可选依赖：缺失时回退 Windows CIM 实现（见 default_host_pivot）
try:
    import psutil
except ImportError:  # pragma: no cover - 依赖探测
    psutil = None  # type: ignore[assignment]

from ladderstock.core import runtime
from ladderstock.core.boundary import PEER_DIR_ENV, PEER_SIBLING_DIRS
from ladderstock.supervisor.adapters import (
    MODULE_ORDER,
    ON_DEMAND_MODULES,
    ModuleAdapter,
    SpawnedProcess,
    module_registry,
)
from ladderstock.supervisor.engine import SupervisorError
from ladderstock.supervisor.modules import MODULE_PORTS, panel_port
from ladderstock.supervisor.proctree import QUERY_TIMEOUT_SEC, split_windows_commandline

#: 端口探测（socket connect）的短超时：只用于轮询，不能拖慢 state()
PROBE_CONNECT_TIMEOUT_SEC = 0.3
#: 识别结果的缓存时间：引擎按 0.25s 轮询 probe()，不能每次都起 PowerShell
EVIDENCE_CACHE_SEC = 1.0
#: 适配器内部优雅停的等待窗口；超时后由适配器整树强杀（引擎的 kill() 仍可兜底）
GRACEFUL_STOP_WAIT_SEC = 3.0
#: 停完回读端口的窗口
STOP_READBACK_WAIT_SEC = 5.0
#: 拉起后（模块专属形态）等监听出现的窗口
SPAWN_WAIT_SEC = 20.0
#: WSL / compose 命令的兜底超时
WSL_COMMAND_TIMEOUT_SEC = 180.0
#: `wsl -l -v` 的探测超时
WSL_LIST_TIMEOUT_SEC = 15.0
#: proxypool：拉起后确认「监听 + 真实握手 + 进程命中」的窗口
PROXYPOOL_SPAWN_WAIT_SEC = 8.0
#: proxypool：停止后回读不可连接的窗口
PROXYPOOL_READBACK_WAIT_SEC = 5.0
#: proxypool 转发进程的命令行标记（probe / stop 的唯一识别依据）
PROXYPOOL_FORWARDER_MARKER = "proxypool_forwarder"
#: 只允许回环监听（配置 ListenHost 必须命中这里）
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
#: 宿主配置（LdstHostConfig.psd1）覆盖变量与默认相对路径（仓库根下）
HOST_CONFIG_ENV = "LDST_HOST_CONFIG"
HOST_CONFIG_REL = Path("ops") / "host" / "LdstHostConfig.psd1"

#: peer 根覆盖：`LDST_PEER_ROOT/<同级名>`，空 = `<安装根>/<同级名>`（bundle 相对）
DEFAULT_PEER_ROOT_ENV = "LDST_PEER_ROOT"
#: 安装根覆盖（bundle 相对解析的根；见 `install_root()`）
INSTALL_ROOT_ENV = "LDST_INSTALL_ROOT"
#: 自有服务（看板 / 看门狗）的启动器覆盖；空 = 按安装根找 venv
LAUNCHER_ENV = "LDST_LAUNCHER"
#: 看板端口按环境解析（QUOT-589 第二轮，prod=5950 / test=5951）：看门狗重启看板
#: 时用它，且看板「不是」被托管模块
PANEL_PORT = 5950
#: 日志目录默认收在既有宿主日志目录下（与 ops/host 的约定一致）
DEFAULT_LOG_SUBDIR = Path("LadderStock") / "logs" / "supervisor"
#: WSL 发行版覆盖；空 = 运行时 `wsl -l -v` 取默认发行版（不许写死）
WSL_DISTRO_ENV = "LDST_WSL_DISTRO"
#: ProxyFleet 计划任务名（只启停已存在的同名任务，不新建、不改运行身份）
PROXYFLEET_TASK_ENV = "LDST_PROXYFLEET_TASK"
DEFAULT_PROXYFLEET_TASK = "ProxyFleet"
#: TickHub-CN 的运行时 env 文件（非密钥 + 代理密钥都在里面，按旧入口原样加载）
TICKHUB_ENV_FILES_ENV = "LDST_TICKHUB_ENV_FILES"


def panel_port_for_env(env: Mapping[str, str]) -> int:
    """看门狗探活目标端口：显式 LDST_PANEL_PORT > 避让记忆 > 环境默认。

    与 `_panel_spawn_command` 的 `--port` 共用 `resolve_panel_port` 单一真源，
    保证避让发生后探活与拉起/重启是同一端口。
    """
    return resolve_panel_port(env)


# --------------------------------------------------------------------------- #
# 宿主接触面
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0


@dataclass(frozen=True)
class ProcessSnapshot:
    """一个进程的可识别信息（端口归属与父链的证据单元）。"""

    pid: int
    parent_pid: int | None
    name: str
    cmdline: tuple[str, ...]

    @property
    def joined(self) -> str:
        return " ".join(self.cmdline).replace("\\", "/").lower()


@dataclass(frozen=True)
class PortEvidence:
    """单个端口的识别证据链。"""

    port: int
    listening: bool
    matched: bool = False
    pid: int | None = None
    name: str = ""
    cmdline: tuple[str, ...] = ()
    parent_chain: tuple[ProcessSnapshot, ...] = ()
    #: 命中的是父链里哪一级（0 = 监听者本身；`ldst.exe` 形态下是它的子进程）
    matched_pid: int | None = None
    detail: str = ""


@dataclass(frozen=True)
class Identification:
    module: str
    matched: bool
    ports: tuple[PortEvidence, ...]

    def describe(self) -> str:
        if not self.ports:
            return f"{self.module}: 未配置端口"
        lines = [f"{self.module}: {'命中' if self.matched else '未命中'}"]
        for item in self.ports:
            state = "listening" if item.listening else "free"
            verdict = "match" if item.matched else "no-match"
            chain = " <- ".join(f"{p.pid}:{p.name}" for p in item.parent_chain)
            lines.append(
                f"  {item.port} {state} {verdict} pid={item.pid} chain=[{chain}]"
                + (f" ({item.detail})" if item.detail else "")
            )
        return "\n".join(lines)


class HostPivot(Protocol):
    """宿主操作面（测试注入假实现）。"""

    def listening(self, port: int) -> bool: ...

    def handshake(self, host: str, port: int) -> bool:
        """对该地址做一次真实 TCP 建连（握手成功才算通）。"""

    def owner_pid(self, port: int) -> int | None: ...

    def snapshot(self, pid: int) -> ProcessSnapshot | None: ...

    def parent_chain(self, pid: int, depth: int = 6) -> tuple[ProcessSnapshot, ...]: ...

    def find_processes(self, marker: str) -> tuple[ProcessSnapshot, ...]:
        """按命令行关键字扫整机（停止后确认无残留用）。"""

    def terminate_tree(self, pid: int, *, force: bool) -> None: ...

    def spawn(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        log_path: Path,
    ) -> int: ...

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float,
        cwd: Path | None = None,
    ) -> CommandResult: ...


def _decode_wsl(raw: bytes | str) -> str:
    """wsl.exe 的管道输出可能是 UTF-16LE 且带 NUL；统一清成可解析文本。"""
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", "replace")
    else:
        text = raw
    return text.replace("\x00", "")


def _snapshots_from_json(raw: str) -> tuple[ProcessSnapshot, ...]:
    """CIM 查询（pid/ppid/name/cmdline）的 JSON 输出 → 进程快照表。"""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return ()
    rows = payload if isinstance(payload, list) else [payload]
    snapshots: list[ProcessSnapshot] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        pid_value = row.get("pid")
        if pid_value is None:
            continue
        try:
            child = int(pid_value)
        except (TypeError, ValueError):
            continue
        ppid_raw = row.get("ppid")
        try:
            ppid = int(ppid_raw) if ppid_raw else None
        except (TypeError, ValueError):
            ppid = None
        cmdline_raw = str(row.get("cmdline") or "")
        snapshots.append(
            ProcessSnapshot(
                pid=child,
                parent_pid=ppid,
                name=str(row.get("name") or ""),
                cmdline=split_windows_commandline(cmdline_raw) if cmdline_raw else (),
            )
        )
    return tuple(snapshots)


class WindowsHostPivot:
    """tasklist / CIM / taskkill / Popen 的真实实现（生产路径）。

    每次查询都起一次 PowerShell 是慢的（200-500ms），调用方必须走
    `BaseAdapter` 的 1s 证据缓存，不能直接拿它做高频轮询。
    """

    def listening(self, port: int) -> bool:  # pragma: no cover - socket 直通
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(PROBE_CONNECT_TIMEOUT_SEC)
            return sock.connect_ex(("127.0.0.1", port)) == 0

    def handshake(self, host: str, port: int) -> bool:  # pragma: no cover - socket 直通
        try:
            with socket.create_connection((host, int(port)), timeout=PROBE_CONNECT_TIMEOUT_SEC):
                return True
        except OSError:
            return False

    def owner_pid(self, port: int) -> int | None:  # pragma: no cover - 子进程查询
        script = (
            "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
            f"$c=Get-NetTCPConnection -State Listen -LocalPort {int(port)} "
            "-ErrorAction SilentlyContinue | Select-Object -First 1;"
            "if ($c) { [int]$c.OwningProcess }"
        )
        out = self._powershell(script)
        if not out:
            out = self._netstat_owner(port)
        return int(out) if out and out.isdigit() else None

    def snapshot(self, pid: int) -> ProcessSnapshot | None:  # pragma: no cover - 子进程查询
        chain = self.parent_chain(pid, depth=1)
        return chain[0] if chain else None

    def parent_chain(  # pragma: no cover - 子进程查询
        self, pid: int, depth: int = 6
    ) -> tuple[ProcessSnapshot, ...]:
        script = (
            "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
            f"$cur={int(pid)};$out=@();"
            f"for($i=0;$i -lt {int(depth)} -and $cur){{"
            '$p=Get-CimInstance Win32_Process -Filter "ProcessId=$cur" '
            "-ErrorAction SilentlyContinue;"
            "if(-not $p){break};"
            "$out+=[pscustomobject]@{pid=[int]$p.ProcessId;ppid=[int]$p.ParentProcessId;"
            "name=[string]$p.Name;cmdline=[string]$p.CommandLine};"
            "$cur=$p.ParentProcessId};"
            "if($out.Count -gt 0){@($out)|ConvertTo-Json -Compress -Depth 3}"
        )
        raw = self._powershell(script)
        if not raw:
            return ()
        return _snapshots_from_json(raw)

    def find_processes(  # pragma: no cover - 子进程查询
        self, marker: str
    ) -> tuple[ProcessSnapshot, ...]:
        needle = str(marker).replace("'", "''").lower()
        if not needle:
            return ()
        script = (
            "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
            f"$n='{needle}';"
            "$rows=@(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | "
            "Where-Object { $_.CommandLine -and $_.CommandLine.ToLower().Contains($n) } | "
            "ForEach-Object { [pscustomobject]@{pid=[int]$_.ProcessId;"
            "ppid=[int]$_.ParentProcessId;name=[string]$_.Name;cmdline=[string]$_.CommandLine} });"
            "if($rows.Count -gt 0){@($rows)|ConvertTo-Json -Compress -Depth 3}"
        )
        raw = self._powershell(script)
        if not raw:
            return ()
        return _snapshots_from_json(raw)

    def terminate_tree(self, pid: int, *, force: bool) -> None:  # pragma: no cover - 子进程终止
        if os.name != "nt":
            sig = getattr(signal, "SIGKILL", signal.SIGTERM) if force else signal.SIGTERM
            try:
                os.kill(pid, sig)
            except OSError:
                pass
            return
        args = ["taskkill", "/PID", str(pid), "/T"]
        if force:
            args.append("/F")
        subprocess.run(
            args,
            shell=False,
            timeout=QUERY_TIMEOUT_SEC,
            capture_output=True,
            text=True,
            errors="replace",
        )

    def spawn(  # pragma: no cover - 子进程拉起
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        log_path: Path,
    ) -> int:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(log_path, "a", encoding="utf-8", errors="replace")  # noqa: SIM115
        flags = 0
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) | int(
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            )
            kwargs["creationflags"] = flags
        else:
            kwargs["start_new_session"] = True
        try:
            proc = subprocess.Popen(
                [str(part) for part in argv],
                cwd=str(cwd),
                env=dict(env),
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=subprocess.STDOUT,
                close_fds=True,
                **kwargs,
            )
        finally:
            handle.close()
        return proc.pid

    def run(  # pragma: no cover - 子进程执行
        self,
        argv: Sequence[str],
        *,
        timeout: float,
        cwd: Path | None = None,
    ) -> CommandResult:
        try:
            proc = subprocess.run(
                [str(part) for part in argv],
                cwd=str(cwd) if cwd else None,
                shell=False,
                timeout=timeout,
                capture_output=True,
                text=True,
                errors="replace",
            )
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                returncode=-1,
                stdout=_decode_wsl(exc.stdout or ""),
                stderr=f"timeout after {timeout:.0f}s",
            )
        except OSError as exc:
            return CommandResult(returncode=-2, stderr=str(exc))
        return CommandResult(
            returncode=proc.returncode,
            stdout=_decode_wsl(proc.stdout or ""),
            stderr=_decode_wsl(proc.stderr or ""),
        )

    def _powershell(self, script: str) -> str:  # pragma: no cover - 子进程查询
        try:
            proc = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                shell=False,
                timeout=QUERY_TIMEOUT_SEC,
                capture_output=True,
                text=True,
                errors="replace",
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        if proc.returncode != 0:
            return ""
        return _decode_wsl(proc.stdout or "").strip()

    def _netstat_owner(self, port: int) -> str:  # pragma: no cover - 兜底通道
        try:
            proc = subprocess.run(
                ["netstat", "-ano", "-p", "TCP"],
                shell=False,
                timeout=QUERY_TIMEOUT_SEC,
                capture_output=True,
                text=True,
                errors="replace",
            )
        except (OSError, subprocess.TimeoutExpired):
            return ""
        needle = f":{port}"
        for line in (proc.stdout or "").splitlines():
            parts = line.split()
            if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[3].upper() == "LISTENING":
                if parts[1].endswith(needle) or parts[1].endswith(needle + " "):
                    return parts[4]
        return ""


class PsutilHostPivot(WindowsHostPivot):
    """psutil 进程识别（跨平台）：Windows / macOS / Linux 同一套查询。

    只替换「读进程」的那几条（owner_pid / snapshot / parent_chain /
    find_processes / terminate_tree / alive），端口探测、拉起、命令执行仍走基类：

    - 原实现每次识别都起一次 PowerShell + `Get-NetTCPConnection`，在非 Windows
      宿主上根本不存在这两个命令；本类用 `psutil.net_connections` 与
      `psutil.Process` 直读，三平台一致；
    - 拉起后短窗内 `posix` 的监听要过几轮 `net_connections` 才可见，调用方必须
      走 `BaseAdapter` 的证据缓存（引擎 0.25s 轮询 -> 1s 缓存），不要直接高频调；
    - psutil 缺失时 `available()` 返回 False，装配侧回退 CIM 实现。
    """

    @staticmethod
    def available() -> bool:  # pragma: no cover - 依赖探测
        return psutil is not None

    def owner_pid(self, port: int) -> int | None:  # pragma: no cover - 子进程查询
        port_number = int(port)
        try:
            for conn in psutil.net_connections(kind="tcp"):
                if conn.status != psutil.CONN_LISTEN or not conn.laddr:
                    continue
                if conn.laddr.port == port_number:
                    return int(conn.pid) if conn.pid else None
        except (psutil.Error, OSError):
            return None
        return None

    def _snapshot_of(self, proc: Any) -> ProcessSnapshot | None:  # pragma: no cover
        try:
            info = proc.as_dict(attrs=["pid", "ppid", "name", "cmdline"])
        except psutil.Error:
            return None
        raw = info.get("cmdline") or []
        return ProcessSnapshot(
            pid=int(info["pid"]),
            parent_pid=int(info["ppid"]) if info.get("ppid") is not None else None,
            name=str(info.get("name") or ""),
            cmdline=tuple(str(part) for part in raw),
        )

    def snapshot(self, pid: int) -> ProcessSnapshot | None:  # pragma: no cover - 子进程查询
        try:
            return self._snapshot_of(psutil.Process(int(pid)))
        except (psutil.Error, OSError):
            return None

    def parent_chain(  # pragma: no cover - 子进程查询
        self, pid: int, depth: int = 6
    ) -> tuple[ProcessSnapshot, ...]:
        chain: list[ProcessSnapshot] = []
        current: Any
        try:
            current = psutil.Process(int(pid))
        except (psutil.Error, OSError):
            return ()
        while current is not None and len(chain) < depth:
            snap = self._snapshot_of(current)
            if snap is None:
                break
            chain.append(snap)
            try:
                current = current.parent()
            except (psutil.Error, OSError):
                current = None
        return tuple(chain)

    def find_processes(  # pragma: no cover - 子进程查询
        self, marker: str
    ) -> tuple[ProcessSnapshot, ...]:
        needle = str(marker).replace("\\", "/").lower()
        if not needle:
            return ()
        found: list[ProcessSnapshot] = []
        for proc in psutil.process_iter(["pid"]):
            snap = self._snapshot_of(proc)
            if snap is None:
                continue
            if needle in " ".join(snap.cmdline).replace("\\", "/").lower():
                found.append(snap)
        return tuple(found)

    def terminate_tree(self, pid: int, *, force: bool) -> None:  # pragma: no cover
        try:
            root = psutil.Process(int(pid))
        except (psutil.Error, OSError):
            return
        # 先收子孙再收根：父进程先死会让子进程被重挂到 init，找不回原树
        try:
            victims = root.children(recursive=True) + [root]
        except (psutil.Error, OSError):
            victims = [root]
        for proc in victims:
            try:
                proc.terminate() if not force else proc.kill()
            except (psutil.Error, OSError):
                continue
        _, alive = psutil.wait_procs(victims, timeout=QUERY_TIMEOUT_SEC)
        for proc in alive:
            try:
                proc.kill()
            except (psutil.Error, OSError):
                continue

    def alive(self, pid: int) -> bool:  # pragma: no cover - 子进程查询
        try:
            return psutil.Process(int(pid)).is_running()
        except (psutil.Error, OSError):
            return False


def default_host_pivot() -> HostPivot:
    """生产宿主：psutil 可用则用跨平台实现，否则回退 Windows CIM 实现。"""
    if PsutilHostPivot.available():
        return PsutilHostPivot()
    return WindowsHostPivot()


# --------------------------------------------------------------------------- #
# 模块启动面
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class StartFace:
    """一个服务面的启动命令与识别关键字（一个模块可有多个面）。"""

    face: str
    argv: tuple[str, ...]
    cwd: Path
    port: int
    #: 监听进程 cmdline 必须同时包含的小写片段（路径分隔符已归一为 `/`）
    match: tuple[str, ...]
    #: 日志文件名
    log_name: str
    #: 允许在父链上逐级向上找命中（`ldst.exe` 一类启动器把监听留给孙进程时用）
    inherit_chain: bool = False
    #: 备用识别关键字组（同一模块的第二形态，如 `ldst <子命令>` 与 `-m ... <子命令>`）
    alt_match: tuple[str, ...] = ()
    #: 额外备用关键字组（打包后 `LadderStock.exe <子命令>` 等第三形态，QUOT-525）
    alt_matches: tuple[tuple[str, ...], ...] = ()


def _norm_tokens(tokens: Sequence[str]) -> tuple[str, ...]:
    return tuple(token.replace("\\", "/").lower() for token in tokens)


def load_env_file(path: Path) -> dict[str, str]:
    """读 KEY=VALUE 形式的 env 文件（注释 / 空行 / 成对引号）。"""
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return values
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values


def _local_appdata_log_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    return base / DEFAULT_LOG_SUBDIR


def install_root() -> Path:
    """安装根：「LadderStock 目录的同级」——peer 都放在它下面。

    三态，全部相对，不含任何写死的盘符：

    - 显式覆盖 `LDST_INSTALL_ROOT`（部署侧一次性指定位置）；
    - 冻结态：Windows/Linux onedir 取 exe 所在 `LadderStock/` 的父目录，
      macOS 取 `.app` 所在目录，AppImage 取原始 `.AppImage` 文件所在目录；
    - 源码树：仓库根的父目录。

    打包布局与 macOS `.app` / AppImage 的差异由 `core.runtime` 收口（QUOT-525），
    这里只做一次委托，避免两处各算一套。
    """
    return runtime.install_root()


def _install_root_from(values: Mapping[str, str]) -> Path:
    """`install_root()` 的显式环境版本（`preflight(env=...)` 用，不读进程环境）。"""
    return runtime.install_root(values)


def default_peer_dir(key: str, env: Mapping[str, str] | None = None) -> Path:
    """peer 目录：`LDST_PEER_DIR_<MODULE>` > `LDST_PEER_ROOT/<同级名>` > `<安装根>/<同级名>`。

    后两级都相对安装根（`install_root()`，bundle 相对），没有「本机既有部署根」这种
    写死兜底——找不到就是找不到，由 `preflight()` 报出缺哪个目录，而不是悄悄去猜
    一个旧路径。
    """
    values = os.environ if env is None else env
    override = (values.get(PEER_DIR_ENV[key]) or "").strip()
    if override:
        return Path(override)
    sibling_name = PEER_SIBLING_DIRS[key]
    root = (values.get(DEFAULT_PEER_ROOT_ENV) or "").strip()
    base = Path(root) if root else _install_root_from(values)
    return base / sibling_name


def _ldst_repo_root() -> Path:
    """本程序目录：源码态是仓库根，冻结态是安装根（子进程 cwd 基准）。"""
    return runtime.project_root()


CONTROL_ARGS: tuple[str, ...] = ("-m", "ladderstock.cli.main")


def launcher_entry(env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """看板/看门狗等「本程序自有服务」的**完整**启动前缀（bundle 相对）。

    优先级：`LDST_LAUNCHER` 显式给出 > 冻结态自身 exe > `<peer 根>/LadderStock`
    下 venv 的 `ldst` > 当前解释器 + `-m`。打包后 exe 本身就是 CLI，**不能再接
    `-m`**（会变成未知参数秒退，QUOT-523 实测）；只有源码态才需要 `-m`。
    """
    values = os.environ if env is None else env
    override = (values.get(LAUNCHER_ENV) or "").strip()
    if override:
        return (override,)
    if runtime.is_frozen():
        return (str(runtime.executable()),)
    merged = {**os.environ, **values}
    root = (merged.get(DEFAULT_PEER_ROOT_ENV) or "").strip()
    candidate = (Path(root) / "LadderStock") if root else _install_root_from(merged) / "LadderStock"
    for exe in ("ldst.exe", "ldst"):
        path = candidate / ".venv" / "Scripts" / exe
        if path.is_file():
            return (str(path),)
        path = candidate / ".venv" / "bin" / exe
        if path.is_file():
            return (str(path),)
    return (sys.executable, *CONTROL_ARGS)


def _venv_exe(peer: Path, exe: str) -> Path:
    return peer / ".venv" / "Scripts" / exe


# --------------------------------------------------------------------------- #
# 启动前检查（bundle 相对路径是否真的落位）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PreflightItem:
    """一个模块的启动前提：目录 / 入口 / 启动器 / 识别依据。"""

    module: str
    ok: bool
    detail: str
    #: 该模块的启动依赖路径（日志里照原样给出，不再二次猜测）
    paths: tuple[Path, ...] = ()


@dataclass(frozen=True)
class PreflightReport:
    ok: bool
    install_root: Path
    items: tuple[PreflightItem, ...]

    def failures(self) -> tuple[PreflightItem, ...]:
        return tuple(item for item in self.items if not item.ok)

    def describe(self) -> str:
        lines = [f"安装根：{self.install_root}"]
        for item in self.items:
            flag = "OK  " if item.ok else "FAIL"
            lines.append(f"[{flag}] {item.module}: {item.detail}")
        return "\n".join(lines)


def preflight(
    env: Mapping[str, str] | None = None,
    *,
    include_on_demand: bool = False,
) -> PreflightReport:
    """按 bundle 相对规则逐模块核启动前提，缺什么就报什么（不抛异常）。

    检查的是「起得来」的必要条件，不碰进程、不占端口：
    `MODULE_ORDER` 每项检查 peer 目录与各启动面的入口文件（watch 额外查看板
    启动器）；「没有启动命令」的模块（financecoffer，属于观测托管）只报形态。
    按需模块（proxypool）默认不查——它不在全启路径上，要查就显式
    `include_on_demand=True`。ProxyFleet 只经计划任务启停，没有可查的入口文件，
    报告里说明识别关键字即可。
    """
    values = os.environ if env is None else env
    root = _install_root_from(values)
    names = list(MODULE_ORDER) + (list(ON_DEMAND_MODULES) if include_on_demand else [])
    built: dict[str, Any] | None = None
    items: list[PreflightItem] = []
    for name in names:
        if name == "proxyfleet":
            task = (values.get(PROXYFLEET_TASK_ENV) or DEFAULT_PROXYFLEET_TASK).strip()
            items.append(
                PreflightItem(
                    module=name,
                    ok=True,
                    detail=f"只经计划任务启停（任务名 {task}）；运行期由 schtasks 结果判定",
                )
            )
            continue
        if name in ON_DEMAND_MODULES:
            path = _host_config_path(values)
            config = load_proxypool_config(host=default_host_pivot(), path=path)
            count = len(config.enabled_mappings())
            items.append(
                PreflightItem(
                    module=name,
                    ok=True,
                    detail=f"按需模块（{path}）："
                    + (f"启用 {count} 条映射" if count else "未启用或无映射"),
                    paths=(path,),
                )
            )
            continue
        if name == "watch":
            launcher = launcher_entry(values)
            entry = Path(launcher[0])
            # 退回当前解释器时没有可查的文件，但解释器本身一定在
            usable = str(entry) == sys.executable or entry.is_file()
            source = (
                "LDST_LAUNCHER"
                if (values.get(LAUNCHER_ENV) or "").strip()
                else ("venv 启动器" if len(launcher) > 1 else "当前解释器")
            )
            items.append(
                PreflightItem(
                    module=name,
                    ok=usable,
                    detail=(
                        # QUOT-589 重修：看门狗无端口化，只核启动器
                        f"启动器 {entry}（{source}）· 无端口自愈监控器"
                        + ("" if usable else "：启动器不存在")
                    ),
                    paths=(entry,),
                )
            )
            continue

        peer = default_peer_dir(name, values)
        if name == "financecoffer":
            deploy = peer / "deploy"
            ok = deploy.is_dir()
            items.append(
                PreflightItem(
                    module=name,
                    ok=ok,
                    detail=f"peer {peer} · WSL compose 目录 {deploy}"
                    + ("" if ok else "：不存在（起不来）"),
                    paths=(peer, deploy),
                )
            )
            continue
        if built is None:
            built = dict(build_default_adapters(env=values, host=default_host_pivot()))
        faces = getattr(built[name], "faces", ())
        if not faces:
            items.append(PreflightItem(module=name, ok=False, detail=f"peer {peer}：没有启动命令"))
            continue
        missing = list(
            dict.fromkeys(Path(face.argv[0]) for face in faces if not Path(face.argv[0]).is_file())
        )
        items.append(
            PreflightItem(
                module=name,
                ok=not missing,
                detail=f"peer {peer} · {len(faces)} 个启动面"
                + ("" if not missing else "；缺入口 " + "、".join(str(p) for p in missing)),
                paths=(peer, *missing),
            )
        )
    return PreflightReport(
        ok=all(item.ok for item in items),
        install_root=root,
        items=tuple(items),
    )


class BaseAdapter:
    """命令行型模块适配器的公共实现（多面模块 = 多条 StartFace）。"""

    def __init__(
        self,
        name: str,
        ports: tuple[int, ...],
        faces: Sequence[StartFace],
        *,
        host: HostPivot | None = None,
        env_files: Sequence[Path] = (),
        env_overrides: Mapping[str, str] | None = None,
        log_dir: Path | None = None,
        evidence_cache_sec: float = EVIDENCE_CACHE_SEC,
        graceful_wait_sec: float = GRACEFUL_STOP_WAIT_SEC,
        readback_wait_sec: float = STOP_READBACK_WAIT_SEC,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.name = name
        self.ports = tuple(ports)
        self.faces = tuple(faces)
        self._host = host or default_host_pivot()
        self._env_files = tuple(env_files)
        self._env_overrides = dict(env_overrides or {})
        self._log_dir = log_dir or _local_appdata_log_dir()
        self._cache_sec = float(evidence_cache_sec)
        self._graceful_wait = float(graceful_wait_sec)
        self._readback_wait = float(readback_wait_sec)
        self._clock = clock
        self._sleep = sleep
        self._roots: list[int] = []
        self._cache: dict[int, tuple[float, PortEvidence]] = {}

    # ------------------------------------------------------------------ 协议

    def start(self) -> SpawnedProcess:
        conflict = self._foreign_occupant()
        if conflict is not None:
            port, evidence = conflict
            raise SupervisorError(
                f"{self.name} 的端口 {port} 已被非本模块进程占用"
                f"（pid={evidence.pid} cmd={' '.join(evidence.cmdline)[:160]}），拒绝重复拉起"
            )
        primary: StartFace | None = None
        primary_pid: int | None = None
        for face in self.faces:
            exe = Path(face.argv[0])
            if not exe.is_file():
                raise SupervisorError(f"{self.name} 的入口不存在：{exe}")
            pid = self._host.spawn(
                face.argv,
                cwd=face.cwd,
                env=self._environment(),
                log_path=self._log_dir / face.log_name,
            )
            self._roots.append(pid)
            if primary is None:
                primary = face
                primary_pid = pid
        if primary is None or primary_pid is None:
            raise SupervisorError(f"{self.name} 没有配置启动命令")
        self._cache.clear()
        # 返回本次拉起的 pid，不是 _roots[0]：自愈重拉不走 stop()，_roots 里
        # 还留着上次崩溃的旧 pid——拿旧 pid 换绑账本会让恢复判定永远失败。
        return SpawnedProcess(
            pid=primary_pid,
            parent_pid=os.getpid(),
            cmdline=tuple(primary.argv),
        )

    def stop(self, pid: int | None = None, timeout: float | None = None) -> None:
        """优雅停：信号 + 整树终止；窗口内没停干净则适配器自己升级强杀。

        `pid` / `timeout` 是 QUOT-482-B 派单里的签名；引擎按 482-A 协议无参调用，
        这里两者都兼容。多面模块必须一次停干净，否则引擎的强杀只认主 PID，
        另一面的树会留孤儿。
        """
        del pid, timeout
        targets = self._stop_targets()
        if not targets:
            return
        for root in self._roots:
            self._signal_group(root)
        for target in targets:
            self._host.terminate_tree(target, force=False)
        if self._wait_quiet(self._graceful_wait):
            self._roots.clear()
            self._cache.clear()
            return
        for target in targets:
            self._host.terminate_tree(target, force=True)
        self._wait_quiet(self._readback_wait)
        self._roots.clear()
        self._cache.clear()

    def kill(self) -> None:
        for target in self._stop_targets():
            self._host.terminate_tree(target, force=True)
        self._roots.clear()
        self._cache.clear()

    def probe(self) -> bool:
        """端口在监听且 cmdline 命中；读不到证据一律 False（失败关闭）。"""
        return any(self.identify(port).matched for port in self.ports)

    # ------------------------------------------------------------------ 识别

    def identify(self, port: int | None = None) -> Identification | PortEvidence:
        if port is not None:
            return self._evidence(port)
        items = tuple(self._evidence(item) for item in self.ports)
        return Identification(module=self.name, matched=any(i.matched for i in items), ports=items)

    def _evidence(self, port: int, *, refresh: bool = False) -> PortEvidence:
        now = self._clock()
        cached = self._cache.get(port)
        if cached and not refresh and now - cached[0] < self._cache_sec:
            return cached[1]
        evidence = self._build_evidence(port)
        self._cache[port] = (now, evidence)
        return evidence

    def _build_evidence(self, port: int) -> PortEvidence:
        if not self._host.listening(port):
            return PortEvidence(port=port, listening=False)
        pid = self._host.owner_pid(port)
        if pid is None:
            return PortEvidence(port=port, listening=True, detail="端口在听但查不到 OwningProcess")
        chain = self._host.parent_chain(pid)
        snap = chain[0] if chain else self._host.snapshot(pid)
        if snap is None:
            return PortEvidence(port=port, listening=True, pid=pid, detail="查不到进程命令行")
        groups = self._match_tokens(port)
        matched_snap = self._match_chain(chain, groups) if self._inherit_chain(port) else None
        if matched_snap is None and any(self._matches(snap, tokens) for tokens in groups):
            matched_snap = snap
        return PortEvidence(
            port=port,
            listening=True,
            matched=matched_snap is not None,
            pid=pid,
            name=snap.name,
            cmdline=snap.cmdline,
            parent_chain=chain,
            matched_pid=matched_snap.pid if matched_snap is not None else None,
        )

    def _match_tokens(self, port: int) -> tuple[tuple[str, ...], ...]:
        for face in self.faces:
            if face.port == port:
                groups = [_norm_tokens(face.match)]
                if face.alt_match:
                    groups.append(_norm_tokens(face.alt_match))
                groups.extend(_norm_tokens(group) for group in face.alt_matches if group)
                return tuple(groups)
        return ()

    def _inherit_chain(self, port: int) -> bool:
        return any(face.port == port and face.inherit_chain for face in self.faces)

    @classmethod
    def _match_chain(
        cls, chain: Sequence[ProcessSnapshot], groups: Sequence[Sequence[str]]
    ) -> ProcessSnapshot | None:
        for snap in chain:
            for tokens in groups:
                if cls._matches(snap, tokens):
                    return snap
        return None

    @staticmethod
    def _matches(snap: ProcessSnapshot, tokens: Sequence[str]) -> bool:
        if not tokens:
            return False
        joined = snap.joined
        return all(token in joined for token in tokens)

    def _foreign_occupant(self) -> tuple[int, PortEvidence] | None:
        for port in self.ports:
            evidence = self._evidence(port, refresh=True)
            if evidence.listening and not evidence.matched:
                return port, evidence
        return None

    # ------------------------------------------------------------------ 内部

    def _stop_targets(self) -> list[int]:
        """要终止的进程：本 adapter 拉起过的根 + 端口上命中关键字的现行持有者。

        后者覆盖「supervisor 崩溃后重启（adopted）」与多面模块：账本只记得主
        PID，另一面的树必须按识别规则重新找回来。关键字不匹配的外部进程一律不碰。
        """
        targets: list[int] = []
        seen: set[int] = set()
        for root in self._roots:
            if root not in seen:
                seen.add(root)
                targets.append(root)
        for port in self.ports:
            evidence = self._evidence(port, refresh=True)
            if evidence.matched and evidence.pid is not None and evidence.pid not in seen:
                seen.add(evidence.pid)
                targets.append(evidence.pid)
        return targets

    def _wait_quiet(self, timeout: float) -> bool:
        deadline = self._clock() + timeout
        while True:
            self._cache.clear()
            if not any(self._host.listening(port) for port in self.ports):
                return True
            if self._clock() >= deadline:
                return False
            self._sleep(0.25)

    @staticmethod
    def _signal_group(pid: int) -> None:
        """给本进程组的控制台发 CTRL_BREAK（Python 服务把它当 SIGINT 优雅退出）。"""
        if os.name != "nt" or not hasattr(signal, "CTRL_BREAK_EVENT"):
            return
        try:
            os.kill(pid, signal.CTRL_BREAK_EVENT)
        except OSError:
            pass

    def _environment(self) -> dict[str, str]:
        env = dict(os.environ)
        for path in self._env_files:
            env.update(load_env_file(path))
        env.update(self._env_overrides)
        env.setdefault("PYTHONUNBUFFERED", "1")
        return env


# --------------------------------------------------------------------------- #
# 命令行型模块
# --------------------------------------------------------------------------- #


def _command_adapters(
    *,
    host: HostPivot,
    env: Mapping[str, str],
    log_dir: Path,
    evidence_cache_sec: float,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> dict[str, ModuleAdapter]:
    tickhub = default_peer_dir("tickhub", env)
    tickhub_us = default_peer_dir("tickhub_us", env)
    wire = default_peer_dir("wire", env)
    senate = default_peer_dir("senate", env)

    tickhub_env_files = _tickhub_env_files(env)
    return {
        "tickhub": BaseAdapter(
            "tickhub",
            MODULE_PORTS["tickhub"],
            (
                StartFace(
                    face="mcp",
                    argv=(
                        str(_venv_exe(tickhub, "th-cn.exe")),
                        "start-all",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        "8000",
                        "--crawler",
                        "on",
                    ),
                    cwd=tickhub,
                    port=8000,
                    match=("th-cn", "start-all"),
                    log_name="tickhub-mcp.log",
                ),
                StartFace(
                    face="admin",
                    argv=(
                        str(_venv_exe(tickhub, "th-cn.exe")),
                        "admin",
                        "serve",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        "8787",
                        "--force",
                    ),
                    cwd=tickhub,
                    port=8787,
                    match=("th-cn", "admin", "serve"),
                    log_name="tickhub-admin.log",
                ),
            ),
            host=host,
            env_files=tickhub_env_files,
            log_dir=log_dir,
            evidence_cache_sec=evidence_cache_sec,
            clock=clock,
            sleep=sleep,
        ),
        "tickhub_us": BaseAdapter(
            "tickhub_us",
            MODULE_PORTS["tickhub_us"],
            (
                StartFace(
                    face="mcp",
                    argv=(
                        str(_venv_exe(tickhub_us, "th-us.exe")),
                        "mcp",
                        "http",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        "8002",
                    ),
                    cwd=tickhub_us,
                    port=8002,
                    match=("th-us", "mcp", "http", "8002"),
                    log_name="tickhub-us-mcp.log",
                ),
            ),
            host=host,
            log_dir=log_dir,
            evidence_cache_sec=evidence_cache_sec,
            clock=clock,
            sleep=sleep,
        ),
        "wire": BaseAdapter(
            "wire",
            MODULE_PORTS["wire"],
            (
                StartFace(
                    face="web",
                    argv=(
                        str(_venv_exe(wire, "qfw.exe")),
                        "serve",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        "8790",
                    ),
                    cwd=wire,
                    port=8790,
                    match=("qfw", "serve"),
                    log_name="wire-web.log",
                ),
                StartFace(
                    face="mcp",
                    argv=(
                        str(_venv_exe(wire, "qfw.exe")),
                        "mcp",
                        "http",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        "8791",
                    ),
                    cwd=wire,
                    port=8791,
                    match=("qfw", "mcp", "http"),
                    log_name="wire-mcp.log",
                ),
            ),
            host=host,
            log_dir=log_dir,
            evidence_cache_sec=evidence_cache_sec,
            clock=clock,
            sleep=sleep,
        ),
        "senate": BaseAdapter(
            "senate",
            MODULE_PORTS["senate"],
            (
                StartFace(
                    face="mcp",
                    argv=(
                        str(_venv_exe(senate, "ssenate.exe")),
                        "mcp",
                        "http",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        "8765",
                    ),
                    cwd=senate,
                    port=8765,
                    match=("ssenate", "mcp", "http"),
                    log_name="senate-mcp.log",
                ),
                StartFace(
                    face="webui",
                    argv=(
                        str(_venv_exe(senate, "ssenate.exe")),
                        "webui",
                        "--host",
                        "127.0.0.1",
                        "--port",
                        "8786",
                    ),
                    cwd=senate,
                    port=8786,
                    match=("ssenate", "webui"),
                    log_name="senate-webui.log",
                ),
            ),
            host=host,
            log_dir=log_dir,
            evidence_cache_sec=evidence_cache_sec,
            clock=clock,
            sleep=sleep,
        ),
        # 看门狗（QUOT-589 重修）：无端口自愈监控器，不再监听 5952——probe/stop
        # 全按 cmdline 扫描（WatchdogAdapter）。被 supervisor 拉起后它只认
        # `--spawn-cmd` 直接拉起看板，不经 schtasks 复活（supervised 环境变量）。
        "watch": WatchdogAdapter(
            argv=(
                *launcher_entry(env),
                "watchdog",
                "--api",
                f"http://127.0.0.1:{panel_port_for_env(env)}",
                "--spawn-cmd",
                _panel_spawn_command(env),
            ),
            cwd=_ldst_repo_root(),
            host=host,
            log_dir=log_dir,
            env_overrides={
                "LDST_WATCHDOG_SUPERVISED": "1",
            },
            clock=clock,
            sleep=sleep,
        ),
    }


def rebase_adapter_ports(
    adapter: BaseAdapter, ports: tuple[int, ...]
) -> BaseAdapter:
    """按避让后的实际端口重建命令行型适配器（QUOT-594 端口自动避让）。

    `_command_adapters()` 构造时端口硬编码在 `StartFace.argv`（`--port NNNN`）
    与 `face.port` 里；避让只换基准表中同位置端口的字面值，argv 其余部分、
    匹配关键字里的端口号、env / 日志配置原样保留。face 顺序即端口表顺序
    （构造处保证），逐位替换。匹配关键字（match/alt_match 等）里内嵌的
    端口号是**基准端口**——避让后监听者 cmdline 用新端口，关键字仍能命中
    exe / 子命令片段（判定不依赖端口字面值），不影响认领与对账。
    """
    if len(ports) != len(adapter.faces):
        raise SupervisorError(
            f"{adapter.name} 避让端口数（{len(ports)}）与启动面数"
            f"（{len(adapter.faces)}）不符"
        )
    faces = []
    for face, port in zip(adapter.faces, ports, strict=True):
        old = str(face.port)
        new = str(port)
        argv = tuple(new if token == old else token for token in face.argv)
        faces.append(
            replace(
                face,
                argv=argv,
                port=port,
                match=tuple(_rebase_match(m, old, new) for m in face.match),
                alt_match=tuple(_rebase_match(m, old, new) for m in face.alt_match),
                alt_matches=tuple(
                    tuple(_rebase_match(m, old, new) for m in group)
                    for group in face.alt_matches
                ),
            )
        )
    return BaseAdapter(
        adapter.name,
        tuple(ports),
        faces,
        host=adapter._host,
        env_files=adapter._env_files,
        env_overrides=dict(adapter._env_overrides),
        log_dir=adapter._log_dir,
        evidence_cache_sec=adapter._cache_sec,
        graceful_wait_sec=adapter._graceful_wait,
        readback_wait_sec=adapter._readback_wait,
        clock=adapter._clock,
        sleep=adapter._sleep,
    )


def _rebase_match(token: str, old: str, new: str) -> str:
    """匹配关键字里的端口号同步替换（仅当片段整体是基准端口字面值）。"""
    return new if token == old.lower() else token


def _panel_spawn_command(env: Mapping[str, str]) -> str:
    """看门狗用的看板拉起命令（JSON 字符串数组；`ldst watchdog --spawn-cmd` 解析）。

    端口解析顺序（QUOT-589 第二轮 × QUOT-594 避让整合）：
    `LDST_PANEL_PORT`（digits 才生效）> ports.yaml 避让记忆（config_dir() 是
    安装根 scoped，test 安装根读自己的记录）> `panel_port(env)` 环境默认
    （prod=5950 / test=5951）。test 环境任何路径都不得回落到正式 5950。
    """
    return str(resolve_panel_port(env))


def resolve_panel_port(env: Mapping[str, str]) -> int:
    """看板实际端口单一真源：spawn-cmd `--port`、watchdog `--api`、自愈
    engine 的 panel_url 三处共用，保证避让后三方同一端口。"""
    from ladderstock.supervisor.modules import panel_port

    override = (env.get("LDST_PANEL_PORT") or "").strip()
    if override.isdigit():
        return int(override)
    try:
        from ladderstock.cli.paths import config_dir
        from ladderstock.supervisor.port_allocator import load_board_override

        remembered = load_board_override(config_dir())
    except Exception:  # noqa: BLE001 - 读配置失败回落环境默认
        remembered = None
    if remembered:
        return int(remembered)
    return panel_port(_env_name(env))
            )
        ],
        ensure_ascii=False,
    )


def _env_name(env: Mapping[str, str]) -> str | None:
    """从进程环境映射取 LDST_ENV 显式值；未设返回 None（走当前环境检测）。"""
    value = (env.get("LDST_ENV") or "").strip().lower()
    return value or None


def _tickhub_env_files(env: Mapping[str, str]) -> tuple[Path, ...]:
    override = (env.get(TICKHUB_ENV_FILES_ENV) or "").strip()
    if override:
        return tuple(Path(item) for item in override.split(os.pathsep) if item.strip())
    candidates = [
        Path.home() / ".tickhub" / "proxy.env",
        default_peer_dir("tickhub", env) / "ops" / "peer.env",
    ]
    return tuple(path for path in candidates if path.is_file())


# --------------------------------------------------------------------------- #
# financecoffer（WSL / docker compose）
# --------------------------------------------------------------------------- #


class FinanceCofferAdapter:
    """FinanceCoffer：服务在 WSL 容器里，Windows 侧只有 wslhost 端口转发。

    - 启动：先在 WSL 内 `docker compose up -d`（deploy/.env 缺失时退回 `docker start`），
      再拉一个 wsl.exe 锚进程（`exec -a fc-anchor sleep infinity`）作为账本 PID——容器
      PID 在 Windows 侧不可寻址，锚进程让「这次托管会话」有一个可对账的活进程，
      其 Linux 侧 argv[0] 固定为 fc-anchor，供跨进程停止时回收。
    - 停止：WSL 内 compose stop → docker stop → docker kill 逐层兜底，**判据是
      回读不到 financecoffer 容器**，不是命令返回 0；然后终止锚进程。
    - 识别：Windows 侧端口 → wslhost（只是转发器，不算归属证据）+ WSL 内
      compose project label 且 service=api 的容器在运行。
    """

    def __init__(
        self,
        *,
        peer_dir: Path,
        host: HostPivot | None = None,
        log_dir: Path | None = None,
        env: Mapping[str, str] | None = None,
        evidence_cache_sec: float = EVIDENCE_CACHE_SEC,
        readback_wait_sec: float = 15.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.name = "financecoffer"
        self.ports = MODULE_PORTS["financecoffer"]
        self._peer = peer_dir
        self._deploy = peer_dir / "deploy"
        self._host = host or default_host_pivot()
        self._log_dir = log_dir or _local_appdata_log_dir()
        self._env = os.environ if env is None else env
        self._cache_sec = float(evidence_cache_sec)
        self._readback_wait = float(readback_wait_sec)
        self._clock = clock
        self._sleep = sleep
        self._roots: list[int] = []
        self._cache: dict[int, tuple[float, PortEvidence]] = {}
        self._distro_cache: str | None = None
        self._fc_cache: tuple[float, str] = (0.0, "")

    # ------------------------------------------------------------------ 协议

    def start(self) -> SpawnedProcess:
        # Windows 侧冲突预检（QUOT-516）：端口被非本模块进程占住时拒绝启动，
        # 否则容器会在 WSL 内照常拉起，Windows 侧却永远连不上（静默假成功）。
        for port in self.ports:
            evidence = self._build_evidence(port)
            if evidence.listening and not evidence.matched:
                raise SupervisorError(
                    f"financecoffer 的端口 {port} 已被非本模块进程占用"
                    f"（pid={evidence.pid} cmd={' '.join(evidence.cmdline)[:160]}），拒绝启动"
                )
        distro = self.distro()
        command = self._start_script()
        result = self._wsl(distro, command, timeout=WSL_COMMAND_TIMEOUT_SEC)
        if not result.ok:
            raise SupervisorError(
                f"financecoffer 启动失败（wsl rc={result.returncode}）："
                f"{(result.stderr or result.stdout).strip()[:300]}"
            )
        anchor = (
            f"cd '{self._wsl_path(self._deploy)}' 2>/dev/null; exec -a fc-anchor sleep infinity"
        )
        argv = (
            "wsl.exe",
            "-d",
            distro,
            "-u",
            "root",
            # `-e` 必须显式给：缺省的 `--` 形态会先把命令交给默认 shell
            # 再二次展开，脚本里的 `$(docker ps -q ...)` 会被提前求值，
            # 多行容器 ID 变成独立命令行（实测 bash: line 2: <id>: command not found）。
            "-e",
            "bash",
            "-lc",
            anchor,
        )
        pid = self._host.spawn(
            argv,
            cwd=self._peer,
            env=dict(self._env),
            log_path=self._log_dir / "financecoffer-anchor.log",
        )
        self._roots.append(pid)
        self._cache.clear()
        self._fc_cache = (0.0, "")
        return SpawnedProcess(pid=pid, parent_pid=os.getpid(), cmdline=argv)

    def stop(self, pid: int | None = None, timeout: float | None = None) -> None:
        del pid, timeout
        distro = self.distro()
        self._wsl(distro, self._stop_script(), timeout=WSL_COMMAND_TIMEOUT_SEC)
        for root in self._roots:
            self._host.terminate_tree(root, force=False)
        self._roots.clear()
        self._wait_quiet(self._readback_wait)
        self._cache.clear()

    def kill(self) -> None:
        distro = self.distro()
        self._wsl(distro, self._kill_script(), timeout=WSL_COMMAND_TIMEOUT_SEC)
        for root in self._roots:
            self._host.terminate_tree(root, force=True)
        self._roots.clear()
        self._cache.clear()

    def probe(self) -> bool:
        return self.identify().matched

    # ------------------------------------------------------------------ 识别

    def identify(self, port: int | None = None) -> Identification | PortEvidence:
        if port is not None:
            return self._evidence(port)
        items = tuple(self._evidence(item) for item in self.ports)
        return Identification(module=self.name, matched=any(i.matched for i in items), ports=items)

    def _evidence(self, port: int) -> PortEvidence:
        now = self._clock()
        cached = self._cache.get(port)
        if cached and now - cached[0] < self._cache_sec:
            return cached[1]
        evidence = self._build_evidence(port)
        self._cache[port] = (now, evidence)
        return evidence

    def _build_evidence(self, port: int) -> PortEvidence:
        listening = self._host.listening(port)
        if not listening:
            return PortEvidence(port=port, listening=False, detail="Windows 侧端口未监听")
        pid = self._host.owner_pid(port)
        chain = self._host.parent_chain(pid) if pid else ()
        containers = self._fc_api_containers()
        matched = bool(containers)
        detail = (
            f"WSL 内 api 容器={containers or '无'}"
            if containers
            else "WSL 内没有 running 的 financecoffer api 容器（端口可能是残留转发）"
        )
        snap = chain[0] if chain else None
        return PortEvidence(
            port=port,
            listening=True,
            matched=matched,
            pid=pid,
            name=snap.name if snap else "",
            cmdline=snap.cmdline if snap else (),
            parent_chain=chain,
            detail=detail,
        )

    def distro(self) -> str:
        """WSL 默认发行版：LDST_WSL_DISTRO 优先，否则 `wsl -l -v` 里带 * 的那行。"""
        override = (self._env.get(WSL_DISTRO_ENV) or "").strip()
        if override:
            return override
        if self._distro_cache:
            return self._distro_cache
        result = self._host.run(("wsl.exe", "-l", "-v"), timeout=WSL_LIST_TIMEOUT_SEC)
        distro = _parse_default_distro(result.stdout)
        if not distro:
            fallback = self._host.run(("wsl.exe", "-l", "-q"), timeout=WSL_LIST_TIMEOUT_SEC)
            distro = next(
                (line.strip() for line in fallback.stdout.splitlines() if line.strip()), ""
            )
        if not distro:
            raise SupervisorError("探测不到 WSL 默认发行版（wsl -l -v 无输出）")
        self._distro_cache = distro
        return distro

    def _fc_api_containers(self) -> str:
        now = self._clock()
        stamp, value = self._fc_cache
        if now - stamp < self._cache_sec:
            return value
        script = (
            "docker ps --filter label=com.docker.compose.project=financecoffer "
            "--filter label=com.docker.compose.service=api --format '{{.Names}}'"
        )
        try:
            result = self._wsl(self.distro(), script, timeout=WSL_LIST_TIMEOUT_SEC)
        except SupervisorError:
            result = CommandResult(returncode=-1)
        names = " ".join(line.strip() for line in result.stdout.splitlines() if line.strip())
        self._fc_cache = (now, names)
        return names

    # ------------------------------------------------------------------ 内部

    def _wsl(self, distro: str, script: str, *, timeout: float) -> CommandResult:
        return self._host.run(
            ("wsl.exe", "-d", distro, "-u", "root", "-e", "bash", "-lc", script),
            timeout=timeout,
        )

    @staticmethod
    def _wsl_path(path: Path) -> str:
        text = str(path).replace("\\", "/")
        if len(text) >= 2 and text[1] == ":":
            text = f"/mnt/{text[0].lower()}{text[2:]}"
        return text

    def _start_script(self) -> str:
        deploy = self._wsl_path(self._deploy)
        return (
            "set -u; "
            f"DEPLOY='{deploy}'; "
            'if [ -f "$DEPLOY/.env" ] && [ -f "$DEPLOY/compose.yaml" ]; then '
            'cd "$DEPLOY" && docker compose --env-file .env -f compose.yaml up -d; '
            "else "
            "ids=$(docker ps -aq --filter label=com.docker.compose.project=financecoffer); "
            '[ -n "$ids" ] || { echo "no deploy/.env and no financecoffer containers" >&2; '
            "exit 1; }; "
            "docker start $ids >/dev/null; "
            "fi"
        )

    def _stop_script(self) -> str:
        deploy = self._wsl_path(self._deploy)
        filter_expr = "label=com.docker.compose.project=financecoffer"
        return (
            "set -u; "
            # 锚进程跨 supervisor 重启后拿不到 pid：锚在 WSL 侧 argv[0] 是
            # fc-anchor，停容器前先由 Linux 侧收掉，wsl.exe 随之退出。
            "pkill -f '^fc-anchor' 2>/dev/null || true; "
            f"DEPLOY='{deploy}'; "
            'if [ -f "$DEPLOY/.env" ] && [ -f "$DEPLOY/compose.yaml" ]; then '
            'cd "$DEPLOY" && docker compose --env-file .env -f compose.yaml stop -t 20; '
            "fi; "
            f"ids=$(docker ps -q --filter {filter_expr}); "
            '[ -n "$ids" ] && echo "$ids" | xargs -r docker stop -t 20; '
            f"remain=$(docker ps -q --filter {filter_expr}); "
            '[ -n "$remain" ] && echo "$remain" | xargs -r docker kill; '
            f"after=$(docker ps -q --filter {filter_expr}); "
            'if [ -z "$after" ]; then echo FC_STOPPED=yes; else '
            "echo FC_STOPPED=no; exit 1; fi"
        )

    def _kill_script(self) -> str:
        return (
            "set -u; "
            "pkill -f '^fc-anchor' 2>/dev/null || true; "
            "ids=$(docker ps -q --filter label=com.docker.compose.project=financecoffer); "
            '[ -n "$ids" ] && echo "$ids" | xargs -r docker kill'
        )

    def _wait_quiet(self, timeout: float) -> bool:
        deadline = self._clock() + timeout
        while True:
            self._cache.clear()
            self._fc_cache = (0.0, "")
            if not any(self._host.listening(port) for port in self.ports):
                return True
            if self._clock() >= deadline:
                return False
            self._sleep(0.25)


def _parse_default_distro(output: str) -> str:
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("*"):
            return stripped[1:].strip().split()[0] if stripped[1:].strip() else ""
    return ""


# --------------------------------------------------------------------------- #
# proxyfleet（SYSTEM 计划任务托管）
# --------------------------------------------------------------------------- #


class ProxyFleetAdapter:
    """ProxyFleet：只通过已存在的同名计划任务启停，绝不改运行身份。

    启动 = `/change /enable` + `/run`（任务账户仍是 SYSTEM）；
    停止 = 先 `/change /disable`（压住 1 分钟自愈触发）再 `/end`；
    强杀兜底同样只走计划任务，不直接 taskkill 它的进程。
    """

    def __init__(
        self,
        *,
        host: HostPivot | None = None,
        task_name: str = DEFAULT_PROXYFLEET_TASK,
        evidence_cache_sec: float = EVIDENCE_CACHE_SEC,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.name = "proxyfleet"
        self.ports = MODULE_PORTS["proxyfleet"]
        self.task_name = task_name
        self._host = host or default_host_pivot()
        self._cache_sec = float(evidence_cache_sec)
        self._clock = clock
        self._sleep = sleep
        self._cache: dict[int, tuple[float, PortEvidence]] = {}

    # ------------------------------------------------------------------ 协议

    def start(self) -> SpawnedProcess:
        # 冲突预检（QUOT-516）：占口时拒绝启动，绝不先触发任务再等超时装死——
        # 那样报错文案不含占用信息，且任务已被 enable（自愈触发会接着捣乱）。
        for port in self.ports:
            evidence = self._build_evidence(port)
            if evidence.listening and not evidence.matched:
                raise SupervisorError(
                    f"proxyfleet 的端口 {port} 已被非本模块进程占用"
                    f"（pid={evidence.pid} cmd={' '.join(evidence.cmdline)[:160]}），拒绝启动"
                )
        changed = self._host.run(
            ("schtasks.exe", "/change", "/tn", self.task_name, "/enable"),
            timeout=QUERY_TIMEOUT_SEC,
        )
        if not changed.ok:
            raise SupervisorError(
                f"ProxyFleet 计划任务 {self.task_name} 启用失败："
                f"{(changed.stderr or changed.stdout).strip()[:200]}"
            )
        run = self._host.run(
            ("schtasks.exe", "/run", "/tn", self.task_name),
            timeout=QUERY_TIMEOUT_SEC,
        )
        # 267011 = 任务正在运行；对「启动」而言已是目标态
        if not run.ok and "267011" not in f"{run.stdout}{run.stderr}":
            raise SupervisorError(
                f"ProxyFleet 计划任务 {self.task_name} 启动失败："
                f"{(run.stderr or run.stdout).strip()[:200]}"
            )
        snapshot = self._await_listener(SPAWN_WAIT_SEC)
        if snapshot is None:
            raise SupervisorError(
                f"ProxyFleet 计划任务已触发，但 {SPAWN_WAIT_SEC:.0f}s 内端口 {self.ports} 未监听"
            )
        self._cache.clear()
        return SpawnedProcess(
            pid=snapshot.pid,
            parent_pid=snapshot.parent_pid,
            cmdline=snapshot.cmdline,
        )

    def stop(self, pid: int | None = None, timeout: float | None = None) -> None:
        del pid, timeout
        self._host.run(
            ("schtasks.exe", "/change", "/tn", self.task_name, "/disable"),
            timeout=QUERY_TIMEOUT_SEC,
        )
        self._host.run(
            ("schtasks.exe", "/end", "/tn", self.task_name),
            timeout=QUERY_TIMEOUT_SEC,
        )
        self._cache.clear()

    def kill(self) -> None:
        self._host.run(
            ("schtasks.exe", "/end", "/tn", self.task_name),
            timeout=QUERY_TIMEOUT_SEC,
        )
        self._cache.clear()

    def probe(self) -> bool:
        return self.identify().matched

    # ------------------------------------------------------------------ 识别

    def identify(self, port: int | None = None) -> Identification | PortEvidence:
        if port is not None:
            return self._evidence(port)
        items = tuple(self._evidence(item) for item in self.ports)
        return Identification(module=self.name, matched=any(i.matched for i in items), ports=items)

    def _evidence(self, port: int) -> PortEvidence:
        now = self._clock()
        cached = self._cache.get(port)
        if cached and now - cached[0] < self._cache_sec:
            return cached[1]
        evidence = self._build_evidence(port)
        self._cache[port] = (now, evidence)
        return evidence

    def _build_evidence(self, port: int) -> PortEvidence:
        if not self._host.listening(port):
            return PortEvidence(port=port, listening=False)
        pid = self._host.owner_pid(port)
        if pid is None:
            return PortEvidence(port=port, listening=True, detail="端口在听但查不到 OwningProcess")
        chain = self._host.parent_chain(pid)
        snap = chain[0] if chain else self._host.snapshot(pid)
        if snap is None:
            return PortEvidence(port=port, listening=True, pid=pid, detail="查不到进程命令行")
        matched = all(token in snap.joined for token in ("pfctl.py", "run"))
        return PortEvidence(
            port=port,
            listening=True,
            matched=matched,
            pid=pid,
            name=snap.name,
            cmdline=snap.cmdline,
            parent_chain=chain,
        )

    def _await_listener(self, timeout: float) -> ProcessSnapshot | None:
        deadline = self._clock() + timeout
        while True:
            for port in self.ports:
                evidence = self._build_evidence(port)
                if evidence.matched and evidence.pid is not None:
                    chain = evidence.parent_chain
                    if chain:
                        return chain[0]
                    return (
                        self._host.snapshot(evidence.pid)
                        if hasattr(self._host, "snapshot")
                        else None
                    )
            if self._clock() >= deadline:
                return None
            self._sleep(0.5)


# --------------------------------------------------------------------------- #
# proxypool（按需 TCP 直通转发池，QUOT-506）
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ProxyPoolMapping:
    """一条端口映射（配置里的 `Mappings[]` 元素）。"""

    mapping_id: str
    listen_port: int
    upstream_host: str
    upstream_port: int
    enabled: bool
    owner: str = ""


@dataclass(frozen=True)
class ProxyPoolConfig:
    """宿主配置里 proxypool 模块段（`LdstHostConfig.psd1` → Modules）。"""

    enabled: bool = False
    #: 只作为配置声明落盘：实现里没有任何读取后自动拉起的路径（默认不启动）
    auto_start: bool = False
    listen_host: str = "127.0.0.1"
    mappings: tuple[ProxyPoolMapping, ...] = ()
    needs_human_on_limit: bool = True

    def enabled_mappings(self) -> tuple[ProxyPoolMapping, ...]:
        return tuple(item for item in self.mappings if item.enabled)


@dataclass(frozen=True)
class ProxyPoolMappingState:
    """一次 start() 的逐映射归属结论（managed = 本次拉起，adopted = 接管既有转发进程）。"""

    mapping_id: str
    listen_port: int
    ownership: str
    pid: int | None = None


@dataclass(frozen=True)
class _MappingProbe:
    evidence: PortEvidence
    connectable: bool


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("true", "1", "yes"):
            return True
        if text in ("false", "0", "no", ""):
            return False
        return default
    return default


def _row_lookup(row: Mapping[str, Any]) -> dict[str, Any]:
    """psd1 → JSON 的键大小写不保证，统一按小写取。"""
    return {str(key).lower(): value for key, value in row.items()}


def _config_from_row(row: Mapping[str, Any]) -> ProxyPoolConfig:
    fields = _row_lookup(row)
    raw_mappings = fields.get("mappings") or []
    if isinstance(raw_mappings, Mapping):
        raw_mappings = [raw_mappings]
    rows = raw_mappings if isinstance(raw_mappings, list) else []
    mappings: list[ProxyPoolMapping] = []
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        item = _row_lookup(raw)
        try:
            listen_port = int(item.get("listen") or 0)
            upstream_port = int(item.get("upstreamport") or 0)
        except (TypeError, ValueError):
            continue
        mapping_id = str(item.get("id") or "").strip()
        upstream_host = str(item.get("upstreamhost") or "").strip()
        if not mapping_id or not upstream_host:
            continue
        if not 0 < listen_port <= 65535 or not 0 < upstream_port <= 65535:
            continue
        mappings.append(
            ProxyPoolMapping(
                mapping_id=mapping_id,
                listen_port=listen_port,
                upstream_host=upstream_host,
                upstream_port=upstream_port,
                enabled=_as_bool(item.get("enabled"), False),
                owner=str(item.get("owner") or "").strip(),
            )
        )
    return ProxyPoolConfig(
        enabled=_as_bool(fields.get("enabled"), False),
        auto_start=_as_bool(fields.get("autostart"), False),
        listen_host=str(fields.get("listenhost") or "127.0.0.1").strip(),
        mappings=tuple(mappings),
        needs_human_on_limit=_as_bool(fields.get("needshumanonlimit"), True),
    )


def load_proxypool_config(*, host: HostPivot, path: Path) -> ProxyPoolConfig:
    """读 `LdstHostConfig.psd1` 的 proxypool 段；读不到一律按「未启用、无映射」处理。

    配置文件是单一事实来源（QUOT-483），这里只读不改；解析走
    `Import-PowerShellDataFile`（psd1 不是 JSON，Python 侧不做伪解析）。
    """
    escaped = str(path).replace("'", "''")
    script = (
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
        f"$cfg=Import-PowerShellDataFile -LiteralPath '{escaped}' -ErrorAction Stop;"
        "$m=@($cfg.Modules) | Where-Object { [string]$_.Name -eq 'proxypool' } | "
        "Select-Object -First 1;"
        "if ($m) { $m | ConvertTo-Json -Depth 8 -Compress }"
    )
    result = host.run(
        ("powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script),
        timeout=QUERY_TIMEOUT_SEC,
    )
    if not result.ok or not result.stdout.strip():
        return ProxyPoolConfig()
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return ProxyPoolConfig()
    if isinstance(payload, list):
        if not payload:
            return ProxyPoolConfig()
        payload = payload[0]
    if not isinstance(payload, Mapping):
        return ProxyPoolConfig()
    return _config_from_row(payload)


class ProxyPoolAdapter:
    """代理池：可被按需拉起的 TCP 直通转发层（QUOT-506）。

    - **默认不启动**：只登记在 `ON_DEMAND_MODULES`，不在 `MODULE_ORDER`（全启路径）里；
      构造不拉起任何进程，`Enabled` / `AutoStart` 只落配置，代码无自启分支。
    - **一个模块管 N 条映射**：`start()` 一次拉起全部 `Enabled` 映射（每条一个转发
      进程），返回首个进程供账本记录；`stop()` / `kill()` 停整池。
    - **probe 双向判据**：端口监听表有持有者 **且** 对该端口真实 TCP 建连成功
      **且** 持有着 cmdline 命中本模块转发标记——只查端口在听不算数。
    - **端口冲突直接报错**：与其它模块端口表冲突、映射间重复、或被非本模块进程
      占用时，`start()` 抛出含端口号的 `SupervisorError`，绝不静默换端口。
    - **归属**：本次拉起的记 `managed`；端口上已有同 Id 的转发进程（前任 supervisor
      的残留）记 `adopted` 并在 `adopted` / `identify()` 里报出，不再拉第二条也
      不强杀；外部进程一律拒绝启动。
    - **停止判据**：回读端口不可连接 **且** 按 cmdline 关键字扫描无残留。
    """

    name = "proxypool"

    def __init__(
        self,
        *,
        config: ProxyPoolConfig,
        host: HostPivot | None = None,
        log_dir: Path | None = None,
        evidence_cache_sec: float = EVIDENCE_CACHE_SEC,
        spawn_wait_sec: float = PROXYPOOL_SPAWN_WAIT_SEC,
        graceful_wait_sec: float = GRACEFUL_STOP_WAIT_SEC,
        readback_wait_sec: float = PROXYPOOL_READBACK_WAIT_SEC,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.ports: tuple[int, ...] = tuple(item.listen_port for item in config.enabled_mappings())
        self._host = host or default_host_pivot()
        self._log_dir = log_dir or _local_appdata_log_dir()
        self._cache_sec = float(evidence_cache_sec)
        self._spawn_wait = float(spawn_wait_sec)
        self._graceful_wait = float(graceful_wait_sec)
        self._readback_wait = float(readback_wait_sec)
        self._clock = clock
        self._sleep = sleep
        self._roots: list[int] = []
        self._started_ports: set[int] = set()
        self._cache: dict[int, tuple[float, _MappingProbe]] = {}
        #: 最近一次 start() 里接管（而非拉起）的映射，调用方据此上报归属
        self.adopted: tuple[ProxyPoolMappingState, ...] = ()
        #: 其它顺序模块的静态端口表（冲突判据之一）
        self._static_ports: dict[int, str] = {
            port: name
            for name, ports in MODULE_PORTS.items()
            if name != self.name
            for port in ports
        }

    # ------------------------------------------------------------------ 协议

    def start(self) -> SpawnedProcess:
        mappings = self.config.enabled_mappings()
        if not mappings:
            raise SupervisorError(
                "proxypool 没有启用的映射（Mappings 里 Enabled=$true 为空），不拉起任何转发进程"
            )
        if self.config.listen_host.strip().lower() not in LOOPBACK_HOSTS:
            raise SupervisorError(
                f"proxypool 的 ListenHost={self.config.listen_host!r} 不是回环地址，拒绝监听"
            )
        self._assert_no_conflicts(mappings)

        self._roots = []
        self._started_ports = set()
        adopted: list[ProxyPoolMappingState] = []
        plans: list[ProxyPoolMapping] = []
        for mapping in mappings:
            pid = self._host.owner_pid(mapping.listen_port)
            snap = self._snapshot(pid) if pid is not None else None
            if snap is not None and self._is_forwarder(snap, mapping.mapping_id):
                adopted.append(
                    ProxyPoolMappingState(
                        mapping_id=mapping.mapping_id,
                        listen_port=mapping.listen_port,
                        ownership="adopted",
                        pid=pid,
                    )
                )
                self._started_ports.add(mapping.listen_port)
                continue
            plans.append(mapping)
        self.adopted = tuple(adopted)
        self._cache.clear()

        spawned: list[tuple[ProxyPoolMapping, tuple[str, ...], int]] = []
        for mapping in plans:
            argv = self._forwarder_argv(mapping)
            pid = self._host.spawn(
                argv,
                cwd=(
                    runtime.app_dir()
                    if runtime.is_frozen()
                    else Path(__file__).resolve().parent
                ),
                env=self._environment(),
                log_path=self._log_dir / f"proxypool-{_safe_log_name(mapping.mapping_id)}.log",
            )
            self._roots.append(pid)
            self._started_ports.add(mapping.listen_port)
            spawned.append((mapping, argv, pid))

        if spawned:
            self._await_ready(mappings)
            _mapping, primary_argv, primary_pid = spawned[0]
            return SpawnedProcess(pid=primary_pid, parent_pid=os.getpid(), cmdline=primary_argv)

        if adopted and adopted[0].pid is not None:
            snap = self._snapshot(adopted[0].pid)
            if snap is not None:
                return SpawnedProcess(
                    pid=snap.pid, parent_pid=snap.parent_pid, cmdline=snap.cmdline
                )
        raise SupervisorError("proxypool 的映射在接管前消失，未拉起也未接管任何转发进程")

    def stop(self, pid: int | None = None, timeout: float | None = None) -> None:
        """停整池：优雅停 → 超时整树强杀 → 回读端口不可连接 + 无残留进程。"""
        del pid, timeout
        ports = set(self._started_ports)
        targets = self._stop_targets()
        for mapping in self.config.mappings:
            probe = self._probe_mapping(mapping, refresh=True)
            if probe.evidence.matched:
                ports.add(mapping.listen_port)
                owner = probe.evidence.pid
                if owner is not None and owner not in targets:
                    targets.append(owner)
        for root in self._roots:
            BaseAdapter._signal_group(root)
        for target in targets:
            self._host.terminate_tree(target, force=False)
        if not self._wait_closed(ports, self._graceful_wait):
            for target in targets:
                self._host.terminate_tree(target, force=True)
            self._wait_closed(ports, self._readback_wait)
        self._reset()

        residual = self._residual_processes()
        if residual:
            listed = ", ".join(f"{item.pid}:{item.name}" for item in residual)
            raise SupervisorError(f"proxypool 停止后仍有残留转发进程：{listed}")
        open_ports = sorted(port for port in ports if self._connectable(port))
        if open_ports:
            raise SupervisorError(f"proxypool 停止后端口仍可连接：{open_ports}")

    def kill(self) -> None:
        for target in self._stop_targets():
            self._host.terminate_tree(target, force=True)
        self._reset()

    def probe(self) -> bool:
        """双向判据：端口在听且真实 TCP 建连成功且 cmdline 命中（失败关闭）。"""
        mappings = self.config.enabled_mappings()
        if not mappings:
            return False
        return all(self._healthy(self._probe_mapping(mapping)) for mapping in mappings)

    # ------------------------------------------------------------------ 识别

    def identify(self, port: int | None = None) -> Identification | PortEvidence:
        if port is not None:
            mapping = next(
                (item for item in self.config.enabled_mappings() if item.listen_port == port),
                None,
            )
            if mapping is None:
                return PortEvidence(port=port, listening=False, detail="没有配置该端口的映射")
            return self._probe_mapping(mapping).evidence
        items = tuple(
            self._probe_mapping(mapping).evidence for mapping in self.config.enabled_mappings()
        )
        return Identification(
            module=self.name, matched=any(item.matched for item in items), ports=items
        )

    def _healthy(self, probe: _MappingProbe) -> bool:
        return probe.evidence.listening and probe.connectable and probe.evidence.matched

    def _probe_mapping(self, mapping: ProxyPoolMapping, *, refresh: bool = False) -> _MappingProbe:
        now = self._clock()
        cached = self._cache.get(mapping.listen_port)
        if cached and not refresh and now - cached[0] < self._cache_sec:
            return cached[1]
        pid = self._host.owner_pid(mapping.listen_port)
        if pid is None:
            probe = _MappingProbe(
                PortEvidence(port=mapping.listen_port, listening=False), connectable=False
            )
        else:
            connectable = self._connectable(mapping.listen_port)
            chain = self._host.parent_chain(pid)
            snap = chain[0] if chain else self._host.snapshot(pid)
            matched = snap is not None and self._is_forwarder(snap, mapping.mapping_id)
            probe = _MappingProbe(
                PortEvidence(
                    port=mapping.listen_port,
                    listening=True,
                    matched=matched,
                    pid=pid,
                    name=snap.name if snap else "",
                    cmdline=snap.cmdline if snap else (),
                    parent_chain=chain,
                    detail=(
                        f"映射 {mapping.mapping_id}；TCP 建连{'成功' if connectable else '失败'}"
                    ),
                ),
                connectable=connectable,
            )
        self._cache[mapping.listen_port] = (now, probe)
        return probe

    @staticmethod
    def _is_forwarder(snap: ProcessSnapshot, mapping_id: str) -> bool:
        tokens = _norm_tokens((PROXYPOOL_FORWARDER_MARKER, "--id", mapping_id))
        joined = snap.joined
        return all(token in joined for token in tokens)

    def _snapshot(self, pid: int) -> ProcessSnapshot | None:
        chain = self._host.parent_chain(pid)
        return chain[0] if chain else self._host.snapshot(pid)

    def _connectable(self, port: int) -> bool:
        return self._host.handshake(self.config.listen_host, port)

    def _stop_targets(self) -> list[int]:
        targets: list[int] = []
        seen: set[int] = set()
        for root in self._roots:
            if root not in seen:
                seen.add(root)
                targets.append(root)
        for mapping in self.config.mappings:
            probe = self._probe_mapping(mapping, refresh=True)
            pid = probe.evidence.pid
            if probe.evidence.matched and pid is not None and pid not in seen:
                seen.add(pid)
                targets.append(pid)
        return targets

    def _residual_processes(self) -> tuple[ProcessSnapshot, ...]:
        if not self.config.mappings:
            return ()
        found = self._host.find_processes(PROXYPOOL_FORWARDER_MARKER)
        return tuple(
            snap
            for snap in found
            if any(self._is_forwarder(snap, item.mapping_id) for item in self.config.mappings)
        )

    # ------------------------------------------------------------------ 内部

    def _assert_no_conflicts(self, mappings: Sequence[ProxyPoolMapping]) -> None:
        seen: dict[int, str] = {}
        for mapping in mappings:
            port = mapping.listen_port
            if port in seen:
                raise SupervisorError(
                    f"proxypool 的映射 {seen[port]!r} 与 {mapping.mapping_id!r} 都监听端口 "
                    f"{port}，拒绝启动（不换端口）"
                )
            seen[port] = mapping.mapping_id
            module = self._static_ports.get(port)
            if module is not None:
                raise SupervisorError(
                    f"proxypool 映射 {mapping.mapping_id!r} 的监听端口 {port} 与模块 "
                    f"{module} 的端口表冲突，拒绝启动（不换端口）"
                )
            pid = self._host.owner_pid(port)
            if pid is None:
                continue
            snap = self._snapshot(pid)
            if snap is not None and self._is_forwarder(snap, mapping.mapping_id):
                continue
            command = " ".join(snap.cmdline)[:160] if snap else "（查不到命令行）"
            raise SupervisorError(
                f"proxypool 映射 {mapping.mapping_id!r} 的监听端口 {port} 已被非本模块"
                f"进程占用（pid={pid} cmd={command}），拒绝启动（不换端口）"
            )

    def _await_ready(self, mappings: Sequence[ProxyPoolMapping]) -> None:
        deadline = self._clock() + self._spawn_wait
        while True:
            pending = [
                mapping
                for mapping in mappings
                if not self._healthy(self._probe_mapping(mapping, refresh=True))
            ]
            if not pending:
                return
            if self._clock() >= deadline:
                listed = ", ".join(f"{item.listen_port}({item.mapping_id})" for item in pending)
                self.kill()
                raise SupervisorError(
                    f"proxypool 端口 {listed} 在 {self._spawn_wait:.0f}s 内未就绪"
                    "（转发进程可能绑定失败），已终止半成品"
                )
            self._sleep(0.1)

    def _wait_closed(self, ports: Sequence[int] | set[int], timeout: float) -> bool:
        deadline = self._clock() + timeout
        while True:
            self._cache.clear()
            if not any(self._connectable(port) for port in ports):
                return True
            if self._clock() >= deadline:
                return False
            self._sleep(0.25)

    def _reset(self) -> None:
        self._roots = []
        self._started_ports = set()
        self.adopted = ()
        self._cache.clear()

    def _forwarder_argv(self, mapping: ProxyPoolMapping) -> tuple[str, ...]:
        # 冻结态没有可执行的 .py，改用自身 exe 的隐藏子命令；命令名保留
        # `proxypool_forwarder` 关键字，probe/stop 的识别口径不变（QUOT-525）。
        entry = (
            (str(runtime.executable()), "proxypool_forwarder")
            if runtime.is_frozen()
            else (
                sys.executable,
                str(Path(__file__).with_name("proxypool_forwarder.py")),
            )
        )
        return (
            *entry,
            "--id",
            mapping.mapping_id,
            "--listen-host",
            self.config.listen_host,
            "--listen-port",
            str(mapping.listen_port),
            "--upstream-host",
            mapping.upstream_host,
            "--upstream-port",
            str(mapping.upstream_port),
        )

    @staticmethod
    def _environment() -> dict[str, str]:
        env = dict(os.environ)
        env.setdefault("PYTHONUNBUFFERED", "1")
        return env


def _safe_log_name(mapping_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", mapping_id)[:64] or "mapping"


# --------------------------------------------------------------------------- #
# 组装
# --------------------------------------------------------------------------- #


def _host_config_path(env: Mapping[str, str]) -> Path:
    override = (env.get(HOST_CONFIG_ENV) or "").strip()
    if override:
        return Path(override)
    if runtime.is_frozen():
        # 部署侧可在 exe 同目录带一份 ops/host/ 配置；bundle 里带了也认。
        for base in (runtime.app_dir(), runtime.bundle_root()):
            candidate = base / HOST_CONFIG_REL
            if candidate.is_file():
                return candidate
        return runtime.app_dir() / HOST_CONFIG_REL
    return Path(__file__).resolve().parents[3] / HOST_CONFIG_REL


class WatchdogAdapter:
    """无端口看门狗适配器（QUOT-589 重修）：probe/start/stop 全按 cmdline 扫描。

    用户裁决全机只留 5950/5951 两个端口后，看门狗不再监听 5952——BaseAdapter
    的「端口在听 + cmdline 命中」证据链对它不成立。这里改用 `find_processes`
    按识别关键字整机扫描（与 ProxyPoolAdapter 转发器同一手法）：

    - probe：扫描命中本 CLI 的 watchdog 子命令 → True；失败关闭。
    - stop/kill：拉起过的根 + 扫描命中的现行持有者，整树终止。
    - 识别关键字与原 StartFace 相同的三形态（venv/打包），只去掉 `--port`。
    """

    MARKER = "watchdog"

    def __init__(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path,
        host: HostPivot | None = None,
        log_dir: Path | None = None,
        env_overrides: Mapping[str, str] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.name = "watch"
        self.ports: tuple[int, ...] = ()
        self._argv = argv
        self._cwd = cwd
        self._host = host or default_host_pivot()
        self._log_dir = log_dir or _local_appdata_log_dir()
        self._env_overrides = dict(env_overrides or {})
        self._clock = clock
        self._sleep = sleep
        self._roots: list[int] = []

    # ------------------------------------------------------------------ 协议

    def start(self) -> SpawnedProcess:
        pid = self._host.spawn(
            self._argv,
            cwd=self._cwd,
            env=self._environment(),
            log_path=self._log_dir / "watchdog.log",
        )
        self._roots.append(pid)
        return SpawnedProcess(pid=pid, parent_pid=os.getpid(), cmdline=self._argv)

    def stop(self, pid: int | None = None, timeout: float | None = None) -> None:
        del pid, timeout
        targets = self._stop_targets()
        for root in self._roots:
            self._host.terminate_tree(root, force=False)
        if not self._wait_gone(targets, GRACEFUL_STOP_WAIT_SEC):
            for target in targets:
                self._host.terminate_tree(target, force=True)
            self._wait_gone(targets, STOP_READBACK_WAIT_SEC)
        self._roots.clear()

    def kill(self) -> None:
        for target in self._stop_targets():
            self._host.terminate_tree(target, force=True)
        self._roots.clear()

    def probe(self) -> bool:
        return bool(self._find())

    def identify(self, port: int | None = None) -> Identification:
        del port  # 无端口模块；识别只按 cmdline
        found = self._find()
        return Identification(module=self.name, matched=bool(found), ports=())

    # ------------------------------------------------------------------ 识别

    def _matches(self, snap: ProcessSnapshot) -> bool:
        joined = snap.joined
        return (
            ("ladderstock.cli.main" in joined and self.MARKER in joined)
            or ("ldst" in joined and self.MARKER in joined)
            or ("ladderstock" in joined and self.MARKER in joined)
        )

    def _find(self) -> tuple[ProcessSnapshot, ...]:
        try:
            found = self._host.find_processes(self.MARKER)
        except (OSError, subprocess.SubprocessError):
            return ()
        return tuple(snap for snap in found if self._matches(snap))

    def _stop_targets(self) -> list[int]:
        targets: list[int] = []
        seen: set[int] = set()
        for root in self._roots:
            if root not in seen:
                seen.add(root)
                targets.append(root)
        for snap in self._find():
            if snap.pid not in seen:
                seen.add(snap.pid)
                targets.append(snap.pid)
        return targets

    def _wait_gone(self, targets: list[int], timeout: float) -> bool:
        deadline = self._clock() + timeout
        while True:
            alive = [pid for pid in targets if self._host.snapshot(pid) is not None]
            if not alive:
                return True
            if self._clock() >= deadline:
                return False
            self._sleep(0.1)

    def _environment(self) -> dict[str, str]:
        values = dict(os.environ)
        values.update(self._env_overrides)
        return values


def build_default_adapters(
    *,
    env: Mapping[str, str] | None = None,
    log_dir: Path | None = None,
    host: HostPivot | None = None,
    evidence_cache_sec: float = EVIDENCE_CACHE_SEC,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, ModuleAdapter]:
    """生产装配：七个顺序模块（含 watch 看门狗）+ 按需模块 proxypool。

    键集合 = `MODULE_ORDER` ∪ `ON_DEMAND_MODULES`；proxypool 不在启动顺序里，
    只能由单模块入口按需拉起（默认不启动）。

    路径全部 bundle 相对（QUOT-523）：peer 目录 = `LDST_PEER_DIR_<MODULE>` >
    `LDST_PEER_ROOT/<peer>` > `<安装根>/<peer>`（`install_root()` 见下）；
    看板/看门狗启动器 = `LDST_LAUNCHER` > `<peer 根>/LadderStock/.venv/.../ldst` >
    当前解释器。不再有 `D:\\workplace` 这类写死兜底。WSL 发行版与 ProxyFleet
    任务名仍可用环境变量覆盖；proxypool 的映射走 `LDST_HOST_CONFIG`（默认仓库内
    `ops/host/LdstHostConfig.psd1`）。缺目录不会静默：`preflight()` 逐项报出。
    """
    values = os.environ if env is None else env
    pivot = host or default_host_pivot()
    logs = log_dir or _local_appdata_log_dir()

    adapters: dict[str, ModuleAdapter] = dict(
        _command_adapters(
            host=pivot,
            env=values,
            log_dir=logs,
            evidence_cache_sec=evidence_cache_sec,
            clock=clock,
            sleep=sleep,
        )
    )
    adapters["financecoffer"] = FinanceCofferAdapter(
        peer_dir=default_peer_dir("financecoffer", values),
        host=pivot,
        log_dir=logs,
        env=values,
        evidence_cache_sec=evidence_cache_sec,
        clock=clock,
        sleep=sleep,
    )
    adapters["proxyfleet"] = ProxyFleetAdapter(
        host=pivot,
        task_name=(values.get(PROXYFLEET_TASK_ENV) or DEFAULT_PROXYFLEET_TASK).strip(),
        evidence_cache_sec=evidence_cache_sec,
        clock=clock,
        sleep=sleep,
    )
    adapters["proxypool"] = ProxyPoolAdapter(
        config=load_proxypool_config(host=pivot, path=_host_config_path(values)),
        host=pivot,
        log_dir=logs,
        evidence_cache_sec=evidence_cache_sec,
        clock=clock,
        sleep=sleep,
    )
    return module_registry(adapters)
