"""订阅版 CLI 通道：复用本机已登录的 Codex / Claude Code / WorkBuddy CLI（QUOT-401）。

为什么要有这一层
----------------
HTTP 通道（`openai` / `anthropic`）要用户自己掏一个 API Key。很多人手上其实已经
有订阅：本机 `claude` / `codex` 早就登录过，`~/.codex/auth.json`、`~/.claude` 都在。
参考实现（vibe-astock 的「请接入 AI」）就是把这三条订阅通道摆出来，点一下就用。

边界（与 DEC-001 一致）
----------------------
- 本模块**只**负责「把一段 prompt 交给本机 CLI，拿回正文」。不抓行情、不读用户目录
  之外的隐私数据；探测登录态只看固定几个已知路径的**存在性**，不解析凭据内容。
- 不做悄悄降级：CLI 找不到就报 `missing`，跑不起来就报 `broken`，绝不回落到 HTTP。
- 探针只发一句「回 ok」，不发送任何本机数据（与参考实现同口径）。

调用形状
--------
三个 CLI 的非交互入口各不相同，统一收在 `CLI_SPECS`：

- `claude -p`：Claude Code 的 print 模式，正文走 stdin；
- `codex exec -`：Codex CLI 的非交互子命令，`-` 表示从 stdin 读 prompt；
- `workbuddy -p`：WorkBuddy / CodeBuddy CLI（若本机装了）。

Windows 上 `claude` 是 npm 的 `.cmd` 垫片，`CreateProcess` 不能直接执行，
必须经 `cmd.exe /c`，否则报 `WinError 193`——这条在 `_wrap` 里统一处理。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ladderstock.ai.fallback import ModelTarget

#: 探测结果的四态。`ok` 才算「能用」；其余都要在 UI 上给出具体原因，不灰不假装。
CliState = Literal["ok", "missing", "broken", "no_login"]

#: 探针超时（秒）：只跑 `--version`，不该慢
PROBE_TIMEOUT = 8.0

#: 真正生成复盘时的默认超时（秒）：CLI 比 HTTP 慢得多，且要跑多段
DEFAULT_GENERATE_TIMEOUT = 600.0

#: 探针 prompt：一句话，不携带任何本机数据
PROBE_PROMPT = "回答一个词：ok"

#: JSON 模式追加的硬要求（与 providers.JSON_ONLY_INSTRUCTION 同义，
#: 但 CLI 没有 `response_format`，只能写进 prompt）
JSON_ONLY_INSTRUCTION = "只输出一个合法 JSON 对象，不要输出 Markdown 代码块或任何解释。"


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
    #: 登录态判定用的文件/目录（任一同在即视为已登录）
    login_paths: tuple[str, ...]
    #: `--version` 是否被支持（不支持的传空元组）
    version_args: tuple[str, ...] = ("--version",)

    def build_argv(self, exe: str, model: str) -> list[str]:
        args = [exe, *self.argv]
        if model and model not in {"default", "-"}:
            args.extend(["--model", model])
        return args

    def login_files(self) -> tuple[Path, ...]:
        home = Path.home()
        return tuple(home / item for item in self.login_paths)


#: 顺序即 UI 顺序（参考实现：Codex → Claude → WorkBuddy）
CLI_SPECS: tuple[CliSpec, ...] = (
    CliSpec(
        id="codex",
        label="Codex 订阅版",
        blurb="使用本机 Codex CLI 的 ChatGPT 登录",
        exe_names=("codex", "codex.cmd", "codex.exe"),
        argv=("exec", "-"),
        login_paths=(".codex/auth.json",),
    ),
    CliSpec(
        id="claude",
        label="Claude 订阅",
        blurb="使用本机 Claude Code 登录",
        exe_names=("claude", "claude.cmd", "claude.exe"),
        argv=("-p",),
        login_paths=(".claude/.credentials.json", ".claude.json", ".claude"),
    ),
    CliSpec(
        id="workbuddy",
        label="WorkBuddy CLI",
        blurb="使用本机 WorkBuddy / CodeBuddy 登录",
        exe_names=("workbuddy", "codebuddy", "workbuddy.cmd", "codebuddy.cmd"),
        argv=("-p",),
        login_paths=(".workbuddy", ".codebuddy"),
    ),
)

_BY_ID = {spec.id: spec for spec in CLI_SPECS}


def cli_spec(cli_id: str) -> CliSpec:
    """按 id 取通道；未知 id 抛 `KeyError`（由调用方翻译成配置错误）。"""
    return _BY_ID[(cli_id or "").strip().lower()]


def find_executable(spec: CliSpec) -> str | None:
    """在 PATH 上找可执行文件；找不到返回 None（不遍历磁盘猜路径）。"""
    for name in spec.exe_names:
        found = shutil.which(name)
        if found:
            return found
    return None


def _wrap(exe: str, args: list[str]) -> list[str]:
    """Windows 上 `.cmd` / `.bat` 必须经 cmd.exe，否则 CreateProcess 报 WinError 193。"""
    if os.name == "nt" and Path(exe).suffix.lower() in {".cmd", ".bat"}:
        return ["cmd.exe", "/c", exe, *args]
    return [exe, *args]


def _clean_env() -> dict[str, str]:
    """清掉 LadderStock 自己的变量，避免把 `LDST_*` 漏给第三方 CLI。"""
    env = {k: v for k, v in os.environ.items() if not k.startswith("LDST_")}
    env["NO_COLOR"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
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
    cmd: list[str], *, stdin_text: str | None, timeout: float
) -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory(prefix="ldst-cli-") as workdir:
        proc = subprocess.run(  # noqa: S603 - 命令由本模块常量拼出，不接受外部字符串
            cmd,
            input=stdin_text.encode("utf-8") if stdin_text is not None else None,
            cwd=workdir,
            env=_clean_env(),
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    return subprocess.CompletedProcess(
        proc.args, proc.returncode, _decode(proc.stdout), _decode(proc.stderr)
    )


def probe(spec: CliSpec) -> dict[str, Any]:
    """探一条通道：装没装、跑不跑得起来、登录态还在不在。

    返回的字典直接进 `/self-ai/cli` 的 JSON，字段名与前端一一对应。
    `state=ok` 是唯一「可以点启用」的状态。
    """
    exe = find_executable(spec)
    login = [str(path) for path in spec.login_files() if path.exists()]
    base: dict[str, Any] = {
        "id": spec.id,
        "label": spec.label,
        "blurb": spec.blurb,
        "exe": exe,
        "login_files": login,
        "version": None,
        "state": "missing",
        "detail": f"没有在 PATH 上找到 {spec.exe_names[0]}",
    }
    if exe is None:
        return base
    if spec.version_args:
        try:
            proc = _run(_wrap(exe, list(spec.version_args)), stdin_text=None, timeout=PROBE_TIMEOUT)
        except (OSError, subprocess.SubprocessError) as exc:
            base.update(state="broken", detail=f"装了但跑不起来：{exc}")
            return base
        if proc.returncode != 0:
            message = (proc.stderr or proc.stdout or "").strip().splitlines()
            base.update(
                state="broken",
                detail=f"`{spec.exe_names[0]} {' '.join(spec.version_args)}` 返回 {proc.returncode}"
                + (f"：{message[0][:160]}" if message else ""),
            )
            return base
        base["version"] = (proc.stdout or "").strip().splitlines()[0][:80] if proc.stdout.strip() else None
    if not login:
        base.update(
            state="no_login",
            detail=f"没找到登录态（{spec.login_paths[0]}）；先在终端跑一次 `{spec.exe_names[0]}` 完成登录",
        )
        return base
    base.update(state="ok", detail="可用：本机已登录")
    return base


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

        prompt = f"{system}\n\n---\n\n{user}" if system else user
        if json_mode:
            prompt = f"{prompt}\n\n{JSON_ONLY_INSTRUCTION}"

        cmd = _wrap(status["exe"], spec.build_argv("", target.model)[1:])
        try:
            proc = _run(cmd, stdin_text=prompt, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise CliUnavailable(f"{spec.label} 超过 {int(timeout)}s 没有返回") from None
        except OSError as exc:
            raise CliUnavailable(f"{spec.label} 启动失败：{exc}") from None

        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()
            raise CliUnavailable(
                f"{spec.label} 退出码 {proc.returncode}"
                + (f"：{detail[0][:200]}" if detail else "（没有输出）")
            )
        text = (proc.stdout or "").strip()
        if not text:
            raise CliUnavailable(f"{spec.label} 没有输出正文")
        return _strip_fences(text) if json_mode else text


class CliUnavailable(RuntimeError):
    """CLI 通道不可用（没装 / 没登录 / 跑挂）：属于配置问题，不该当成网络故障重试。"""


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
    "CLI_SPECS",
    "CliChatClient",
    "CliProvider",
    "CliSpec",
    "CliUnavailable",
    "build_chat_client",
    "channel_status",
    "cli_spec",
    "detect_all",
    "default_backend_kind",
    "find_executable",
    "probe",
]
