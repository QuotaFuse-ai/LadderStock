"""QUOT-500 配置校验：非法配置必须**当场报错**，不许静默用默认值顶上。

三条硬规则（``config.py`` 模块说明）：

1. 默认值写在 ``config/backtest.yaml``；读不到时兜底并标 ``fallback``，读得到但写坏了 →
   报错，**不**回落默认值；
2. CLI 可覆盖，覆盖后运行卡记录的「实际生效值 / 来源」必须跟着变；
3. 负数、滑点为 0、切分比例越界、档位顺序不对 → ``BacktestConfigError``。

每条非法用例都同时断言：抛的是 ``BacktestConfigError``（不是 TypeError / ValueError 别的子类），
并且**没有**悄悄返回一个「看起来正常」的配置对象。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ladderstock.backtest.config import (
    FIELD_CLI_FLAG,
    FIELD_YAML_PATH,
    BacktestConfig,
    BacktestConfigError,
    default_config_path,
    load_config,
)

GOOD_YAML = """
costs:
  commission: 0.00025
  slippage: 0.001
  stamp_duty: 0.001
  tiers: [0.0002, 0.0005, 0.001]
gate:
  min_trades: 20
  in_sample_ratio: 0.7
"""

#: QUOT-535 真实券商写法（万分之口径）
NEW_YAML = """
costs:
  commission_rate_wan: 0.9
  commission_floor_yuan: 0.01
  transfer_fee_wan: 0.1
  stamp_duty_wan: 5
  slippage: 0.001
  tiers: [0.0002, 0.0005, 0.001]
  presets:
    - key: wan09_free5_001
      label: 万0.9 免五 0.01 元起收
      commission_rate_wan: 0.9
      commission_floor_yuan: 0.01
    - key: wan065_min5
      label: 万0.65 不免五 5 元起收
      commission_rate_wan: 0.65
      commission_floor_yuan: 5
gate:
  min_trades: 20
  in_sample_ratio: 0.7
"""


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "backtest.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def _fallback() -> BacktestConfig:
    return load_config(None)


# --------------------------------------------------------------------------- #
# 1. 文件名 / 路径 / 默认值口径
# --------------------------------------------------------------------------- #

def test_default_path_is_config_backtest_yaml(tmp_path: Path) -> None:
    assert default_config_path(tmp_path) == tmp_path / "config" / "backtest.yaml"


def test_field_maps_are_consistent() -> None:
    assert set(FIELD_YAML_PATH) == set(FIELD_CLI_FLAG)
    # 每个 CLI 参数都以 -- 开头，且把下划线换成连字符（--stamp-duty）
    for key, flag in FIELD_CLI_FLAG.items():
        assert flag.startswith("--")
        assert flag == "--" + key.replace("_", "-")


def test_missing_file_falls_back_and_is_marked() -> None:
    cfg = load_config(Path("does-not-exist.yaml"))
    assert cfg.yaml_path is None
    assert set(cfg.provenance().values()) == {"fallback"}
    assert cfg.commission > 0 and cfg.slippage > 0 and cfg.stamp_duty > 0


def test_good_yaml_is_loaded_and_marked(tmp_path: Path) -> None:
    path = _write(tmp_path, GOOD_YAML)
    cfg = load_config(path)
    assert cfg.yaml_path == path
    assert set(cfg.provenance().values()) == {"yaml"}
    assert cfg.cost_tiers == (pytest.approx(0.0002), pytest.approx(0.0005), pytest.approx(0.001))


def test_legacy_yaml_keeps_old_cost_caliber(tmp_path: Path) -> None:
    """旧式小数口径仍可读：最低佣金 / 过户费按 0（与 QUOT-500 撮合口径一致）。"""
    cfg = load_config(_write(tmp_path, GOOD_YAML))
    assert cfg.commission == pytest.approx(0.00025)
    assert cfg.stamp_duty == pytest.approx(0.001)
    assert cfg.commission_floor_yuan == pytest.approx(0.0)
    assert cfg.transfer_fee == pytest.approx(0.0)
    assert cfg.presets == ()


def test_new_style_yaml_converts_wan_units(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, NEW_YAML))
    assert cfg.commission_rate_wan == pytest.approx(0.9)
    assert cfg.commission == pytest.approx(0.00009)
    assert cfg.commission_floor_yuan == pytest.approx(0.01)
    assert cfg.transfer_fee_wan == pytest.approx(0.1)
    assert cfg.transfer_fee == pytest.approx(0.00001)
    assert cfg.stamp_duty_wan == pytest.approx(5.0)
    assert cfg.stamp_duty == pytest.approx(0.0005)
    assert [preset.key for preset in cfg.presets] == ["wan09_free5_001", "wan065_min5"]
    assert dict(cfg.provenance())["transfer_fee_wan"] == "yaml"


def test_new_style_missing_cost_field_raises(tmp_path: Path) -> None:
    broken = NEW_YAML.replace("  transfer_fee_wan: 0.1\n", "")
    with pytest.raises(BacktestConfigError, match="transfer_fee_wan"):
        load_config(_write(tmp_path, broken))


def test_new_style_missing_preset_field_raises(tmp_path: Path) -> None:
    broken = NEW_YAML.replace("      commission_floor_yuan: 5\n", "")
    with pytest.raises(BacktestConfigError, match="commission_floor_yuan"):
        load_config(_write(tmp_path, broken))


def test_override_new_style_keys_and_legacy_alias_converge() -> None:
    by_wan = _fallback().with_overrides(commission_rate_wan=0.5, stamp_duty_wan=2)
    by_decimal = _fallback().with_overrides(commission=0.00005, stamp_duty=0.0002)
    assert by_wan.commission == pytest.approx(by_decimal.commission)
    assert by_wan.stamp_duty == pytest.approx(by_decimal.stamp_duty)
    floor = _fallback().with_overrides(commission_floor_yuan=5.0, transfer_fee_wan=1.0)
    assert floor.commission_floor_yuan == pytest.approx(5.0)
    assert floor.transfer_fee == pytest.approx(0.0001)
    # 来源按同一内部字段记账：旧键与新键都能读到 cli
    assert floor.provenance()["commission_floor_yuan"] == "cli"
    assert floor.provenance()["transfer_fee_wan"] == "cli"


def test_repo_config_file_is_valid() -> None:
    """仓库里真正要提交的那份 ``config/backtest.yaml`` 必须是可读的合法配置。"""
    repo_root = Path(__file__).resolve().parents[3]
    path = default_config_path(repo_root)
    if not path.is_file():
        pytest.skip(f"仓库还没有 {path}（骨架阶段）")
    cfg = load_config(path)
    assert cfg.provenance()["commission"] == "yaml"


# --------------------------------------------------------------------------- #
# 2. 写坏了就报错，不回落默认值
# --------------------------------------------------------------------------- #

def test_missing_field_raises_instead_of_default(tmp_path: Path) -> None:
    broken = GOOD_YAML.replace("  slippage: 0.001\n", "")
    with pytest.raises(BacktestConfigError, match="slippage"):
        load_config(_write(tmp_path, broken))


def test_unparseable_yaml_raises(tmp_path: Path) -> None:
    with pytest.raises(BacktestConfigError):
        load_config(_write(tmp_path, "costs: [unclosed\n"))


def test_non_mapping_toplevel_raises(tmp_path: Path) -> None:
    with pytest.raises(BacktestConfigError):
        load_config(_write(tmp_path, "- just\n- a\n- list\n"))


def test_tiers_as_csv_string_is_accepted(tmp_path: Path) -> None:
    text = GOOD_YAML.replace("[0.0002, 0.0005, 0.001]", '"0.0002,0.0005,0.001"')
    cfg = load_config(_write(tmp_path, text))
    assert len(cfg.cost_tiers) == 3


def test_tiers_non_numeric_raises(tmp_path: Path) -> None:
    text = GOOD_YAML.replace("[0.0002, 0.0005, 0.001]", "[a, b, c]")
    with pytest.raises(BacktestConfigError):
        load_config(_write(tmp_path, text))


# --------------------------------------------------------------------------- #
# 3. 数值规则
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "field",
    [
        "commission",
        "slippage",
        "stamp_duty",
        "in_sample_ratio",
        "commission_rate_wan",
        "commission_floor_yuan",
        "transfer_fee_wan",
        "stamp_duty_wan",
    ],
)
def test_negative_numbers_raise(field: str) -> None:
    with pytest.raises(BacktestConfigError):
        _fallback().with_overrides(**{field: -0.01})


@pytest.mark.parametrize(
    "field", ["commission", "slippage", "stamp_duty", "commission_rate_wan", "stamp_duty_wan"]
)
def test_zero_cost_fields_raise(field: str) -> None:
    with pytest.raises(BacktestConfigError):
        _fallback().with_overrides(**{field: 0.0})


@pytest.mark.parametrize("ratio", [0.0, 1.0, -0.5, 1.5])
def test_in_sample_ratio_out_of_range_raises(ratio: float) -> None:
    with pytest.raises(BacktestConfigError):
        _fallback().with_overrides(in_sample_ratio=ratio)


def test_in_sample_ratio_bool_and_nan_raise() -> None:
    with pytest.raises(BacktestConfigError):
        _fallback().with_overrides(in_sample_ratio=True)
    with pytest.raises(BacktestConfigError):
        _fallback().with_overrides(commission=float("nan"))
    with pytest.raises(BacktestConfigError):
        _fallback().with_overrides(slippage=float("inf"))


@pytest.mark.parametrize("value", [0, -3])
def test_min_trades_must_be_positive_int(value: int) -> None:
    with pytest.raises(BacktestConfigError):
        _fallback().with_overrides(min_trades=value)


def test_min_trades_rejects_float_and_bool() -> None:
    with pytest.raises(BacktestConfigError):
        _fallback().with_overrides(min_trades=20.5)
    with pytest.raises(BacktestConfigError):
        _fallback().with_overrides(min_trades=True)


def test_cost_tiers_must_be_ascending() -> None:
    with pytest.raises(BacktestConfigError, match="排序"):
        _fallback().with_overrides(cost_tiers=(0.001, 0.0005, 0.0002))


def test_cost_tiers_need_at_least_two() -> None:
    with pytest.raises(BacktestConfigError):
        _fallback().with_overrides(cost_tiers=(0.0005,))


def test_cost_tiers_reject_negative() -> None:
    with pytest.raises(BacktestConfigError):
        _fallback().with_overrides(cost_tiers=(-0.0001, 0.0005))


def test_unknown_override_key_raises() -> None:
    with pytest.raises(BacktestConfigError, match="未知覆盖字段"):
        _fallback().with_overrides(leverage=2.0)


# --------------------------------------------------------------------------- #
# 4. CLI 覆盖：实际生效值 + 来源
# --------------------------------------------------------------------------- #

def test_cli_override_changes_value_and_source() -> None:
    cfg = _fallback().with_overrides(commission=0.0005)
    assert cfg.commission == pytest.approx(0.0005)
    assert cfg.provenance()["commission"] == "cli"
    # 没覆盖的字段来源不变
    assert cfg.provenance()["slippage"] == "fallback"


def test_cli_override_none_is_ignored() -> None:
    cfg = _fallback()
    same = cfg.with_overrides(commission=None, slippage=None)
    assert same.commission == cfg.commission
    assert same.provenance() == cfg.provenance()


def test_override_over_yaml_is_marked_cli(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, GOOD_YAML)).with_overrides(commission=0.0009)
    assert cfg.commission == pytest.approx(0.0009)
    assert cfg.provenance()["commission"] == "cli"
    assert cfg.provenance()["slippage"] == "yaml"


def test_as_dict_reports_effective_values(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, GOOD_YAML)).with_overrides(min_trades=7)
    values = cfg.as_dict()
    assert values["min_trades"] == 7
    assert values["cost_tiers"] == [
        pytest.approx(0.0002),
        pytest.approx(0.0005),
        pytest.approx(0.001),
    ]
    assert values["commission"] == pytest.approx(0.00025)
