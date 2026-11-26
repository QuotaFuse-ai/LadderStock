"""A 股交易日历（最小实现）：周末 + 可配置节假日表。

口径（2026-11-21 用户裁决）：不接商业数据源，节假日不靠猜。

- 周六 / 周日 → 非交易日，这是 A 股固定规则；
- 法定节假日 → 读 `config/trading_holidays.json`（可选）。文件缺失或写坏时
  只按周末判，**不假装知道**节假日（判成交易日，让真实缺口暴露出来）。

用途只有一个：面板判断「今天该不该有行情数据」。休市日本来就不产生日线，
不能因为库里没有今天的记录就报「降级」。
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

HOLIDAY_FILE = "trading_holidays.json"

STATUS_TRADING = "trading"
STATUS_WEEKEND = "closed_weekend"
STATUS_HOLIDAY = "closed_holiday"


def holidays_path(config_root: str | os.PathLike[str] | None = None) -> Path:
    """节假日表位置：显式目录 > LDST_CONFIG_DIR > 仓库 config/。"""
    if config_root is not None:
        return Path(config_root) / HOLIDAY_FILE
    override = os.environ.get("LDST_CONFIG_DIR")
    if override:
        return Path(override) / HOLIDAY_FILE
    from ladderstock.cli.paths import config_dir

    return config_dir() / HOLIDAY_FILE


def holidays(config_root: str | os.PathLike[str] | None = None) -> set[date]:
    """读节假日表；缺文件 / 坏 JSON 一律返回空集（只按周末判）。"""
    path = holidays_path(config_root)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    items = raw.get("holidays") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return set()
    parsed: set[date] = set()
    for item in items:
        try:
            parsed.add(date.fromisoformat(str(item)))
        except ValueError:
            continue
    return parsed


def market_status(
    day: date,
    config_root: str | os.PathLike[str] | None = None,
) -> str:
    """`trading` / `closed_weekend` / `closed_holiday`。"""
    if day.weekday() >= 5:
        return STATUS_WEEKEND
    if day in holidays(config_root):
        return STATUS_HOLIDAY
    return STATUS_TRADING


def is_trading_day(day: date, config_root: str | os.PathLike[str] | None = None) -> bool:
    return market_status(day, config_root) == STATUS_TRADING


__all__ = [
    "HOLIDAY_FILE",
    "STATUS_HOLIDAY",
    "STATUS_TRADING",
    "STATUS_WEEKEND",
    "holidays",
    "holidays_path",
    "is_trading_day",
    "market_status",
]
