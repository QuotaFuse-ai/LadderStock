"""与 `journal stats` 同源对账（QUOT-292 / DEC-038）。

`journal stats` 的 `realized_pnl` 是 **FIFO**（服务逐笔纪律复盘），
持仓侧的 `realized` 是**移动加权平均**（服务「和券商对账单对上」）。
两者不可能在任意场景同数，所以这里钉的是「同源」的三层真实含义：

1. **同一条时间线**：同一张 `trades` 表、同一 `ORDER BY (trade_date, id)`、同一取数上限；
2. **全部清仓时逐分不差**：已实现 = 卖出总额 − 买入总额，与结转法无关；
3. **未平仓时差额有恒等式**，不是含糊的「大约」：

```
FIFO 已实现 − 移动加权已实现  ≡  FIFO 剩余成本基础 − 移动加权剩余成本基础
```

用例（手算；两只票的成交日分开，所以能用 `--from/--to` 把对账范围隔开）：

- **600000（全清仓，不在持仓表里）**：买 1000@10、买 500@16、卖 1500@14 →
  `21000 − 18000 = 3000`，两种方法同数；
- **000001（卖 600@14，剩 900）**：FIFO `8400 − 6000 = 2400`、
  移动加权 `8400 − 7200 = 1200`；剩余基础 FIFO `400@10 + 500@16 = 12000`、
  加权 `900@12 = 10800`，差 **1200** 正好是两法已实现之差。

合计：FIFO `5400` / 移动加权 `4200`，差额正好 `1200`。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from ladderstock.cli.main import app
from ladderstock.journal import log as log_module
from ladderstock.journal import position as position_module
from ladderstock.journal.stats import compute_stats
from ladderstock.web.radar import RadarPlane

runner = CliRunner()

CLOSED = "600000"  # 全部清仓、已从持仓里删掉
HELD = "000001"  # 部分卖出，还剩 900 股
CLOSED_DAY = "2026-10-03"
HELD_DAY = "2026-10-09"

#: 每笔成交：(code, 日期, 方向, 价格, 数量)
TRADES: tuple[tuple[str, str, str, float, float], ...] = (
    (CLOSED, "2026-10-01", "buy", 10.0, 1000.0),
    (CLOSED, "2026-10-01", "buy", 16.0, 500.0),
    (CLOSED, CLOSED_DAY, "sell", 14.0, 1500.0),
    (HELD, HELD_DAY, "buy", 10.0, 1000.0),
    (HELD, HELD_DAY, "buy", 16.0, 500.0),
    (HELD, "2026-10-10", "sell", 14.0, 600.0),
)


@pytest.fixture(autouse=True)
def _private_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo: Path) -> Path:
    """隐私库钉在 tmp：对账用例不在仓库 `data/` 里留任何记录。"""
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    return tmp_path


def _seed(root: Path) -> None:
    for code, day, side, price, qty in TRADES:
        log_module.add_trade(code, side, price, qty, trade_date=day, root=root)
    # 用户手填的成本价 12、数量 900：与流水复算无关，只服务浮动盈亏
    position_module.add_position(HELD, 12.0, 900.0, entry_date=HELD_DAY, root=root)


def _cli(argv: list[str]) -> dict[str, Any]:
    result = runner.invoke(app, argv)
    assert result.exit_code == 0, result.stderr
    return json.loads(result.stdout)


# --------------------------------------------------------------------------- #
# 1. 同一条时间线
# --------------------------------------------------------------------------- #


def test_both_surfaces_replay_the_identical_trade_rows(tmp_path: Path) -> None:
    _seed(tmp_path)

    via_positions = position_module.list_journal_trades(root=tmp_path)
    via_journal = log_module.list_trades(limit=1_000_000, root=tmp_path)
    assert [t["id"] for t in via_positions] == [t["id"] for t in via_journal]
    assert [(t["trade_date"], t["code"], t["side"]) for t in via_positions] == [
        (t["trade_date"], t["code"], t["side"]) for t in via_journal
    ]


# --------------------------------------------------------------------------- #
# 2. 全部清仓：与 FIFO 逐分不差
# --------------------------------------------------------------------------- #


def test_fully_closed_code_matches_fifo_to_the_cent(tmp_path: Path) -> None:
    _seed(tmp_path)

    # 区间只盖住 600000（成交在 10-01 ~ 10-03）→ stats 这个数就是它单只票的 FIFO 已实现
    fifo_closed = compute_stats(day_to=CLOSED_DAY, root=tmp_path)["realized_pnl"]
    realized = _cli(["position", "list", "--output", "json"])["realized"]

    assert fifo_closed == 3000.0
    assert realized["unheld_pnl"] == 3000.0  # 已清仓、不在持仓表里 → 得失仍然看得见
    assert realized["unheld_codes"] == [CLOSED]
    assert realized["total_pnl"] == 4200.0  # 3000（清仓）+ 1200（000001 部分卖出）


# --------------------------------------------------------------------------- #
# 3. 未平仓：差额由恒等式解释
# --------------------------------------------------------------------------- #


def test_open_position_delta_equals_the_remaining_basis_difference(tmp_path: Path) -> None:
    _seed(tmp_path)

    fifo_held = compute_stats(day_from=HELD_DAY, root=tmp_path)["realized_pnl"]
    body = _cli(["position", "list", "--output", "json"])
    wavg_held = body["realized"]["held_pnl"]

    assert fifo_held == 2400.0
    assert wavg_held == 1200.0

    # FIFO 剩余基础 = 400@10 + 500@16；移动加权剩余基础 = 900@12（部分卖出后单价不变）
    basis_difference = (400.0 * 10.0 + 500.0 * 16.0) - 900.0 * 12.0
    assert basis_difference == 1200.0
    assert fifo_held - wavg_held == basis_difference

    # 合计侧是同一条恒等式：stats 全区间 5400 vs 持仓侧 4200
    assert compute_stats(root=tmp_path)["realized_pnl"] - body["realized"]["total_pnl"] == 1200.0


def test_realized_pnl_never_double_counts(tmp_path: Path) -> None:
    _seed(tmp_path)
    before = [t["id"] for t in log_module.list_trades(root=tmp_path)]

    body = _cli(["position", "list", "--output", "json"])
    held_sum = sum(item["realized_pnl"] or 0.0 for item in body["items"])
    assert round(held_sum + body["realized"]["unheld_pnl"], 2) == body["realized"]["total_pnl"]

    # 只读：算一次已实现盈亏不新增成交、不动 positions（不新增第二真相）
    assert [t["id"] for t in log_module.list_trades(root=tmp_path)] == before
    assert body["items"][0]["qty"] == 900.0 and body["items"][0]["cost"] == 12.0


def test_api_and_cli_report_the_same_realized_numbers(tmp_path: Path) -> None:
    """两处出口同形同数：接口层不许再算一遍（issue 的硬约束）。"""
    _seed(tmp_path)

    api_body = RadarPlane(data_root=tmp_path).positions_view(None)
    cli_body = _cli(["position", "list", "--output", "json"])

    assert api_body["realized"] == cli_body["realized"]
    assert [i["realized_pnl"] for i in api_body["items"]] == [
        i["realized_pnl"] for i in cli_body["items"]
    ]
    assert api_body["realized"]["total_pnl"] == 4200.0
