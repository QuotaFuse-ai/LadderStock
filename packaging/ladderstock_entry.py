"""打包入口（PyInstaller / py2app 共用）。

双击启动 = `__desktop`：起看板 + 托管子模块、端口就绪后打开默认浏览器。
CLI 参数照常透传，所以打包产物同时是 `LadderStock.exe <子命令>`（supervisor /
看门狗 / 调度器都是同一个 exe 的子进程）。

windowed 形态（console=False）里 stdout/stderr 是 None——必须先接文件
兜底，否则任何 print / logging / 报错都会把应用静默打死。
"""

from __future__ import annotations

import sys

from ladderstock.cli.windowed import ensure_windowed_sinks


def run() -> None:
    ensure_windowed_sinks()
    if len(sys.argv) == 1:
        sys.argv = [sys.argv[0], "__desktop"]
    from ladderstock.cli.main import main

    main()  # type: ignore[no-untyped-call]


if __name__ == "__main__":
    run()
