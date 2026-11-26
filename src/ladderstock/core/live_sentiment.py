r"""盘中实时情绪快照（`data/live/sentiment/<date>.json`）。

为什么需要它：`limit_up_pool` 只给**最近一个已收盘交易日**的涨停池，所以 15:35 收盘
同步之前，`archive/<date>/limit_pool.parquet` 里根本没有今天的数据，情绪温度页一整天
都是「缺失」。而 TickHub 的 `market_sentiment` 在盘中是**当日实时**的：涨停家数、
炸板家数、跌停家数、封板率、最高连板、龙头名单。

这份快照只做两件事：把那一刻的实时读数原样落盘（带 `fetched_at`），让页面能读到
「今天此刻的涨停情况」；收盘归档落齐后由 `compute_sentiment` 优先走 archive，实时快照
自然退居其后。不写 archive、不改水位、不合成 archive 口径的日 K 指标——盘中就是盘中，
收盘就是收盘，两者的出处都写在 `source_detail` 里。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any


def live_dir(root: Any = None) -> Path:
    """实时快照目录（`<data_root>/live/sentiment`）。"""
    from ladderstock.cli.paths import data_dir

    base = Path(root) if root is not None else data_dir()
    return base / "live" / "sentiment"


def live_path(day: date | str, root: Any = None) -> Path:
    return live_dir(root) / f"{day}.json"


def write_snapshot(
    day: date | str,
    records: dict[str, Any],
    *,
    fetched_at: datetime,
    source: str = "tickhub_cn/market_sentiment",
    root: Any = None,
) -> Path:
    """覆盖写当日实时快照（每轮一条，只保留最新一轮）。"""
    path = live_path(day, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "date": str(day),
        "fetched_at": fetched_at.isoformat(timespec="seconds"),
        "source": source,
        "records": records,
    }
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_snapshot(day: date | str, root: Any = None) -> dict[str, Any] | None:
    """读当日实时快照；文件缺失或结构不合法一律返回 None（不抛）。"""
    path = live_path(day, root)
    if not path.is_file():
        return None
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(body, dict) or str(body.get("date")) != str(day):
        return None
    records = body.get("records")
    if not isinstance(records, dict):
        return None
    return body
