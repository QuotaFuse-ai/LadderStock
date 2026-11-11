"""py2app 规格：生成自带解释器的 `LadderStock.app`（QUOT-525）。

运行方式（由 `packaging/build_macos.sh` 调用，cwd = `packaging/`）：

    python macos/setup_py2app.py py2app --dist-dir ../dist

产物：`dist/LadderStock.app`（install_root = `dist/`，peer 放在 .app 同级）。
资源落 `Contents/Resources/`，与 `core.runtime.bundle_root()` 的冻结态解析一致。
"""

from __future__ import annotations

import sys
from pathlib import Path

from setuptools import setup

HERE = Path(__file__).resolve().parent
PACKAGING = HERE.parent
ROOT = PACKAGING.parent
sys.path.insert(0, str(PACKAGING))

from bundle_manifest import EXCLUDES, HIDDEN_IMPORTS, RESOURCE_DIRS  # noqa: E402


def _data_files() -> list[tuple[str, list[str]]]:
    """把 RESOURCE_DIRS 展开成 py2app/distutils 的 `data_files`。

    目标目录保留相对层级（`frontend/dist/assets/...`），与 PyInstaller 产物同形。
    """
    files: dict[str, list[str]] = {}
    for src_rel, dest_rel in RESOURCE_DIRS:
        src = ROOT / src_rel
        if not src.exists():
            raise SystemExit(f"随包资源缺失：{src_rel}（先 npm ci && npm run build）")
        for path in sorted(src.rglob("*")):
            if not path.is_file():
                continue
            parent = path.parent.relative_to(src).as_posix()
            dest = dest_rel if parent == "." else f"{dest_rel}/{parent}"
            files.setdefault(dest, []).append(str(path))
    return sorted(files.items())


OPTIONS = {
    "packages": ["ladderstock", "anyio"],
    "includes": list(HIDDEN_IMPORTS) + ["platformdirs"],
    "excludes": list(EXCLUDES),
    "argv_emulation": False,
    "strip": False,
    "site_packages": True,
    "plist": {
        "CFBundleName": "LadderStock",
        "CFBundleDisplayName": "天梯股复盘台",
        "CFBundleIdentifier": "com.quotafuse.ladderstock",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "0.1.0",
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
    },
}

setup(
    name="LadderStock",
    version="0.1.0",
    app=["ladderstock_entry.py"],
    data_files=_data_files(),
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
