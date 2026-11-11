"""随包资源清单契约（QUOT-525）。

打包脚本（PyInstaller spec / py2app setup）、单测、运行时三处共用同一份清单；
这里保证目标路径永远是相对的，避免产物被绑定到构建机的绝对路径。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

PACKAGING = Path(__file__).resolve().parents[2] / "packaging"


def _load_manifest() -> ModuleType:
    path = PACKAGING / "bundle_manifest.py"
    spec = importlib.util.spec_from_file_location("bundle_manifest_under_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def manifest() -> ModuleType:
    return _load_manifest()


def test_all_resource_destinations_are_relative(manifest: ModuleType) -> None:
    datas = [(src, dest) for src, dest in manifest.RESOURCE_DIRS]
    manifest.assert_relative_destinations(datas)
    for _src, dest in datas:
        assert not Path(dest).is_absolute()
        assert ".." not in Path(dest).parts


def test_collect_datas_keeps_relative_destinations(
    manifest: ModuleType, tmp_path: Path
) -> None:
    for src_rel, _dest in manifest.RESOURCE_DIRS:
        (tmp_path / src_rel).mkdir(parents=True)

    datas = manifest.collect_datas(tmp_path)

    assert [dest for _src, dest in datas] == [dest for _src, dest in manifest.RESOURCE_DIRS]
    manifest.assert_relative_destinations(datas)


def test_collect_datas_fails_on_missing_resource(
    manifest: ModuleType, tmp_path: Path
) -> None:
    missing_rel, _dest = manifest.RESOURCE_DIRS[-1]
    for src_rel, _dest in manifest.RESOURCE_DIRS[:-1]:
        (tmp_path / src_rel).mkdir(parents=True)

    with pytest.raises(FileNotFoundError) as excinfo:
        manifest.collect_datas(tmp_path)

    assert missing_rel in str(excinfo.value)


@pytest.mark.parametrize(
    "dest",
    [r"C:\abs\dest", "/abs/dest", "\\abs\\dest", r"D:\other"],
)
def test_absolute_destination_rejected(manifest: ModuleType, dest: str) -> None:
    with pytest.raises(ValueError):
        manifest.assert_relative_destinations([("src", dest)])


def test_packaging_entrypoints_exist() -> None:
    for name in (
        "ladderstock_entry.py",
        "LadderStock.spec",
        "build_win.ps1",
        "build_macos.sh",
        "build_linux_appimage.sh",
        "smoke_win.ps1",
        "smoke_posix.sh",
        "macos/setup_py2app.py",
        "linux/ladderstock.desktop",
    ):
        assert (PACKAGING / name).is_file(), name
