"""回测单测共用的合成 K 线 / 策略 / 数据目录工具（QUOT-500）。

设计原则：

- **合成数据全部在内存**：A 股规则（涨停/跌停/停牌/T+1）在真实 600000 上不存在，
  只能拿人造 K 线钉死；不联网、不依赖本机 ``market.duckdb``。
- **只造「刚好够」的序列**：每根 bar 都能一眼看出该规则是否生效，不掺噪音。
- **写盘只写 tmp_path**：``write_daily_k`` 造一个小 DuckDB，供端到端测试用。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from ladderstock.backtest.feed import Bar


def flat_bar(
    day: str,
    price: float,
    *,
    open_price: float | None = None,
    high: float | None = None,
    low: float | None = None,
    volume: float = 1_000_000.0,
) -> Bar:
    """一根「价格不变」的 K 线；涨跌停/一字板都可以用 ``open/high/low`` 单独覆盖。"""
    return Bar(
        date=day,
        open=price if open_price is None else open_price,
        high=price if high is None else high,
        low=price if low is None else low,
        close=price,
        volume=volume,
    )


def series(prices: Sequence[float], *, start: str = "2025-01-02") -> list[Bar]:
    """连续交易日（自然日 +1，不含周末语义，够用）的收盘价序列；OHLC 同价、量正常。"""
    begin = date.fromisoformat(start)
    return [
        flat_bar((begin + timedelta(days=index)).isoformat(), float(price))
        for index, price in enumerate(prices)
    ]


class Scripted:
    """按「信号日 ⇒ 目标仓位」查表的策略；未登记的交易日**回到** ``default``（默认空仓）。

    「原样口头指令」而不是「写一次就永久生效」：A 股规则测试要逐日控制「今天信号是什么」，
    若查不到就沿用上一状态，停牌/涨停测试里下一根会意外继续下单，掩盖真正要钉的规则。
    需要「信号持续多日」的用例，把每一天都显式登记进 ``table`` 即可。
    """

    name = "scripted"

    def __init__(self, table: dict[str, float], *, default: float = 0.0) -> None:
        self.table = dict(table)
        self.default = default
        self.calls: list[str] = []

    def params(self) -> dict[str, Any]:
        return {"strategy": self.name, "lookup": sorted(self.table)}

    def target(self, history: Sequence[Bar]) -> float:
        day = history[-1].date
        self.calls.append(day)
        return self.table.get(day, self.default)


class TomorrowOracle:
    """**故意知道明天**的策略：用 ``history`` 的最后一根预测下一根会涨还是跌。

    真正「偷看未来」的实现会直接读 ``bars[k+1]``；这里只拿到 ``bars[:k]``，作弊的
    唯一手段是用「最后一根收盘 vs 前一根收盘」推断下一根——因为测试数据是**隔天
    交替涨跌**的锯齿序列，这个推断在纯价格世界里百发百中，但只要撮合层真的把成交
    推迟到 t+1，锯齿本身就意味着一买就跌、一卖就涨，收益必然为负。
    """

    name = "tomorrow_oracle"

    def params(self) -> dict[str, Any]:
        return {"strategy": self.name}

    def target(self, history: Sequence[Bar]) -> float:
        if len(history) < 2:
            return 0.0
        up_today = history[-1].close > history[-2].close
        # 锯齿：今天涨 -> 明天跌 -> 空仓；今天跌 -> 明天涨 -> 满仓
        return 0.0 if up_today else 1.0


def write_daily_k(
    root: Path,
    symbol: str,
    bars: Iterable[Bar],
    *,
    adjust: str = "qfq",
) -> Path:
    """在 ``root/market.duckdb`` 建一张最小 ``daily_k``，返回库路径。

    只造回测需要的列（``code / trade_date / payload``）；真实库还有 ``ingested_at``、
    ``source``，但 ``feed.load_series`` 只读这三列，少造不影响的就不造。
    """
    from ladderstock.data import duckdb_store

    root.mkdir(parents=True, exist_ok=True)
    path = duckdb_store.db_path(root)
    conn = duckdb_store.connect(path)
    try:
        conn.execute(
            'CREATE TABLE IF NOT EXISTS "daily_k" ('
            "code VARCHAR, trade_date DATE, payload VARCHAR)"
        )
        # 同一 symbol 重复写入 = 覆盖（同一天只能一行）；否则「写两遍再回测」会撞重复日期
        conn.execute('DELETE FROM "daily_k" WHERE code = ?', [symbol])
        rows = []
        for bar in bars:
            payload = json.dumps(
                {
                    "date": bar.date,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                    "adjust": adjust,
                },
                ensure_ascii=False,
            )
            rows.append((symbol, bar.date, payload))
        if rows:
            conn.executemany('INSERT INTO "daily_k" VALUES (?, ?, ?)', rows)
    finally:
        duckdb_store.close(conn)
    return path


__all__ = ["Scripted", "TomorrowOracle", "flat_bar", "series", "write_daily_k"]
