"""回测成本与阈值：默认值 + ``config/backtest.yaml`` + CLI 覆盖（QUOT-500 §5.3 / QUOT-535）。

三条硬规则（缺一不可）：

1. **默认值写在配置文件里**。这里的 ``_YAML_DEFAULTS`` 只是**无法读到配置文件时的兜底**，
   且运行卡记录时必须区分「来自 yaml」「来自 CLI 覆盖」「来自兜底」——否则无法判断一次回测是不是
   用了尚未提交进仓库的本地参数。检查逻辑见 ``BacktestConfig.sources``。
2. **CLI 可覆盖**。构造 ``BacktestConfig`` 时传入的 ``overrides`` 优先；每个 key ↔ yaml 路径 ↔ CLI
   参数名固定映射（``FIELD_YAML_PATH`` / ``FIELD_CLI_FLAG``），运行卡按此打印「实际生效值」。
3. **非法配置报错**。任何负数、滑点为 0、切分比例越界、成本档位顺序不对都立即抛
   ``BacktestConfigError``，**不静默回落到默认值**。

成本口径（QUOT-535 起对接真实券商，单位全部写进字段名，避免「千 1 写成万 1」的口径事故）：

| 字段 | 单位 | 默认值 | 含义 |
|---|---|---|---|
| ``commission_rate_wan`` | 万分之 | 0.9 | 佣金率（万 X），按成交额、买卖双边 |
| ``commission_floor_yuan`` | 元 | 0.01 | 单笔最低佣金（5=不免五；0.01/1/0=免五或自定义起收） |
| ``transfer_fee_wan`` | 万分之 | 0.1 | 过户费万 0.1，**买卖双边**（现行官方口径） |
| ``stamp_duty_wan`` | 万分之 | 5 | 印花税万 5，**仅卖出**（2023-08-28 起千 1 减半） |
| ``slippage`` | 小数 | 0.001 | 滑点率（千 1），按成交价；买 +滑、卖 −滑 |
| ``min_trades`` | 笔 | 20 | 校验门最小完整交易次数 |
| ``in_sample_ratio`` | 小数 | 0.7 | 样本内占全区间比例（按时间顺序前 70%，不许随机） |
| ``cost_tiers`` | 小数 | [0.0002, 0.0005, 0.001] | 成本敏感性档位：每档只替换佣金率 |

单笔佣金 = ``max(成交额 × commission_rate, commission_floor_yuan)``；买入成本再叠加过户费，
卖出成本再叠加过户费与印花税。

**旧字段兼容**：``costs.commission`` / ``costs.stamp_duty``（小数）仍然可读——按
``commission = rate/10000`` 反推，最低佣金与过户费记为 0，与旧版本撮合口径完全一致。
新配置里只要出现任一 ``costs.commission_rate_wan`` 等新字段，就按新口径**严格要求**全部新字段，
不允许新旧混写时静默挑一边。

「成本档位」是**佣金率**扫描：在默认佣金上下取高、中、低三档看结论是否翻转。档位只替换佣金率，
最低佣金 / 过户费 / 滑点 / 印花税与主回测一致。
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

#: 真实券商口径的字段（万分之单位）；任一出现即视为「新式配置」，要求全部到齐
COST_FIELD_YAML_PATH: Final[Mapping[str, str]] = {
    "commission_rate_wan": "costs.commission_rate_wan",
    "commission_floor_yuan": "costs.commission_floor_yuan",
    "transfer_fee_wan": "costs.transfer_fee_wan",
    "stamp_duty_wan": "costs.stamp_duty_wan",
}

COST_FIELD_CLI_FLAG: Final[Mapping[str, str]] = {
    "commission_rate_wan": "--commission-rate-wan",
    "commission_floor_yuan": "--commission-floor-yuan",
    "transfer_fee_wan": "--transfer-fee-wan",
    "stamp_duty_wan": "--stamp-duty-wan",
}

#: 兜底默认值（只在读不到配置文件时使用；运行卡 ``sources`` 会显式标 ``fallback``）
_YAML_DEFAULTS: Final[Mapping[str, Any]] = {
    "commission_rate_wan": 0.9,
    "commission_floor_yuan": 0.01,
    "transfer_fee_wan": 0.1,
    "stamp_duty_wan": 5.0,
    "slippage": 0.001,
    "min_trades": 20,
    "in_sample_ratio": 0.7,
    "cost_tiers": [0.0002, 0.0005, 0.001],
}

#: 用户券商三档（均不含过户费，过户费另加）；不含「自定义」，自定义由页面用各自输入框拼
_DEFAULT_PRESETS: Final[tuple[Mapping[str, Any], ...]] = (
    {
        "key": "wan09_free5_001",
        "label": "万0.9 免五 0.01 元起收",
        "commission_rate_wan": 0.9,
        "commission_floor_yuan": 0.01,
    },
    {
        "key": "wan085_free5_1",
        "label": "万0.85 免五 1 元起收",
        "commission_rate_wan": 0.85,
        "commission_floor_yuan": 1.0,
    },
    {
        "key": "wan065_min5",
        "label": "万0.65 不免五 5 元起收",
        "commission_rate_wan": 0.65,
        "commission_floor_yuan": 5.0,
    },
)

_RATIO_FIELDS: Final[frozenset[str]] = frozenset(
    {"commission", "slippage", "stamp_duty", "in_sample_ratio"}
)

#: 覆盖键（含旧小数别名与新万分之口径）：用户从哪个入口传都落到同一组规范化字段
_OVERRIDE_ALIASES: Final[Mapping[str, str]] = {
    "commission": "commission",
    "slippage": "slippage",
    "stamp_duty": "stamp_duty",
    "min_trades": "min_trades",
    "in_sample_ratio": "in_sample_ratio",
    "cost_tiers": "cost_tiers",
    "commission_rate_wan": "commission_rate_wan",
    "commission_floor_yuan": "commission_floor_yuan",
    "transfer_fee_wan": "transfer_fee_wan",
    "stamp_duty_wan": "stamp_duty_wan",
}


class BacktestConfigError(ValueError):
    """配置非法：字段 / 路径 / 期望 / 实收；CLI 退出码 3（与 ``EXIT_CONFIG`` 同口径）。"""


@dataclass(frozen=True, slots=True)
class CostPreset:
    """一档券商成本（只描述佣金率与最低佣金；过户费由配置统一另加）。"""

    key: str
    label: str
    commission_rate_wan: float
    commission_floor_yuan: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "commission_rate_wan": self.commission_rate_wan,
            "commission_floor_yuan": self.commission_floor_yuan,
        }


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    #: 佣金率（小数，0.00009 = 万 0.9）；与 ``commission_rate_wan`` 互为换算
    commission: float
    slippage: float
    #: 卖出印花税（小数，0.0005 = 万 5）
    stamp_duty: float
    min_trades: int
    in_sample_ratio: float
    cost_tiers: tuple[float, ...]
    #: 单笔最低佣金（元）；0 = 无下限
    commission_floor_yuan: float = 0.0
    #: 过户费（小数，0.00001 = 万 0.1），买卖双边
    transfer_fee: float = 0.0
    #: 券商成本预设（页面下拉用；不参与撮合，撮合只认上面的实际生效值）
    presets: tuple[CostPreset, ...] = ()
    #: 每个字段的实际来源：``yaml`` / ``cli`` / ``fallback``
    sources: Mapping[str, str] = field(default_factory=dict)
    #: yaml 文件路径（`None` = 没读到，走了兜底）
    yaml_path: Path | None = None

    @property
    def commission_rate_wan(self) -> float:
        return self.commission * 10_000

    @property
    def transfer_fee_wan(self) -> float:
        return self.transfer_fee * 10_000

    @property
    def stamp_duty_wan(self) -> float:
        return self.stamp_duty * 10_000

    def as_dict(self) -> dict[str, Any]:
        """运行卡用：`{key: value, ...}`（值都是实际生效值）。

        旧键（``commission`` / ``stamp_duty``，小数口径）原样保留，避免破坏已合并的
        运行卡契约；新键给出万分之口径，方便人直接对照券商截图。
        """
        return {
            "commission": self.commission,
            "slippage": self.slippage,
            "stamp_duty": self.stamp_duty,
            "min_trades": self.min_trades,
            "in_sample_ratio": self.in_sample_ratio,
            "cost_tiers": list(self.cost_tiers),
            "commission_rate_wan": self.commission_rate_wan,
            "commission_floor_yuan": self.commission_floor_yuan,
            "transfer_fee_wan": self.transfer_fee_wan,
            "stamp_duty_wan": self.stamp_duty_wan,
        }

    def provenance(self) -> dict[str, str]:
        """运行卡用：每个字段的实际来源；未登记的字段视为 ``fallback``。

        旧键名（``commission`` / ``stamp_duty``）与新键名都给出，且指向同一个内部来源——
        运行卡与页面无论按哪套字段读，都能看到「这一项来自 yaml / cli / fallback」。
        """
        aliases = {
            "commission": "commission",
            "slippage": "slippage",
            "stamp_duty": "stamp_duty",
            "min_trades": "min_trades",
            "in_sample_ratio": "in_sample_ratio",
            "cost_tiers": "cost_tiers",
            "commission_rate_wan": "commission",
            "commission_floor_yuan": "commission_floor_yuan",
            "transfer_fee_wan": "transfer_fee",
            "stamp_duty_wan": "stamp_duty",
        }
        return {key: self.sources.get(internal, "fallback") for key, internal in aliases.items()}

    def with_overrides(self, **kwargs: Any) -> BacktestConfig:
        """CLI / HTTP 覆盖：`None` 的键忽略；非 `None` 的键进 ``sources`` 标 ``cli``。

        同时接受旧小数口径（``commission`` / ``stamp_duty``）与券商万分之口径
        （``commission_rate_wan`` / ``stamp_duty_wan`` / ``transfer_fee_wan``）。
        新键在写入前换算成内部小数口径，保证 ``sources`` 只有一个真源。
        """
        changed: dict[str, Any] = {}
        sources = dict(self.sources)
        for key, value in kwargs.items():
            if key not in _OVERRIDE_ALIASES:
                allowed = sorted(_OVERRIDE_ALIASES)
                raise BacktestConfigError(f"未知覆盖字段 {key!r}（可用：{allowed}）")
            if value is None:
                continue
            if key == "commission_rate_wan":
                changed["commission"] = float(value) / 10_000
                sources["commission"] = "cli"
            elif key == "stamp_duty_wan":
                changed["stamp_duty"] = float(value) / 10_000
                sources["stamp_duty"] = "cli"
            elif key == "transfer_fee_wan":
                changed["transfer_fee"] = float(value) / 10_000
                sources["transfer_fee"] = "cli"
            elif key == "commission_floor_yuan":
                changed["commission_floor_yuan"] = value
                sources["commission_floor_yuan"] = "cli"
            else:
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
    if isinstance(cfg.commission_floor_yuan, bool) or not isinstance(
        cfg.commission_floor_yuan, int | float
    ):
        raise BacktestConfigError(
            f"commission_floor_yuan 必须是数值，收到 {cfg.commission_floor_yuan!r}"
        )
    if not math.isfinite(float(cfg.commission_floor_yuan)) or cfg.commission_floor_yuan < 0:
        raise BacktestConfigError(
            f"commission_floor_yuan 必须是 >= 0 的有限数值，收到 {cfg.commission_floor_yuan!r}"
        )
    if isinstance(cfg.transfer_fee, bool) or not isinstance(cfg.transfer_fee, int | float):
        raise BacktestConfigError(f"transfer_fee 必须是数值，收到 {cfg.transfer_fee!r}")
    if not math.isfinite(float(cfg.transfer_fee)) or cfg.transfer_fee < 0:
        raise BacktestConfigError(
            f"transfer_fee 必须是 >= 0 的有限数值，收到 {cfg.transfer_fee!r}"
        )
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
    for index, preset in enumerate(cfg.presets):
        if not isinstance(preset, CostPreset):
            raise BacktestConfigError(f"presets[{index}] 必须是 CostPreset，收到 {preset!r}")
        if not preset.key.strip() or not preset.label.strip():
            raise BacktestConfigError(f"presets[{index}] 的 key/label 不能为空")
        for name, value in (
            ("commission_rate_wan", preset.commission_rate_wan),
            ("commission_floor_yuan", preset.commission_floor_yuan),
        ):
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise BacktestConfigError(f"presets[{index}].{name} 必须是数值，收到 {value!r}")
            if not math.isfinite(float(value)) or value < 0:
                raise BacktestConfigError(
                    f"presets[{index}].{name} 必须是 >= 0 的有限数值，收到 {value!r}"
                )
        if preset.commission_rate_wan == 0:
            raise BacktestConfigError(f"presets[{index}].commission_rate_wan 不能为 0")


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


def _require(raw: Mapping[str, Any], path: str, *, source: Path) -> Any:
    present, value = _dig(raw, path)
    if not present:
        raise BacktestConfigError(
            f"{source}:缺字段 {path!r}；不静默回落默认值，请补上"
        )
    return value


def _coerce_presets(raw: Any, *, source: Path) -> tuple[CostPreset, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, Sequence) or isinstance(raw, str | bytes | bytearray):
        raise BacktestConfigError(f"{source}:costs.presets 必须是列表，收到 {raw!r}")
    presets: list[CostPreset] = []
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise BacktestConfigError(f"{source}:costs.presets[{index}] 必须是映射")
        missing = [
            key
            for key in ("key", "label", "commission_rate_wan", "commission_floor_yuan")
            if key not in item
        ]
        if missing:
            raise BacktestConfigError(
                f"{source}:costs.presets[{index}] 缺字段 {missing}"
            )
        presets.append(
            CostPreset(
                key=str(item["key"]),
                label=str(item["label"]),
                commission_rate_wan=float(item["commission_rate_wan"]),
                commission_floor_yuan=float(item["commission_floor_yuan"]),
            )
        )
    return tuple(presets)


def _fallback_config() -> BacktestConfig:
    defaults = _YAML_DEFAULTS
    cfg = BacktestConfig(
        commission=float(defaults["commission_rate_wan"]) / 10_000,
        slippage=float(defaults["slippage"]),
        stamp_duty=float(defaults["stamp_duty_wan"]) / 10_000,
        min_trades=int(defaults["min_trades"]),
        in_sample_ratio=float(defaults["in_sample_ratio"]),
        cost_tiers=_coerce_tiers(defaults["cost_tiers"]),
        commission_floor_yuan=float(defaults["commission_floor_yuan"]),
        transfer_fee=float(defaults["transfer_fee_wan"]) / 10_000,
        presets=tuple(
            CostPreset(
                key=str(item["key"]),
                label=str(item["label"]),
                commission_rate_wan=float(item["commission_rate_wan"]),
                commission_floor_yuan=float(item["commission_floor_yuan"]),
            )
            for item in _DEFAULT_PRESETS
        ),
        sources={
            key: "fallback"
            for key in (
                "commission",
                "slippage",
                "stamp_duty",
                "min_trades",
                "in_sample_ratio",
                "cost_tiers",
                "commission_floor_yuan",
                "transfer_fee",
            )
        },
        yaml_path=None,
    )
    _validate(cfg)
    return cfg


def load_config(path: Path | None = None) -> BacktestConfig:
    """读 ``config/backtest.yaml``；文件不存在时**兜底**并标 ``fallback``。

    已存在但字段缺失/类型不对/文件本身非法 → ``BacktestConfigError``。这是刻意的：
    配置文件是人写的，写坏了就得当场发现，而不是「读不到某个键就静默用默认值」。

    两种写法：

    - **新式**（任一 ``costs.commission_rate_wan`` 等出现）：四个万分之字段必须齐全；
    - **旧式**（只有 ``costs.commission`` / ``costs.stamp_duty``）：换算成小数口径，
      最低佣金与过户费按 0 处理（与旧撮合口径一致），``commission`` 与 ``stamp_duty``
      这两个键的 ``provenance`` 仍如实标 ``yaml``。
    """
    if path is None or not Path(path).is_file():
        return _fallback_config()
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise BacktestConfigError(f"{path}:yaml 解析失败：{exc}") from exc
    if not isinstance(raw, Mapping):
        raise BacktestConfigError(f"{path}:顶层必须是映射，收到 {type(raw).__name__}")

    new_style = any(_dig(raw, dotted)[0] for dotted in COST_FIELD_YAML_PATH.values())
    sources: dict[str, str] = {}
    if new_style:
        values = {
            key: _require(raw, dotted, source=path)
            for key, dotted in COST_FIELD_YAML_PATH.items()
        }
        commission = float(values["commission_rate_wan"]) / 10_000
        stamp_duty = float(values["stamp_duty_wan"]) / 10_000
        transfer_fee = float(values["transfer_fee_wan"]) / 10_000
        commission_floor = float(values["commission_floor_yuan"])
    else:
        legacy = {
            key: _require(raw, dotted, source=path)
            for key, dotted in FIELD_YAML_PATH.items()
        }
        commission = float(legacy["commission"])
        stamp_duty = float(legacy["stamp_duty"])
        transfer_fee = 0.0
        commission_floor = 0.0

    # slippage / tiers / gate 两种写法都必填（缺一即报错，不回落）
    slippage = float(_require(raw, "costs.slippage", source=path))
    cost_tiers = _coerce_tiers(_require(raw, "costs.tiers", source=path))
    min_trades = int(_require(raw, "gate.min_trades", source=path))
    in_sample_ratio = float(_require(raw, "gate.in_sample_ratio", source=path))
    presets = _coerce_presets(_dig(raw, "costs.presets")[1], source=path)

    for key in (
        "commission",
        "slippage",
        "stamp_duty",
        "min_trades",
        "in_sample_ratio",
        "cost_tiers",
        "commission_floor_yuan",
        "transfer_fee",
    ):
        sources[key] = "yaml"

    cfg = BacktestConfig(
        commission=commission,
        slippage=slippage,
        stamp_duty=stamp_duty,
        min_trades=min_trades,
        in_sample_ratio=in_sample_ratio,
        cost_tiers=cost_tiers,
        commission_floor_yuan=commission_floor,
        transfer_fee=transfer_fee,
        presets=presets,
        sources=sources,
        yaml_path=path,
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
    "COST_FIELD_CLI_FLAG",
    "COST_FIELD_YAML_PATH",
    "CostPreset",
    "FIELD_CLI_FLAG",
    "FIELD_YAML_PATH",
    "default_config_path",
    "load_config",
]
