"""独立看门狗（QUOT-443 / DEC-063）：5952 上的探活与重启入口。

为什么不塞进 `ldst run`：主服务自重启的前提是它还活着；进程挂在 uvicorn 里时
重启入口和被测对象一起消失。看门狗因此是**第二个常驻进程**，只依赖标准库 +
httpx + fastapi/uvicorn（web 依赖，与 `ldst run` 相同）：

- `GET /platform/health`：实时探测 `{api}/api/health`（超时 2s），三态
  `ok` / `degraded` / `offline`；只读健康字段，不读持仓、日志、任何隐私数据。
- `POST /platform/restart`：先结束**自己记录的、且此刻仍占用 API 端口的那个 PID**，
  再按 `--spawn-cmd`（直接拉起 argv）→ `--restart-task`（schtasks）→
  `--restart-script`（固定绝对路径）顺序尝试。被 supervisor 托管时只给
  `--spawn-cmd`（QUOT-523：同一进程树，不再经计划任务复活看板）。
  请求体不参与任何命令拼装；30s 内重复请求回 429。
- `GET /`：纯内联离线壳页（无 CDN / 无外部依赖）。主服务已死时打开它，
  能看到状态和重启按钮，恢复后自动跳回看板。

安全边界（硬约束）：

1. 只绑回环；`POST` 校验 `Origin` 白名单，无 `Origin` 拒绝，绝不回 `*`。
2. 绝不按映像名批量杀进程；只杀「记录的 PID == 当前 API 端口监听者」，
   且映像名含 `multica` 时一律拒绝（那是工作区守护进程）。
3. 重启脚本路径只来自命令行参数，且必须是绝对路径的现存文件。
4. 状态文件 `state/watchdog.json` 只写 PID / 端口 / 时间戳 / 版本，无凭据。

端口 `5952` 是 DEC-063 的裁决：`5951` 已被 MCP http 固定占用（DEC-004）。
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
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
from ladderstock.cli.run import DEFAULT_HOST, LOOPBACK_HOSTS, is_addr_in_use, port_free
from ladderstock.errors import EXIT_CONFIG, EXIT_OK

DEFAULT_WATCHDOG_PORT = 5952
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

#: 看板（5950）、vite dev（5173）。看门狗**自己的** origin 不进静态白名单：
#: 它跟实例端口走（`origin_allowed` 的 `port` 参数），QUOT-589 之后 dev 看门狗
#: （5953）不得把 prod 看门狗的 5952 也当成自己的回环 origin 放行。
ALLOWED_ORIGINS = frozenset(
    {
        "http://127.0.0.1:5950",
        "http://localhost:5950",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    }
)

#: 只对 `/platform/*` 生效的响应头
NO_STORE = "no-store"


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
    host: str = DEFAULT_HOST
    port: int = DEFAULT_WATCHDOG_PORT
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


def _api_state(state: dict[str, Any]) -> dict[str, Any]:
    value = state.get("api")
    return value if isinstance(value, dict) else {}


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
            errors="replace",
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


def build_health_payload(
    config: WatchdogConfig,
    uptime_sec: int,
    *,
    probe: Callable[[], dict[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """平台健康汇总：顶层字段描述**主服务**，`watchdog` 才是看门狗自己。

    顶层与 `/api/health` 同名同义（`status` / `uptime_sec` / `version` / `db_ok`），
    离线时 `uptime_sec` / `db_ok` 为 `null`——不能拿看门狗自己的运行时长冒充平台时长。
    """
    api = (probe or (lambda: probe_api(config.api_base)))()
    return {
        "status": judge(api),
        "uptime_sec": api.get("uptime_sec") if api is not None else None,
        "version": (api.get("version") if api is not None else None) or __version__,
        "db_ok": api.get("db_ok") if api is not None else None,
        "checked_at": _now_iso(),
        "api": api,
        "watchdog": {"uptime_sec": uptime_sec, "version": __version__},
    }


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
    """结束旧进程并按 spawn-cmd → 任务 → 脚本的顺序拉起；返回 (HTTP 状态码, 载荷)。"""
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
# 离线壳页
# --------------------------------------------------------------------------- #


def offline_shell_html(api_base: str = DEFAULT_API_BASE) -> str:
    """纯内联壳页：不引任何外部资源，主服务全挂时也能打开。"""
    target = json.dumps(api_base.rstrip("/")).replace("<", "\\u003c")
    return (
        """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LadderStock · 服务状态</title>
<style>
:root { color-scheme: light dark; }
body { margin: 0; min-height: 100vh; display: grid; place-items: center;
  font: 14px/1.6 -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
  background: #f6f7f9; color: #1c1e21; }
@media (prefers-color-scheme: dark) { body { background: #16181c; color: #e8eaee; } }
main { width: min(520px, 92vw); padding: 28px; border-radius: 12px;
  background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
@media (prefers-color-scheme: dark) { main { background: #202329; } }
h1 { margin: 0 0 4px; font-size: 18px; }
.sub { margin: 0 0 20px; color: #6b7280; font-size: 12px; }
.state { display: flex; align-items: center; gap: 10px; padding: 14px 16px;
  border-radius: 8px; background: #f2f3f5; margin-bottom: 14px; }
@media (prefers-color-scheme: dark) { .state { background: #2a2e35; } }
.dot { width: 10px; height: 10px; border-radius: 50%; background: #9ca3af; }
.dot.ok { background: #16a34a; } .dot.degraded { background: #d97706; }
.dot.offline { background: #dc2626; }
.kv { display: grid; grid-template-columns: 88px 1fr; gap: 4px 12px; font-size: 13px; }
.kv dt { color: #6b7280; } .kv dd { margin: 0; font-variant-numeric: tabular-nums; }
button { margin-top: 18px; width: 100%; padding: 10px 14px; border: 0; border-radius: 8px;
  background: #2563eb; color: #fff; font: inherit; font-weight: 600; cursor: pointer; }
button:disabled { opacity: .55; cursor: progress; }
.msg { margin: 10px 0 0; font-size: 12px; color: #6b7280; word-break: break-word; }
.msg.err { color: #dc2626; }
</style>
</head>
<body>
<main>
  <h1>LadderStock 服务状态</h1>
  <p class="sub">这是独立看门狗（端口 5952）。主服务恢复后会自动跳回看板。</p>
  <div class="state"><span id="dot" class="dot"></span><strong id="label">探测中…</strong></div>
  <dl class="kv">
    <dt>已运行</dt><dd id="uptime">—</dd>
    <dt>版本</dt><dd id="version">—</dd>
    <dt>数据库</dt><dd id="db">—</dd>
    <dt>检查时间</dt><dd id="checked">—</dd>
  </dl>
  <button id="restart" type="button" disabled>重启平台</button>
  <p id="msg" class="msg"></p>
</main>
<script>
(function () {
  var TARGET = __TARGET__;
  var LABEL = { ok: '服务正常', degraded: '服务降级', offline: '服务离线' };
  var dot = document.getElementById('dot');
  var label = document.getElementById('label');
  var uptime = document.getElementById('uptime');
  var version = document.getElementById('version');
  var db = document.getElementById('db');
  var checked = document.getElementById('checked');
  var button = document.getElementById('restart');
  var msg = document.getElementById('msg');
  var restarting = false;

  function fmtUptime(sec) {
    if (typeof sec !== 'number' || sec < 0) return '—';
    var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    return (h ? h + ' 小时 ' : '') + (h || m ? m + ' 分 ' : '') + s + ' 秒';
  }
  function paint(data) {
    var state = data.status || 'offline';
    dot.className = 'dot ' + state;
    label.textContent = LABEL[state] || state;
    uptime.textContent = fmtUptime(data.uptime_sec);
    version.textContent = data.version || '—';
    db.textContent = data.db_ok === true ? '可读' : (data.db_ok === false ? '不可读' : '未知');
    checked.textContent = data.checked_at || '—';
    button.disabled = restarting || state !== 'offline';
    button.textContent = restarting ? '重启中…' : '重启平台';
    return state;
  }
  function poll() {
    fetch('/platform/health', { cache: 'no-store' })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var state = paint(data);
        if (state === 'ok') { window.location.replace(TARGET); return; }
        setTimeout(poll, 3000);
      })
      .catch(function () {
        dot.className = 'dot offline';
        label.textContent = '看门狗不可达';
        button.disabled = restarting;
        setTimeout(poll, 3000);
      });
  }
  button.addEventListener('click', function () {
    if (!window.confirm('确认重启 LadderStock 平台？正在编辑的内容可能丢失。')) return;
    restarting = true;
    button.disabled = true;
    button.textContent = '重启中…';
    msg.className = 'msg';
    msg.textContent = '已发出重启请求，等待服务恢复（最多 90 秒）…';
    fetch('/platform/restart', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
      cache: 'no-store',
    })
      .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, data: d }; }); })
      .then(function (res) {
        if (!res.ok || !res.data.ok) {
          throw new Error((res.data && (res.data.reason || res.data.detail)) || '重启请求失败');
        }
        var deadline = Date.now() + 90000;
        (function wait() {
          fetch('/platform/health', { cache: 'no-store' })
            .then(function (r) { return r.json(); })
            .then(function (data) {
              if (data.status === 'ok') { window.location.replace(TARGET); return; }
              if (Date.now() > deadline) {
                restarting = false;
                msg.className = 'msg err';
                msg.textContent = '90 秒内未恢复；请查看看门狗日志或手动启动 ldst run。';
                paint(data);
                return;
              }
              setTimeout(wait, 2000);
            })
            .catch(function () { setTimeout(wait, 2000); });
        })();
      })
      .catch(function (err) {
        restarting = false;
        button.disabled = false;
        button.textContent = '重启平台';
        msg.className = 'msg err';
        msg.textContent = String(err && err.message ? err.message : err);
      });
  });
  poll();
})();
</script>
</body>
</html>
"""
    ).replace("__TARGET__", target)


# --------------------------------------------------------------------------- #
# HTTP 应用
# --------------------------------------------------------------------------- #


def _cors_headers(origin: str) -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Max-Age": "600",
        "Vary": "Origin",
        "Cache-Control": NO_STORE,
    }


def origin_allowed(origin: str | None, port: int = DEFAULT_WATCHDOG_PORT) -> bool:
    if not origin:
        return False
    if origin in ALLOWED_ORIGINS:
        return True
    # `--port` 换端口时壳页 origin 跟着变，但仍只认回环上的看门狗端口
    return origin in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}


def create_watchdog_app(
    config: WatchdogConfig | None = None,
    *,
    probe: Callable[[], dict[str, Any] | None] | None = None,
    restart: Callable[[], tuple[int, dict[str, Any]]] | None = None,
) -> Any:
    """看门狗 FastAPI 应用；`probe` / `restart` 可注入，便于单测。"""
    from fastapi import FastAPI, Request
    from fastapi.responses import HTMLResponse, JSONResponse, Response

    config = config if config is not None else WatchdogConfig()

    started_at = time.monotonic()
    probe_fn = probe if probe is not None else (lambda: probe_api(config.api_base))
    restart_fn = restart if restart is not None else (lambda: restart_service(config))
    throttle = {"last": 0.0}
    lock = threading.Lock()

    app = FastAPI(
        title="LadderStock 看门狗",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def platform_guard(request: Request, call_next: Any) -> Any:
        path = request.url.path
        origin = request.headers.get("origin")
        if not path.startswith("/platform"):
            response = await call_next(request)
            response.headers.setdefault("Cache-Control", NO_STORE)
            return response

        if request.method == "OPTIONS":
            if not origin_allowed(origin, config.port):
                return JSONResponse({"detail": "Forbidden"}, status_code=403)
            return Response(status_code=204, headers=_cors_headers(origin or ""))

        if request.method not in {"GET", "HEAD"} and not origin_allowed(origin, config.port):
            # 无 Origin / 白名单外一律拒绝：不依赖浏览器自动带的凭据，也不回 `*`
            return JSONResponse({"detail": "Forbidden"}, status_code=403)

        response = await call_next(request)
        if origin_allowed(origin, config.port):
            for key, value in _cors_headers(origin or "").items():
                response.headers[key] = value
        else:
            response.headers["Cache-Control"] = NO_STORE
        return response

    @app.get("/platform/health")
    def platform_health() -> dict[str, Any]:
        uptime = max(0, int(time.monotonic() - started_at))
        return build_health_payload(config, uptime, probe=probe_fn)

    @app.post("/platform/restart")
    def platform_restart() -> Any:
        now = time.monotonic()
        with lock:
            elapsed = now - throttle["last"]
            if throttle["last"] and elapsed < RESTART_THROTTLE_SEC:
                remaining = int(RESTART_THROTTLE_SEC - elapsed) + 1
                return JSONResponse(
                    {"ok": False, "reason": f"重启过于频繁，请 {remaining}s 后再试"},
                    status_code=429,
                )
            throttle["last"] = now
        try:
            code, payload = restart_fn()
        except Exception as exc:  # noqa: BLE001 - 重启失败要如实回报，不能静默
            return JSONResponse({"ok": False, "reason": str(exc)[:300]}, status_code=500)
        return JSONResponse(payload, status_code=code)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def shell() -> HTMLResponse:
        return HTMLResponse(offline_shell_html(config.api_base))

    return app


# --------------------------------------------------------------------------- #
# 启动
# --------------------------------------------------------------------------- #


def _track_api_pid(config: WatchdogConfig) -> None:
    """后台跟踪 API 端口监听者，把 PID 记进 `state/watchdog.json`（供重启使用）。"""
    interval = max(1.0, float(config.probe_interval))
    while True:
        time.sleep(interval)
        try:
            if probe_api(config.api_base) is None:
                continue
            pid = pid_listening_on(config.api_port)
            if not pid:
                continue
            if _api_state(read_state(config.root)).get("pid") == pid:
                continue
            record_api_pid(config.root, pid, config.api_port)
        except (OSError, ValueError):
            continue


def serve_watchdog(config: WatchdogConfig) -> None:
    """阻塞运行看门狗；Ctrl+C 正常退出。"""
    if config.host not in LOOPBACK_HOSTS:
        error("看门狗只允许绑定回环地址（DEC-063）")
        raise typer.Exit(EXIT_CONFIG)
    if not port_free(config.port, config.host):
        error(
            f"端口 {config.port} 已被占用；请用 --port 指定其他端口（不会自动换端口，见 DEC-004）"
        )
        raise typer.Exit(EXIT_CONFIG)
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
        "host": config.host,
        "port": config.port,
        "version": __version__,
        "started_at": _now_iso(),
    }
    api_pid = pid_listening_on(config.api_port)
    if api_pid:
        state["api"] = {"pid": api_pid, "port": config.api_port, "recorded_at": _now_iso()}
    try:
        write_state(config.root, state)
    except OSError as exc:
        _log(f"状态文件写入失败（不影响探活）：{exc}")

    app = create_watchdog_app(config)
    threading.Thread(target=_track_api_pid, args=(config,), daemon=True).start()

    from ladderstock.web.server import serve

    _log(f"看门狗已启动 http://{config.host}:{config.port}（探活 {config.api_base}；Ctrl+C 退出）")
    try:
        serve(app, host=config.host, port=config.port)
    except KeyboardInterrupt:  # Ctrl+C / Ctrl+Break
        pass
    except OSError as exc:
        if is_addr_in_use(exc):
            error(
                f"端口 {config.port} 已被占用：{exc}；"
                "请用 --port 指定其他端口（不会自动换端口，见 DEC-004）"
            )
        else:
            error(f"端口 {config.port} 无法监听：{exc}；请用 --port 指定其他端口（见 DEC-004）")
        raise typer.Exit(EXIT_CONFIG) from None
    _log("看门狗已停止，端口已释放")
    raise typer.Exit(EXIT_OK)


def watchdog_command(
    host: Annotated[str, typer.Option(help="监听地址，默认仅回环")] = DEFAULT_HOST,
    port: Annotated[int, typer.Option(help="看门狗端口（DEC-063 固定 5952）")] = (
        DEFAULT_WATCHDOG_PORT
    ),
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
        float, typer.Option("--probe-interval", help="后台探活间隔（秒），用于跟踪 API PID")
    ] = 2.0,
    output: OutputOption = None,
) -> None:
    """独立看门狗：5952 探活 + 重启入口。主服务挂了它仍活着。"""
    del output  # 长驻进程没有结构化输出；保留参数是为了全局 --output 契约
    serve_watchdog(
        WatchdogConfig(
            api_base=api,
            host=host,
            port=port,
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
        help="独立看门狗：5952 探活 + 重启入口（主服务挂了它仍活着）",
    )(watchdog_command)


__all__ = [
    "ALLOWED_ORIGINS",
    "DEFAULT_API_BASE",
    "DEFAULT_RESTART_TASK",
    "DEFAULT_WATCHDOG_PORT",
    "RESTART_THROTTLE_SEC",
    "SUPERVISED_ENV",
    "WatchdogConfig",
    "build_health_payload",
    "create_watchdog_app",
    "judge",
    "kill_guard",
    "offline_shell_html",
    "origin_allowed",
    "pid_listening_on",
    "probe_api",
    "read_state",
    "register",
    "restart_service",
    "script_issue",
    "serve_watchdog",
    "spawn_command_issue",
    "supervised",
    "state_path",
    "write_state",
]
