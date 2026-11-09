"""六模块生命周期适配器（QUOT-482-B）：启动 / 停止 / 探活 / 进程识别。

实现 `supervisor.adapters.ModuleAdapter`（QUOT-482-A 的协议），不碰内核：

- `start() -> SpawnedProcess`：拉起并立即返回；端口等待由引擎轮询 `probe()`；
- `stop()`：按模块形态停（命令行模块发信号 + 整树终止，WSL 走 compose stop，
  计划任务模块走 `schtasks /change + /end`）；适配器内部带一次有界升级，
  保证多面模块（wire 8790+8791）不会只停一半；
- `kill()`：整树强杀兜底（引擎在停超时后调用）；
- `probe()`：**端口在监听且命令行命中**（本单约定的识别口径）。
  命令行读不到 / 不匹配时失败关闭（返回 False），绝不凭端口猜归属。

识别规则（每个模块两条独立证据链）：

1. 端口 → `Get-NetTCPConnection` 的 `OwningProcess` → 父链上溯
   （`Get-CimInstance Win32_Process` 的 `ParentProcessId`）直到可识别的 cmdline；
2. 监听进程的 cmdline 必须同时命中该服务面的关键字（如 `th-cn` + `start-all`、
   `qfw` + `serve`、`pfctl.py` + `run`）；financecoffer 的第二条证据在 WSL 内
   （compose project label + service=api 的运行容器），因为 Windows 侧的持有者
   只是 `wslhost.exe` 转发器，不能当作归属证据。

所有外部接触面（端口探测 / 进程查询 / 终止 / 拉起 / 命令执行）都收在 `HostPivot`
后面，测试注入假实现，不占真端口。
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ladderstock.core.boundary import PEER_DIR_ENV, PEER_SIBLING_DIRS
from ladderstock.supervisor.adapters import (
    MODULE_PORTS,
    ModuleAdapter,
    SpawnedProcess,
    module_registry,
)
from ladderstock.supervisor.engine import SupervisorError
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

#: 默认 peer 根目录：环境变量 > LadderStock 同级目录 > 本机既有部署根
DEFAULT_PEER_ROOT_ENV = "LDST_PEER_ROOT"
DEFAULT_PEER_ROOT = Path(r"D:\workplace")
#: 日志目录默认收在既有宿主日志目录下（与 ops/host 的约定一致）
DEFAULT_LOG_SUBDIR = Path("LadderStock") / "logs" / "supervisor"
#: WSL 发行版覆盖；空 = 运行时 `wsl -l -v` 取默认发行版（不许写死）
WSL_DISTRO_ENV = "LDST_WSL_DISTRO"
#: ProxyFleet 计划任务名（只启停已存在的同名任务，不新建、不改运行身份）
PROXYFLEET_TASK_ENV = "LDST_PROXYFLEET_TASK"
DEFAULT_PROXYFLEET_TASK = "ProxyFleet"
#: TickHub-CN 的运行时 env 文件（非密钥 + 代理密钥都在里面，按旧入口原样加载）
TICKHUB_ENV_FILES_ENV = "LDST_TICKHUB_ENV_FILES"


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

    def owner_pid(self, port: int) -> int | None: ...

    def snapshot(self, pid: int) -> ProcessSnapshot | None: ...

    def parent_chain(self, pid: int, depth: int = 6) -> tuple[ProcessSnapshot, ...]: ...

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


class WindowsHostPivot:
    """tasklist / CIM / taskkill / Popen 的真实实现（生产路径）。

    每次查询都起一次 PowerShell 是慢的（200-500ms），调用方必须走
    `BaseAdapter` 的 1s 证据缓存，不能直接拿它做高频轮询。
    """

    def listening(self, port: int) -> bool:  # pragma: no cover - socket 直通
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(PROBE_CONNECT_TIMEOUT_SEC)
            return sock.connect_ex(("127.0.0.1", port)) == 0

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
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return ()
        rows = payload if isinstance(payload, list) else [payload]
        chain: list[ProcessSnapshot] = []
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
            chain.append(
                ProcessSnapshot(
                    pid=child,
                    parent_pid=ppid,
                    name=str(row.get("name") or ""),
                    cmdline=split_windows_commandline(cmdline_raw) if cmdline_raw else (),
                )
            )
        return tuple(chain)

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


def default_host_pivot() -> HostPivot:
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


def default_peer_dir(key: str, env: Mapping[str, str] | None = None) -> Path:
    """peer 目录：环境变量 > LadderStock 同级目录 > LDST_PEER_ROOT > 既有部署根。"""
    values = os.environ if env is None else env
    override = (values.get(PEER_DIR_ENV[key]) or "").strip()
    if override:
        return Path(override)
    sibling_name = PEER_SIBLING_DIRS[key]
    repo_root = Path(__file__).resolve().parents[3]
    sibling = repo_root.parent / sibling_name
    if sibling.is_dir():
        return sibling
    root = (values.get(DEFAULT_PEER_ROOT_ENV) or "").strip()
    return Path(root) / sibling_name if root else DEFAULT_PEER_ROOT / sibling_name


def _venv_exe(peer: Path, exe: str) -> Path:
    return peer / ".venv" / "Scripts" / exe


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
        if primary is None:
            raise SupervisorError(f"{self.name} 没有配置启动命令")
        self._cache.clear()
        return SpawnedProcess(
            pid=self._roots[0],
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
        tokens = self._match_tokens(port)
        matched = self._matches(snap, tokens)
        return PortEvidence(
            port=port,
            listening=True,
            matched=matched,
            pid=pid,
            name=snap.name,
            cmdline=snap.cmdline,
            parent_chain=chain,
        )

    def _match_tokens(self, port: int) -> tuple[str, ...]:
        for face in self.faces:
            if face.port == port:
                return _norm_tokens(face.match)
        return ()

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
) -> dict[str, BaseAdapter]:
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
    }


def _tickhub_env_files(env: Mapping[str, str]) -> tuple[Path, ...]:
    override = (env.get(TICKHUB_ENV_FILES_ENV) or "").strip()
    if override:
        return tuple(Path(item) for item in override.split(os.pathsep) if item.strip())
    candidates = [
        Path.home() / ".tickhub" / "proxy.env",
        Path(r"D:\QuotaFuseModules\TickHub-CN\ops\peer.env"),
    ]
    return tuple(path for path in candidates if path.is_file())


# --------------------------------------------------------------------------- #
# financecoffer（WSL / docker compose）
# --------------------------------------------------------------------------- #


class FinanceCofferAdapter:
    """FinanceCoffer：服务在 WSL 容器里，Windows 侧只有 wslhost 端口转发。

    - 启动：先在 WSL 内 `docker compose up -d`（deploy/.env 缺失时退回 `docker start`），
      再拉一个 wsl.exe 锚进程（`exec sleep infinity`）作为账本 PID——容器 PID 在
      Windows 侧不可寻址，锚进程让「这次托管会话」有一个可对账的活进程。
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
        distro = self.distro()
        command = self._start_script()
        result = self._wsl(distro, command, timeout=WSL_COMMAND_TIMEOUT_SEC)
        if not result.ok:
            raise SupervisorError(
                f"financecoffer 启动失败（wsl rc={result.returncode}）："
                f"{(result.stderr or result.stdout).strip()[:300]}"
            )
        anchor = (
            f"cd '{self._wsl_path(self._deploy)}' 2>/dev/null; exec sleep infinity"
        )
        argv = (
            "wsl.exe",
            "-d",
            distro,
            "-u",
            "root",
            "--",
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

    def identify(self) -> Identification:
        items = tuple(self._evidence(port) for port in self.ports)
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
        result = self._host.run(
            ("wsl.exe", "-l", "-v"), timeout=WSL_LIST_TIMEOUT_SEC
        )
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
        names = " ".join(
            line.strip() for line in result.stdout.splitlines() if line.strip()
        )
        self._fc_cache = (now, names)
        return names

    # ------------------------------------------------------------------ 内部

    def _wsl(self, distro: str, script: str, *, timeout: float) -> CommandResult:
        return self._host.run(
            ("wsl.exe", "-d", distro, "-u", "root", "--", "bash", "-lc", script),
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
            'ids=$(docker ps -aq --filter label=com.docker.compose.project=financecoffer); '
            '[ -n "$ids" ] || { echo "no deploy/.env and no financecoffer containers" >&2; '
            "exit 1; }; "
            "docker start $ids >/dev/null; "
            "fi"
        )

    def _stop_script(self) -> str:
        deploy = self._wsl_path(self._deploy)
        filter_expr = 'label=com.docker.compose.project=financecoffer'
        return (
            "set -u; "
            f"DEPLOY='{deploy}'; "
            'if [ -f "$DEPLOY/.env" ] && [ -f "$DEPLOY/compose.yaml" ]; then '
            'cd "$DEPLOY" && docker compose --env-file .env -f compose.yaml stop -t 20; '
            "fi; "
            f'ids=$(docker ps -q --filter {filter_expr}); '
            '[ -n "$ids" ] && echo "$ids" | xargs -r docker stop -t 20; '
            f'remain=$(docker ps -q --filter {filter_expr}); '
            '[ -n "$remain" ] && echo "$remain" | xargs -r docker kill; '
            f'after=$(docker ps -q --filter {filter_expr}); '
            'if [ -z "$after" ]; then echo FC_STOPPED=yes; else '
            'echo FC_STOPPED=no; exit 1; fi'
        )

    def _kill_script(self) -> str:
        return (
            "set -u; "
            'ids=$(docker ps -q --filter label=com.docker.compose.project=financecoffer); '
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

    def identify(self) -> Identification:
        items = tuple(self._evidence(port) for port in self.ports)
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
# 组装
# --------------------------------------------------------------------------- #


def build_default_adapters(
    *,
    env: Mapping[str, str] | None = None,
    log_dir: Path | None = None,
    host: HostPivot | None = None,
    evidence_cache_sec: float = EVIDENCE_CACHE_SEC,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, ModuleAdapter]:
    """生产装配：六个真实适配器，键集合与 `MODULE_ORDER` 一致。

    目录解析顺序（每个模块）：`LDST_PEER_DIR_<MODULE>` > LadderStock 同级目录 >
    `LDST_PEER_ROOT`（默认 `D:\\workplace`）。WSL 发行版与 ProxyFleet 任务名同理
    可用环境变量覆盖；全部不写死。
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
    return module_registry(adapters)
