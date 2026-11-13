"""无端口看门狗（QUOT-589 重修）：不监听任何端口的探活自愈监控器。

QUOT-589 用户裁决（硬约束）：全机只留 5950 正式 / 5951 测试两个端口，
看门狗不得再占第三端口（旧 5952 与 QUOT-589 中途出现的 5953 都废除）。
`/platform/health` + `/platform/restart` 改由主服务自己提供（`web/platform.py`）；
本模块只做**自愈**：连续探测失败确认主服务死亡后，按配置的重启方式拉起它。

- 探活：`GET {api}/api/health`（超时 2s）；连续 `FAILURES_BEFORE_RED` 次失败
  才算确认中断（单次抖动不触发重启），恢复即清零。
- 自愈：确认中断 → `restart_service()`（与重启入口同一套 kill_guard / 节流），
  带冷却期防止「拉起又立刻失败」变成重启风暴。
- 安全边界（沿用 QUOT-443 硬约束，一条不减）：
  1. 绝不按映像名批量杀进程；只杀「此刻真的在监听 API 端口」的那个 PID，
     且映像名含 `multica` 时一律拒绝（那是工作区守护进程）。
  2. 重启方式只来自命令行参数；被 supervisor 托管（`LDST_WATCHDOG_SUPERVISED`）
     时只认 `--spawn-cmd`（QUOT-523：同一进程树，绝不复活被关停的看板）。
  3. 不绑任何端口：端口冲突 / 双进程竞态在 spawn 侧由看板自身的
     DEC-004（端口被占退出码 3）兜底。
  4. 状态文件 `state/watchdog.json` 只写 PID / 时间戳 / 版本（`port: null`），
     无凭据。
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlsplit

import httpx
import typer

from ladderstock import __version__
from ladderstock.cli.output import OutputOption, error
from ladderstock.cli.paths import data_dir
from ladderstock.errors import EXIT_CONFIG, EXIT_OK

DEFAULT_API_BASE = "http://127.0.0.1:5950"
DEFAULT_RESTART_TASK = "LadderStock API"

#: supervisor 托管标记（QUOT-523）：置 1 后看门狗只认 `--spawn-cmd`，不再回退
#: 计划任务 / 重启脚本——否则「单入口关闭全停」会被 schtasks 复活看板破坏。
SUPERVISED_ENV = "LDST_WATCHDOG_SUPERVISED"


def supervised(env: Mapping[str, str] | None = None) -> bool:
    values = os.environ if env is None else env
    return (values.get(SUPERVISED_ENV) or "").strip() not in ("", "0")


PROBE_TIMEOUT_SEC = 2.0
RESTART_THROTTLE_SEC = 30.0
#: 连续失败到这个次数才算确认中断（与前端 FAILURES_BEFORE_RED 同语义）：
#: 单次抖动绝不触发自愈重启。
FAILURES_BEFORE_RED = 2


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _log(message: str) -> None:
    sys.stderr.write(f"ldst: {message}\n")
    sys.stderr.flush()


# --------------------------------------------------------------------------- #
# 配置与状态
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class WatchdogConfig:
    api_base: str = DEFAULT_API_BASE
    restart_task: str = DEFAULT_RESTART_TASK
    restart_script: str = ""
    #: 直接拉起看板的固定 argv（JSON 数组）；被 supervisor 托管时由它给出，
    #: 排在 task 之前——同一进程树里不再经 schtasks 复活看板（QUOT-523）。
    spawn_cmd: str = ""
    #: supervisor 托管（QUOT-523）：只按 spawn-cmd 拉起看板，schedule/脚本兜底一律不用
    supervised: bool = False
    probe_interval: float = 2.0
    data_root: Path | None = None

    @property
    def root(self) -> Path:
        return self.data_root if self.data_root is not None else data_dir()

    @property
    def api_port(self) -> int:
        return _port_of_base(self.api_base)


def _port_of_base(base: str) -> int:
    parts = urlsplit(base)
    if parts.port:
        return int(parts.port)
    return 443 if parts.scheme == "https" else 80


def state_path(root: Path | None = None) -> Path:
    return (root if root is not None else data_dir()) / "state" / "watchdog.json"


def read_state(root: Path | None = None) -> dict[str, Any]:
    try:
        data = json.loads(state_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(root: Path | None, state: dict[str, Any]) -> None:
    """原子写：先落 `.tmp` 再 `os.replace`，中途断电也不会留下半截 JSON。"""
    path = state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def record_api_pid(root: Path | None, pid: int, port: int) -> None:
    state = read_state(root)
    state["api"] = {"pid": int(pid), "port": int(port), "recorded_at": _now_iso()}
    write_state(root, state)


# --------------------------------------------------------------------------- #
# 进程与端口（只用于「找端点 / 结束自己记住的那一个」）
# --------------------------------------------------------------------------- #


def _split_port(address: str) -> int | None:
    """`127.0.0.1:5950` / `[::1]:5950` → 5950。"""
    try:
        return int(address.rsplit(":", 1)[1])
    except (IndexError, ValueError):
        return None


def pid_listening_on(port: int) -> int | None:
    """当前监听 `port` 的本机 PID；查不到返回 None（尽力而为，不抛）。"""
    try:
        if os.name == "nt":
            listing = subprocess.run(
                ["netstat", "-ano", "-p", "TCP"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                check=False,
            ).stdout
            for line in listing.splitlines():
                parts = line.split()
                if len(parts) < 5 or parts[0].upper() != "TCP":
                    continue
                if parts[3].upper() != "LISTENING" or _split_port(parts[1]) != port:
                    continue
                try:
                    return int(parts[4])
                except ValueError:
                    continue
        else:
            listing = subprocess.run(
                ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                check=False,
            ).stdout
            for line in listing.splitlines():
                if line.strip().isdigit():
                    return int(line.strip())
    except (OSError, subprocess.SubprocessError):
        return None
    return None


def process_image_name(pid: int) -> str:
    """进程映像名（小写）；拿不到返回空串。"""
    try:
        if os.name == "nt":
            listing = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5,
                check=False,
            ).stdout
            for line in listing.splitlines():
                if line.startswith('"'):
                    return line.split('","', 1)[0].strip('"').lower()
            return ""
        comm = Path(f"/proc/{pid}/comm")
        if comm.is_file():
            return comm.read_text(encoding="utf-8").strip().lower()
        output = subprocess.run(
            ["ps", "-p", str(pid), "-o", "comm="],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=5,
            check=False,
        ).stdout
        return output.strip().lower()
    except (OSError, subprocess.SubprocessError):
        return ""


def terminate_pid(pid: int) -> bool:
    """只结束给定 PID，不按映像名扩散、不杀进程树。"""
    try:
        if os.name == "nt":
            result = subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
            )
            return result.returncode == 0
        os.kill(pid, signal.SIGTERM)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def kill_guard(
    pid: int,
    port: int,
    *,
    name_of: Callable[[int], str] = process_image_name,
    lookup: Callable[[int], int | None] = pid_listening_on,
) -> str:
    """能不能结束这个 PID：返回空串表示可以，否则是拒绝原因。

    判据只有两条：必须是**此刻真的在监听 API 端口**的那个 PID，且不能是
    工作区守护进程 `multica`。这比「记录过的 PID」更严——PID 会被系统复用，
    端口归属是当下事实。
    """
    if pid <= 0:
        return "无效 PID"
    if pid == os.getpid():
        return "拒绝结束看门狗自身"
    name = name_of(pid)
    if "multica" in name:
        return f"PID {pid} 是工作区守护进程（{name or '未知'}），拒绝结束"
    owner = lookup(port)
    if owner != pid:
        return f"PID {pid} 已不是端口 {port} 的监听者，跳过"
    return ""


# --------------------------------------------------------------------------- #
# 探活
# --------------------------------------------------------------------------- #


def probe_api(api_base: str, timeout: float = PROBE_TIMEOUT_SEC) -> dict[str, Any] | None:
    """GET `{api}/api/health`；失败（超时 / 非 200 / 非 JSON 对象）返回 None。"""
    url = f"{api_base.rstrip('/')}/api/health"
    try:
        response = httpx.get(url, timeout=timeout)
        if response.status_code != 200:
            return None
        payload = response.json()
    except (httpx.HTTPError, ValueError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def judge(payload: dict[str, Any] | None) -> str:
    """三态判定：探测失败 → offline；`db_ok=false` 或 `status!=ok` → degraded。"""
    if payload is None:
        return "offline"
    if payload.get("db_ok") is False:
        return "degraded"
    status = payload.get("status")
    if status is None:
        # 兼容还没升级的旧主服务：只有 `ok` 字段时以它为准
        return "ok" if payload.get("ok") else "degraded"
    return "ok" if status == "ok" else "degraded"


# --------------------------------------------------------------------------- #
# 重启
# --------------------------------------------------------------------------- #


def script_issue(path: str) -> str | None:
    """`--restart-script` 的静态校验；返回 None 表示可用。"""
    if not path:
        return None
    candidate = Path(path)
    if not candidate.is_absolute():
        return "必须是绝对路径"
    if not candidate.is_file():
        return "文件不存在"
    return None


def _run_command(run: Callable[..., Any], argv: list[str], *, timeout: float) -> tuple[int, str]:
    """执行一条固定 argv（`shell=False`）；返回值码与末尾输出。

    输出落临时文件而不是管道：重启脚本会拉起常驻子进程，它可能继承写端，
    管道永远等不到 EOF，`subprocess.run` 的 timeout 清理阶段会把整个请求拖死
    （实测 91s：30s 超时后仍阻塞到被拉起的 API 退出）。
    """
    handle, path = tempfile.mkstemp(prefix="ldst-restart-", suffix=".log")
    try:
        with os.fdopen(handle, "wb") as sink:
            try:
                completed = run(
                    argv,
                    stdout=sink,
                    stderr=subprocess.STDOUT,
                    timeout=timeout,
                    check=False,
                    shell=False,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                return 127, str(exc)[:300]
        with open(path, "rb") as sink:
            output = sink.read().decode("utf-8", "replace").strip()
        return int(completed.returncode), output[-300:]
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def spawn_command_issue(spec: str) -> str | None:
    """`--spawn-cmd` 的静态校验；返回 None 表示可用。"""
    if not spec.strip():
        return None
    try:
        argv = json.loads(spec)
    except json.JSONDecodeError:
        return "不是合法 JSON 数组"
    if not isinstance(argv, list) or not argv or not all(isinstance(part, str) for part in argv):
        return "必须是字符串数组"
    return None


def restart_service(
    config: WatchdogConfig,
    *,
    run: Callable[..., Any] = subprocess.run,
    lookup_pid: Callable[[int], int | None] = pid_listening_on,
    name_of: Callable[[int], str] = process_image_name,
    terminate: Callable[[int], bool] = terminate_pid,
    wait: Callable[[float], None] = time.sleep,
) -> tuple[int, dict[str, Any]]:
    """结束旧进程并按 spawn-cmd → 任务 → 脚本的顺序拉起；返回 (状态码, 载荷)。

    状态码沿用重启入口的契约（`web/platform.py` 也用它）：200 成功、
    409 没配置重启方式、429 冷却中、503 全部方式失败。
    """
    if config.supervised:
        # 托管模式下看板与看门狗同一棵树（QUOT-523）：计划任务会绕过 supervisor
        # 的归属判定复活一个它管不到的看板，「关闭全停」因此失效——只留 spawn-cmd。
        task = ""
        script = ""
    else:
        task = config.restart_task.strip()
        script = config.restart_script.strip()
    spawn_spec = config.spawn_cmd.strip()
    if not task and not script and not spawn_spec:
        return 409, {
            "ok": False,
            "reason": "未配置重启方式：请用 --spawn-cmd / --restart-task / --restart-script",
        }

    port = config.api_port
    owner = lookup_pid(port)
    if owner:
        try:
            record_api_pid(config.root, owner, port)
        except OSError:
            pass

    killed: int | None = None
    skipped: str | None = None
    if owner:
        refused = kill_guard(owner, port, name_of=name_of, lookup=lookup_pid)
        if refused:
            skipped = refused
        elif terminate(owner):
            killed = owner
            # 等旧进程真正让出端口（最多 5s），否则新进程会按 DEC-004 直接退出 3
            for _ in range(10):
                wait(0.5)
                if lookup_pid(port) is None:
                    break

    attempts: list[dict[str, Any]] = []
    if spawn_spec:
        # 被 supervisor 托管时优先直接拉起：同一进程树里不经 schtasks，
        # 看板因此随 supervisor 的整树停一起停（QUOT-523）。
        issue = spawn_command_issue(spawn_spec)
        if issue is not None:
            attempts.append({"via": "spawn", "ok": False, "detail": issue})
        else:
            argv = [str(part) for part in json.loads(spawn_spec)]
            code, detail = _run_command(run, argv, timeout=30)
            attempts.append({"via": "spawn", "ok": code == 0, "detail": detail})
            if code == 0:
                return 200, {
                    "ok": True,
                    "via": "spawn",
                    "killed_pid": killed,
                    "skipped": skipped,
                    "attempts": attempts,
                }

    if task:
        code, detail = _run_command(run, ["schtasks", "/run", "/tn", task], timeout=15)
        attempts.append({"via": "schtasks", "ok": code == 0, "detail": detail})
        if code == 0:
            return 200, {
                "ok": True,
                "via": "schtasks",
                "killed_pid": killed,
                "skipped": skipped,
                "attempts": attempts,
            }

    if script:
        issue = script_issue(script)
        if issue is not None:
            attempts.append({"via": "script", "ok": False, "detail": issue})
        else:
            code, detail = _run_command(
                run,
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script],
                timeout=30,
            )
            attempts.append({"via": "script", "ok": code == 0, "detail": detail})
            if code == 0:
                return 200, {
                    "ok": True,
                    "via": "script",
                    "killed_pid": killed,
                    "skipped": skipped,
                    "attempts": attempts,
                }

    return 503, {
        "ok": False,
        "reason": "所有重启方式都失败（见 attempts）",
        "killed_pid": killed,
        "skipped": skipped,
        "attempts": attempts,
    }


# --------------------------------------------------------------------------- #
# 自愈监控循环（无端口）
# --------------------------------------------------------------------------- #


def run_self_heal(
    config: WatchdogConfig,
    *,
    probe: Callable[[str], dict[str, Any] | None] = probe_api,
    restart: Callable[[WatchdogConfig], tuple[int, dict[str, Any]]] = restart_service,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    stop_requested: Callable[[], bool] = lambda: False,
    log: Callable[[str], None] = _log,
) -> None:
    """无端口自愈主循环：探活 → 确认中断 → 节流内自愈重启，永不返回。

    - 连续 `FAILURES_BEFORE_RED` 次失败才确认中断；恢复（含 degraded——服务
      还活着）立即清零，单次抖动绝不触发重启。
    - 两次自愈之间至少隔 `RESTART_THROTTLE_SEC` 秒，防止「拉起又立刻失败」
      变成重启风暴。
    - `stop_requested()` 周期性问询（page_boot 守护的停止标记），置真即干净退出。
    """
    interval = max(0.5, float(config.probe_interval))
    last_restart = 0.0
    failures = 0
    log(f"无端口看门狗已启动（探活 {config.api_base}，{interval}s 一拍）")
    while True:
        if stop_requested():
            log("收到停止标记，看门狗退出")
            return
        alive = probe(config.api_base) is not None
        if alive:
            failures = 0
        else:
            failures += 1
            if failures >= FAILURES_BEFORE_RED:
                now = monotonic()
                if last_restart and now - last_restart < RESTART_THROTTLE_SEC:
                    # 冷却期：不重启也不清零计数，保持「确认中断」状态
                    pass
                else:
                    log(f"主服务连续 {failures} 次无响应，开始自愈重启")
                    try:
                        code, payload = restart(config)
                    except Exception as exc:  # noqa: BLE001 - 自愈失败要留下记录
                        log(f"自愈重启异常：{exc}")
                        code, payload = 500, {"ok": False, "reason": str(exc)[:300]}
                    last_restart = now
                    failures = 0
                    log(f"自愈重启完成（status={code}，ok={payload.get('ok')}）")
        sleep(interval)


def _default_stop_marker(root: Path) -> Path:
    return root / "state" / "watchdog-stop"


def _stop_requested_by_marker(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def serve_watchdog(config: WatchdogConfig) -> None:
    """阻塞运行无端口自愈监控器；Ctrl+C 正常退出。"""
    issue = script_issue(config.restart_script)
    if issue is not None:
        error(f"--restart-script 不可用（{issue}）：{config.restart_script}")
        raise typer.Exit(EXIT_CONFIG)
    issue = spawn_command_issue(config.spawn_cmd)
    if issue is not None:
        error(f"--spawn-cmd 不可用（{issue}）：{config.spawn_cmd}")
        raise typer.Exit(EXIT_CONFIG)

    state = read_state(config.root)
    state["watchdog"] = {
        "pid": os.getpid(),
        "port": None,
        "version": __version__,
        "started_at": _now_iso(),
    }
    try:
        write_state(config.root, state)
    except OSError as exc:
        _log(f"状态文件写入失败（不影响自愈）：{exc}")

    marker = _default_stop_marker(config.root)
    try:
        run_self_heal(config, stop_requested=lambda: _stop_requested_by_marker(marker))
    except KeyboardInterrupt:  # Ctrl+C / Ctrl+Break
        pass
    _log("无端口看门狗已停止")
    raise typer.Exit(EXIT_OK)


def watchdog_command(
    api: Annotated[str, typer.Option(help="被测主服务基地址")] = DEFAULT_API_BASE,
    restart_task: Annotated[
        str, typer.Option("--restart-task", help="计划任务名（schtasks /run /tn）")
    ] = DEFAULT_RESTART_TASK,
    restart_script: Annotated[
        str, typer.Option("--restart-script", help="无提权重启脚本的绝对路径（.ps1）")
    ] = "",
    spawn_cmd: Annotated[
        str,
        typer.Option(
            "--spawn-cmd",
            help="直接拉起看板的 argv（JSON 字符串数组）；被 supervisor 托管时由它给出",
        ),
    ] = "",
    probe_interval: Annotated[
        float, typer.Option("--probe-interval", help="探活间隔（秒）")
    ] = 2.0,
    output: OutputOption = None,
) -> None:
    """无端口自愈监控器：探活主服务，确认中断后自动重启。不监听任何端口。"""
    del output  # 长驻进程没有结构化输出；保留参数是为了全局 --output 契约
    serve_watchdog(
        WatchdogConfig(
            api_base=api,
            restart_task=restart_task,
            restart_script=restart_script,
            spawn_cmd=spawn_cmd,
            supervised=supervised(),
            probe_interval=probe_interval,
        )
    )


def register(app: typer.Typer) -> None:
    app.command(
        "watchdog",
        help="无端口自愈监控器：探活主服务，确认中断后自动重启（不监听任何端口）",
    )(watchdog_command)


__all__ = [
    "DEFAULT_API_BASE",
    "DEFAULT_RESTART_TASK",
    "FAILURES_BEFORE_RED",
    "RESTART_THROTTLE_SEC",
    "SUPERVISED_ENV",
    "WatchdogConfig",
    "judge",
    "kill_guard",
    "pid_listening_on",
    "probe_api",
    "read_state",
    "register",
    "restart_service",
    "run_self_heal",
    "script_issue",
    "serve_watchdog",
    "spawn_command_issue",
    "supervised",
    "state_path",
    "terminate_pid",
    "write_state",
]
