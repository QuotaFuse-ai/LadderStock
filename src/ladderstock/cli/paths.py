"""路径解析：配置目录、数据目录、仓库根。

优先级（DEC-001 / DEC-071）：
- `LDST_CONFIG_DIR` / `LDST_DATA_DIR` 显式指定（最高，开发/排障用）
- 当前工作目录下的 `config/`、`data/`（源码开发态）
- 用户数据目录（安装态）：`platformdirs` 解析的
  `user_data_dir("LadderStock", "QuotaFuse")`，含 `config/`、`data/` 两个子目录

安装态**不写死任何绝对路径**，也不假设 `~/.ladderstock`：Windows 落到
`%LOCALAPPDATA%\\QuotaFuse\\LadderStock`，macOS 落到
`~/Library/Application Support/LadderStock`，
Linux 落到 `$XDG_DATA_HOME/LadderStock`（默认 `~/.local/share/LadderStock`）。

**旧前缀兼容**：过渡期继续读取 `VRA_*` / `VIBE_ASTOCK_*`（旧版 vibe-astock 的命名），
但**新前缀 `LDST_*` 永远优先**——旧变量只在新变量缺失时兜底，绝不覆盖新值。
"""

from __future__ import annotations

import os
from pathlib import Path

from ladderstock.core import runtime

CONFIG_FILES: tuple[str, ...] = (
    "sources.yaml",
    "sync.yaml",
    "ai.yaml",
    "state.yaml",
    "schedule.yaml",
    # R5 产业研究的行业清单与窗口参数（QUOT-143，口径见 SPEC-INDUSTRY-METRICS.md）
    "industry.yaml",
    # 回测成本与阈值默认值（QUOT-500，口径见 SPEC-BACKTEST.md）
    "backtest.yaml",
    # 历史日线回补的票池 / 窗口 / 阶段标注（QUOT-501，见 docs/CLI.md §N）
    "backfill.yaml",
)

#: 随包发布的配置模板目录（相对源码树工程根）；运行时首次启动据此 seed
BUNDLE_CONFIG_DIRNAME = "config"

#: 旧版环境变量前缀，按顺序兜底（旧前缀双读，新前缀优先）
LEGACY_ENV_PREFIXES: tuple[str, ...] = ("VRA_", "VIBE_ASTOCK_")

#: platformdirs 应用标识（DEC-071）
APP_NAME = "LadderStock"
APP_AUTHOR = "QuotaFuse"


def _env_with_legacy(name: str) -> str | None:
    """读环境变量：`name`（`LDST_*`）优先，缺失时才回落到旧前缀。

    旧前缀只做兼容读取，不参与写入，也不会反过来覆盖 `LDST_*`——
    否则老 `.env` 会悄悄把用户已经显式配置的新路径顶掉。
    """
    value = os.environ.get(name)
    if value:
        return value
    suffix = name.removeprefix("LDST_")
    for prefix in LEGACY_ENV_PREFIXES:
        legacy = os.environ.get(f"{prefix}{suffix}")
        if legacy:
            return legacy
    return None


def user_data_root() -> Path:
    """跨平台用户数据根目录（安装态落点，DEC-071）。

    `platformdirs` 在 Windows 返回 `%LOCALAPPDATA%\\QuotaFuse\\LadderStock`、macOS 返回
    `~/Library/Application Support/LadderStock`、Linux 返回
    `$XDG_DATA_HOME/LadderStock`；迁移或打包时改这里即可全平台跟随，
    代码里不再出现任何盘符或 `D:\\...` 常量。
    """
    from platformdirs import user_data_dir

    return Path(user_data_dir(APP_NAME, APP_AUTHOR))


def config_dir() -> Path:
    env = _env_with_legacy("LDST_CONFIG_DIR")
    if env:
        return Path(env)
    # 冻结态（双击 exe）的 cwd 是 exe 所在目录，绝不能把安装目录里的
    # 任何 `config/` 当作用户配置；安装态只认 platformdirs（DEC-071 / QUOT-525）。
    if not runtime.is_frozen():
        local = Path.cwd() / "config"
        if local.is_dir():
            return local
    return user_data_root() / "config"


def data_dir() -> Path:
    env = _env_with_legacy("LDST_DATA_DIR")
    if env:
        return Path(env)
    if not runtime.is_frozen():
        local = Path.cwd() / "data"
        if local.is_dir():
            return local
    return user_data_root() / "data"


def bundle_config_dir() -> Path:
    """随包配置模板目录。

    源码态优先包内 `assets/config/`（与 wheel 安装态同一路径），没有包内副本时
    退回工程根 `config/`；冻结态由 `runtime.bundle_root()` 定位解包目录。
    """
    if runtime.is_frozen():
        return runtime.bundle_config_dir()
    packaged = Path(__file__).resolve().parents[1] / "assets" / BUNDLE_CONFIG_DIRNAME
    if packaged.is_dir():
        return packaged
    return project_root() / BUNDLE_CONFIG_DIRNAME


def repo_root() -> Path:
    """源码根目录（`src/`），边界扫描的范围。"""
    return runtime.source_root()


def project_root() -> Path:
    """工程根目录（含 `config/`、`tests/`、`docs/`）；冻结态是安装根。"""
    return runtime.project_root()
