"""打包入口（PyInstaller / py2app 共用）。

双击启动 = `__desktop`：起看板、端口就绪后打开默认浏览器。CLI 参数照常透传，
所以打包产物同时是 `LadderStock.exe <子命令>`（supervisor / 看门狗 / 调度器
都是同一个 exe 的子进程）。
"""

from __future__ import annotations

import sys

from ladderstock.cli.main import main


def run() -> None:
    if len(sys.argv) == 1:
        sys.argv = [sys.argv[0], "__desktop"]
    main()


if __name__ == "__main__":
    run()
