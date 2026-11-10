"""QUOT-500 run_id 可复现：``bt_`` + sha256(参数 + 数据指纹)[:8]。

验收口径：**同参数 + 同数据 → 同 run_id，两次跑出来的运行卡除 ``generated_at`` 外逐字段一致**。
这里把整条链路（读库 → 撮合 → 计分 → 校验门 → 落盘）真跑两遍来钉死，不去单测哈希函数——
单测哈希只能证明「函数是纯的」，证明不了「跑两遍用的是同一份参数」。

另外三条边界：

1. id 形状必须是 ``bt_`` + 8 位小写十六进制（前端按这个正则展示，不能有别的字符）；
2. 改**配置**（佣金）/ 改**数据** / 改**策略参数** 都必须换 id——否则两次结论不同却同名，
   运行卡目录会互相覆盖；
3. ``generated_at`` 不参与 id：它变了 id 也不能变（否则「今天跑过的和昨天跑过的」永远对不上）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from ladderstock.backtest.config import BacktestConfig, load_config
from ladderstock.backtest.feed import Bar
from ladderstock.backtest.runner import compute_run_id, run_backtest
from tests.unit.backtest.conftest import flat_bar, write_daily_k

CASH = 1_000_000.0
RUN_ID_RE = re.compile(r"^bt_[0-9a-f]{8}$")

#: 一段「有涨有跌、够长能切样本内外」的合成序列；不依赖本机 market.duckdb
def _bars(count: int = 120) -> list[Bar]:
    out: list[Bar] = []
    price = 10.0
    from datetime import date, timedelta

    begin = date.fromisoformat("2024-01-02")
    for index in range(count):
        price = round(price * (1.0 + (0.012 if index % 7 in (0, 1, 2, 3) else -0.009)), 2)
        out.append(flat_bar((begin + timedelta(days=index)).isoformat(), price))
    return out


def _run(tmp_path: Path, *, commission: float | None = None, count: int = 120):
    data_root = tmp_path / "data"
    write_daily_k(data_root, "600000", _bars(count))
    cfg = load_config(None)  # 兜底默认（成本非 0）
    if commission is not None:
        cfg = cfg.with_overrides(commission=commission)
    return run_backtest(
        strategy="ma_cross",
        symbol="600000",
        start="2024-01-02",
        end="2024-12-31",
        config=cfg,
        initial_cash=CASH,
        data_root=data_root,
        artifacts_root_dir=tmp_path / "artifacts",
    )


def _strip_volatile(card: dict) -> dict:
    out = json.loads(json.dumps(card))
    out.pop("generated_at", None)
    return out


# --------------------------------------------------------------------------- #
# 1. 同参数 + 同数据 ⇒ 同 id，同内容
# --------------------------------------------------------------------------- #

def test_same_inputs_produce_same_run_id_and_identical_card(tmp_path: Path) -> None:
    first = _run(tmp_path)
    second = _run(tmp_path)

    assert RUN_ID_RE.match(first.run_id), first.run_id
    assert first.run_id == second.run_id
    # 落盘也落在同一个目录（同 id 覆盖写，不产生第二份）
    assert first.artifacts_dir == second.artifacts_dir
    assert first.json_path == second.json_path

    assert _strip_volatile(first.card) == _strip_volatile(second.card)


def test_rerun_overwrites_single_artifact_set(tmp_path: Path) -> None:
    first = _run(tmp_path)
    second = _run(tmp_path)
    run_dir = second.artifacts_dir
    assert run_dir is not None and run_dir.is_dir()
    names = sorted(p.name for p in run_dir.iterdir())
    assert names == [
        f"{first.run_id}.html",
        f"{first.run_id}.json",
        f"{first.run_id}.md",
    ]
    # 磁盘上读回来的卡也必须就是这一次的卡（不是陈旧文件）
    on_disk = json.loads(second.json_path.read_text(encoding="utf-8"))
    assert on_disk["run_id"] == second.run_id


# --------------------------------------------------------------------------- #
# 2. 任一输入变 ⇒ id 变
# --------------------------------------------------------------------------- #

def test_different_commission_changes_run_id_and_card(tmp_path: Path) -> None:
    base = _run(tmp_path)
    dearer = _run(tmp_path, commission=0.001)

    assert dearer.run_id != base.run_id
    assert base.card["config"]["commission"] != dearer.card["config"]["commission"]
    # 配置不同 → 成本不同 → 结论可以不同，但卡里必须如实记录实际生效值
    assert dearer.card["config_sources"]["commission"] == "cli"


def test_different_data_changes_run_id(tmp_path: Path) -> None:
    short = _run(tmp_path, count=120)
    longer = _run(tmp_path, count=150)
    assert longer.run_id != short.run_id
    assert longer.card["data"]["fingerprint"] != short.card["data"]["fingerprint"]


def test_compute_run_id_ignores_generated_at_and_orders_keys() -> None:
    params = {"b": 2, "a": [1, 2], "x": {"deep": 0.1}}
    reordered = {"x": {"deep": 0.1}, "a": [1, 2], "b": 2}
    fp = "120:2024-01-02:2024-12-31:0123456789abcdef"
    same = compute_run_id(params=params, data_fp=fp)
    assert same == compute_run_id(params=reordered, data_fp=fp)
    assert same != compute_run_id(params=params, data_fp=fp + "0")


def test_compute_run_id_requires_data_fingerprint() -> None:
    with pytest.raises(ValueError):
        compute_run_id(params={}, data_fp="")


# --------------------------------------------------------------------------- #
# 3. 配置来源如实记录（yaml / cli / fallback 三态可区分）
# --------------------------------------------------------------------------- #

def test_card_records_effective_config_and_sources(tmp_path: Path) -> None:
    cfg = BacktestConfig(
        commission=0.0003,
        slippage=0.0012,
        stamp_duty=0.001,
        min_trades=5,
        in_sample_ratio=0.6,
        cost_tiers=(0.0002, 0.0005, 0.001),
        sources={"commission": "yaml", "slippage": "cli"},
    )
    data_root = tmp_path / "data"
    write_daily_k(data_root, "600000", _bars())
    result = run_backtest(
        strategy="ma_cross",
        symbol="600000",
        start="2024-01-02",
        end="2024-12-31",
        config=cfg,
        data_root=data_root,
        artifacts_root_dir=tmp_path / "artifacts",
    )
    card = result.card
    assert card["config"]["commission"] == pytest.approx(0.0003)
    assert card["config"]["min_trades"] == 5
    assert card["config"]["in_sample_ratio"] == pytest.approx(0.6)
    assert card["config_sources"]["commission"] == "yaml"
    assert card["config_sources"]["slippage"] == "cli"
    # 未登记的字段按 fallback 展示；不许出现「来源未知」的空洞
    assert card["config_sources"]["stamp_duty"] == "fallback"
