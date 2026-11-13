"""便携模式（QUOT-592）：config/data 落安装根内 + install_root 新布局上溯。

不真正起进程；验证给定 sys.frozen / sys.executable / `.portable` 标记时
`paths.config_dir()` / `paths.data_dir()` / `runtime.install_root()` 的解析。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from ladderstock.cli import paths
from ladderstock.core import runtime


@pytest.fixture()
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(runtime.INSTALL_ROOT_ENV, raising=False)
    monkeypatch.delenv("LDST_CONFIG_DIR", raising=False)
    monkeypatch.delenv("LDST_DATA_DIR", raising=False)
    for prefix in paths.LEGACY_ENV_PREFIXES:
        monkeypatch.delenv(f"{prefix}CONFIG_DIR", raising=False)
        monkeypatch.delenv(f"{prefix}DATA_DIR", raising=False)


def _clear_cwd_config_data(monkeypatch: pytest.MonkeyPatch, cwd: Path) -> None:
    monkeypatch.chdir(cwd)


# --------------------------------------------------------------------------- #
# 便携判定
# --------------------------------------------------------------------------- #

def test_frozen_is_always_portable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert runtime.is_portable() is True


def test_source_tree_without_marker_is_not_portable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert runtime.is_portable() is False


def test_source_tree_with_marker_is_portable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / runtime.PORTABLE_MARKER).write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setenv(runtime.INSTALL_ROOT_ENV, str(tmp_path))
    assert runtime.is_portable() is True


# --------------------------------------------------------------------------- #
# 便携模式下 config/data 落安装根内
# --------------------------------------------------------------------------- #

def test_portable_config_data_resolve_inside_install_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / runtime.PORTABLE_MARKER).write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setenv(runtime.INSTALL_ROOT_ENV, str(tmp_path))
    # cwd 里的 config/ 不应被优先（便携优先于 cwd 惯例）
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "data").mkdir(exist_ok=True)

    assert paths.config_dir() == tmp_path / "config"
    assert paths.data_dir() == tmp_path / "data"


def test_portable_frozen_config_data_inside_install_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    exe = tmp_path / "LadderStock" / "LadderStock.exe"
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.chdir(tmp_path)

    assert paths.config_dir() == tmp_path / "config"
    assert paths.data_dir() == tmp_path / "data"


# --------------------------------------------------------------------------- #
# 非便携兜底不变
# --------------------------------------------------------------------------- #

def test_non_portable_source_still_prefers_cwd(
    clean_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()

    assert paths.config_dir() == tmp_path / "config"


def test_non_portable_frozen_falls_back_to_user_data(
    clean_env: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.chdir(tmp_path)
    user_root = tmp_path / "userdata"
    monkeypatch.setattr(paths, "user_data_root", lambda: user_root)

    assert paths.config_dir() == user_root / "config"
    assert paths.data_dir() == user_root / "data"


# --------------------------------------------------------------------------- #
# 显式覆盖永远最高优先（含便携模式）
# --------------------------------------------------------------------------- #

def test_explicit_env_override_wins_over_portable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    (tmp_path / runtime.PORTABLE_MARKER).write_text("", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setenv(runtime.INSTALL_ROOT_ENV, str(tmp_path))
    cfg = tmp_path / "custom-cfg"
    dat = tmp_path / "custom-dat"
    monkeypatch.setenv("LDST_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("LDST_DATA_DIR", str(dat))

    assert paths.config_dir() == cfg
    assert paths.data_dir() == dat


# --------------------------------------------------------------------------- #
# install_root 新 onedir 布局（exe 在 bin/LadderStock/ 下）
# --------------------------------------------------------------------------- #

def test_install_root_new_onedir_bin_layout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(
        sys, "executable", str(tmp_path / "bin" / "LadderStock" / "LadderStock.exe")
    )

    assert runtime.install_root() == tmp_path.resolve()
    assert paths.config_dir() == tmp_path / "config"
    assert paths.data_dir() == tmp_path / "data"


def test_install_root_legacy_onedir_layout_still_works(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(
        sys, "executable", str(tmp_path / "LadderStock" / "LadderStock.exe")
    )

    assert runtime.install_root() == tmp_path.resolve()


# --------------------------------------------------------------------------- #
# 启动器无绝对路径
# --------------------------------------------------------------------------- #

def test_launchers_have_no_absolute_drive_paths() -> None:
    repo = Path(__file__).resolve().parents[3]
    offenders = [
        p
        for p in repo.glob("file/ops/*.cmd")
        if any(
            seg.upper().startswith(("D:\\", "C:\\", "D:/", "C:/"))
            for seg in p.read_text(encoding="utf-8", errors="ignore").splitlines()
            if not seg.lstrip().startswith("rem")
        )
    ]
    assert offenders == []
