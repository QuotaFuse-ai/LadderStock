"""路径解析：配置目录、数据目录、仓库根。

优先级（DEC-001 / 文档 §6）：
- `LDST_CONFIG_DIR` 显式指定
- 当前工作目录下的 `config/`（开发态）
- `~/.ladderstock/config`（安装态）

**旧前缀兼容**：过渡期继续读取 `VRA_*` / `VIBE_ASTOCK_*`（旧版 vibe-astock 的命名），
但**新前缀 `LDST_*` 永远优先**——旧变量只在新变量缺失时兜底，绝不覆盖新值。
"""

from __future__ import annotations

import os
from pathlib import Path

CONFIG_FILES: tuple[str, ...] = (
    "sources.yaml",
    "sync.yaml",
    "ai.yaml",
    "state.yaml",
    "schedule.yaml",
    # R5 产业研究的行业清单与窗口参数（QUOT-143，口径见 SPEC-INDUSTRY-METRICS.md）
    "industry.yaml",
)

#: 旧版环境变量前缀，按顺序兜底（旧前缀双读，新前缀优先）
LEGACY_ENV_PREFIXES: tuple[str, ...] = ("VRA_", "VIBE_ASTOCK_")


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


def config_dir() -> Path:
    env = _env_with_legacy("LDST_CONFIG_DIR")
    if env:
        return Path(env)
    local = Path.cwd() / "config"
    if local.is_dir():
        return local
    return Path.home() / ".ladderstock" / "config"


def data_dir() -> Path:
    env = _env_with_legacy("LDST_DATA_DIR")
    if env:
        return Path(env)
    local = Path.cwd() / "data"
    if local.is_dir():
        return local
    return Path.home() / ".ladderstock" / "data"


def repo_root() -> Path:
    """源码根目录（`src/`），边界扫描的范围。"""
    return Path(__file__).resolve().parents[2]


def project_root() -> Path:
    """工程根目录（含 `config/`、`tests/`、`docs/`）。"""
    return Path(__file__).resolve().parents[3]
