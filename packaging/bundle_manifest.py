"""随包资源清单（PyInstaller spec / py2app setup / 单测共用）。

只允许出现**相对**目标路径：解包目录（`sys._MEIPASS` / `Contents/Resources`）
里的位置由 PyInstaller / py2app 决定，运行时统一经 `core.runtime` 解析。
源码态与打包态的对应关系写死在这里，避免两份清单漂移。
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

#: (源码相对路径, 解包后目标目录)。目标必须是相对路径，不能出现盘符或绝对路径。
RESOURCE_DIRS: tuple[tuple[str, str], ...] = (
    ("src/ladderstock/web/static", "ladderstock/web/static"),
    ("src/ladderstock/assets/config", "ladderstock/assets/config"),
    ("frontend/dist", "frontend/dist"),
)

#: 动态导入/运行期才用到的包：静态分析抓不到，必须显式声明。
HIDDEN_IMPORTS: tuple[str, ...] = (
    "apscheduler",
    "apscheduler.schedulers.background",
    "apscheduler.schedulers.blocking",
    "apscheduler.triggers.cron",
    "apscheduler.triggers.interval",
    "duckdb",
    "fastapi",
    "httpx",
    "jsonschema",
    "mcp",
    "platformdirs",
    "psutil",
    "pydantic",
    "reportlab",
    "reportlab.pdfgen.canvas",
    "fontTools",
    "typer",
    "uvicorn",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    "yaml",
)

#: 三平台都不需要、且会显著增大产物的包（本仓是独立应用，不 import 这些 peer）。
EXCLUDES: tuple[str, ...] = (
    "ProxyFleet",
    "proxyfleet",
    "stocksenate",
    "pytest",
    "mypy",
    "ruff",
    "IPython",
    "matplotlib",
    "numpy",
    "pandas",
)


def collect_datas(repo_root: str | Path) -> list[tuple[str, str]]:
    """返回 PyInstaller `datas` 形态的 `(源目录, 相对目标)`。

    源目录缺失直接报错：缺 `frontend/dist` 打出来的包只能看到「前端未构建」提示页，
    那不算「双击起看板」，必须在构建期失败而不是交付后才发现。
    """
    root = Path(repo_root)
    datas: list[tuple[str, str]] = []
    missing: list[str] = []
    for src_rel, dest_rel in RESOURCE_DIRS:
        src = root / src_rel
        if not src.exists():
            missing.append(src_rel)
            continue
        datas.append((str(src), dest_rel))
    if missing:
        raise FileNotFoundError(
            "随包资源缺失：" + ", ".join(missing) + "（前端请先 `npm ci && npm run build`）"
        )
    return datas


def assert_relative_destinations(datas: Iterable[tuple[str, str]]) -> None:
    """目标目录必须是相对路径：绝对路径会把产物绑定到构建机。"""
    for _src, dest in datas:
        path = Path(dest)
        if path.is_absolute() or path.drive or dest.startswith(("/", "\\")):
            raise ValueError(f"随包目标必须是相对路径，实际为 {dest!r}")
