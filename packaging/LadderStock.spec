# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 规格（Windows onedir / Linux AppDir 共用）。

产物布局（bundle 相对，QUOT-524/525 同一口径）：

    dist/LadderStock/LadderStock.exe        # onedir 启动器
    dist/LadderStock/_internal/...          # 启动器自己的解释器与依赖
    dist/LadderStock/python/                # 随包 embeddable Python（peer venv 底座）
    dist/LadderStock/...                    # peer 放 dist/ 下即 install_root

`python/` 不是给主程序用的（主程序由 _internal 承载），而是给
`modules/installer/runner.py` 的 `portable_python()` 用的：装子模块时要
`<安装根>/python/python.exe`，普通用户机上没有别的 Python 可回落。

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
# `agents`（openai-agents）的提示词模板是 .md 数据文件，不 collect 时运行期
# `read_text` 直接 FileNotFoundError（烟测实测：memory_consolidation_prompt.md 缺失）。
datas += collect_data_files("agents")

# 随包 embeddable Python：主程序用 _internal，但安装子模块需要
# `<安装根>/python/python.exe` 去建 peer venv。目录不存在就跳过（macOS/Linux
# 的 AppDir 由各自脚本补），Windows 便携包必须带上。
_portable_py = REPO_ROOT / "python"
if (_portable_py / "python.exe").is_file():
    datas.append((str(_portable_py), "python"))

hiddenimports = list(HIDDEN_IMPORTS)
for package in (
    "uvicorn",
    "fastapi",
    "pydantic",
    "mcp",
    "apscheduler",
    "agents",
    # 记忆引擎的子模块是运行期按名字加载（mem0 的 embedder/llm/storage 都是），
    # 只写顶层包名不够；torch/transformers 交给 PyInstaller 自带 hook 收。
    "mem0",
    "qdrant_client",
    "sentence_transformers",
):
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
