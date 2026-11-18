"""盘中分钟样本落盘：`<data_root>/minute_bars/<date>.jsonl`（盯盘信号的唯一输入）。

**这里只落盘，不采集、不联网**（DEC-006 边界不变）：采集发生在
`cli/schedule.py:_run_watch_live` 现取实时报价那一步——那一份价本来就是"此刻的价"，
顺手记成分钟样本，盯盘扫描器（`supervisor/page_boot.py:WatchScanner`）才有东西可扫。

本次补齐的正是此前缺失的一环：整仓只有**读** `minute_bars` 的地方
（`cli/watch.py`、`supervisor/page_boot.py`），没有**写**的地方，所以盯盘页永远
`waiting_source`、`watch/status.json` 永远不会生成。本模块是那个写入者。

三条硬规矩：

1. **只写当日**：样本时间戳的日期必须等于文件名里的日期，否则整条拒收。
   旧交易日的价写进今天的文件，就是拿历史数据冒充当日信号——页面会照着这个价报警，
   比没有数据更糟。
2. **一分钟一条**：同一 `(code, 分钟)` 只保留最后一条。30 秒一轮采样会让
   `09:31:00` 与 `09:31:30` 落进同一分钟，两条都留就会破坏「恰好 3 分钟前」的窗口匹配。
3. **按分钟归并后整体重写**：文件规模 = 自选数 × 240 分钟（几十只票也就几千行），
   重写成本远低于「追加 + 事后去重」的正确性风险。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any

BARS_DIRNAME = "minute_bars"

#: 一条记录允许出现的字段（写出去时只保留这些，别把上游整包塞进文件）
#: `name` 是给 `/watch` 信号行显示用的（原先只有代码，名称列只能空着）
BAR_FIELDS: tuple[str, ...] = ("ts", "code", "price", "limit_price", "name")


class BarRejected(ValueError):
    """单条样本不合格（缺字段 / 价格非法 / 不是当日）。"""


def bars_path(day: date | str, root: Path | None = None) -> Path:
    """`<data_root>/minute_bars/<YYYY-MM-DD>.jsonl`。"""
    from ladderstock.cli.paths import data_dir

    base = Path(root) if root is not None else data_dir()
    return base / BARS_DIRNAME / f"{day.isoformat() if isinstance(day, date) else day}.jsonl"


def _parse_ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    text = str(value or "").strip()
    if not text:
        raise BarRejected("缺少 ts")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BarRejected(f"ts 无法解析：{text!r}") from exc


def _normalize(row: Mapping[str, Any], day: str) -> dict[str, Any]:
    code = str(row.get("code") or "").strip()
    if not code:
        raise BarRejected("缺少 code")
    ts = _parse_ts(row.get("ts"))
    if ts.date().isoformat() != day:
        raise BarRejected(f"{code} 的样本日期 {ts.date().isoformat()} 不是 {day}（拒收旧数据）")
    try:
        price = float(row.get("price"))
    except (TypeError, ValueError) as exc:
        raise BarRejected(f"{code} 的 price 非法：{row.get('price')!r}") from exc
    if price <= 0:
        raise BarRejected(f"{code} 的 price 必须为正：{price}")

    limit_price = row.get("limit_price")
    item: dict[str, Any] = {
        "ts": ts.isoformat(timespec="seconds"),
        "code": code,
        "price": price,
    }
    if limit_price is not None:
        try:
            item["limit_price"] = float(limit_price)
        except (TypeError, ValueError):
            pass  # 涨停价取不到就不写，封板信号据此判不了（如实留空，不猜）
    name = str(row.get("name") or "").strip()
    if name:
        item["name"] = name  # 名称只是显示用；取不到就不写，不拿代码顶名字
    return item


def _minute_key(item: Mapping[str, Any]) -> tuple[str, str]:
    """去重键：`(code, 分钟)`。"""
    ts = _parse_ts(item.get("ts"))
    return str(item.get("code")), ts.strftime("%Y-%m-%dT%H:%M")


def _read_existing(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except ValueError:
                continue  # 坏行跳过：单行损坏不该让整份样本作废
            if isinstance(row, dict):
                rows.append(row)
    except OSError:
        return []
    return rows


def append_minute_bars(
    day: date | str, rows: Iterable[Mapping[str, Any]], *, root: Path | None = None
) -> dict[str, Any]:
    """把一批实时样本并入当日分钟文件，返回写入结果（不抛上游异常）。

    `rejected` 里逐条给出拒收原因，调用方写进任务 detail——「为什么没写进去」必须能看见，
    否则又变成「页面空着但没人知道为什么」。
    """
    day_text = day.isoformat() if isinstance(day, date) else str(day)
    accepted: list[dict[str, Any]] = []
    rejected: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping):
            rejected.append("样本不是对象")
            continue
        try:
            accepted.append(_normalize(row, day_text))
        except BarRejected as exc:
            rejected.append(str(exc))

    if not accepted:
        return {
            "path": str(bars_path(day_text, root)),
            "written": 0,
            "rejected": len(rejected),
            "rejected_detail": rejected[:10],
            "detail": "没有可写入的当日样本",
        }

    path = bars_path(day_text, root)
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for row in _read_existing(path):
        try:
            merged[_minute_key(row)] = row
        except (BarRejected, TypeError, ValueError):
            continue
    for row in accepted:
        merged[_minute_key(row)] = row  # 同分钟后者覆盖前者

    ordered = [merged[key] for key in sorted(merged)]
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(json.dumps(row, ensure_ascii=False) for row in ordered) + "\n"
    path.write_text(text, encoding="utf-8")

    return {
        "path": str(path),
        "written": len(accepted),
        "total": len(ordered),
        "rejected": len(rejected),
        "rejected_detail": rejected[:10],
        "detail": f"并入 {len(accepted)} 条当日样本，文件共 {len(ordered)} 行",
    }


__all__ = ["BAR_FIELDS", "BARS_DIRNAME", "BarRejected", "append_minute_bars", "bars_path"]
