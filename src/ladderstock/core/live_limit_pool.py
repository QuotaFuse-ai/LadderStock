r"""盘中实时涨停池快照（`data/live/limit_pool/<date>.json`）。

为什么需要它：`limit_up_pool` 在盘中有两种表现——

- **返回当日实时全集**（实测 2026-11-17 11:50 拿到 42 只，含 `streak` 与行业），
  这时晋级率、每一档家数、梯队断层**都能真算**；
- 只返回最近一个已收盘交易日（更早的一次实测），这时只能退到「龙头前 N 只」。

这份快照只做一件事：把某一刻的当日涨停池**原样**落下来，让页面在盘中就能读到
「今天此刻的涨停全集」，而不是整天等着 15:35。三条边界与 `live_sentiment` 同一条：

1. 只写 live 目录，不碰 archive、不推水位（收盘口径仍归 15:35 的 `sync_close`）；
2. **接口给的必须是今天**才落盘，否则整份不写（拿昨天的池当今天的池就是造假）；
3. 落盘只保留判定需要的字段，不把上游整包塞进文件。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

#: 一条记录保留的字段（页面与计算只用到这些）
RECORD_FIELDS: tuple[str, ...] = (
    "code",
    "name",
    "streak",
    "price",
    "change_pct",
    "industry",
    "first_seal_time",
    "last_seal_time",
    "broken_times",
    "seal_amount",
    "amount",
    "float_market_cap",
    "turnover_rate",
)


def pool_dir(root: Any = None) -> Path:
    from ladderstock.cli.paths import data_dir

    base = Path(root) if root is not None else data_dir()
    return base / "live" / "limit_pool"


def pool_path(day: date | str, root: Any = None) -> Path:
    return pool_dir(root) / f"{day}.json"


def _clean_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        code = str(row.get("code") or "").strip()
        if not code:
            continue
        item: dict[str, Any] = {"code": code}
        for key in RECORD_FIELDS:
            if key == "code":
                continue
            value = row.get(key)
            if value is not None:
                item[key] = value
        out.append(item)
    return out


def write_snapshot(
    day: date | str,
    rows: Iterable[Mapping[str, Any]],
    *,
    fetched_at: datetime,
    source: str = "tickhub_cn/limit_up_pool",
    root: Any = None,
) -> Path:
    """覆盖写当日实时涨停池（每轮一份，只保留最新一轮）。"""
    path = pool_path(day, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "date": str(day),
        "fetched_at": fetched_at.isoformat(timespec="seconds"),
        "source": source,
        "records": _clean_rows(rows),
    }
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def read_snapshot(day: date | str, root: Any = None) -> dict[str, Any] | None:
    """读当日实时涨停池；文件缺失 / 不是当天 / 结构不合法一律返回 None（不抛）。"""
    path = pool_path(day, root)
    if not path.is_file():
        return None
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(body, dict) or str(body.get("date")) != str(day):
        return None
    records = body.get("records")
    if not isinstance(records, list) or not records:
        return None
    return body


def rows_of(body: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """快照里的记录列表（结构不合法时给空表，调用方据此走兜底）。"""
    if not isinstance(body, Mapping):
        return []
    records = body.get("records")
    if not isinstance(records, list):
        return []
    return [dict(row) for row in records if isinstance(row, Mapping)]


__all__ = ["RECORD_FIELDS", "pool_dir", "pool_path", "read_snapshot", "rows_of", "write_snapshot"]
