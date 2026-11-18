"""Redis 单一入口（DEC-086 决策 2）。

官方没有 Windows 版，增强包用社区移植版，并按平台放四份目录：
`runtime/redis/{win-x64,linux-x64,linux-arm64,macos-arm64}`，**共用一份 `conf/redis.conf`**。
对外只有这一个入口，页面/CLI/安装器都从这里起停——不在别处再拼一遍命令行。

二进制缺失时如实报「未安装，缺哪个文件」，不假装启动成功。
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from ladderstock.modules.financecoffer import layout

#: 各平台的服务器/客户端可执行文件名（移植版常见两种布局：根目录或 bin/）
_SERVER_NAMES = ("redis-server.exe", "redis-server")
_CLI_NAMES = ("redis-cli.exe", "redis-cli")


def _find_binary(root: Path | None, names: tuple[str, ...]) -> Path | None:
    base = layout.redis_dir(root)
    for name in names:
        for candidate in (base / name, base / "bin" / name):
            if candidate.is_file():
                return candidate
    return None


def server_binary(root: Path | None = None) -> Path | None:
    return _find_binary(root, _SERVER_NAMES)


def cli_binary(root: Path | None = None) -> Path | None:
    return _find_binary(root, _CLI_NAMES)


def conf_path(root: Path | None = None) -> Path:
    """按 env 选 conf：测试服 `redis.conf`（历史名），正式服 `redis-prod.conf`。

    共用一份 conf 会让两个服抢同一个端口的 Redis。分开之后，正式服第一次运行
    由 :func:`ensure_conf` 从基准 conf 派生，不需要人工维护第二份文件。
    """
    return layout.conf_dir(root) / f"redis{layout.env_suffix()}.conf"


def _conf_port_for_env() -> int:
    """本实例 Redis 的基准端口（正式 6379 / 测试 16379），与 ``services._offset`` 同源。"""
    try:
        from ladderstock.core.boundary import port_offset

        return 6379 + int(port_offset())
    except Exception:  # pragma: no cover - 取不到偏移按正式服算
        return 6379


def ensure_conf(root: Path | None = None) -> Path:
    """确保本 env 的 conf 存在；缺失时从基准 conf 派生（只替换 port 与 dir）。"""
    conf = conf_path(root)
    if conf.is_file():
        return conf
    base = layout.conf_dir(root) / "redis.conf"
    port = _conf_port_for_env()
    data_name = layout.data_dir(root, "redisdata").name
    if base.is_file():
        lines: list[str] = []
        for line in base.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if text.lower().startswith("port "):
                lines.append(f"port {port}")
            elif text.lower().startswith("dir "):
                lines.append(f"dir {data_name}")
            else:
                lines.append(line)
    else:  # pragma: no cover - 安装器总会写基准 conf
        lines = [f"port {port}", f"dir {data_name}"]
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return conf


def status(root: Path | None = None, *, timeout: float = 2.0) -> dict[str, Any]:
    """当前 Redis 是否可起、是否在跑；一切判断都基于实际文件与一次 PING。"""
    plat = layout.current_redis_platform()
    binary = server_binary(root)
    if binary is None:
        return {
            "ok": False,
            "state": "not_installed",
            "platform": plat,
            "platform_dir": str(layout.redis_dir(root)),
            "detail": f"Redis 运行时未安装：{layout.redis_dir(root)} 里没有 redis-server",
        }
    conf = conf_path(root)
    if not conf.is_file():
        return {
            "ok": False,
            "state": "missing_conf",
            "platform": plat,
            "detail": f"缺少共用配置：{conf}（安装器会写入一份默认配置）",
        }

    port = _conf_port(conf) or _conf_port_for_env()
    cli = cli_binary(root)
    if cli is None:
        return {
            "ok": False,
            "state": "missing_cli",
            "platform": plat,
            "port": port,
            "detail": "缺少 redis-cli：装好后再判断是否在跑",
        }
    try:
        out = subprocess.run(
            [str(cli), "-p", str(port), "ping"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            cwd=str(layout.module_root(root)),
        )
        alive = out.stdout.strip().upper().startswith("PONG")
    except (OSError, subprocess.TimeoutExpired) as exc:
        # 连不上（超时/拒绝）就是没在跑，不是「未知错误」——否则启动逻辑会卡在这里
        return {
            "ok": False,
            "state": "stopped",
            "platform": plat,
            "port": port,
            "detail": f"PING 无响应（{type(exc).__name__}）",
        }

    return {
        "ok": alive,
        "state": "running" if alive else "stopped",
        "platform": plat,
        "port": port,
        "binary": str(binary),
        "conf": str(conf),
        "detail": "PONG" if alive else "进程存在但没有响应 PING",
    }


def start(root: Path | None = None, *, wait_seconds: float = 3.0) -> dict[str, Any]:
    """后台起 Redis；已经 PONG 就直接返回 running，不重复起第二个实例。"""
    current = status(root)
    if current.get("state") == "running":
        return {**current, "ok": True, "detail": "已在运行，未重复启动"}
    if current.get("state") not in {"stopped"} and not current.get("ok"):
        # 未安装 / 缺配置 / 缺 cli 都不能盲目起
        return current

    binary = server_binary(root)
    if binary is None:  # pragma: no cover - 上面 status 已拦
        return current
    pid_file = layout.run_dir(root) / f"redis{layout.env_suffix()}.pid"
    log_file = layout.logs_dir(root) / f"redis{layout.env_suffix()}.log"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    conf = ensure_conf(root)
    cmd = [str(binary), f"conf/{conf.name}"]
    # cwd=模块根 + 相对 conf 路径：cygwin 移植版不认盘符路径（会把 D:\ 拼成 /cygdrive/c/...）
    cwd = str(layout.module_root(root))
    creationflags = 0
    if sys.platform.startswith("win"):
        creationflags = 0x00000008  # DETACHED_PROCESS：不挂在当前控制台上
    try:
        with log_file.open("ab") as log:
            subprocess.Popen(
                cmd,
                cwd=cwd,
                stdout=log,
                stderr=log,
                creationflags=creationflags,
                close_fds=True,
            )
    except OSError as exc:
        return {"ok": False, "state": "error", "detail": f"启动失败：{exc}"}

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        probe = status(root)
        if probe.get("state") == "running":
            return {**probe, "ok": True, "detail": "已启动并响应 PING"}
        time.sleep(0.3)
    return {
        "ok": False,
        "state": "starting",
        "detail": f"已拉起进程但 {wait_seconds:.0f} 秒内没有 PONG，日志见 {log_file}",
    }


def stop(root: Path | None = None, *, timeout: float = 5.0) -> dict[str, Any]:
    """用 redis-cli shutdown 优雅停；没装/没跑就如实返回。"""
    current = status(root)
    if current.get("state") != "running":
        return {**current, "ok": current.get("state") == "stopped", "detail": "没有在跑的 Redis"}

    cli = cli_binary(root)
    if cli is None:  # pragma: no cover
        return {"ok": False, "state": "missing_cli", "detail": "缺少 redis-cli，无法优雅停止"}
    port = int(current.get("port") or 16379)
    try:
        subprocess.run(
            [str(cli), "-p", str(port), "shutdown"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "state": "error", "detail": str(exc)}

    deadline = time.time() + timeout
    while time.time() < deadline:
        if status(root).get("state") != "running":
            return {"ok": True, "state": "stopped", "detail": "已停止"}
        time.sleep(0.3)
    return {"ok": False, "state": "running", "detail": f"{timeout:.0f} 秒内仍能 PONG，可能停不掉"}


def _conf_port(conf: Path) -> int | None:
    try:
        for line in conf.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            if text.lower().startswith("port "):
                return int(text.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def platform_matrix(root: Path | None = None) -> dict[str, Any]:
    """三端清单（给页面展示「打哪个包、缺哪份」）。"""

    def _has_binary(name: str) -> bool:
        base = layout.redis_dir(root, platform=name)
        return any((base / exe).is_file() for exe in ("redis-server", "redis-server.exe")) or any(
            (base / "bin" / exe).is_file() for exe in ("redis-server", "redis-server.exe")
        )

    return {
        "current": layout.current_redis_platform(),
        "host": f"{platform.system()} {platform.machine()}",
        "platforms": {
            name: {
                "dir": str(layout.redis_dir(root, platform=name)),
                "installed": _has_binary(name),
            }
            for name in layout.REDIS_PLATFORMS
        },
    }


def as_json(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False)


__all__ = [
    "as_json",
    "cli_binary",
    "conf_path",
    "platform_matrix",
    "server_binary",
    "start",
    "status",
    "stop",
]
