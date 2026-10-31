"""`config/industry.yaml` 的加载与校验（R5，QUOT-143）。

清单是**数据**不是口径，但读错时必须按 **3（配置错误）** 停下：
行业配错时给出的快照比「一片 null」更危险——看起来像数据缺了，其实是查错了行业。
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from ladderstock.errors import EXIT_CONFIG, LadderStockError
from ladderstock.industry.catalog import DEFAULT_WINDOW, L1_CODE_PATTERN, load_catalog

BASE: dict[str, Any] = {
    "industry": {
        "version": 1,
        "membership_caliber": "sw_l1",
        "industries": [
            {"name": "食品饮料", "l1_code": "340000", "anchors": ["010201"]},
            {"name": "电子", "l1_code": "270000"},
        ],
    }
}


def write(tmp_path: Path, doc: Any) -> Path:
    root = tmp_path / "config"
    root.mkdir(parents=True, exist_ok=True)
    (root / "industry.yaml").write_text(
        yaml.safe_dump(doc, allow_unicode=True, sort_keys=False)
        if isinstance(doc, dict)
        else str(doc),
        encoding="utf-8",
    )
    return root


def problem(doc: Any, tmp_path: Path) -> str:
    with pytest.raises(LadderStockError) as excinfo:
        load_catalog(write(tmp_path, doc))
    assert excinfo.value.code == EXIT_CONFIG
    return excinfo.value.message


def test_repo_catalog_loads_and_binds_shenwan_l1(repo: Path) -> None:
    """仓库里那份是真清单：申万一级 31 个，代码格式合法，一个代码只属一个行业。"""
    catalog = load_catalog(repo / "config")

    assert catalog.membership_caliber == "sw_l1"
    assert len(catalog.entries) == 31
    assert len({entry.name for entry in catalog.entries}) == 31
    assert all(
        entry.l1_code is None or L1_CODE_PATTERN.match(entry.l1_code) for entry in catalog.entries
    )
    # 实测只有 4 个行业与板块同名（农林牧渔/有色金属/交通运输/房地产），
    # 其余行业的板块字段必须留 null，所以绑定数不能悄悄涨
    assert sum(1 for entry in catalog.entries if entry.board_name) == 4
    assert [entry.name for entry in catalog.entries if not entry.bound] == ["综合"]
    assert catalog.window == DEFAULT_WINDOW
    assert catalog.provenance.get("bound_via")


def test_window_and_helpers(tmp_path: Path) -> None:
    doc = {
        "industry": {
            "industries": [
                {"name": "食品饮料", "l1_code": "340000"},
                {"name": "钢铁", "l1_code": "230000", "enabled": False},
            ],
            "window": {"fund_days": 10, "momentum_windows": [3, 6]},
        }
    }
    catalog = load_catalog(write(tmp_path, doc))

    # 只写一个键不代表丢掉别的键：未列出的窗口仍取默认值
    assert catalog.window["fund_days"] == 10
    assert catalog.window["momentum_windows"] == [3, 6]
    assert catalog.window["valuation_lookback_days"] == DEFAULT_WINDOW["valuation_lookback_days"]
    assert catalog.window_days("turnover_lookback_days") == 60
    # `enabled: false` 是裁剪子集的唯一入口：清单里留着，对外不出现
    assert catalog.names() == ["食品饮料"]
    assert catalog.names(enabled_only=False) == ["食品饮料", "钢铁"]
    assert catalog.find("钢铁") is not None
    assert catalog.find(" 食品饮料 ") is not None
    assert catalog.find("白酒") is None
    assert catalog.find("") is None


@pytest.mark.parametrize(
    ("mutate", "needle"),
    [
        (lambda d: d["industry"].pop("industries"), "非空列表"),
        (lambda d: d["industry"].__setitem__("industries", []), "非空列表"),
        (lambda d: d["industry"].__setitem__("industries", ["食品饮料"]), "不是对象"),
        (
            lambda d: d["industry"]["industries"].append({"name": "食品饮料", "l1_code": "270000"}),
            "行业名称重复",
        ),
        (
            lambda d: d["industry"]["industries"][1].__setitem__("l1_code", "340000"),
            "l1_code 重复绑定",
        ),
        (
            lambda d: d["industry"]["industries"][0].__setitem__("l1_code", "34000"),
            "6 位申万一级代码",
        ),
        (lambda d: d["industry"]["industries"][0].pop("name"), "缺 name"),
        (lambda d: d["industry"].__setitem__("window", {"lookback": 5}), "未知的 window 键"),
        (lambda d: d["industry"].__setitem__("window", {"fund_days": 0}), "必须 ≥"),
        (lambda d: d["industry"].__setitem__("window", {"momentum_windows": []}), "非空列表"),
        (lambda d: d["industry"].__setitem__("window", {"momentum_windows": [0]}), "必须 ≥ 1"),
    ],
)
def test_structural_problems_are_config_errors(mutate: Any, needle: str, tmp_path: Path) -> None:
    doc = copy.deepcopy(BASE)
    mutate(doc)
    message = problem(doc, tmp_path)
    assert message.startswith("config/industry.yaml："), message
    assert needle in message, message


def test_missing_file_and_bad_yaml_are_config_errors(tmp_path: Path) -> None:
    with pytest.raises(LadderStockError) as excinfo:
        load_catalog(tmp_path / "nope")
    assert excinfo.value.code == EXIT_CONFIG
    assert "文件不存在" in excinfo.value.message

    root = tmp_path / "config"
    root.mkdir()
    (root / "industry.yaml").write_text("industry: [未闭合", encoding="utf-8")
    with pytest.raises(LadderStockError) as excinfo:
        load_catalog(root)
    assert "YAML 解析失败" in excinfo.value.message

    (root / "industry.yaml").write_text("other: 1\n", encoding="utf-8")
    with pytest.raises(LadderStockError) as excinfo:
        load_catalog(root)
    assert "缺少顶层 `industry` 段" in excinfo.value.message
