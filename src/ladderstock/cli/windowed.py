"""无控制台模式兜底（DEC-092）：pythonw / 打包 windowed exe 下把标准流落盘。

`pythonw.exe` 与 PyInstaller `console=False` 启动时 `sys.stdout` / `sys.stderr`
是 **None**：任何 `print` / `sys.stderr.write` / `logging.StreamHandler`
都会直接抛异常，而用户什么都看不见（没有窗口）。这里在入口最早处补两个
追加模式的日志文件，让报错可被看见、也让 uvicorn/typer 的默认处理器活着。

文件位置：`<数据目录>/service/logs/app-stdout.log` 与 `app-stderr.log`。
失败（目录不可写等）静默降级——不能因为"想写日志"把应用启动搞挂。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import IO, Any


def _open_append(path: Path) -> IO[Any] | None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        return open(path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115 - 常驻进程，随主进程关闭
    except Exception:  # noqa: BLE001 - 日志落盘失败不能挡启动
        return None


def ensure_windowed_sinks() -> bool:
    """stdout / stderr 为 None 时补文件流；返回是否接了兜底。

    只在「真没有流」时动手：开发态终端 / CI 重定向一律原样保留。
    """
    attached = False
    base = None
    try:
        from ladderstock.cli.paths import data_dir

        base = Path(data_dir()) / "service" / "logs"
    except Exception:  # noqa: BLE001 - 数据目录都解析不出来时退回当前目录
        base = Path("service-logs")

    if sys.stdout is None:
        stream = _open_append(base / "app-stdout.log")
        if stream is not None:
            sys.stdout = stream
            attached = True
    if sys.stderr is None:
        stream = _open_append(base / "app-stderr.log")
        if stream is not None:
            sys.stderr = stream
            attached = True
    return attached


__all__ = ["ensure_windowed_sinks"]
