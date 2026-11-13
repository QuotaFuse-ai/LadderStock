"""冻结态（打包）路径解析（QUOT-525）。

这里只验证「给定 sys.frozen / sys._MEIPASS / sys.executable 时，三个根目录
（bundle / install / project）解析成什么」，不真正起进程；真实干净机器验证
见 `packaging/smoke_win.ps1` 与 `packaging/smoke_posix.sh`。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from ladderstock.cli import paths
from ladderstock.core import runtime
from ladderstock.supervisor.module_adapters import launcher_entry
from ladderstock.supervisor.page_boot import PageBoot


@pytest.fixture()
def frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    """清掉所有可能干扰的覆盖变量，默认无 _MEIPASS。"""
    for key in (runtime.INSTALL_ROOT_ENV, runtime.APPIMAGE_ENV):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)


def _set_exe(monkeypatch: pytest.MonkeyPatch, exe: Path) -> Path:
    monkeypatch.setattr(sys, "executable", str(exe))
    return Path(str(exe)).resolve()


def _set_meipass(monkeypatch: pytest.MonkeyPatch, path: Path) -> Path:
    monkeypatch.setattr(sys, "_MEIPASS", str(path), raising=False)
    return path


# --------------------------------------------------------------------------- #
# 源码态
# --------------------------------------------------------------------------- #

def test_source_tree_roots_are_repo_relative() -> None:
    repo = Path(__file__).resolve().parents[3]
    assert runtime.bundle_root() == repo
    assert runtime.app_dir() == repo
    assert runtime.project_root() == repo
    assert runtime.install_root() == repo.parent
    # 边界扫描的根是 `src/`，不是仓库根（否则会扫到 scripts/ 里的夹具字符串）
    assert runtime.source_root() == repo / "src"
    assert runtime.package_dir() == repo / "src" / "ladderstock"
    assert runtime.frontend_dist_dir() == repo / "frontend" / "dist"
    assert runtime.web_static_dir() == repo / "src" / "ladderstock" / "web" / "static"


# --------------------------------------------------------------------------- #
# Windows onedir / onefile
# --------------------------------------------------------------------------- #

def test_windows_onedir_layout(
    frozen: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    exe = _set_exe(monkeypatch, tmp_path / "LadderStock" / "LadderStock.exe")
    meipass = _set_meipass(monkeypatch, tmp_path / "LadderStock" / "_internal")

    assert runtime.install_root() == tmp_path.resolve()
    assert runtime.app_dir() == exe.parent
    assert runtime.project_root() == tmp_path.resolve()
    assert runtime.bundle_root() == meipass
    assert runtime.bundle_config_dir() == meipass / "ladderstock" / "assets" / "config"
    assert runtime.frontend_dist_dir() == meipass / "frontend" / "dist"
    assert runtime.web_static_dir() == meipass / "ladderstock" / "web" / "static"


def test_windows_onefile_layout(
    frozen: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_exe(monkeypatch, tmp_path / "LadderStock.exe")
    meipass = _set_meipass(monkeypatch, tmp_path / "_MEI123456")

    assert runtime.install_root() == tmp_path.resolve()
    assert runtime.app_dir() == tmp_path.resolve()
    assert runtime.bundle_root() == meipass


# --------------------------------------------------------------------------- #
# macOS .app
# --------------------------------------------------------------------------- #

def test_macos_app_layout(frozen: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    exe = _set_exe(
        monkeypatch, tmp_path / "LadderStock.app" / "Contents" / "MacOS" / "LadderStock"
    )
    app = exe.parent.parent.parent
    resources = exe.parent.parent / "Resources"
    resources.mkdir(parents=True)

    assert runtime.install_root() == tmp_path.resolve()
    assert runtime.app_dir() == app
    assert runtime.bundle_root() == resources
    assert runtime.frontend_dist_dir() == resources / "frontend" / "dist"


def test_macos_without_resources_falls_back_to_exe_dir(
    frozen: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    exe = _set_exe(monkeypatch, tmp_path / "LadderStock.app" / "Contents" / "MacOS" / "LadderStock")
    assert runtime.bundle_root() == exe.parent


# --------------------------------------------------------------------------- #
# Linux AppImage
# --------------------------------------------------------------------------- #

def test_appimage_install_root_is_original_file_dir(
    frozen: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mount = tmp_path / ".mount_LadderStock"
    exe = _set_exe(monkeypatch, mount / "usr" / "bin" / "LadderStock")
    appimage = tmp_path / "LadderStock-x86_64.AppImage"
    monkeypatch.setenv(runtime.APPIMAGE_ENV, str(appimage))

    assert runtime.install_root() == tmp_path.resolve()
    assert runtime.bundle_root() == exe.parent


# --------------------------------------------------------------------------- #
# 显式覆盖
# --------------------------------------------------------------------------- #

def test_install_root_env_override_wins(
    frozen: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _set_exe(monkeypatch, tmp_path / "LadderStock" / "LadderStock.exe")
    override = tmp_path / "custom-root"
    monkeypatch.setenv(runtime.INSTALL_ROOT_ENV, str(override))

    assert runtime.install_root() == override
    assert runtime.project_root() == override


# --------------------------------------------------------------------------- #
# 用户目录：冻结态绝不把 cwd 当配置目录
# --------------------------------------------------------------------------- #

def test_frozen_config_dir_never_uses_cwd(
    frozen: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    cwd = tmp_path / "install"
    (cwd / "config").mkdir(parents=True)
    (cwd / "data").mkdir(parents=True)
    monkeypatch.chdir(cwd)
    monkeypatch.delenv("LDST_CONFIG_DIR", raising=False)
    monkeypatch.delenv("LDST_DATA_DIR", raising=False)
    for prefix in paths.LEGACY_ENV_PREFIXES:
        monkeypatch.delenv(f"{prefix}CONFIG_DIR", raising=False)
        monkeypatch.delenv(f"{prefix}DATA_DIR", raising=False)

    # QUOT-592：冻结态即便携态，config/data 落安装根内（绝不落 cwd 之外的目录）
    install = runtime.install_root()
    assert paths.config_dir() == install / "config"
    assert paths.data_dir() == install / "data"
    assert paths.bundle_config_dir() == runtime.bundle_config_dir()


def test_source_config_dir_still_prefers_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(runtime, "is_frozen", lambda: False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    monkeypatch.delenv("LDST_CONFIG_DIR", raising=False)
    for prefix in paths.LEGACY_ENV_PREFIXES:
        monkeypatch.delenv(f"{prefix}CONFIG_DIR", raising=False)

    assert paths.config_dir() == tmp_path / "config"


# --------------------------------------------------------------------------- #
# 子进程入口：冻结态不能出现 `-m`
# --------------------------------------------------------------------------- #

def test_launcher_entry_frozen_is_bare_exe(
    frozen: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    exe = _set_exe(monkeypatch, tmp_path / "LadderStock" / "LadderStock.exe")
    monkeypatch.delenv("LDST_LAUNCHER", raising=False)

    assert launcher_entry() == (str(exe),)
    assert "-m" not in launcher_entry()


def test_page_boot_self_exe_frozen_is_bare_exe(
    frozen: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    exe = _set_exe(monkeypatch, tmp_path / "LadderStock" / "LadderStock.exe")

    assert PageBoot._self_exe_command(object()) == (str(exe),)  # type: ignore[arg-type]
