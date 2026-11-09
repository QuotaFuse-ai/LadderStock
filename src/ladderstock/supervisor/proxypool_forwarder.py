"""proxypool 单映射 TCP 直通转发器（QUOT-506）。

由 `supervisor.module_adapters.ProxyPoolAdapter` 按配置映射逐条拉起：

- 纯 TCP 直通：不解析协议、不记录载荷、不带任何凭据；
- 只允许监听回环地址（127.0.0.1 / localhost / ::1）；
- 命令行固定含 `proxypool_forwarder --id <映射 Id> --listen-port <端口>`，
  这是 supervisor `probe()` / `stop()` 识别本进程的唯一依据（由父链 cmdline 命中）；
- 绑定失败（端口被占）直接非零退出，**不做换端口重试**——冲突由适配器报给调用方。

启动示例：

    python proxypool_forwarder.py --id multica-api --listen-host 127.0.0.1 \
        --listen-port 18080 --upstream-host 127.0.0.1 --upstream-port 8080
"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys

#: 允许的监听地址（只回环；与 module_adapters 的校验口径一致）
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
#: 单次转发的读块大小（只影响内存占用，不改变载荷）
CHUNK_BYTES = 64 * 1024


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(CHUNK_BYTES)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
        except OSError:
            pass


async def _handle(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    upstream_host: str,
    upstream_port: int,
) -> None:
    try:
        up_reader, up_writer = await asyncio.open_connection(upstream_host, upstream_port)
    except OSError:
        # 上游不可达：直接断开本次连接，不重试、不改监听端口
        try:
            client_writer.close()
        except OSError:
            pass
        return
    await asyncio.gather(
        _pipe(client_reader, up_writer),
        _pipe(up_reader, client_writer),
        return_exceptions=True,
    )


async def _serve(
    *,
    listen_host: str,
    listen_port: int,
    upstream_host: str,
    upstream_port: int,
) -> None:
    server = await asyncio.start_server(
        lambda reader, writer: _handle(reader, writer, upstream_host, upstream_port),
        host=listen_host,
        port=listen_port,
        reuse_address=False,
    )
    async with server:
        await server.serve_forever()


def _interrupt(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt


def _install_signal_handlers() -> None:
    for name in ("SIGTERM", "SIGINT", "SIGBREAK"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _interrupt)
        except (OSError, ValueError):
            continue


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="proxypool 单映射 TCP 直通转发器")
    parser.add_argument("--id", dest="mapping_id", required=True)
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, required=True)
    parser.add_argument("--upstream-host", required=True)
    parser.add_argument("--upstream-port", type=int, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    listen_host = str(args.listen_host).strip().lower()
    if listen_host not in LOOPBACK_HOSTS:
        print(
            f"proxypool_forwarder: 拒绝监听非回环地址 {args.listen_host!r}",
            file=sys.stderr,
        )
        return 2
    for port in (int(args.listen_port), int(args.upstream_port)):
        if not 0 < port <= 65535:
            print(f"proxypool_forwarder: 端口越界 {port}", file=sys.stderr)
            return 2
    _install_signal_handlers()
    try:
        asyncio.run(
            _serve(
                listen_host=listen_host,
                listen_port=int(args.listen_port),
                upstream_host=str(args.upstream_host),
                upstream_port=int(args.upstream_port),
            )
        )
    except KeyboardInterrupt:
        return 0
    except OSError as exc:
        # 绑定/监听失败：端口冲突的最终兜底（适配器的预检已提前拒一次）
        print(
            f"proxypool_forwarder: 监听 {listen_host}:{args.listen_port} 失败：{exc}",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
