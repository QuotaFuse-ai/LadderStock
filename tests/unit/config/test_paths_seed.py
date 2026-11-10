"""路径解析与首跑 seed 的契约（DEC-071）：

- 安装态解析到 `platformdirs` 的用户数据目录，不依赖任何硬编码盘符；
- `LDST_*` 显式覆盖永远优先，旧前缀只兜底；
- 干净目录首跑从随包模板补齐**完整**配置（含 `schema/`），已存在的文件绝不覆盖；
- seed 后的 `sources.yaml` 里必须有 QUOT-471 的美股指数源 `tickhub_us_index_quote`
  （此前缺它就是首页美股两格「漂移」的根因）；
- 配置模板里不落明文密钥（只写环境变量名）。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from ladderstock.cli import paths
from ladderstock.cli.paths import (
    CONFIG_FILES,
    bundle_config_dir,
    config_dir,
    data_dir,
    user_data_root,
)
from ladderstock.config.seed import seed_config, seed_if_needed

REPO_CONFIG = Path(__file__).resolve().parents[3] / "config"

US_INDEX_SOURCE_ID = "tickhub_us_index_quote"


@pytest.fixture()
def clean_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """模拟干净安装态的 HOME：清掉所有 LDST_* 覆盖，并把 platformdirs 指向临时目录。"""
    for key in list({f"LDST_{n}" for n in ("CONFIG_DIR", "DATA_DIR")}):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("VRA_CONFIG_DIR", raising=False)
    monkeypatch.delenv("VIBE_ASTOCK_CONFIG_DIR", raising=False)
    monkeypatch.delenv("VRA_DATA_DIR", raising=False)
    monkeypatch.delenv("VIBE_ASTOCK_DATA_DIR", raising=False)
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    # 安装态落点固定到临时目录，避免测试写进真实用户目录
    monkeypatch.setattr(paths, "user_data_root", lambda: tmp_path / "userdata")
    return tmp_path / "userdata"


def _needs_repo_config() -> None:
    if not (REPO_CONFIG / "sources.yaml").is_file():
        pytest.skip("仓库 config/ 模板不在（安装态测试不适用于裸 wheel）")


# --------------------------------------------------------------------------- #
# 路径解析
# --------------------------------------------------------------------------- #

def test_installed_config_dir_under_user_data_root(clean_home: Path, tmp_path: Path) -> None:
    # 不含 cwd/config 的干净目录 → 落到用户数据目录
    tmp_cwd = tmp_path / "elsewhere"
    tmp_cwd.mkdir()
    import os

    old = os.getcwd()
    os.chdir(tmp_cwd)
    try:
        assert config_dir() == clean_home / "config"
        assert data_dir() == clean_home / "data"
    finally:
        os.chdir(old)


def test_explicit_env_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    custom = tmp_path / "custom-config"
    monkeypatch.setenv("LDST_CONFIG_DIR", str(custom))
    assert config_dir() == custom


def test_legacy_env_only_fallback(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    monkeypatch.delenv("LDST_CONFIG_DIR", raising=False)
    monkeypatch.setenv("VRA_CONFIG_DIR", str(legacy))
    assert config_dir() == legacy
    # 新前缀一旦存在就必须压过旧前缀
    fresh = tmp_path / "fresh"
    monkeypatch.setenv("LDST_CONFIG_DIR", str(fresh))
    assert config_dir() == fresh


def test_user_data_root_has_no_hardcoded_production_path() -> None:
    root = user_data_root()
    assert root.is_absolute()
    # 安装态落点必须来自 platformdirs，而不是任何仓库自带的盘符常量
    assert "workplace" not in str(root).lower()


# --------------------------------------------------------------------------- #
# 首跑 seed
# --------------------------------------------------------------------------- #

def test_seed_fills_clean_dir(clean_home: Path) -> None:
    _needs_repo_config()
    target = clean_home / "config"
    assert not target.exists()

    report = seed_config(config_root=target, bundle_root=REPO_CONFIG)

    assert report.changed
    for name in CONFIG_FILES:
        assert (target / name).is_file(), f"首跑 seed 漏了 {name}"
    # schema 也要补齐，否则干净目录下 config validate 跑不了
    assert (target / "schema" / "sources.schema.json").is_file()


def test_seed_repairs_missing_sources_file(clean_home: Path) -> None:
    """漂移回归（主路径）：目录里没有 sources.yaml → seed 出一份带 US2 指数源的完整文件。"""
    _needs_repo_config()
    target = clean_home / "config"
    target.mkdir(parents=True)
    # 旧安装最常见的漂移态：只迁了 state.yaml，sources.yaml 一直没落盘
    (target / "state.yaml").write_text("state: {}\n", encoding="utf-8")

    seed_config(config_root=target, bundle_root=REPO_CONFIG)

    src = yaml.safe_load((target / "sources.yaml").read_text(encoding="utf-8"))
    ids = {s.get("id") for s in src["sources"]}
    assert US_INDEX_SOURCE_ID in ids


def test_existing_sources_file_is_not_overwritten(clean_home: Path) -> None:
    """用户已有的 sources.yaml 不被 seed 覆盖（写过的配置优先，升级不回滚）。"""
    _needs_repo_config()
    target = clean_home / "config"
    target.mkdir(parents=True)
    existing = yaml.safe_load((REPO_CONFIG / "sources.yaml").read_text(encoding="utf-8"))
    existing["sources"] = [s for s in existing["sources"] if s.get("id") != US_INDEX_SOURCE_ID]
    mine = target / "sources.yaml"
    mine.write_text(yaml.safe_dump(existing, allow_unicode=True), encoding="utf-8")

    report = seed_config(config_root=target, bundle_root=REPO_CONFIG)

    assert str(mine) in report.skipped
    loaded = yaml.safe_load(mine.read_text(encoding="utf-8"))
    assert US_INDEX_SOURCE_ID not in {s.get("id") for s in loaded["sources"]}


def test_seed_clean_dir_sources_contains_us_index(clean_home: Path) -> None:
    _needs_repo_config()
    target = clean_home / "config"
    seed_config(config_root=target, bundle_root=REPO_CONFIG)

    src = yaml.safe_load((target / "sources.yaml").read_text(encoding="utf-8"))
    ids = {s.get("id") for s in src["sources"]}
    assert US_INDEX_SOURCE_ID in ids
    entry = next(s for s in src["sources"] if s.get("id") == US_INDEX_SOURCE_ID)
    assert entry["dataset"] == "index_quote_us"
    assert entry["via"] == "tickhub_us"


def test_seed_never_overwrites_existing(clean_home: Path) -> None:
    _needs_repo_config()
    target = clean_home / "config"
    target.mkdir(parents=True)
    mine = target / "sources.yaml"
    mine.write_text("sources: []\n", encoding="utf-8")

    report = seed_config(config_root=target, bundle_root=REPO_CONFIG)

    assert mine.read_text(encoding="utf-8") == "sources: []\n"
    assert str(mine) in report.skipped


def test_seed_idempotent(clean_home: Path) -> None:
    _needs_repo_config()
    target = clean_home / "config"
    seed_config(config_root=target, bundle_root=REPO_CONFIG)
    second = seed_config(config_root=target, bundle_root=REPO_CONFIG)
    assert second.seeded == []


def test_seed_if_needed_only_in_install_mode(
    clean_home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _needs_repo_config()
    # 安装态跑在任意 CWD（没有 cwd/config），才会落到 user_data_root()/config
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    report = seed_if_needed()
    assert report is not None
    assert (clean_home / "config" / "sources.yaml").is_file()

    # 源码开发态（cwd/config 命中）不 seed
    monkeypatch.chdir(REPO_CONFIG.parent)
    assert seed_if_needed() is None

    # 显式 LDST_CONFIG_DIR 不属于安装态，程序不往里塞文件
    custom = tmp_path / "custom"
    monkeypatch.setenv("LDST_CONFIG_DIR", str(custom))
    assert seed_if_needed() is None
    assert not custom.exists()


# --------------------------------------------------------------------------- #
# 红线：无硬编码路径 / 密钥不进模板
# --------------------------------------------------------------------------- #

def test_bundle_config_has_no_absolute_drive_paths() -> None:
    _needs_repo_config()
    offenders: list[str] = []
    for path in REPO_CONFIG.rglob("*.yaml"):
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if ":\\\\" in line or "D:/" in line or "C:/" in line:
                offenders.append(f"{path.name}:{lineno}: {stripped}")
    assert offenders == [], "配置模板里出现硬编码绝对路径：" + "; ".join(offenders)


def test_config_templates_carry_no_plaintext_secrets() -> None:
    _needs_repo_config()
    ai = yaml.safe_load((REPO_CONFIG / "ai.yaml").read_text(encoding="utf-8"))
    for backend in ai["ai"]["backends"]:
        # 只允许环境变量名，不允许直接把密钥写进模板
        assert backend.get("api_key_env"), backend
        assert "api_key" not in backend, f"{backend['id']} 写了明文 api_key"
        assert "base_url" not in backend, f"{backend['id']} 写了明文 base_url"


def test_bundle_config_ships_with_package_data() -> None:
    """打包口径：随包模板必须在 `config/`，seed 才找得到（QUOT-525 打包时同步）。"""
    _needs_repo_config()
    assert (REPO_CONFIG / "sources.yaml").is_file()


def test_bundle_templates_inside_package() -> None:
    """安装态（site-packages）里没有工程根，模板必须随包发布。"""
    packaged = Path(paths.__file__).resolve().parents[1] / "assets" / "config"
    assert (packaged / "sources.yaml").is_file()
    assert (packaged / "schema" / "sources.schema.json").is_file()
    assert bundle_config_dir() == packaged


def test_packaged_templates_match_repo_config() -> None:
    """包内副本与仓库 `config/` 同步：两处各改一份就会让开发态与安装态漂移。"""
    _needs_repo_config()
    packaged = Path(paths.__file__).resolve().parents[1] / "assets" / "config"
    repo_files = {p.relative_to(REPO_CONFIG) for p in REPO_CONFIG.rglob("*") if p.is_file()}
    pkg_files = {p.relative_to(packaged) for p in packaged.rglob("*") if p.is_file()}
    assert repo_files == pkg_files
    for rel in sorted(repo_files):
        assert (REPO_CONFIG / rel).read_bytes() == (packaged / rel).read_bytes(), rel


def test_platformdirs_layout_per_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """落点口径：Windows 带 appauthor，macOS / Linux 只带 appname（DEC-071）。

    `Windows.user_data_dir` 读的是 Known Folder（ctypes），在非 Windows 上按库语义抛
    `NotImplementedError`；这里把 `get_win_folder` 换成假路径，才能在 Linux CI 上验证
    同一段拼接逻辑，而不是让测试裸抛。macOS / Unix 的落点只依赖 `expanduser`，全平台可跑。
    """
    from platformdirs import windows as platformdirs_windows
    from platformdirs.macos import MacOS
    from platformdirs.unix import Unix

    monkeypatch.setattr(
        platformdirs_windows,
        "get_win_folder",
        lambda _const: r"C:\Users\tester\AppData\Local",
    )
    win = platformdirs_windows.Windows(appname=paths.APP_NAME, appauthor=paths.APP_AUTHOR)
    assert Path(win.user_data_dir.replace("\\", "/")).parts[-2:] == (
        paths.APP_AUTHOR,
        paths.APP_NAME,
    )

    mac = Path(MacOS(appname=paths.APP_NAME, appauthor=paths.APP_AUTHOR).user_data_dir)
    assert mac.parts[-1] == paths.APP_NAME

    unix = Path(Unix(appname=paths.APP_NAME, appauthor=paths.APP_AUTHOR).user_data_dir)
    assert unix.parts[-1] == paths.APP_NAME


def test_user_data_root_matches_platformdirs() -> None:
    """本机安装态落点与 platformdirs 的 `user_data_dir(APP_NAME, APP_AUTHOR)` 一致。"""
    from platformdirs import user_data_dir

    expected = Path(user_data_dir(paths.APP_NAME, paths.APP_AUTHOR))
    assert user_data_root() == expected
    assert user_data_root().name == paths.APP_NAME


def test_source_template_matches_expected_files() -> None:
    _needs_repo_config()
    for name in CONFIG_FILES:
        assert (REPO_CONFIG / name).is_file(), f"随包模板缺 {name}"


def test_clean_seed_matches_bundle_file_count(clean_home: Path) -> None:
    _needs_repo_config()
    target = clean_home / "config"
    shutil.rmtree(target, ignore_errors=True)
    report = seed_config(config_root=target, bundle_root=REPO_CONFIG)
    expected = len(list(REPO_CONFIG.glob("*.yaml"))) + len(
        list((REPO_CONFIG / "schema").glob("*.json"))
    )
    assert len(report.seeded) == expected
