r"""订阅版 CLI 通道：复用本机已登录的 Codex / Claude Code / WorkBuddy CLI（QUOT-401）。

为什么要有这一层
----------------
HTTP 通道（`openai` / `anthropic`）要用户自己掏一个 API Key。很多人手上其实已经
有订阅：本机 `claude` / `codex` 早就登录过，`~/.codex/auth.json`、`~/.claude` 都在。
参考实现（vibe-astock 的「请接入 AI」）就是把这三条订阅通道摆出来，点一下就用。

「装了却报没装」的两个坑
------------------------
1. **不在 PATH 上**。Codex 桌面版把 CLI 装在
   `%LOCALAPPDATA%\OpenAI\\Codex\\bin\\<hash>\\codex.exe`，从不写 PATH；WorkBuddy 把
   CodeBuddy CLI 塞在 `<安装目录>\\resources\\app.asar.unpacked\\cli\\bin\\codebuddy`
   （无扩展名的 node 脚本，还得靠 `node` 拉起）。所以除了 `shutil.which`，还要按
   已知位置 `glob`，并允许 `LDST_CLI_<ID>_BIN` 直接指定。
2. **看不到用户的 home**。看板服务以 SYSTEM 账户跑时 `Path.home()` 是
   `C:\\Windows\\system32\\config\\systemprofile`，那里的 `~/.codex` 当然不存在。
   所以先用 `LDST_CLI_HOME` 指定，Windows 上再回落到「哪个用户目录里有登录态标记」，
   并把解析出的 home 通过 `USERPROFILE` / `HOME` 传给子进程——否则 CLI 自己也找不到凭据。

边界（与 DEC-001 一致）
----------------------
- 本模块**只**负责「把一段 prompt 交给本机 CLI，拿回正文」。不抓行情、不读用户目录
  之外的隐私数据；探测登录态只看固定几个已知路径的**存在性**与 CLI 自带的
  `login status`，不解析凭据内容。
- 不做悄悄降级：CLI 找不到就报 `missing`，跑不起来就报 `broken`，绝不回落到 HTTP。
- 探针只发一句「回 ok」，不发送任何本机数据（与参考实现同口径）。

调用形状
--------
三个 CLI 的非交互入口各不相同，统一收在 `CLI_SPECS`：

- `claude -p`：Claude Code 的 print 模式，正文走 stdin；
- `codex exec -`：Codex CLI 的非交互子命令，`-` 表示从 stdin 读 prompt；
- `codebuddy -p --port 0`：WorkBuddy 内置的 CodeBuddy CLI（`--port 0` 是为了避开
  桌面端已占用的固定端口，否则 print 模式也会 `EADDRINUSE`）。

Windows 上 `claude` 是 npm 的 `.cmd` 垫片，`CreateProcess` 不能直接执行，
必须经 `cmd.exe /c`，否则报 `WinError 193`——这条在 `_wrap` 里统一处理。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ladderstock.ai.fallback import ModelTarget

#: 探测结果的四态。`ok` 才算「能用」；其余都要在 UI 上给出具体原因，不灰不假装。
CliState = Literal["ok", "missing", "broken", "no_login"]

#: 探针超时（秒）：只跑 `--version` / `login status`，不该慢
PROBE_TIMEOUT = 8.0

#: 没有文件可判登录态时，真跑一句的兜底超时（秒）：Node 系 CLI 冷启动就要几秒
PROBE_CALL_TIMEOUT = 45.0

#: 上一次真跑探测的缓存（避免状态接口把 CLI 反复拉起来）
_PROBE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_PROBE_CACHE_TTL = 120.0

#: 真正生成复盘时的默认超时（秒）：CLI 比 HTTP 慢得多，且要跑多段
DEFAULT_GENERATE_TIMEOUT = 600.0

#: 探针 prompt：一句话，不携带任何本机数据
PROBE_PROMPT = "回答一个词：ok"

#: JSON 模式追加的硬要求（与 providers.JSON_ONLY_INSTRUCTION 同义，
#: 但 CLI 没有 `response_format`，只能写进 prompt）
JSON_ONLY_INSTRUCTION = "只输出一个合法 JSON 对象，不要输出 Markdown 代码块或任何解释。"

#: 服务账户看不到用户 home 时的显式指定（可写进 config/ai.env）
CLI_HOME_ENV = "LDST_CLI_HOME"

#: 各 CLI 认的 home 变量：解析出的 home 要一并塞给子进程，否则 CLI 自己也找不到凭据
_HOME_ENV_NAMES = ("USERPROFILE", "HOME", "HOMEDRIVE", "HOMEPATH")

#: 判定「这个用户目录像是有人在用」的标记
_PROFILE_MARKERS = (".codex", ".claude", ".workbuddy", ".codebuddy")

#: 登录态失败时 CLI 的典型输出（Node 系 CLI 走这条）
_LOGIN_HINTS = ("authentication required", "please use /login", "not logged in", "请先登录")

#: Key 片段打码（`codex login status` 会回显 `sk-xxxx***`）
_KEY_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{4})[A-Za-z0-9_\-]{4,}")


@dataclass(frozen=True)
class CliSpec:
    """一个订阅通道的静态描述：叫什么、装在哪、怎么调、登录态落在哪。"""

    id: str
    label: str
    blurb: str
    #: PATH 上的可执行文件名（Windows 会补 .cmd / .exe）
    exe_names: tuple[str, ...]
    #: 非交互调用的固定参数；prompt 一律走 stdin（避免 Windows 32k 命令行上限）
    argv: tuple[str, ...]
    #: 登录态判定用的文件/目录（相对 home）。留空表示「文件判不了，真跑一句」
    login_paths: tuple[str, ...]
    #: 已知安装位置（相对 home 的 glob），用于 PATH 上没有的情况
    known_paths: tuple[str, ...] = ()
    #: 已知安装位置（相对「程序安装目录」的 glob），用于装在非标准盘符的情况
    install_paths: tuple[str, ...] = ()
    #: 显式指定可执行文件的变量名（写进 config/ai.env 即可）
    bin_env: str = ""
    #: 无扩展名脚本的解释器（如 `node`）；为空表示直接执行
    runner: str | None = None
    #: `--version` 是否被支持（不支持的传空元组）
    version_args: tuple[str, ...] = ("--version",)
    #: CLI 自带的登录态检查命令（比猜文件可靠）；为空则按 `login_paths` 判
    verify_args: tuple[str, ...] = ()
    #: 缺登录态时给用户的操作提示
    login_hint: str = ""
    #: 调这个 CLI 前必须补的环境变量（WorkBuddy 的 lite bundle 靠它分流）
    env_overrides: tuple[tuple[str, str], ...] = ()

    def build_argv(self, model: str) -> list[str]:
        args = list(self.argv)
        if model and model not in {"default", "-"}:
            args.extend(["--model", model])
        return args

    def login_files(self, home: Path) -> tuple[Path, ...]:
        return tuple(home / item for item in self.login_paths)


#: 顺序即 UI 顺序（参考实现：Codex → Claude → WorkBuddy）
CLI_SPECS: tuple[CliSpec, ...] = (
    CliSpec(
        id="codex",
        label="Codex 订阅版",
        blurb="使用本机 Codex CLI 的登录态（API Key 或 ChatGPT 订阅）",
        exe_names=("codex", "codex.cmd", "codex.exe"),
        # 临时目录不是 git 仓库，不加这个参数 codex 会直接拒跑
        argv=("exec", "--skip-git-repo-check", "-"),
        login_paths=(".codex/auth.json",),
        known_paths=(
            "AppData/Local/OpenAI/Codex/bin/*/codex.exe",
            "AppData/Roaming/npm/codex.cmd",
            ".codex/bin/codex.exe",
        ),
        bin_env="LDST_CLI_CODEX_BIN",
        verify_args=("login", "status"),
        login_hint="在终端跑 `codex login` 完成登录",
    ),
    CliSpec(
        id="claude",
        label="Claude 订阅",
        blurb="使用本机 Claude Code 登录",
        exe_names=("claude", "claude.cmd", "claude.exe"),
        argv=("-p",),
        login_paths=(".claude/.credentials.json", ".claude.json"),
        known_paths=(
            "AppData/Roaming/npm/claude.cmd",
            ".local/bin/claude.exe",
            ".local/bin/claude",
        ),
        bin_env="LDST_CLI_CLAUDE_BIN",
        login_hint="在终端跑 `claude` 并输入 `/login` 完成登录",
    ),
    CliSpec(
        id="workbuddy",
        label="WorkBuddy CLI",
        blurb="使用本机 WorkBuddy / CodeBuddy CLI（需单独登录一次，与桌面端不共享）",
        exe_names=("workbuddy", "codebuddy", "workbuddy.cmd", "codebuddy.cmd"),
        # `--port 0`：桌面端占着固定端口，print 模式不指定会 EADDRINUSE
        argv=("-p", "--port", "0"),
        # CodeBuddy CLI 的登录态不落这几个文件（桌面端登录也不共享），只能真跑一句判
        login_paths=(),
        known_paths=(
            "AppData/Local/Programs/WorkBuddy/resources/app.asar.unpacked/cli/bin/codebuddy",
            "AppData/Local/Programs/CodeBuddy/resources/app.asar.unpacked/cli/bin/codebuddy",
        ),
        install_paths=("resources/app.asar.unpacked/cli/bin/codebuddy",),
        bin_env="LDST_CLI_WORKBUDDY_BIN",
        runner="node",
        login_hint="在终端跑 `codebuddy` 并输入 `/login`（桌面端登录不会共享给 CLI）",
        # WorkBuddy 的精简 bundle 靠这个变量分流；不设会去 require 并不存在的
        # `dist/codebuddy-headless`，报 "Cannot find module '../dist/codebuddy'"
        env_overrides=(("CODEBUDDY_FORCE_LITE_WB_BUNDLE", "1"),),
    ),
)

_BY_ID = {spec.id: spec for spec in CLI_SPECS}

#: node 的常见位置（服务账户的 PATH 里通常没有用户装的 node）
_NODE_KNOWN_PATHS = (
    "AppData/Roaming/npm/node.exe",
    ".workbuddy/binaries/node/versions/*/node.exe",
)
_NODE_SYSTEM_PATHS = (
    "C:/Program Files/nodejs/node.exe",
    "D:/nodejs/node.exe",
)


def cli_spec(cli_id: str) -> CliSpec:
    """按 id 取通道；未知 id 抛 `KeyError`（由调用方翻译成配置错误）。"""
    return _BY_ID[(cli_id or "").strip().lower()]


def _profile_freshness(home: Path) -> float:
    newest = 0.0
    for marker in _PROFILE_MARKERS:
        try:
            newest = max(newest, (home / marker).stat().st_mtime)
        except OSError:
            continue
    return newest


def _is_service_profile(home: Path) -> bool:
    """服务账户（SYSTEM / LocalService）的 home 不是真实用户目录。"""
    text = str(home).lower().replace("/", "\\")
    return "systemprofile" in text or "\\windows\\system32" in text


def profile_candidates() -> tuple[Path, ...]:
    """可能存着登录态的用户目录，按「最近用过」排序。

    `LDST_CLI_HOME` 最优先；其次是进程自己的 home；**只有当进程跑在服务账户下**
    （home 是 `C:\\Windows\\system32\\config\\systemprofile`）才去扫 `C:\\Users\\*`，
    找有 `.codex` / `.claude` 标记的目录——普通用户进程不该乱翻别人的 profile。
    """
    seen: list[Path] = []

    def add(path: Path) -> None:
        try:
            resolved = path.expanduser()
        except (OSError, RuntimeError):
            return
        if _safe_is_dir(resolved) and resolved not in seen:
            seen.append(resolved)

    override = os.environ.get(CLI_HOME_ENV, "").strip()
    if override:
        add(Path(override))
    try:
        current = Path.home()
    except (OSError, RuntimeError):
        current = None
    if current is not None:
        add(current)

    if os.name == "nt" and not override and current is not None and _is_service_profile(current):
        users = Path(os.environ.get("SystemDrive", "C:") + "\\Users")
        if _safe_is_dir(users):
            try:
                entries = list(users.iterdir())
            except OSError:
                entries = []
            found = [
                item
                for item in entries
                if _safe_is_dir(item)
                and any(_safe_exists(item / mark) for mark in _PROFILE_MARKERS)
            ]
            found.sort(key=_profile_freshness, reverse=True)
            for item in found:
                add(item)
    return tuple(seen)


def _safe_exists(path: Path) -> bool:
    """服务账户可能会被 ACL 挡住（WinError 5）；「读不到」按「不存在」处理，别把接口打成 500。"""
    try:
        return path.exists()
    except OSError:
        return False


def _safe_is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError:
        return False


def _safe_is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def cli_home(spec: CliSpec) -> Path:
    """挑一个对这个通道最有意义的 home：优先真有登录态的那个。"""
    candidates = profile_candidates()
    for home in candidates:
        if spec.login_paths and any(_safe_exists(path) for path in spec.login_files(home)):
            return home
    return candidates[0] if candidates else Path.home()


def _search_dirs() -> list[str]:
    """PATH 之外的补充搜索目录：npm 全局、WindowsApps、Codex bin。"""
    dirs: list[str] = []
    for home in profile_candidates():
        for rel in (
            "AppData/Roaming/npm",
            "AppData/Local/Microsoft/WindowsApps",
            ".local/bin",
        ):
            candidate = home / rel
            if _safe_is_dir(candidate):
                dirs.append(str(candidate))
        for hit in home.glob("AppData/Local/OpenAI/Codex/bin/*"):
            if _safe_is_dir(hit):
                dirs.append(str(hit))
    for extra in _NODE_SYSTEM_PATHS:
        parent = str(Path(extra).parent)
        if _safe_is_dir(Path(parent)):
            dirs.append(parent)
    return dirs


def _registry_install_dirs() -> list[Path]:
    """从注册表卸载项拿安装目录（WorkBuddy 装在 `D:\\wk\\WorkBuddy` 这类非标准位置）。"""
    if os.name != "nt":
        return []
    try:
        import winreg
    except ImportError:  # pragma: no cover - 非 Windows 不会走到
        return []
    # Linux CI 的 mypy 下 typeshed 给 winreg 的是空模块，属性访问全报 attr-defined；
    # 模块名不能重赋值改型，故起 Any 别名。运行期走到这里必然是 Windows
    # （上面已挡 os.name != "nt"），行为不变。
    _winreg: Any = winreg

    roots = (
        (_winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (_winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
        (
            _winreg.HKEY_LOCAL_MACHINE,
            r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        ),
    )
    found: list[Path] = []
    for hive, sub in roots:
        try:
            parent = _winreg.OpenKey(hive, sub)
        except OSError:
            continue
        with parent:
            index = 0
            while True:
                try:
                    name = _winreg.EnumKey(parent, index)
                except OSError:
                    break
                index += 1
                try:
                    with _winreg.OpenKey(parent, name) as child:
                        display = str(_winreg.QueryValueEx(child, "DisplayName")[0] or "")
                        if (
                            "workbuddy" not in display.lower()
                            and "codebuddy" not in display.lower()
                        ):
                            continue
                        # DisplayIcon 形如 `D:\wk\WorkBuddy\WorkBuddy.exe,0`；
                        # InstallLocation 常是脏值，只有 DisplayIcon 可信。
                        icon = str(_winreg.QueryValueEx(child, "DisplayIcon")[0] or "")
                except OSError:
                    continue
                path_text = icon.split(",")[0].strip().strip('"')
                if not path_text:
                    continue
                target = Path(path_text)
                directory = target if _safe_is_dir(target) else target.parent
                if _safe_is_dir(directory) and directory not in found:
                    found.append(directory)
    return found


def install_roots() -> tuple[Path, ...]:
    """可能装着「带 CLI 的桌面客户端」的目录。"""
    roots: list[Path] = list(_registry_install_dirs())
    fixed = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "WorkBuddy"
        if os.environ.get("LOCALAPPDATA")
        else None,
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "CodeBuddy"
        if os.environ.get("LOCALAPPDATA")
        else None,
        Path("C:/Program Files/WorkBuddy"),
        Path("C:/Program Files (x86)/WorkBuddy"),
    ]
    for item in fixed:
        if item is not None and _safe_is_dir(item) and item not in roots:
            roots.append(item)
    return tuple(roots)


def find_executable(spec: CliSpec) -> str | None:
    """找可执行文件：显式变量 → PATH → 补充目录 → 已知安装位置 → 安装目录。"""
    override = os.environ.get(spec.bin_env, "").strip() if spec.bin_env else ""
    if override and _safe_is_file(Path(override)):
        return override

    extra = _search_dirs()
    for name in spec.exe_names:
        found = shutil.which(name)
        if found:
            return found
        if extra:
            found = shutil.which(name, path=os.pathsep.join(extra))
            if found:
                return found

    for home in profile_candidates():
        for pattern in spec.known_paths:
            hits = [path for path in home.glob(pattern) if _safe_is_file(path)]
            if hits:
                return str(sorted(hits)[-1])

    for root in install_roots():
        for pattern in spec.install_paths:
            hits = [path for path in root.glob(pattern) if _safe_is_file(path)]
            if hits:
                return str(sorted(hits)[-1])
    return None


def _resolve_runner(name: str) -> str | None:
    """找解释器（目前只用到 node）。"""
    override = os.environ.get("LDST_CLI_NODE", "").strip()
    if override and _safe_is_file(Path(override)):
        return override
    found = shutil.which(name)
    if found:
        return found
    for home in profile_candidates():
        for pattern in _NODE_KNOWN_PATHS:
            hits = [path for path in home.glob(pattern) if _safe_is_file(path)]
            if hits:
                return str(sorted(hits)[-1])
    for candidate in _NODE_SYSTEM_PATHS:
        if _safe_is_file(Path(candidate)):
            return candidate
    return None


def _wrap(exe: str, args: list[str]) -> list[str]:
    """Windows 上 `.cmd` / `.bat` 必须经 cmd.exe，否则 CreateProcess 报 WinError 193。"""
    if os.name == "nt" and Path(exe).suffix.lower() in {".cmd", ".bat"}:
        return ["cmd.exe", "/c", exe, *args]
    return [exe, *args]


def _command(spec: CliSpec, exe: str, args: list[str]) -> list[str] | None:
    """把「可执行文件 + 参数」拼成真正的命令行；解释器缺失时返回 None。"""
    suffix = Path(exe).suffix.lower()
    if spec.runner and suffix not in {".exe", ".cmd", ".bat", ".ps1"}:
        runner = _resolve_runner(spec.runner)
        if runner is None:
            return None
        return [runner, exe, *args]
    return _wrap(exe, args)


def _clean_env(home: Path | None = None, extra: tuple[tuple[str, str], ...] = ()) -> dict[str, str]:
    """子进程环境：清掉 LadderStock 变量，并把 home 指向解析出的用户目录。"""
    env = {k: v for k, v in os.environ.items() if not k.startswith("LDST_")}
    env["NO_COLOR"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    for name, value in extra:
        env[name] = value
    if home is not None:
        env["USERPROFILE"] = str(home)
        env["HOME"] = str(home)
        if os.name == "nt":
            drive = home.drive or "C:"
            env["HOMEDRIVE"] = drive
            env["HOMEPATH"] = str(home)[len(drive) :] or "\\"
    return env


def _decode(data: bytes) -> str:
    """CLI 的输出编码不可控：Windows 原生工具常吐 GBK，Node 系吐 UTF-8。"""
    for encoding in ("utf-8", "gbk", "cp936"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def _run(
    cmd: list[str],
    *,
    stdin_text: str | None,
    timeout: float,
    home: Path | None = None,
    extra_env: tuple[tuple[str, str], ...] = (),
) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory(prefix="ldst-cli-") as workdir:
        proc = subprocess.run(  # noqa: S603 - 命令由本模块常量拼出，不接受外部字符串
            cmd,
            input=stdin_text.encode("utf-8") if stdin_text is not None else None,
            cwd=workdir,
            env=_clean_env(home, extra_env),
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    return subprocess.CompletedProcess(
        proc.args, proc.returncode, _decode(proc.stdout), _decode(proc.stderr)
    )


#: 取消 / 超时后等进程退出的宽限（秒）：taskkill 之后不该再等太久
_KILL_GRACE_SEC = 5.0


def _child_flags() -> dict[str, Any]:
    """子进程要能「连坐」终止：Windows 独立进程组 + 无窗口，POSIX 独立会话。"""
    if os.name == "nt":  # pragma: no cover - Windows 专用
        return {
            "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        }
    return {"start_new_session": True}


async def _terminate_tree(proc: asyncio.subprocess.Process) -> None:
    """连根杀掉子进程树：`cmd.exe /c` 与 npm 垫片都会再生子进程，只 kill 父级会留悬挂。"""
    if proc.returncode is not None:
        return
    try:
        if os.name == "nt":  # pragma: no cover - Windows 专用
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/PID",
                str(proc.pid),
                "/T",
                "/F",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            await killer.wait()
        else:
            try:
                # killpg/SIGKILL 只存在于 POSIX；Windows 分支在上面已返回
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)  # type: ignore[attr-defined]
            except (OSError, ProcessLookupError):
                proc.kill()
    except (OSError, ProcessLookupError, NotImplementedError):
        with contextlib.suppress(OSError, ProcessLookupError):
            proc.kill()
    with contextlib.suppress(BaseException):
        await asyncio.wait_for(proc.wait(), _KILL_GRACE_SEC)


async def _run_async(
    cmd: list[str],
    *,
    stdin_text: str | None,
    timeout: float,
    home: Path | None = None,
    extra_env: tuple[tuple[str, str], ...] = (),
) -> tuple[int, str, str]:
    """`_run` 的异步版：同一套命令与环境，但超时 / 取消时会把整棵进程树杀掉。

    返回 `(returncode, stdout, stderr)`；超时抛 `CliTimeout`，外部取消抛 `CancelledError`。
    """
    with tempfile.TemporaryDirectory(prefix="ldst-cli-") as workdir:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workdir,
            env=_clean_env(home, extra_env),
            **_child_flags(),
        )
        payload = stdin_text.encode("utf-8") if stdin_text is not None else None
        try:
            out, err = await asyncio.wait_for(proc.communicate(payload), timeout=timeout)
        except TimeoutError:
            await _terminate_tree(proc)
            raise CliTimeout(f"超过 {int(timeout)}s 没有返回") from None
        except asyncio.CancelledError:
            # 前端 abort / 服务端关停：清理动作不能把取消本身吞掉
            with contextlib.suppress(BaseException):
                await _terminate_tree(proc)
            raise
    return int(proc.returncode or 0), _decode(out or b""), _decode(err or b"")


def _first_line(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return _mask(lines[0][:200]) if lines else ""


def _mask(text: str) -> str:
    """登录态检查的输出里可能带 Key 片段，上报前打码。"""
    return _KEY_RE.sub(r"\1***", text)


def _error_line(text: str) -> str:
    """从 CLI 的一堆日志里挑出真正的原因。

    codex 会先打版本横幅再打 `ERROR: unexpected status 403 ...`，取第一行只会得到
    "OpenAI Codex v0.166"，对用户毫无帮助，所以要倒着找带 error 的那行。
    """
    lines = [_mask(line.strip()) for line in (text or "").splitlines() if line.strip()]
    for line in reversed(lines):
        lowered = line.lower()
        if "error" in lowered or "failed" in lowered or "订阅" in line or "unauthorized" in lowered:
            return line[:300]
    return lines[-1][:300] if lines else ""


def _friendly(reason: str) -> str:
    """把上游的错误码翻译成一句人话（否则用户只看到 403）。"""
    if "SUBSCRIPTION_NOT_FOUND" in reason or "没有可用订阅" in reason:
        return f"{reason}（上游账户当前分组没有可用订阅：去供应商后台续费或换到有订阅的分组）"
    if "Unauthorized" in reason or "401" in reason:
        return f"{reason}（凭据失效，重新登录一次这个 CLI）"
    return reason


def _looks_like_no_login(text: str) -> bool:
    lowered = (text or "").lower()
    return any(hint in lowered for hint in _LOGIN_HINTS)


def _probe_by_call(spec: CliSpec, exe: str, home: Path) -> dict[str, Any]:
    """文件判不了登录态时，用一句探针真跑一次（带缓存，避免状态接口被拖慢）。"""
    cache_key = f"{spec.id}:{exe}"
    cached = _PROBE_CACHE.get(cache_key)
    if cached and time.monotonic() - cached[0] < _PROBE_CACHE_TTL:
        return cached[1]

    cmd = _command(spec, exe, spec.build_argv(""))
    if cmd is None:
        result = {
            "state": "broken",
            "detail": f"缺少解释器 {spec.runner}，无法拉起 {Path(exe).name}",
        }
        _PROBE_CACHE[cache_key] = (time.monotonic(), result)
        return result
    try:
        proc = _run(
            cmd,
            stdin_text=PROBE_PROMPT,
            timeout=PROBE_CALL_TIMEOUT,
            home=home,
            extra_env=spec.env_overrides,
        )
    except subprocess.TimeoutExpired:
        result = {"state": "broken", "detail": f"探针超过 {int(PROBE_CALL_TIMEOUT)}s 没有返回"}
    except OSError as exc:
        result = {"state": "broken", "detail": f"拉不起来：{exc}"}
    else:
        text = f"{proc.stdout}\n{proc.stderr}"
        if _looks_like_no_login(text):
            result = {"state": "no_login", "detail": spec.login_hint or "需要先登录"}
        elif proc.returncode == 0 and proc.stdout.strip():
            result = {"state": "ok", "detail": "可用：探针已跑通"}
        else:
            reason = _friendly(_error_line(text)) or "没有输出"
            result = {
                "state": "broken",
                "detail": f"探针退出码 {proc.returncode}：{reason}",
            }
    _PROBE_CACHE[cache_key] = (time.monotonic(), result)
    return result


def probe(spec: CliSpec) -> dict[str, Any]:
    """探一条通道：装没装、跑不跑得起来、登录态还在不在。

    返回的字典直接进 `/self-ai/cli` 的 JSON，字段名与前端一一对应。
    `state=ok` 是唯一「可以点启用」的状态。
    """
    exe = find_executable(spec)
    home = cli_home(spec)
    login = [str(path) for path in spec.login_files(home) if _safe_exists(path)]
    base: dict[str, Any] = {
        "id": spec.id,
        "label": spec.label,
        "blurb": spec.blurb,
        "exe": exe,
        "home": str(home),
        "login_files": login,
        "version": None,
        "state": "missing",
        "detail": f"没找到 {spec.exe_names[0]}：PATH 与已知安装位置都没有",
        "login_hint": spec.login_hint,
    }
    if exe is None:
        return base

    if spec.version_args:
        cmd = _command(spec, exe, list(spec.version_args))
        if cmd is None:
            base.update(
                state="broken", detail=f"缺少解释器 {spec.runner}，无法拉起 {Path(exe).name}"
            )
            return base
        try:
            proc = _run(
                cmd, stdin_text=None, timeout=PROBE_TIMEOUT, home=home, extra_env=spec.env_overrides
            )
        except (OSError, subprocess.SubprocessError) as exc:
            base.update(state="broken", detail=f"装了但跑不起来：{exc}")
            return base
        if proc.returncode != 0:
            message = (proc.stderr or proc.stdout or "").strip().splitlines()
            base.update(
                state="broken",
                detail=f"`{Path(exe).name} {' '.join(spec.version_args)}` 返回 {proc.returncode}"
                + (f"：{message[0][:160]}" if message else ""),
            )
            return base
        first = _first_line(proc.stdout)
        base["version"] = first if first and not _looks_like_warning(first) else None

    if spec.verify_args:
        cmd = _command(spec, exe, list(spec.verify_args))
        if cmd is not None:
            try:
                proc = _run(
                    cmd,
                    stdin_text=None,
                    timeout=PROBE_TIMEOUT,
                    home=home,
                    extra_env=spec.env_overrides,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                base.update(state="broken", detail=f"登录态检查失败：{exc}")
                return base
            text = f"{proc.stdout}\n{proc.stderr}".strip()
            if proc.returncode != 0 or _looks_like_no_login(text):
                base.update(
                    state="no_login",
                    detail=spec.login_hint
                    or f"`{Path(exe).name} {' '.join(spec.verify_args)}` 说没登录",
                )
                return base
            base.update(state="ok", detail=f"可用：{_first_line(proc.stdout) or '登录态正常'}")
            return base

    if not spec.login_paths:
        # 没有可靠的登录态文件 → 真跑一句（结果缓存两分钟）
        verdict = _probe_by_call(spec, exe, home)
        base.update(state=verdict["state"], detail=verdict["detail"])
        return base

    if not login:
        base.update(
            state="no_login",
            detail=spec.login_hint or f"没找到登录态（{spec.login_paths[0]}）",
        )
        return base
    base.update(state="ok", detail="可用：本机已登录")
    return base


def _looks_like_warning(text: str) -> bool:
    lowered = text.lower()
    return lowered.startswith("warning") or "warning:" in lowered


def detect_all() -> list[dict[str, Any]]:
    """三条通道全探一遍；UI 一次拿全，避免逐条点开才报错。"""
    return [probe(spec) for spec in CLI_SPECS]


def channel_status(cli_id: str) -> dict[str, Any] | None:
    try:
        return probe(cli_spec(cli_id))
    except KeyError:
        return None


def _strip_fences(text: str) -> str:
    """CLI 习惯给 Markdown 围栏；JSON 模式下剥掉，其余原样返回。"""
    body = text.strip()
    if not body.startswith("```"):
        return body
    lines = body.splitlines()
    if not lines:
        return body
    lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


class CliProvider:
    """把「一段 prompt」交给本机 CLI。只实现补全，不实现 tool-calling。"""

    @staticmethod
    def _prompt_text(system: str, user: str, json_mode: bool) -> str:
        prompt = f"{system}\n\n---\n\n{user}" if system else user
        if json_mode:
            prompt = f"{prompt}\n\n{JSON_ONLY_INSTRUCTION}"
        return prompt

    def complete(
        self,
        target: ModelTarget,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        timeout: float = DEFAULT_GENERATE_TIMEOUT,
        transport: Any = None,  # 与 HTTP provider 同签名，CLI 用不上
    ) -> str:
        del transport
        cli_id = (target.backend.cli or "").strip().lower()
        try:
            spec = cli_spec(cli_id)
        except KeyError:
            raise CliUnavailable(f"未知的 CLI 通道：{cli_id!r}") from None
        status = probe(spec)
        if status["state"] != "ok":
            raise CliUnavailable(str(status["detail"]))

        prompt = self._prompt_text(system, user, json_mode)

        cmd = _command(spec, status["exe"], spec.build_argv(target.model))
        if cmd is None:
            raise CliUnavailable(f"{spec.label} 缺少解释器 {spec.runner}")
        try:
            proc = _run(
                cmd,
                stdin_text=prompt,
                timeout=timeout,
                home=Path(status["home"]),
                extra_env=spec.env_overrides,
            )
        except subprocess.TimeoutExpired:
            raise CliTimeout(f"{spec.label} 超过 {int(timeout)}s 没有返回") from None
        except OSError as exc:
            raise CliUnavailable(f"{spec.label} 启动失败：{exc}") from None

        if proc.returncode != 0:
            reason = _error_line(f"{proc.stderr}\n{proc.stdout}")
            raise CliUnavailable(
                f"{spec.label} 退出码 {proc.returncode}"
                + (f"：{_friendly(reason)}" if reason else "（没有输出）")
            )
        text = (proc.stdout or "").strip()
        if not text:
            raise CliUnavailable(f"{spec.label} 没有输出正文")
        return _strip_fences(text) if json_mode else text

    async def acomplete(
        self,
        target: ModelTarget,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        timeout: float = DEFAULT_GENERATE_TIMEOUT,
    ) -> str:
        """`complete` 的异步版（QUOT-473）：超时 / 取消时连根杀掉进程树。

        探针仍走同步实现（有 120s 缓存），放到线程里跑，避免阻塞事件循环。
        """
        cli_id = (target.backend.cli or "").strip().lower()
        try:
            spec = cli_spec(cli_id)
        except KeyError:
            raise CliUnavailable(f"未知的 CLI 通道：{cli_id!r}") from None
        status = await asyncio.to_thread(probe, spec)
        if status["state"] != "ok":
            raise CliUnavailable(str(status["detail"]))

        prompt = self._prompt_text(system, user, json_mode)
        cmd = _command(spec, status["exe"], spec.build_argv(target.model))
        if cmd is None:
            raise CliUnavailable(f"{spec.label} 缺少解释器 {spec.runner}")
        try:
            code, out, err = await _run_async(
                cmd,
                stdin_text=prompt,
                timeout=timeout,
                home=Path(status["home"]),
                extra_env=spec.env_overrides,
            )
        except CliTimeout:
            raise CliTimeout(f"{spec.label} 超过 {int(timeout)}s 没有返回") from None
        except OSError as exc:
            raise CliUnavailable(f"{spec.label} 启动失败：{exc}") from None

        if code != 0:
            reason = _error_line(f"{err}\n{out}")
            raise CliUnavailable(
                f"{spec.label} 退出码 {code}"
                + (f"：{_friendly(reason)}" if reason else "（没有输出）")
            )
        text = (out or "").strip()
        if not text:
            raise CliUnavailable(f"{spec.label} 没有输出正文")
        return _strip_fences(text) if json_mode else text


class CliUnavailable(RuntimeError):
    """CLI 通道不可用（没装 / 没登录 / 跑挂）：属于配置问题，不该当成网络故障重试。"""


class CliTimeout(CliUnavailable):
    """CLI 在时限内没有返回：与「跑挂」分开，前端要能报超时并允许重试。"""


class CliChatClient:
    """只实现 `complete` 的客户端：编排层的 `supports_tools()` 判不到，自动走单次补全。"""

    def __init__(self, config: Any, *, timeout: float = DEFAULT_GENERATE_TIMEOUT) -> None:
        self.config = config
        self.timeout = timeout
        self._provider = CliProvider()

    def complete(self, model: str, system: str, user: str, *, json_mode: bool) -> str:
        target = self.config.resolve(model)
        return self._provider.complete(
            target, system, user, json_mode=json_mode, timeout=self.timeout
        )


def default_backend_kind(config: Any) -> str:
    """当前默认模型落在哪种后端上（`cli` / `openai` / `anthropic`）。"""
    try:
        return str(config.resolve(config.default_model).backend.kind)
    except Exception:  # noqa: BLE001 - 配置坏掉时按 HTTP 走，让下游报原始错误
        return "openai"


def build_chat_client(config: Any) -> Any:
    """按默认后端选客户端：CLI 通道用 `CliChatClient`，其余仍走 `HttpChatClient`。

    刻意不做「CLI 失败自动回落 HTTP」——那等于在用户没同意的情况下换计费方式。
    """
    from ladderstock.ai.orchestrator import HttpChatClient

    if default_backend_kind(config) == "cli":
        return CliChatClient(config)
    return HttpChatClient(config)


__all__ = [
    "CLI_HOME_ENV",
    "CLI_SPECS",
    "CliChatClient",
    "CliProvider",
    "CliSpec",
    "CliTimeout",
    "CliUnavailable",
    "build_chat_client",
    "channel_status",
    "cli_home",
    "cli_spec",
    "detect_all",
    "default_backend_kind",
    "find_executable",
    "probe",
    "profile_candidates",
]
