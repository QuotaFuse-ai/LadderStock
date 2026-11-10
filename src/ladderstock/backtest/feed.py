"""回测数据源：从本机 ``daily_k`` 读日频 OHLCV（QUOT-500 §5.1）。

数据形态（实测 2026-11-10）：

- 表结构：``code, trade_date, ingested_at, source, payload``，``payload`` 为 JSON；
- 600000 = 641 根，2024-03-14 ~ 2026-11-05，``adjust=qfq``（前复权，直接用作回测价序列）；
- 000001 / 300750 / 002594 / 600519 / 601899 各约 320 根（2025-07 起），**M1 不用**；
- ``index_daily`` 仅 ``000300``（沪深 300），**没有 SSE 指数**，所以不提供「上证指数」基准；
  M1 的基准只能取**买入持有**（见 `benchmark.py` 以外的口径说明）。缺的部分不编造，缺就报缺。

复权口径：``payload.adjust`` 目前一律为 ``qfq``，前复权价直接用；如果未来有 ``adjust=null`` 的
未复权行（或口径混杂），本模块**不猜测**——报错并写明该票需要什么口径。回测同样只用、
只报一个复权口径，不前后混用。
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Final

from ladderstock.data import duckdb_store

PRICE_DATASET: Final = "daily_k"
SUPPORTED_ADJUST: Final = frozenset({"qfq"})


class FeedError(ValueError):
    """数据缺失 / 口径不支持都在这里抛（不静默补数、不猜口径）。"""


@dataclass(frozen=True, slots=True)
class Bar:
    """单根日 K；价格按数据源里的复权口径（``adjust`` 字段标注），不做二次换算。"""

    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


@dataclass(frozen=True, slots=True)
class BarSeries:
    """一只票的有序 K 线；`data_fp` 由 ``ladderstock.calc.data_fingerprint`` 生成。"""

    symbol: str
    adjust: str
    bars: tuple[Bar, ...]
    data_fp: str

    def to_rows(self) -> list[dict[str, Any]]:
        """换成 ``ladderstock.calc`` 认识的纯字典列表（用于重新计算指纹 / 传给指标算子）。"""
        return [b.to_dict() for b in self.bars]

    def __len__(self) -> int:
        return len(self.bars)

    @property
    def first_date(self) -> str:
        return self.bars[0].date

    @property
    def last_date(self) -> str:
        return self.bars[-1].date


def _finite(name: str, value: Any, *, where: str) -> float:
    if value is None or isinstance(value, bool):
        raise FeedError(f"{where}:{name} 缺失或不是数值")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise FeedError(f"{where}:{name} 非数 {value!r}") from exc
    if number != number or number in (float("inf"), float("-inf")):
        raise FeedError(f"{where}:{name} 非有限数 {value!r}")
    return number


def _row_to_bar(raw: dict[str, Any], *, symbol: str) -> Bar:
    day = str(raw.get("date") or raw.get("trade_date") or "").strip()
    if not day or len(day) < 10:
        raise FeedError(f"{symbol}:payload 缺 date 字段：{raw!r}")
    where = f"{symbol}@{day[:10]}"
    open_ = _finite("open", raw.get("open"), where=where)
    high = _finite("high", raw.get("high"), where=where)
    low = _finite("low", raw.get("low"), where=where)
    close = _finite("close", raw.get("close"), where=where)
    volume = _finite("volume", raw.get("volume"), where=where)
    for name, value in (("open", open_), ("high", high), ("low", low), ("close", close)):
        if value <= 0:
            raise FeedError(f"{where}:{name} 必须为正，收到 {value!r}")
    if high < low:
        raise FeedError(f"{where}:high < low（{high} < {low}）")
    for name, value in (("open", open_), ("close", close)):
        if not (low <= value <= high):
            raise FeedError(f"{where}:{name}={value} 不在 [low, high] 内")
    if volume < 0:
        raise FeedError(f"{where}:volume 不能为负，收到 {volume!r}")
    return Bar(date=day[:10], open=open_, high=high, low=low, close=close, volume=volume)


def _read_adjust(raw_rows: Iterable[tuple[Any, Any, Any]]) -> set[str]:
    seen: set[str] = set()
    for _code, _day, payload in raw_rows:
        try:
            record = json.loads(payload) if payload else {}
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            value = record.get("adjust")
            if value is None:
                seen.add("<null>")
            elif isinstance(value, str) and value:
                seen.add(value)
    return seen


def load_series(
    symbol: str,
    *,
    start: date | None = None,
    end: date | None = None,
    root: Path | None = None,
) -> BarSeries:
    """从本机 ``daily_k`` 读一只票的日 K 序列。

    缺数据不补：``start`` 之前的缺口、``end`` 之后的缺口都只是区间外；区间内某一天没有
    行（停牌）由调用方决定（撮合会跳过）。整只票都没数据 → ``FeedError`` 指明。
    """
    code = str(symbol).strip().upper()
    if not code:
        raise FeedError("symbol 不能为空")
    path = duckdb_store.db_path(root)
    if not path.is_file():
        raise FeedError(f"本机还没有历史库：{path}（先 `ldst sync --full` 拉数）")
    conn = duckdb_store.connect(path, read_only=True)
    try:
        if not duckdb_store.table_exists(conn, PRICE_DATASET):
            raise FeedError(f"历史库里没有 {PRICE_DATASET} 表：{path}")
        day_filter = ""
        params: list[Any] = [code]
        if start is not None:
            day_filter += " AND trade_date >= ?"
            params.append(start)
        if end is not None:
            day_filter += " AND trade_date <= ?"
            params.append(end)
        raw_rows = conn.execute(
            f'SELECT code, trade_date, payload FROM "{PRICE_DATASET}" '
            f"WHERE code = ?{day_filter} ORDER BY trade_date",
            params,
        ).fetchall()
    finally:
        duckdb_store.close(conn)

    if not raw_rows:
        span = ""
        if start and end:
            span = f"（{start.isoformat()} ~ {end.isoformat()}）"
        raise FeedError(
            f"本机 {PRICE_DATASET} 里没有 {code}{span} 的行；缺就报缺，不补数，不猜"
        )

    adjusts = _read_adjust(raw_rows)
    if len(adjusts) != 1 or next(iter(adjusts), "") not in SUPPORTED_ADJUST:
        raise FeedError(
            f"{code}: 不支持复权口径 {sorted(adjusts)!r}；M1 仅接受 {sorted(SUPPORTED_ADJUST)}，"
            "缺的标准口径请先补齐再回测"
        )

    bars: list[Bar] = []
    seen_days: set[str] = set()
    for _code, _day, payload in raw_rows:
        try:
            record = json.loads(payload) if payload else {}
        except json.JSONDecodeError as exc:
            raise FeedError(f"{code}:payload 无法解析为 JSON：{exc}") from exc
        if not isinstance(record, dict):
            raise FeedError(f"{code}:payload 不是对象：{type(record).__name__}")
        bar = _row_to_bar(record, symbol=code)
        if bar.date in seen_days:
            raise FeedError(f"{code}:日期重复 {bar.date}（同一票同一交易日只能一行）")
        seen_days.add(bar.date)
        bars.append(bar)

    bars.sort(key=lambda b: b.date)
    if len(bars) < 2:
        raise FeedError(f"{code}:只有 {len(bars)} 根 K 线，至少 2 根（1 买入 1 卖出）才能回测")
    rows = [b.to_dict() for b in bars]
    # 避免顶层 import 与 `ladderstock.calc` 的加载顺序耦合（calc 在包初始化时装载子模块）
    from ladderstock.calc.fingerprint import data_fingerprint

    return BarSeries(
        symbol=code,
        adjust=next(iter(adjusts)),
        bars=tuple(bars),
        data_fp=data_fingerprint(rows),
    )


def index_series(
    code: str = "000300",
    *,
    start: date | None = None,
    end: date | None = None,
    root: Path | None = None,
) -> BarSeries | None:
    """读 ``index_daily`` 里的指数序列（供基准使用）；读不到返回 ``None``。

    实测 ``index_daily`` 只有 ``000300``（沪深 300），**没有上证指数**。规格说基准用上证
    指数；M1 在没有上证指数的前提下，把「基准」实现为**该标的买入持有**（见 ``score.py``），
    此处只作为未来补数后的扩展点，不在 M1 主动调用。
    """
    path = duckdb_store.db_path(root)
    if not path.is_file():
        return None
    conn = duckdb_store.connect(path, read_only=True)
    try:
        if not duckdb_store.table_exists(conn, "index_daily"):
            return None
        day_filter = ""
        params: list[Any] = [code]
        if start is not None:
            day_filter += " AND trade_date >= ?"
            params.append(start)
        if end is not None:
            day_filter += " AND trade_date <= ?"
            params.append(end)
        raw_rows = conn.execute(
            f'SELECT code, trade_date, payload FROM "index_daily" '
            f"WHERE code = ?{day_filter} ORDER BY trade_date",
            params,
        ).fetchall()
    finally:
        duckdb_store.close(conn)
    if not raw_rows:
        return None
    bars: list[Bar] = []
    for _code, _day, payload in raw_rows:
        try:
            record = json.loads(payload) if payload else {}
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            try:
                bars.append(_row_to_bar(record, symbol=code))
            except FeedError:
                continue
    if len(bars) < 2:
        return None
    rows = [b.to_dict() for b in bars]
    from ladderstock.calc.fingerprint import data_fingerprint

    return BarSeries(
        symbol=code, adjust="index", bars=tuple(bars), data_fp=data_fingerprint(rows)
    )


__all__ = [
    "Bar",
    "BarSeries",
    "FeedError",
    "PRICE_DATASET",
    "SUPPORTED_ADJUST",
    "index_series",
    "load_series",
]
