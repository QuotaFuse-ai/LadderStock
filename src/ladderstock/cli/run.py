"""`ldst run` 命令体：启动本地看板 + 只读 API（默认 127.0.0.1:5950）。

DEC-004：端口被占用**直接报错退出码 3** 并提示 `--port`，绝不静默换端口
（IndexedDB 按 origin 隔离，端口漂移会让前端状态恢复失效）。

日志（启动/停止/uvicorn）全部写 stderr；Ctrl+C 由这里兜底成正常退出。
"""

from __future__ import annotations

import errno
import signal
import socket
import sys

import typer

from ladderstock.cli.output import error
from ladderstock.errors import EXIT_CONFIG, EXIT_OK

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5950

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")

#: Windows 的 `WSAEADDRINUSE`；POSIX 下 `errno.EADDRINUSE` 已覆盖，这里只补 Windows 形态
WSAEADDRINUSE = 10048


def port_free(port: int, host: str = DEFAULT_HOST) -> bool:
    """端口是否可监听：连得上就说明已有进程在听。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return sock.connect_ex((host, port)) != 0


def is_addr_in_use(exc: OSError) -> bool:
    """是不是真正的端口占用（`EADDRINUSE` / Windows `WSAEADDRINUSE`）。

    绑定策略拒绝也抛 `OSError`，但那是配置问题、端口本身没问题；两类文案必须分开，
    否则用户会照着「请用 --port」去换端口，越试越糊涂（QUOT-112）。
    """
    return exc.errno == errno.EADDRINUSE or getattr(exc, "winerror", None) == WSAEADDRINUSE


def _raise_keyboard_interrupt(signum: int, frame: object) -> None:
    raise KeyboardInterrupt


def _install_break_handler() -> object | None:
    """把 Ctrl+Break（Windows `SIGBREAK`）转成 `KeyboardInterrupt`。

    uvicorn 在优雅关闭后会**重新抛出**收到的信号；`SIGINT` 会变成
    `KeyboardInterrupt`，而 `SIGBREAK` 的默认动作是直接杀进程（退出码不是 0，
    `finally` 也不执行）。这里先装一个把 Break 升成 `KeyboardInterrupt` 的处理器，
    uvicorn 会先保存再恢复它，重抛时就能走到优雅退出分支。
    """
    signum = getattr(signal, "SIGBREAK", None)
    if signum is None:  # 非 Windows
        return None
    try:
        return signal.signal(signum, _raise_keyboard_interrupt)
    except (OSError, ValueError):  # pragma: no cover - 非主线程无法装信号处理器
        return None


def _restore_break_handler(previous: object | None) -> None:
    signum = getattr(signal, "SIGBREAK", None)
    if previous is None or signum is None:
        return
    try:
        signal.signal(signum, previous)  # type: ignore[arg-type]
    except (OSError, ValueError):  # pragma: no cover
        pass


def _log(message: str) -> None:
    sys.stderr.write(f"ldst: {message}\n")
    sys.stderr.flush()


def launch(*, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    """阻塞运行看板，直到 Ctrl+C。"""
    # 绑定策略先于端口检查：策略拒绝是配置问题，不能报成「端口无法监听」，
    # 也不能先打「看板已启动」再失败（QUOT-112）。判据仍只有 core/listener.py 一份。
    from ladderstock.web.server import require_binding_policy

    try:
        require_binding_policy(host)
    except OSError as exc:
        error(str(exc))
        raise typer.Exit(EXIT_CONFIG) from None

    if not port_free(port, host if host in LOOPBACK_HOSTS else DEFAULT_HOST):
        error(f"端口 {port} 已被占用；请用 --port 指定其他端口（不会自动换端口，见 DEC-004）")
        raise typer.Exit(EXIT_CONFIG)

    # 源文档 §6.5：首次启动自动检测旧配置/旧数据目录并只读迁移。
    # 迁移失败这里只告警、不阻塞启动（回退到现有目录继续跑）。
    from ladderstock.config.migrate import ensure_migrated

    ensure_migrated()

    if host not in LOOPBACK_HOSTS:
        _log(f"警告：监听 {host} 会暴露到本机以外；默认应为 127.0.0.1（DEC-004）")

    from ladderstock.core.fsguard import SharedPeerDirectoryError
    from ladderstock.web.server import create_app, serve

    try:
        app = create_app()
    except SharedPeerDirectoryError as exc:
        # 控制面的启动校验（peer 目录不得在共享盘上，QUOT-194 P1-3）是配置问题：
        # 按配置错误退出，而不是抛一屏 traceback
        error(str(exc))
        raise typer.Exit(EXIT_CONFIG) from None
    _log(f"看板已启动 http://{host}:{port}（只读 API + 静态页面；Ctrl+C 退出）")
    previous_break = _install_break_handler()
    try:
        serve(app, host=host, port=port)
    except KeyboardInterrupt:  # Ctrl+C / Ctrl+Break
        pass
    except OSError as exc:
        # 两类 OSError 文案必须分开：真正的占用才建议换端口（DEC-004）
        if is_addr_in_use(exc):
            error(
                f"端口 {port} 已被占用：{exc}；"
                "请用 --port 指定其他端口（不会自动换端口，见 DEC-004）"
            )
        else:
            error(f"端口 {port} 无法监听：{exc}；请用 --port 指定其他端口（见 DEC-004）")
        raise typer.Exit(EXIT_CONFIG) from None
    finally:
        _restore_break_handler(previous_break)
        _log("看板已停止，端口已释放")
    raise typer.Exit(EXIT_OK)


__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "is_addr_in_use",
    "launch",
    "port_free",
]
