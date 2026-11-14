"""占端口常驻的假模块进程（QUOT-550 ephemeral 验收）。

不是测试用例：由验收测试经 supervisor 适配器的 spawn 通道拉起，行为等价一个
真实服务面——绑定指定端口、接受 TCP 连接、常驻直到被终止。cmdline 自带
`fake_module_server --module <name>` 识别关键字，宿主进程识别（psutil pivot
的 parent_chain / cmdline 匹配）据此判定端口归属。
"""

from __future__ import annotations

import argparse
import socket
import time


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--module", required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", args.port))
    server.listen(16)
    print(f"fake-module {args.module} listening on {args.port}", flush=True)
    while True:
        try:
            conn, _ = server.accept()
            conn.close()
        except OSError:
            time.sleep(0.2)


if __name__ == "__main__":
    main()
