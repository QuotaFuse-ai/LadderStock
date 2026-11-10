"""回测成本与阈值：默认值 + ``config/backtest.yaml`` + CLI 覆盖（QUOT-500 §5.3）。

三条硬规则（缺一不可）：

1. **默认值写在配置文件里**。这里的 ``_YAML_DEFAULTS`` 只是**无法读到配置文件时的兜底**，
   且运行卡记录时必须区分「来自 yaml」「来自 CLI 覆盖」「来自兜底」——否则无法判断一次回测是不是
   用了尚未提交进仓库的本地参数。检查逻辑见 ``BacktestConfig.sources``。
2. **CLI 可覆盖**。构造 ``BacktestConfig`` 时传入的 ``overrides`` 优先；每个 key ↔ yaml 路径 ↔ CLI
   参数名固定映射（``FIELD_YAML_PATH`` / ``FIELD_CLI_FLAG``），运行卡按此打印「实际生效值」。
3. **非法配置报错**。任何负数、滑点为 0、切分比例越界、成本档位顺序不对都立即抛
   ``BacktestConfigError``，**不静默回落到默认值**。

成本口径（与函数签名一致，避免把「千 1」写成「万 1」的口径事故）：

| 字段 | 单位 | 默认值 | 含义 |
|---|---|---|---|
| ``commission`` | 小数 | 0.00025 | 佣金率（万 2.5），按成交额、买卖双边 |
| ``slippage`` | 小数 | 0.001 | 滑点率（千 1），按成交价；买 +滑、卖 −滑 |
| ``stamp_duty`` | 小数 | 0.001 | 卖出印花税（千 1），只对卖出成交额收 |
| ``min_trades`` | 笔 | 20 | 校验门最小完整交易次数 |
| ``in_sample_ratio`` | 小数 | 0.7 | 样本内占全区间比例（按时间顺序前 70%，不许随机） |
| ``cost_tiers`` | 列表[小数] | [0.0002, 0.0005, 0.001] | 成本敏感性档位：每档只替换佣金率 |

「成本档位」是**佣金率**扫描：在默认佣金额上下取高、中、低三档看结论是否翻转。规格里
写的是「万2 / 万5 / 千1」，也就是 0.0002 / 0.0005 / 0.001 三个佣金率，滑点与印花税不动。
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Final

import yaml

CONFIG_FILENAME: Final = "backtest.yaml"

#: `config/backtest.yaml` 在仓库里的相对路径（`load_config()` 不走环境变量，由 caller 显式传）
FIELD_YAML_PATH: Final[Mapping[str, str]] = {
    "commission": "costs.commission",
    "slippage": "costs.slippage",
    "stamp_duty": "costs.stamp_duty",
    "min_trades": "gate.min_trades",
    "in_sample_ratio": "gate.in_sample_ratio",
    "cost_tiers": "costs.tiers",
}

FIELD_CLI_FLAG: Final[Mapping[str, str]] = {
    "commission": "--commission",
    "slippage": "--slippage",
    "stamp_duty": "--stamp-duty",
    "min_trades": "--min-trades",
    "in_sample_ratio": "--in-sample-ratio",
    "cost_tiers": "--cost-tiers",
}

#: 兜底默认值（只在读不到配置文件时使用；运行卡 ``sources`` 会显式标 ``fallback``）
_YAML_DEFAULTS: Final[Mapping[str, Any]] = {
    "commission": 0.00025,
    "slippage": 0.001,
    "stamp_duty": 0.001,
    "min_trades": 20,
    "in_sample_ratio": 0.7,
    "cost_tiers": [0.0002, 0.0005, 0.001],
}

_RATIO_FIELDS: Final[frozenset[str]] = frozenset(
    {"commission", "slippage", "stamp_duty", "in_sample_ratio"}
)


class BacktestConfigError(ValueError):
    """配置非法：字段 / 路径 / 期望 / 实收；CLI 退出码 3（与 ``EXIT_CONFIG`` 同口径）。"""


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    commission: float
    slippage: float
    stamp_duty: float
    min_trades: int
    in_sample_ratio: float
    cost_tiers: tuple[float, ...]
    #: 每个字段的实际来源：``yaml`` / ``cli`` / ``fallback``
    sources: Mapping[str, str] = field(default_factory=dict)
    #: yaml 文件路径（`None` = 没读到，走了兜底）
    yaml_path: Path | None = None

    def as_dict(self) -> dict[str, Any]:
        """运行卡用：`{key: value, ...}`（值都是实际生效值）。"""
        return {
            "commission": self.commission,
            "slippage": self.slippage,
            "stamp_duty": self.stamp_duty,
            "min_trades": self.min_trades,
            "in_sample_ratio": self.in_sample_ratio,
            "cost_tiers": list(self.cost_tiers),
        }

    def provenance(self) -> dict[str, str]:
        """运行卡用：每个字段的实际来源；未登记的字段视为 ``fallback``。"""
        return {
            key: self.sources.get(key, "fallback") for key in FIELD_YAML_PATH
        }

    def with_overrides(self, **kwargs: Any) -> BacktestConfig:
        """CLI 覆盖：`None` 的键忽略；非 `None` 的键进 ``sources`` 标 ``cli``。"""
        changed: dict[str, Any] = {}
        sources = dict(self.sources)
        for key, value in kwargs.items():
            if key not in FIELD_YAML_PATH:
                allowed = sorted(FIELD_YAML_PATH)
                raise BacktestConfigError(f"未知覆盖字段 {key!r}（可用：{allowed}）")
            if value is None:
                continue
            changed[key] = value
            sources[key] = "cli"
        merged = replace(self, **changed, sources=sources)
        _validate(merged)
        return merged


def _validate(cfg: BacktestConfig) -> None:
    for name in _RATIO_FIELDS:
        value = getattr(cfg, name)
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise BacktestConfigError(f"{name} 必须是数值，收到 {value!r}")
        if not math.isfinite(float(value)):
            raise BacktestConfigError(f"{name} 必须是有限数值，收到 {value!r}")
        if float(value) < 0:
            raise BacktestConfigError(f"{name} 不能为负数，收到 {value!r}")
    if cfg.commission == 0:
        raise BacktestConfigError("commission 不能为 0（默认 0 成本违反规格 §5.3）")
    if cfg.slippage == 0:
        raise BacktestConfigError("slippage 不能为 0（默认 0 成本违反规格 §5.3）")
    if cfg.stamp_duty == 0:
        raise BacktestConfigError("stamp_duty 不能为 0（默认 0 成本违反规格 §5.3）")
    if not (0 < cfg.in_sample_ratio < 1):
        raise BacktestConfigError(
            f"in_sample_ratio 必须在 (0, 1) 开区间内，收到 {cfg.in_sample_ratio!r}"
        )
    if isinstance(cfg.min_trades, bool) or not isinstance(cfg.min_trades, int):
        raise BacktestConfigError(f"min_trades 必须是整数，收到 {cfg.min_trades!r}")
    if cfg.min_trades < 1:
        raise BacktestConfigError(f"min_trades 必须 >= 1，收到 {cfg.min_trades!r}")
    if not isinstance(cfg.cost_tiers, tuple) or len(cfg.cost_tiers) < 2:
        raise BacktestConfigError(
            f"cost_tiers 至少 2 档，收到 {cfg.cost_tiers!r}"
        )
    for index, tier in enumerate(cfg.cost_tiers):
        if isinstance(tier, bool) or not isinstance(tier, int | float):
            raise BacktestConfigError(f"cost_tiers[{index}] 必须是数值，收到 {tier!r}")
        if float(tier) < 0:
            raise BacktestConfigError(f"cost_tiers[{index}] 不能为负数，收到 {tier!r}")
    if list(cfg.cost_tiers) != sorted(cfg.cost_tiers):
        raise BacktestConfigError(
            f"cost_tiers 必须按从小到大排序，收到 {cfg.cost_tiers!r}"
        )


def _dig(node: Mapping[str, Any], path: str) -> tuple[bool, Any]:
    current: Any = node
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _coerce_tiers(raw: Any) -> tuple[float, ...]:
    if isinstance(raw, str):
        # 配置文件里允许逗号分隔（"0.0002,0.0005,0.001"），CLI 覆盖也一样
        parts = [chunk.strip() for chunk in raw.split(",") if chunk.strip()]
        try:
            return tuple(float(chunk) for chunk in parts)
        except ValueError as exc:
            raise BacktestConfigError(f"cost_tiers 无法解析为数值列表：{raw!r}") from exc
    if isinstance(raw, Sequence) and not isinstance(raw, str | bytes | bytearray):
        try:
            return tuple(float(item) for item in raw)
        except (TypeError, ValueError) as exc:
            raise BacktestConfigError(f"cost_tiers 无法解析为数值列表：{raw!r}") from exc
    raise BacktestConfigError(f"cost_tiers 必须是数值列表，收到 {raw!r}")


def load_config(path: Path | None = None) -> BacktestConfig:
    """读 ``config/backtest.yaml``；文件不存在时**兜底**并标 ``fallback``。

    已存在但字段缺失/类型不对/文件本身非法 → ``BacktestConfigError``。这是刻意的：
    配置文件是人写的，写坏了就得当场发现，而不是「读不到某个键就静默用默认值」。
    """
    if path is None or not Path(path).is_file():
        defaults = dict(_YAML_DEFAULTS)
        cfg = BacktestConfig(
            commission=float(defaults["commission"]),
            slippage=float(defaults["slippage"]),
            stamp_duty=float(defaults["stamp_duty"]),
            min_trades=int(defaults["min_trades"]),
            in_sample_ratio=float(defaults["in_sample_ratio"]),
            cost_tiers=_coerce_tiers(defaults["cost_tiers"]),
            sources={key: "fallback" for key in FIELD_YAML_PATH},
            yaml_path=None,
        )
        _validate(cfg)
        return cfg
    text = Path(path).read_text(encoding="utf-8")
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise BacktestConfigError(f"{path}:yaml 解析失败：{exc}") from exc
    if not isinstance(raw, Mapping):
        raise BacktestConfigError(f"{path}:顶层必须是映射，收到 {type(raw).__name__}")
    values: dict[str, Any] = {}
    sources: dict[str, str] = {}
    for key, dotted in FIELD_YAML_PATH.items():
        present, value = _dig(raw, dotted)
        if not present:
            raise BacktestConfigError(
                f"{path}:缺字段 {dotted!r}（对应 {' / '.join([key, FIELD_CLI_FLAG[key]])}）；"
                "不静默回落默认值，请补上"
            )
        values[key] = value
        sources[key] = "yaml"
    cost_tiers = _coerce_tiers(values["cost_tiers"])
    cfg = BacktestConfig(
        commission=float(values["commission"]),
        slippage=float(values["slippage"]),
        stamp_duty=float(values["stamp_duty"]),
        min_trades=int(values["min_trades"]),
        in_sample_ratio=float(values["in_sample_ratio"]),
        cost_tiers=cost_tiers,
        sources=sources,
        yaml_path=Path(path),
    )
    _validate(cfg)
    return cfg


def default_config_path(root: Path | None = None) -> Path:
    """仓库默认路径 ``<root>/config/backtest.yaml``（`root` 为空时取当前工作目录）。"""
    base = Path(root) if root is not None else Path.cwd()
    return base / "config" / CONFIG_FILENAME


__all__ = [
    "BacktestConfig",
    "BacktestConfigError",
    "CONFIG_FILENAME",
    "FIELD_CLI_FLAG",
    "FIELD_YAML_PATH",
    "default_config_path",
    "load_config",
]
