# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 规格（Windows onedir / Linux AppDir 共用）。

产物布局（bundle 相对，QUOT-524/525 同一口径）：

    dist/LadderStock/LadderStock.exe        # onedir
    dist/LadderStock/_internal/...          # 解释器与依赖（随包，无需系统 Python）
    dist/LadderStock/...                    # peer 放 dist/ 下即 install_root

资源目标目录必须保持相对（`packaging/bundle_manifest.py` 有单测守）。
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

SPEC_DIR = Path(SPECPATH)
REPO_ROOT = SPEC_DIR.parent

import sys

sys.path.insert(0, str(SPEC_DIR))

from bundle_manifest import (  # noqa: E402
    EXCLUDES,
    HIDDEN_IMPORTS,
    assert_relative_destinations,
    collect_datas,
)

datas = collect_datas(REPO_ROOT)
assert_relative_destinations(datas)
datas += collect_data_files("uvicorn")
datas += collect_data_files("fastapi")

hiddenimports = list(HIDDEN_IMPORTS)
for package in ("uvicorn", "fastapi", "pydantic", "mcp", "apscheduler"):
    hiddenimports += collect_submodules(package)

a = Analysis(
    [str(SPEC_DIR / "ladderstock_entry.py")],
    pathex=[str(REPO_ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=list(EXCLUDES),
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LadderStock",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="LadderStock",
)
