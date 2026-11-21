"""`ldst peer serve`：模块启动器的登记包装层。

为什么要有它：financecoffer / proxyfleet 这类模块没有自述卡代码，面板只能按
「基础端口 + 环境偏移」推算，一换端口就失联。包装层不碰模块源码，只在启动
命令前面套一层：

    ldst peer serve --module financecoffer --mcp 18101 --web 18100 -- <原命令...>

包装层负责：
1. 启动前写 `<card-root>/.runtime/peer.json`（card-root 默认是启动时的 cwd，
   也就是模块安装目录；可用 `--card-root` 显式指定）；
2. 子进程退出（含 Ctrl+C）后删卡，避免把已停的服务继续当成在线。

它和 `tickhub_cn.runtime_card` 是同一份卡片契约，面板读的都是它。
"""

from __future__ import annotations

import os
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

import typer

from ladderstock.core import discovery

peer_app = typer.Typer(no_args_is_help=True, help="服务发现：模块自述卡")

_MIN_PORT = 1
_MAX_PORT = 65535


@contextmanager
def _card_lock(root: Path):
    """同一模块的两个面（MCP / API）是两个包装层进程，各写一次卡。

    没有锁时「读旧卡 → 合并 → 替换」会丢端口：后写者读到对方写之前的空卡。
    锁文件独立，获取超时就照写（宁可少合并，也不让启动卡住）。
    """
    lock = root / discovery.CARD_DIR / "peer.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock, "a+b")
    try:
        handle.seek(0)
        if handle.read(1) == b"":
            handle.seek(0)
            handle.write(b"x")
            handle.flush()
        deadline = time.monotonic() + 2.0
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        handle.close()


@peer_app.command("serve")
def serve(
    module: str = typer.Option(..., "--module", help="模块 id（与 config/sources.yaml 的 peers 键一致）"),
    mcp: int = typer.Option(0, "--mcp", help="MCP 面端口；模块没有这一面就不传"),
    web: int = typer.Option(0, "--web", help="Web 面端口；模块没有这一面就不传"),
    face: str = typer.Option(
        "", "--face", help="快捷面名：mcp / web（控制面按面的名字登记时用）"
    ),
    port: int = typer.Option(0, "--port", help="配合 --face 使用的端口"),
    extra: list[int] = typer.Option(None, "--extra", help="附加端口，可重复"),
    product: str = typer.Option("", "--product", help="对外展示的产品名"),
    card_root: str = typer.Option(
        "", "--card-root", help="卡片落点（模块安装目录）；默认取启动时的 cwd"
    ),
    command: list[str] = typer.Argument(None, help="原启动命令；必须放在 -- 之后"),
) -> None:
    """先登记真实端口，再执行原启动命令；退出时撤卡。"""
    if not command:
        typer.echo(
            "缺少原启动命令：ldst peer serve --module <id> [--mcp N] [--web N] -- <命令...>",
            err=True,
        )
        raise typer.Exit(2)

    # 控制面按「面」拉起（一个面一个进程），用 --face/--port 比记 --mcp/--web 更直接。
    if face and port:
        if face == "mcp":
            mcp = mcp or port
        elif face in ("web", "api", "admin", "http"):
            web = web or port
        else:
            extra = [*(extra or []), port]

    for value in [mcp, web, *(extra or [])]:
        if value and not (_MIN_PORT <= value <= _MAX_PORT):
            typer.echo(f"端口不合法：{value}", err=True)
            raise typer.Exit(2)

    root = Path(card_root) if card_root else Path.cwd()
    with _card_lock(root):
        old = discovery.read_card(root) or {}
        merged_extra = sorted({*(old.get("extra") or ()), *(extra or [])})
        card = discovery.write_card(
            root,
            module=module,
            ports={
                "mcp": mcp or old.get("mcp"),
                "web": web or old.get("web"),
                "extra": merged_extra,
            },
            product=product or old.get("product"),
            env="test" if os.environ.get("LDST_PORT_OFFSET") else "prod",
            pid=os.getpid(),
        )
    typer.echo(f"peer card -> {card}")

    try:
        proc = subprocess.Popen(list(command))
    except OSError as exc:
        discovery.card_path(root).unlink(missing_ok=True)
        typer.echo(f"启动失败：{exc}", err=True)
        raise typer.Exit(1) from exc

    try:
        code = proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        try:
            code = proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            code = proc.wait()
    finally:
        discovery.card_path(root).unlink(missing_ok=True)
        discovery.clear_cache()

    raise typer.Exit(code)


__all__ = ["peer_app"]
