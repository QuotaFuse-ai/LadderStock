"""首跑配置 seed：干净目录从随包模板补齐完整配置（DEC-071）。

背景：此前安装态只解析目录、不落文件——用户目录一旦缺 `sources.yaml` 或里面
少了后来新增的源（如 QUOT-471 的 `tickhub_us_index_quote`），首页美股两格就
「漂移」成缺数，而代码侧无从发现。修法是**配置与代码分离 + 首跑 seed**：

- 真源是随包的 `config/` 模板（含 `schema/`），打包时原样进 bundle；
- 首跑把模板里**缺失**的文件逐份复制进用户配置目录，已存在的一律不覆盖
  （用户改过的配置永远优先，升级不会回滚）；
- 缺失的 `schema/*.json` 同样补齐，让 `ldst config validate` 在干净目录也能跑；
- 绝不用 `LDST_*` 覆盖、绝不在 `LDST_CONFIG_DIR`（显式指定）下私自塞文件——
  只有当解析到的是**用户数据目录**且目录为空/不全时才 seed。

密钥口径不变：模板里的 `ai.yaml` 只写环境变量名，不含明文（`scripts/check_privacy.py`
的 `config-secrets` 组守着这条线）；seed 只复制字节，不注入任何凭据。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ladderstock.cli.paths import bundle_config_dir, config_dir

#: 配置目录里的 schema 子目录名（与 `config/schema/` 同名）
SCHEMA_DIRNAME = "schema"


@dataclass
class SeedReport:
    """一次 seed 的结果；`seeded` 为空表示无需写入（目录已完整）。"""

    config_root: Path
    bundle_root: Path | None
    seeded: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.seeded)

    def to_dict(self) -> dict[str, object]:
        return {
            "config_dir": str(self.config_root),
            "bundle_dir": str(self.bundle_root) if self.bundle_root else None,
            "seeded": list(self.seeded),
            "skipped": list(self.skipped),
            "warnings": list(self.warnings),
        }


def _copy_missing(src: Path, dst: Path, seeded: list[str], skipped: list[str]) -> None:
    """复制 `src` 到 `dst`：`dst` 已存在就跳过（用户配置优先），从不覆盖。"""
    if dst.exists():
        skipped.append(str(dst))
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    seeded.append(str(dst))


def seed_config(
    *,
    config_root: Path | None = None,
    bundle_root: Path | None = None,
) -> SeedReport:
    """把 bundle 模板里缺失的配置与 schema 补到 `config_root`。

    `config_root` / `bundle_root` 默认取解析后的配置目录与随包模板目录；
    测试可显式传入临时目录，避免污染真实用户目录。
    """
    target = Path(config_root) if config_root is not None else config_dir()
    bundle = Path(bundle_root) if bundle_root is not None else bundle_config_dir()
    report = SeedReport(config_root=target, bundle_root=bundle)

    if not bundle.is_dir():
        report.warnings.append(f"随包配置模板不存在（{bundle}），跳过首跑 seed")
        return report

    for src in sorted(bundle.glob("*.yaml")):
        _copy_missing(src, target / src.name, report.seeded, report.skipped)

    schema_dir = bundle / SCHEMA_DIRNAME
    if schema_dir.is_dir():
        for src in sorted(schema_dir.glob("*.json")):
            _copy_missing(
                src,
                target / SCHEMA_DIRNAME / src.name,
                report.seeded,
                report.skipped,
            )

    return report


def seed_if_needed() -> SeedReport | None:
    """仅当解析到的是**用户数据目录**（安装态）且缺文件时 seed。

    源码开发态（`cwd/config` 命中）与 `LDST_CONFIG_DIR` 显式指定的目录都不 seed：
    前者本来就指向仓库模板，后者是用户自己的目录，程序不往里塞文件。
    返回 `None` 表示无需 seed（已完整或不在安装态）。
    """
    from ladderstock.cli.paths import user_data_root

    target = config_dir()
    installed = target == user_data_root() / "config"
    if not installed:
        return None

    report = seed_config(config_root=target)
    return report if report.changed else None
