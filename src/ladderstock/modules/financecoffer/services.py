"""增强包服务的拉起与体检（DEC-086 决策 4/5 的运行面）。

三个必需服务 + 一个可选服务，全部在模块目录内运行、端口可覆盖：

| 服务 | 默认端口 | 数据 |
|---|---|---|
| PostgreSQL | 15432 | `pgdata/` |
| Redis（移植版） | 16379 | `redisdata/`（conf 里指好） |
| MinIO | 19000 / 控制台 19001 | `miniodata/` |
| embedding（可选） | 18010 | `runtime/embedding/`（bge-m3） |

端口一律可被 `LDST_FC_*_PORT` 覆盖：测试服整体 +10000 平移时不必改代码。
每个启动动作都做一次真实探活（`pg_isready` / `PING` / `/minio/health/live`），
不看「命令返回 0」就宣布成功——那正是上次「端口亮了但服务是残留转发」的坑。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from ladderstock.modules.financecoffer import layout, redis_service


def _offset() -> int:
    """当前实例的端口偏移（正式服 0，测试服 10000）。

    2026-11-18 修正：此前的默认值把「测试服口径」当成了默认（PG 15432 / Redis 16379 /
    MinIO 19000 / embedding 18010 全是 base+10000），只有 API 写了裸 8100，于是同一个
    实例里 API 落在 8100、其余落 18100 段，测试服 peer 打不通也解释不通。
    正确口径是所有服务统一取 `base + port_offset()`：正式服 5432/6379/9000/8010/8100，
    测试服 15432/16379/19000/18010/18100。环境变量 `LDST_FC_*_PORT` 仍可逐项覆盖。
    """
    try:
        from ladderstock.core.boundary import port_offset

        return int(port_offset())
    except Exception:  # pragma: no cover - 取不到偏移就按正式服算
        return 0


def _port(env_name: str, base: int) -> int:
    value = (os.environ.get(env_name) or "").strip()
    try:
        return int(value) if value else base + _offset()
    except ValueError:
        return base + _offset()


def _is_elevated() -> bool:
    """当前进程是否持有提升的管理员令牌（PostgreSQL 会因此拒绝启动）。"""
    if not sys.platform.startswith("win"):
        return False
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # pragma: no cover - 取不到就当非管理员
        return False


def _start_as_basic_user(root: Path | None, cmd: list[str], log: Path) -> bool:
    """用 `runas /trustlevel:0x20000`（基本用户令牌）拉起 PG。

    实测：管理员会话下直接起 postgres 会被官方限制拒绝（"不能以具有管理员权限的用户运行"），
    而低权限信任级别能正常起并监听端口。脚本落在模块自己的 `run/` 下，输出进 logs。
    """
    run_dir = layout.run_dir(root)
    run_dir.mkdir(parents=True, exist_ok=True)
    # 脚本名按 env 分开：两服共用一份时，测试服每轮监控都会覆盖它，
    # 正在起的正式服拿到的就是别人的 -D 参数（表现为「20s 内未就绪」且日志无新行）。
    script = run_dir / f"pg-lowpriv{layout.env_suffix()}.cmd"
    body = subprocess.list2cmdline(cmd)
    script.write_text("@echo off\r\n" + f'{body} >> "{log}" 2>&1\r\n', encoding="utf-8")
    try:
        out = subprocess.run(
            ["runas", "/trustlevel:0x20000", str(script)],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return out.returncode == 0


def pg_port() -> int:
    return _port("LDST_FC_PG_PORT", 5432)


def redis_port() -> int:
    return _port("LDST_FC_REDIS_PORT", 6379)


def minio_api_port() -> int:
    return _port("LDST_FC_MINIO_API_PORT", 9000)


def minio_console_port() -> int:
    return _port("LDST_FC_MINIO_CONSOLE_PORT", 9001)


def api_port() -> int:
    """FinanceCoffer API（结构化查询 / 宽表 / 公告）。"""
    return _port("LDST_FC_API_PORT", 8100)


def embedding_port() -> int:
    """bge-m3 embedding 服务（向量检索）。"""
    return _port("LDST_FC_EMBEDDING_PORT", 8010)


def api_deps_dir(root: Path | None = None) -> Path:
    return layout.module_root(root) / "runtime" / "api-deps"


def api_src_dir(root: Path | None = None) -> Path:
    return layout.module_root(root) / "runtime" / "api" / "src"


def embedding_deps_dir(root: Path | None = None) -> Path:
    return layout.module_root(root) / "runtime" / "embedding-deps"


def _http_json(url: str, *, timeout: float = 3.0) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return None


# ------------------------------------------------------------------ PostgreSQL


def pg_bin(root: Path | None = None, name: str = "pg_ctl") -> Path | None:
    """两种 PG 布局都要认：EDB 官方包在 `pg/bin`，conda 环境在 `pg/Library/bin`（win）。

    conda 布局是方案 A 的结果（postgresql + pgvector 同编译链，见 `pg_runtime.py`）；
    只认 `pg/bin` 会让 conda 安装被判成「未安装」。
    """
    from ladderstock.modules.financecoffer import pg_runtime

    for base in pg_runtime.pg_bin_candidates(root):
        for candidate in (base / f"{name}.exe", base / name):
            if candidate.is_file():
                return candidate
    return None


def pg_status(root: Path | None = None, *, timeout: float = 5.0) -> dict[str, Any]:
    binary = pg_bin(root)
    if binary is None:
        return {
            "ok": False,
            "state": "not_installed",
            "detail": f"PostgreSQL 未安装：{layout.pg_dir(root)}/bin 里没有 pg_ctl",
        }
    data = layout.data_dir(root, "pgdata")
    if not (data / "PG_VERSION").is_file():
        return {"ok": False, "state": "no_cluster", "detail": f"pgdata 还没有 initdb：{data}"}

    ready = pg_bin(root, "pg_isready")
    if ready is None:
        return {"ok": False, "state": "missing_tool", "detail": "缺少 pg_isready"}
    try:
        out = subprocess.run(
            [str(ready), "-h", "127.0.0.1", "-p", str(pg_port())],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "state": "error", "detail": str(exc)}
    alive = out.returncode == 0
    return {
        "ok": alive,
        "state": "running" if alive else "stopped",
        "port": pg_port(),
        "data_dir": str(data),
        "detail": (out.stdout or out.stderr or "").strip()[:120],
    }


def initdb(root: Path | None = None, *, timeout: float = 180.0) -> dict[str, Any]:
    """初始化实例；已初始化就跳过（幂等，结果写清楚是「跳过」）。"""
    binary = pg_bin(root, "initdb")
    if binary is None:
        return {"ok": False, "state": "not_installed", "detail": "缺少 initdb，先装 PG 运行时"}
    data = layout.data_dir(root, "pgdata")
    if (data / "PG_VERSION").is_file():
        return {"ok": True, "state": "already_initialized", "detail": f"已有实例：{data}"}

    data.mkdir(parents=True, exist_ok=True)
    if any(data.iterdir()):
        return {
            "ok": False,
            "state": "dirty_dir",
            "detail": f"{data} 非空但不是 PG 实例，拒绝在它上面 initdb",
        }
    cmd = [
        str(binary),
        "-D",
        str(data),
        "-U",
        "fc",
        "--auth-local=trust",
        "--auth-host=trust",
        "-E",
        "UTF8",
    ]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "state": "error", "detail": str(exc)}
    if out.returncode != 0:
        return {
            "ok": False,
            "state": "failed",
            "detail": (out.stderr or out.stdout or "").strip()[-400:],
        }
    return {"ok": True, "state": "initialized", "detail": f"initdb 完成：{data}"}


def start_pg(root: Path | None = None, *, wait_seconds: float = 15.0) -> dict[str, Any]:
    """直接拉 postgres 进程再轮询 pg_isready。

    不用 `pg_ctl start`：它在 Windows 下会阻塞等待子进程句柄，把调用方一起挂住
    （本次实测：库都已经在 15432 上接受连接了，`pg_ctl` 调用还没返回）。
    """
    current = pg_status(root)
    if current.get("state") == "running":
        return {**current, "ok": True, "detail": "已在运行，未重复启动"}
    if current.get("state") != "stopped":
        return current

    binary = pg_bin(root, "postgres")
    if binary is None:
        return {
            "ok": False,
            "state": "missing_tool",
            "detail": f"{layout.pg_dir(root)}/bin 里没有 postgres，先装 PG 运行时",
        }
    data = layout.data_dir(root, "pgdata")
    # 日志按**实例端口**分开：正式服(5432) 与测试服(15432) 共用 `pg.log` 时，
    # prod 的 postmaster 长期持有该文件句柄，test 侧 `>>` 追加打开失败 → 低权限
    # 启动脚本静默退出，PG 从来没起来（表现为「15s 内未就绪」且日志无新增行）。
    # 按端口命名比按 env 后缀更硬：两个环境的端口天然不同，历史遗留的
    # `pg.log` 也不会再被任何一个实例争用。
    log = layout.logs_dir(root) / f"pg-{pg_port()}.log"
    # 强杀 / 掉电后 `postmaster.pid` 会留在原地，postmaster 启动时看到锁文件直接
    # 退出。只在「文件里的 PID 已经不存活」时才清，在跑的实例绝不动。
    lock = data / "postmaster.pid"
    if lock.is_file():
        try:
            lock_pid = int(lock.read_text(encoding="utf-8").splitlines()[0].strip())
        except (OSError, ValueError, IndexError):
            lock_pid = 0
        if lock_pid <= 0 or not _pid_alive(lock_pid):
            lock.unlink(missing_ok=True)
    log.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(binary),
        "-D",
        str(data),
        "-p",
        str(pg_port()),
        # 日志按英文写，避免 GBK 中文在文件里变成乱码
        "-c",
        "lc_messages=C",
    ]
    creationflags = 0x00000008 if sys.platform.startswith("win") else 0  # DETACHED_PROCESS
    if _is_elevated():
        # PostgreSQL 在 Windows 上拒绝以管理员权限运行（官方限制，与实现无关）。
        # 便携包不能因此要求用户「先退出管理员」，改用基本用户令牌拉起。
        if not _start_as_basic_user(root, cmd, log):
            return {
                "ok": False,
                "state": "needs_unprivileged",
                "detail": (
                    "PostgreSQL 拒绝以管理员权限运行；本会话是提升的管理员令牌，"
                    "低权限拉起也失败。请用普通账户启动，或在非管理员窗口里启动增强包。"
                ),
            }
    else:
        try:
            with log.open("ab") as out:
                subprocess.Popen(
                    cmd,
                    stdout=out,
                    stderr=out,
                    creationflags=creationflags,
                    close_fds=True,
                )
        except OSError as exc:
            return {"ok": False, "state": "error", "detail": str(exc)}

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        probe = pg_status(root)
        if probe.get("state") == "running":
            return {**probe, "ok": True, "detail": "已启动并接受连接"}
        time.sleep(0.6)
    return {"ok": False, "state": "starting", "detail": f"{wait_seconds:.0f}s 内未就绪，日志 {log}"}


def stop_pg(root: Path | None = None, *, timeout: float = 30.0) -> dict[str, Any]:
    current = pg_status(root)
    if current.get("state") != "running":
        return {**current, "ok": current.get("state") == "stopped", "detail": "没有在跑的 PG"}
    ctl = pg_bin(root)
    if ctl is None:  # pragma: no cover
        return current
    try:
        subprocess.run(
            [str(ctl), "-D", str(layout.data_dir(root, "pgdata")), "-m", "fast", "stop"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "state": "error", "detail": str(exc)}
    return {"ok": pg_status(root).get("state") != "running", "state": "stopped", "detail": "已停止"}


# ------------------------------------------------------------------ MinIO


def minio_binary(root: Path | None = None) -> Path | None:
    base = layout.minio_dir(root)
    for name in ("minio.exe", "minio"):
        if (base / name).is_file():
            return base / name
    return None


def minio_status(root: Path | None = None, *, timeout: float = 3.0) -> dict[str, Any]:
    binary = minio_binary(root)
    if binary is None:
        return {
            "ok": False,
            "state": "not_installed",
            "detail": f"MinIO 未安装：{layout.minio_dir(root)} 里没有 minio",
        }
    url = f"http://127.0.0.1:{minio_api_port()}/minio/health/live"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            alive = resp.status == 200
    except Exception:
        alive = False
    return {
        "ok": alive,
        "state": "running" if alive else "stopped",
        "api_port": minio_api_port(),
        "console_port": minio_console_port(),
        "data_dir": str(layout.data_dir(root, "miniodata")),
        "detail": "health/live 通过" if alive else "端口没有响应 health/live",
    }


def start_minio(root: Path | None = None, *, wait_seconds: float = 10.0) -> dict[str, Any]:
    current = minio_status(root)
    if current.get("state") == "running":
        return {**current, "ok": True, "detail": "已在运行，未重复启动"}
    if current.get("state") != "stopped":
        return current

    binary = minio_binary(root)
    if binary is None:  # pragma: no cover
        return current
    data = layout.data_dir(root, "miniodata")
    data.mkdir(parents=True, exist_ok=True)
    log = layout.logs_dir(root) / "minio.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        str(binary),
        "server",
        str(data),
        "--address",
        f"127.0.0.1:{minio_api_port()}",
        "--console-address",
        f"127.0.0.1:{minio_console_port()}",
    ]
    creationflags = 0x00000008 if sys.platform.startswith("win") else 0  # DETACHED_PROCESS
    try:
        with log.open("ab") as out:
            proc = subprocess.Popen(
                cmd, stdout=out, stderr=out, creationflags=creationflags, close_fds=True
            )
        (layout.run_dir(root) / "minio.pid").write_text(str(proc.pid), encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "state": "error", "detail": str(exc)}

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        probe = minio_status(root)
        if probe.get("state") == "running":
            return {**probe, "ok": True, "detail": "已启动并通过 health/live"}
        time.sleep(0.4)
    return {"ok": False, "state": "starting", "detail": f"{wait_seconds:.0f}s 内未就绪，日志 {log}"}


# ------------------------------------------------------------------ 体检


def _pid_of_port(port: int) -> int | None:
    """按监听端口反查 PID（pid 文件丢了、或进程不是本进程拉起时用）。

    Windows 走 `netstat -ano`，POSIX 走 `lsof`（没有就 `ss`）。取不到就返回 None，
    不猜 pid——停错进程比停不掉更糟。
    """
    try:
        if sys.platform.startswith("win"):
            out = subprocess.run(
                ["netstat", "-ano", "-p", "tcp"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            ).stdout
            for line in out.splitlines():
                parts = line.split()
                if (
                    len(parts) >= 5
                    and parts[0].upper() == "TCP"
                    and parts[3].upper() == "LISTENING"
                ):
                    if parts[1].endswith(f":{port}"):
                        return int(parts[4])
            return None
        out = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        ).stdout.strip()
        return int(out.splitlines()[0]) if out else None
    except (OSError, ValueError, subprocess.TimeoutExpired, IndexError):
        return None


def stop_minio(root: Path | None = None, *, timeout: float = 8.0) -> dict[str, Any]:
    """按 pid 停 MinIO：先看启动时记的 pid 文件，没有就按监听端口反查。"""
    current = minio_status(root)
    if current.get("state") != "running":
        return {**current, "ok": current.get("state") == "stopped", "detail": "没有在跑的 MinIO"}

    pid_file = layout.run_dir(root) / "minio.pid"
    pid: int | None = None
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        pid = None
    if pid is None:
        pid = _pid_of_port(minio_api_port())
    if pid is None:
        return {
            "ok": False,
            "state": "running",
            "detail": (
                f"缺 {pid_file}，也没能从端口 {minio_api_port()} 反查到 pid；"
                "请在任务管理器里结束 minio"
            ),
        }
    try:
        if sys.platform.startswith("win"):
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        else:
            os.kill(pid, 15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "state": "running", "detail": f"结束进程失败：{exc}"}

    deadline = time.time() + timeout
    while time.time() < deadline:
        if minio_status(root).get("state") != "running":
            pid_file.unlink(missing_ok=True)
            return {"ok": True, "state": "stopped", "detail": f"已停止（pid {pid}）"}
        time.sleep(0.4)
    return {"ok": False, "state": "running", "detail": f"{timeout:.0f}s 内仍在响应 health/live"}


def stop_all(root: Path | None = None) -> dict[str, Any]:
    """停掉全部服务（重装 / 替换二进制之前必须先停，否则文件被占用复制会失败）。

    每一步独立捕获异常：任何一步失败都不能打断后面的步骤。原实现顺序调用，
    中间一步抛异常（例如可选组件 embedding 没在跑时的边界态）会让 api/minio/
    pg 全留在原地，控制面看到「端口还在监听」→ 判停止超时 → 模块记 failed
    （真实踩过，表现为停止按钮永远失败）。
    """
    results: dict[str, Any] = {}
    errors: dict[str, str] = {}
    # 顺序：先断消费与对外面（ETL / MCP / API / embedding），最后停数据库。
    # 反过来会让 API 在停机过程中报一堆连接错误，也会让 ETL 半途失败。
    steps: tuple[tuple[str, Any], ...] = (
        ("etl", stop_etl),
        ("mcp", stop_mcp),
        ("embedding", stop_embedding),
        ("api", stop_api),
        ("minio", stop_minio),
        ("redis", redis_service.stop),
        ("pg", stop_pg),
    )
    for key, fn in steps:
        try:
            results[key] = fn(root)
        except Exception as exc:  # noqa: BLE001 - 单步失败不打断整条停机链
            errors[key] = f"{type(exc).__name__}: {exc}"
            results[key] = {"ok": False, "state": "unknown", "detail": errors[key]}
    payload: dict[str, Any] = {
        "ok": all(bool(item.get("ok")) for item in results.values()),
        "services": results,
    }
    if errors:
        payload["errors"] = errors
    return payload


# ------------------------------------------------------------------ FinanceCoffer API


def api_status(root: Path | None = None, *, timeout: float = 3.0) -> dict[str, Any]:
    """`GET /health`：可达即 running，`status=degraded` 时把各依赖的回显照抛出去。"""
    if not api_src_dir(root).is_dir():
        return {
            "ok": False,
            "state": "not_installed",
            "detail": f"API 源码未装：{api_src_dir(root)}",
        }
    payload = _http_json(f"http://127.0.0.1:{api_port()}/health", timeout=timeout)
    if payload is None:
        return {
            "ok": False,
            "state": "stopped",
            "port": api_port(),
            "detail": f"{api_port()} 端口没有响应 /health",
        }
    data = payload.get("data") or {}
    checks = data.get("checks") or {}
    return {
        "ok": True,
        "state": "running",
        "port": api_port(),
        "status": data.get("status"),
        "checks": checks,
        "detail": (
            f"状态 {data.get('status')}；database={checks.get('database')} "
            f"embedding={checks.get('embedding')}"
        ),
    }


def _fc_env(root: Path | None, python_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(python_path), str(api_src_dir(root))) if part
    )
    env["FC_DB_URL"] = f"postgresql+psycopg://fc@127.0.0.1:{pg_port()}/financecoffer"
    # db 1：FinanceCoffer 自己的默认是 `/1`，上游 TickHub-CN 的 `FC_EVENT_REDIS_URL` 也是 `/1`。
    # 写成 `/0` 会让「上游 XADD 到 1 号库、ETL 去 0 号库读」永远读不到事件（踩过）。
    env["FC_REDIS_URL"] = f"redis://127.0.0.1:{redis_port()}/1"
    env["FC_EMBEDDING_URL"] = f"http://127.0.0.1:{embedding_port()}/v1/embeddings"
    # MinIO：API 的 /health 要 HeadBucket，凭据不给就只会报 not_configured（看起来
    # 像「没配」而实际是漏传）；端口也必须走 port_offset，否则测试服去探正式服那个。
    # 凭据优先级：LS 显式覆盖 → MinIO 自己的 ROOT 变量 → MinIO 默认账号。
    env["FC_MINIO_ENDPOINT"] = f"http://127.0.0.1:{minio_api_port()}"
    env["FC_MINIO_ACCESS_KEY"] = (
        os.environ.get("LDST_FC_MINIO_ACCESS_KEY")
        or os.environ.get("MINIO_ROOT_USER")
        or "minioadmin"
    ).strip()
    env["FC_MINIO_SECRET_KEY"] = (
        os.environ.get("LDST_FC_MINIO_SECRET_KEY")
        or os.environ.get("MINIO_ROOT_PASSWORD")
        or "minioadmin"
    ).strip()
    env["FC_MINIO_BUCKET"] = (os.environ.get("LDST_FC_MINIO_BUCKET") or "financecoffer").strip()
    env["FC_STATE_DIR"] = str(layout.module_root(root) / "runtime" / "api-state")
    env["PYTHONUNBUFFERED"] = "1"
    return env


def start_api(root: Path | None = None, *, wait_seconds: float = 25.0) -> dict[str, Any]:
    """起 FinanceCoffer API；依赖装在模块自己的 `runtime/api-deps` 里，不碰全局环境。"""
    current = api_status(root)
    if current.get("state") == "running":
        return {**current, "ok": True, "detail": "已在运行，未重复启动"}
    if current.get("state") != "stopped":
        return current
    if not api_deps_dir(root).is_dir():
        return {
            "ok": False,
            "state": "missing_deps",
            "detail": f"缺 {api_deps_dir(root)}（sqlalchemy / psycopg / pgvector 等），先补依赖",
        }
    if pg_status(root).get("state") != "running":
        return {"ok": False, "state": "needs_pg", "detail": "PostgreSQL 没在跑，先起数据库再起 API"}

    # 库不存在就建（幂等：已存在时 createdb 报错，忽略即可）
    createdb = pg_bin(root, "createdb")
    if createdb is not None:
        try:
            subprocess.run(
                [
                    str(createdb),
                    "-h",
                    "127.0.0.1",
                    "-p",
                    str(pg_port()),
                    "-U",
                    "fc",
                    "financecoffer",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass

    log = layout.logs_dir(root) / "fc-api.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    (layout.module_root(root) / "runtime" / "api-state").mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "financecoffer",
        "serve",
        "api",
        "--host",
        "127.0.0.1",
        "--port",
        str(api_port()),
    ]
    creationflags = 0x00000008 if sys.platform.startswith("win") else 0
    try:
        with log.open("ab") as out:
            proc = subprocess.Popen(
                cmd,
                env=_fc_env(root, api_deps_dir(root)),
                cwd=str(layout.module_root(root)),
                stdout=out,
                stderr=out,
                creationflags=creationflags,
                close_fds=True,
            )
        (layout.run_dir(root) / "fc-api.pid").write_text(str(proc.pid), encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "state": "error", "detail": str(exc)}

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        probe = api_status(root)
        if probe.get("state") == "running":
            return {**probe, "ok": True, "detail": f"已启动：{probe.get('detail')}"}
        time.sleep(0.8)
    return {
        "ok": False,
        "state": "starting",
        "detail": f"{wait_seconds:.0f}s 内 /health 未就绪，日志 {log}",
    }


# ------------------------------------------------------------------ embedding 服务


def embedding_status(root: Path | None = None, *, timeout: float = 3.0) -> dict[str, Any]:
    model_dir = layout.module_root(root) / "runtime" / "embedding"
    if not embedding_deps_dir(root).is_dir():
        return {
            "ok": False,
            "state": "missing_deps",
            "detail": f"缺 {embedding_deps_dir(root)}（torch / transformers），向量检索不可用",
        }
    payload = _http_json(f"http://127.0.0.1:{embedding_port()}/health", timeout=timeout)
    if payload is None:
        return {
            "ok": False,
            "state": "stopped",
            "port": embedding_port(),
            "detail": f"{embedding_port()} 端口没有响应 /health",
        }
    return {
        "ok": True,
        "state": "running",
        "port": embedding_port(),
        "model_dir": str(model_dir),
        "detail": f"bge-m3 已加载（{embedding_port()}）",
    }


def start_embedding(root: Path | None = None, *, wait_seconds: float = 60.0) -> dict[str, Any]:
    """起 bge-m3 embedding 服务；首次加载 30–60 秒，所以等待窗口给足。"""
    current = embedding_status(root)
    if current.get("state") == "running":
        return {**current, "ok": True, "detail": "已在运行，未重复启动"}
    if current.get("state") != "stopped":
        return current

    script = layout.module_root(root) / "runtime" / "api" / "scripts" / "embedding_server.py"
    if not script.is_file():
        return {"ok": False, "state": "missing_script", "detail": f"缺 {script}"}

    log = layout.logs_dir(root) / "embedding.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        str(script),
        "--host",
        "127.0.0.1",
        "--port",
        str(embedding_port()),
        "--model-dir",
        str(layout.module_root(root) / "runtime" / "embedding"),
    ]
    creationflags = 0x00000008 if sys.platform.startswith("win") else 0
    try:
        with log.open("ab") as out:
            proc = subprocess.Popen(
                cmd,
                env=_fc_env(root, embedding_deps_dir(root)),
                cwd=str(layout.module_root(root)),
                stdout=out,
                stderr=out,
                creationflags=creationflags,
                close_fds=True,
            )
        (layout.run_dir(root) / "embedding.pid").write_text(str(proc.pid), encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "state": "error", "detail": str(exc)}

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        probe = embedding_status(root)
        if probe.get("state") == "running":
            return {**probe, "ok": True, "detail": "已启动并加载模型"}
        time.sleep(1.0)
    return {"ok": False, "state": "starting", "detail": f"{wait_seconds:.0f}s 内未就绪，日志 {log}"}


def _stop_by_name(root: Path | None, name: str, port: int, probe) -> dict[str, Any]:  # noqa: ANN001
    """按 `run/<name>.pid` 或监听端口停进程（通用：API / embedding / MinIO 共用）。"""
    current = probe(root)
    if current.get("state") != "running":
        return {**current, "ok": current.get("state") == "stopped", "detail": "没有在跑"}
    pid: int | None = None
    try:
        pid = int((layout.run_dir(root) / f"{name}.pid").read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        pid = None
    # 以「端口上真正在服务的那个进程」为准：pid 文件可能是死的、也可能是上一代
    # 进程留下的。只信 pid 文件会「杀了个空」还报 stopped，而旧进程继续作答——
    # 结果就是改了配置重启后行为完全不变（踩过，表现为改 env 无效果）。
    listening = _pid_of_port(port)
    if listening is not None and listening != pid:
        pid = listening
    if pid is None:
        pid = listening
    if pid is None:
        return {
            "ok": False,
            "state": "running",
            "detail": f"拿不到 pid（既没有 pid 文件，也无法从端口 {port} 反查）",
        }
    try:
        if sys.platform.startswith("win"):
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        else:
            os.kill(pid, 15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "state": "running", "detail": str(exc)}
    deadline = time.time() + 10
    while time.time() < deadline:
        if probe(root).get("state") != "running":
            (layout.run_dir(root) / f"{name}.pid").unlink(missing_ok=True)
            return {"ok": True, "state": "stopped", "detail": f"已停止（pid {pid}）"}
        time.sleep(0.4)
    return {"ok": False, "state": "running", "detail": "进程还在响应"}


def stop_api(root: Path | None = None) -> dict[str, Any]:
    return _stop_by_name(root, "fc-api", api_port(), api_status)


def stop_embedding(root: Path | None = None) -> dict[str, Any]:
    return _stop_by_name(root, "embedding", embedding_port(), embedding_status)


# ------------------------------------------------------------------ MCP / ETL


def mcp_port() -> int:
    """FC 的 MCP 面（`fcoffer mcp http`）：正式服 8101，测试服 18101。"""
    return _port("LDST_FC_MCP_PORT", 8101)


def mcp_token() -> str:
    """MCP 的 Bearer token，与 LS 侧 `LDST_FINANCECOFFER_MCP_TOKEN` 取同一个值。"""
    for name in ("FC_MCP_TOKEN", "LDST_FINANCECOFFER_MCP_TOKEN"):
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return "fc-local-mcp"


def _mcp_rpc(
    payload: dict[str, Any], *, session: str | None = None, timeout: float = 3.0
) -> dict[str, Any] | None:
    """发一条 JSON-RPC 到 MCP 端点；任何异常都返回 None（探活只关心「通不通」）。"""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {mcp_token()}",
    }
    if session:
        headers["Mcp-Session-Id"] = session
    request = urllib.request.Request(
        f"http://127.0.0.1:{mcp_port()}/mcp",
        data=json.dumps(payload).encode(),
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
            new_session = response.headers.get("Mcp-Session-Id") or session
    except Exception:  # noqa: BLE001 - 连不上 / 401 / 超时一律算「不通」
        return None
    for line in raw.splitlines():
        if line.startswith("data:"):
            raw = line[5:].strip()
            break
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return None
    parsed["_session"] = new_session
    return parsed


def mcp_status(root: Path | None = None, *, timeout: float = 3.0) -> dict[str, Any]:
    """按协议真握一次手：活着的 MCP 会回 `serverInfo` 和三个工具名。

    只看端口是否监听会误判——残留的转发、别的进程占着同一端口都算「通」，
    而 agent 真正调工具时才失败。所以这里要求 initialize 成功。
    """
    if not api_src_dir(root).is_dir():
        return {
            "ok": False,
            "state": "not_installed",
            "detail": f"MCP 源码未装：{api_src_dir(root)}",
        }
    hello = _mcp_rpc(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "ladderstock-doctor", "version": "1"},
            },
        },
        timeout=timeout,
    )
    if hello is None:
        return {
            "ok": False,
            "state": "stopped",
            "port": mcp_port(),
            "detail": f"{mcp_port()} 端口没有响应 MCP 握手",
        }
    server = ((hello.get("result") or {}).get("serverInfo") or {}).get("name")
    listed = _mcp_rpc(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        session=hello.get("_session"),
        timeout=timeout,
    )
    tools: list[str] = []
    if listed:
        tools = [str(t.get("name")) for t in (listed.get("result") or {}).get("tools") or []]
    return {
        "ok": True,
        "state": "running",
        "port": mcp_port(),
        "server": server,
        "tools": tools,
        "detail": f"MCP {server} 在跑，工具 {len(tools)} 个：{', '.join(tools)}",
    }


def start_mcp(root: Path | None = None, *, wait_seconds: float = 20.0) -> dict[str, Any]:
    """起 MCP（Streamable HTTP）。依赖同 API：PG 在跑 + api-deps 齐全。"""
    current = mcp_status(root)
    if current.get("state") == "running":
        return {**current, "ok": True, "detail": "已在运行，未重复启动"}
    if current.get("state") != "stopped":
        return current
    if not api_deps_dir(root).is_dir():
        return {
            "ok": False,
            "state": "missing_deps",
            "detail": f"缺 {api_deps_dir(root)}，先补依赖",
        }
    if pg_status(root).get("state") != "running":
        return {"ok": False, "state": "needs_pg", "detail": "PostgreSQL 没在跑，先起数据库再起 MCP"}

    log = layout.logs_dir(root) / "fc-mcp.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "financecoffer",
        "mcp",
        "http",
        "--host",
        "127.0.0.1",
        "--port",
        str(mcp_port()),
        "--token",
        mcp_token(),
    ]
    creationflags = 0x00000008 if sys.platform.startswith("win") else 0
    try:
        with log.open("ab") as out:
            proc = subprocess.Popen(
                cmd,
                env=_fc_env(root, api_deps_dir(root)),
                cwd=str(layout.module_root(root)),
                stdout=out,
                stderr=out,
                creationflags=creationflags,
                close_fds=True,
            )
        (layout.run_dir(root) / "fc-mcp.pid").write_text(str(proc.pid), encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "state": "error", "detail": str(exc)}

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        probe = mcp_status(root)
        if probe.get("state") == "running":
            return {**probe, "ok": True, "detail": f"已启动：{probe.get('detail')}"}
        time.sleep(0.8)
    return {
        "ok": False,
        "state": "starting",
        "detail": f"{wait_seconds:.0f}s 内 MCP 未就绪，日志 {log}",
    }


def stop_mcp(root: Path | None = None) -> dict[str, Any]:
    return _stop_by_name(root, "fc-mcp", mcp_port(), mcp_status)


def _pid_alive(pid: int) -> bool:
    """跨端判断 pid 是否还活着（ETL 不监听端口，只能看进程）。"""
    if pid <= 0:
        return False
    if sys.platform.startswith("win"):
        try:
            done = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return str(pid) in (done.stdout or "")
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _read_pid(root: Path | None, name: str) -> int | None:
    """读 `run/<name>.pid`；只取数字，容忍 BOM / 空白 / 尾随换行。"""
    try:
        text = (layout.run_dir(root) / f"{name}.pid").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    return int(digits) if digits else None


def etl_status(root: Path | None = None) -> dict[str, Any]:
    """ETL 消费者（Redis Stream → ODS → DWD）是否在跑。

    它不监听端口，所以判据是 pid 文件 + 进程存活；`--once` 形态跑完就退出，
    这里如实报 stopped，不假装常驻。
    """
    if not api_src_dir(root).is_dir():
        return {
            "ok": False,
            "state": "not_installed",
            "detail": f"ETL 源码未装：{api_src_dir(root)}",
        }
    pid = _read_pid(root, "fc-etl")
    if pid is None:
        return {"ok": False, "state": "stopped", "detail": "没有 pid 文件（run/fc-etl.pid）"}
    if not _pid_alive(pid):
        return {"ok": False, "state": "stopped", "pid": pid, "detail": f"pid {pid} 已不在"}
    return {"ok": True, "state": "running", "pid": pid, "detail": f"ETL 消费者在跑（pid {pid}）"}


def start_etl(root: Path | None = None, *, wait_seconds: float = 8.0) -> dict[str, Any]:
    current = etl_status(root)
    if current.get("state") == "running":
        return {**current, "ok": True, "detail": "已在运行，未重复启动"}
    if current.get("state") != "stopped":
        return current
    if redis_service.status(root).get("state") != "running":
        return {"ok": False, "state": "needs_redis", "detail": "Redis 没在跑，ETL 读不到事件流"}
    if pg_status(root).get("state") != "running":
        return {"ok": False, "state": "needs_pg", "detail": "PostgreSQL 没在跑，ETL 无处落库"}

    log = layout.logs_dir(root) / "fc-etl.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "financecoffer",
        "etl",
        "consume",
        "--consumer",
        "redis",
        "--group",
        "fc-etl",
    ]
    creationflags = 0x00000008 if sys.platform.startswith("win") else 0
    try:
        with log.open("ab") as out:
            proc = subprocess.Popen(
                cmd,
                env=_fc_env(root, api_deps_dir(root)),
                cwd=str(layout.module_root(root)),
                stdout=out,
                stderr=out,
                creationflags=creationflags,
                close_fds=True,
            )
        (layout.run_dir(root) / "fc-etl.pid").write_text(str(proc.pid), encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "state": "error", "detail": str(exc)}

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        probe = etl_status(root)
        if probe.get("state") == "running":
            return {**probe, "ok": True, "detail": f"已启动（pid {probe.get('pid')}），日志 {log}"}
        time.sleep(0.5)
    return {
        "ok": False,
        "state": "starting",
        "detail": f"{wait_seconds:.0f}s 内 ETL 进程没起来，日志 {log}",
    }


def stop_etl(root: Path | None = None) -> dict[str, Any]:
    """停 ETL：没有端口可探，只看 pid 文件 + 进程是否存在。"""
    pid = _read_pid(root, "fc-etl")
    if pid is None or not _pid_alive(pid):
        (layout.run_dir(root) / "fc-etl.pid").unlink(missing_ok=True)
        return {"ok": True, "state": "stopped", "detail": "本来就没在跑"}
    try:
        if sys.platform.startswith("win"):
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        else:
            os.kill(pid, 15)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "state": "running", "detail": str(exc)}
    deadline = time.time() + 10
    while time.time() < deadline:
        if not _pid_alive(pid):
            (layout.run_dir(root) / "fc-etl.pid").unlink(missing_ok=True)
            return {"ok": True, "state": "stopped", "detail": f"已停止（pid {pid}）"}
        time.sleep(0.4)
    return {"ok": False, "state": "running", "detail": "进程还在"}


#: 自愈顺序：数据库 → 缓存 → 对象存储 → REST → 向量 → MCP → ETL。
#: embedding 是可选件（没装权重时不算故障），ETL 允许后起（查询不依赖它）。
SERVICE_ORDER: tuple[str, ...] = ("pg", "redis", "minio", "api", "embedding", "mcp", "etl")
_OPTIONAL_SERVICES: tuple[str, ...] = ("embedding", "etl")


def _service_map() -> dict[str, dict[str, Any]]:
    return {
        "pg": {"probe": pg_status, "start": start_pg},
        "redis": {"probe": redis_service.status, "start": redis_service.start},
        "minio": {"probe": minio_status, "start": start_minio},
        "api": {"probe": api_status, "start": start_api},
        "embedding": {"probe": embedding_status, "start": start_embedding},
        "mcp": {"probe": mcp_status, "start": start_mcp},
        "etl": {"probe": etl_status, "start": start_etl},
    }


def embedding_enabled() -> bool:
    """形态 2：公告语义检索已迁到 LS 的 qdrant，FC 的 bge-m3 默认不再起。

    显式设 `LDST_FC_EMBEDDING_ENABLE=1` 才拉起——用于还需要 FC 自己跑
    `vector embed` / `/v1/search/announcements` 的场合（约 2.76GB 权重）。
    """

    raw = (os.environ.get("LDST_FC_EMBEDDING_ENABLE") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def ensure_running(root: Path | None = None, *, include_optional: bool = True) -> dict[str, Any]:
    """自愈一次：只拉起挂掉的服务，已经跑的不动。

    这是模块监控与便携包共用的入口——跨端纯 Python，不依赖 Windows 计划任务、
    也不写 `.vbs`。返回本轮实际做了什么，没做就明说 `actions: []`。
    """
    mapping = _service_map()
    actions: list[dict[str, Any]] = []
    for key in SERVICE_ORDER:
        if key == "embedding" and not embedding_enabled():
            continue
        probe = mapping[key]["probe"](root)
        state = probe.get("state")
        if state == "running":
            continue
        if key in _OPTIONAL_SERVICES and not include_optional:
            continue
        if key in _OPTIONAL_SERVICES and state in ("not_installed", "missing_deps"):
            actions.append(
                {"service": key, "before": state, "ok": False, "skipped": "可选件未安装"}
            )
            continue
        result = mapping[key]["start"](root)
        actions.append(
            {
                "service": key,
                "before": state,
                "after": result.get("state"),
                "ok": bool(result.get("ok")),
                "detail": result.get("detail"),
            }
        )
        if not result.get("ok") and key not in _OPTIONAL_SERVICES:
            break
    doctor_now = doctor(root)
    return {
        "ok": doctor_now.get("usable", False),
        "actions": actions,
        "missing_required": doctor_now.get("missing_required", []),
        "doctor": doctor_now,
    }


def guard(
    root: Path | None = None,
    *,
    interval: float = 30.0,
    rounds: int | None = None,
    should_stop=None,  # noqa: ANN001 - 可传入无参 callable，便于上层优雅退出
    on_round=None,  # noqa: ANN001 - 每轮回调，用于写日志/上报
) -> dict[str, Any]:
    """跨端自愈守护循环（纯 Python，由模块监控调用）。

    `rounds=None` 表示一直守；每轮调一次 :func:`ensure_running`，只打印/回报
    真正发生的动作，避免每 30 秒刷无用日志。Windows / macOS / Linux 同一份代码
    ——不写计划任务、不写 `.vbs`、不改机器级环境变量。
    """
    rounds_done = 0
    restarts = 0
    history: list[dict[str, Any]] = []
    while rounds is None or rounds_done < rounds:
        result = ensure_running(root)
        rounds_done += 1
        if result.get("actions"):
            restarts += len(result["actions"])
            history.append({"round": rounds_done, "actions": result["actions"]})
            if on_round is not None:
                on_round(history[-1])
        if should_stop is not None and should_stop():
            break
        if rounds is not None and rounds_done >= rounds:
            break
        time.sleep(interval)
    return {"rounds": rounds_done, "restarts": restarts, "history": history}


def doctor(root: Path | None = None) -> dict[str, Any]:
    """七件一起体检：组件在不在、服务起没起、端口是多少。

    `embedding` 是**可选件**：没装权重时算「未就绪但可接受」，页面据此标注，
    不把它当成安装失败。`etl` 也是可选件——查询不依赖它，但它停了数据就不再刷新。
    其余五件（PG / Redis / MinIO / API / MCP）缺一件就用不了。
    """
    services = {
        "pg": pg_status(root),
        "redis": redis_service.status(root),
        "minio": minio_status(root),
        "api": api_status(root),
        "embedding": embedding_status(root),
        "mcp": mcp_status(root),
        "etl": etl_status(root),
    }
    # embedding 缺权重不算故障（可选件）；ETL 允许后起（查询不依赖它，只是数据不刷新）。
    optional = ("embedding", "etl")
    # pgvector 单独报：PG 起着但扩展缺位时，/health 是 ok、查询却全表不存在——
    # 这种情况必须在体检里显式说出来，否则页面显示「5/5 就绪」而实际查不动（踩过）。
    from ladderstock.modules.financecoffer import pg_runtime

    vector = pg_runtime.pgvector_status(root)
    ready = sum(1 for item in services.values() if item.get("ok"))
    missing_required = [
        key for key, item in services.items() if not item.get("ok") and key not in optional
    ]
    if not vector.get("ok") and "pgvector" not in missing_required:
        missing_required.append("pgvector")
    return {
        "module_root": str(layout.module_root(root)),
        "ready": ready,
        "total": len(services),
        "optional": list(optional),
        "missing_required": missing_required,
        "usable": not missing_required,
        "services": services,
        "pgvector": vector,
        "ports": {
            "pg": pg_port(),
            "redis": redis_port(),
            "minio_api": minio_api_port(),
            "minio_console": minio_console_port(),
            "api": api_port(),
            "embedding": embedding_port(),
            "mcp": mcp_port(),
        },
    }


def as_json(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2)


__all__ = [
    "api_deps_dir",
    "api_port",
    "api_status",
    "as_json",
    "doctor",
    "embedding_deps_dir",
    "embedding_port",
    "embedding_status",
    "ensure_running",
    "etl_status",
    "guard",
    "initdb",
    "mcp_port",
    "mcp_status",
    "mcp_token",
    "minio_binary",
    "minio_console_port",
    "minio_status",
    "pg_bin",
    "pg_port",
    "pg_status",
    "redis_port",
    "SERVICE_ORDER",
    "start_api",
    "start_embedding",
    "start_etl",
    "start_mcp",
    "start_minio",
    "start_pg",
    "stop_all",
    "stop_api",
    "stop_embedding",
    "stop_etl",
    "stop_mcp",
    "stop_minio",
    "stop_pg",
]
