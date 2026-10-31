"""ISO 周标签（`YYYY-Www`）：解析、校验与日期换算。

只用 ISO-8601 周历（`date.isocalendar()`），不引入第三方周历库：

- 一周固定为周一到周日七天，`Www` 为 ISO 周序（`W01`…`W53`）；
- 周标签会进文件名（`data/weekly/<week>.json`），所以必须先过白名单再拼接路径，
  与 `research/checkpoint.normalize_code()` 同一道防线（防目录穿越）；
- 本模块**没有 `now()`**：「本周是哪一周」由调用方把今天传进来（`current_week(today)`），
  这样周度事实包在测试里可复算，命令行为在跨零点时也不会自己换周。

口径与示例：`2026-W44` = 2026-10-26（周一）～ 2026-11-01（周日）。
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

#: `YYYY-Www`（大写 W，两位周序）；小写输入由 `normalize_week()` 先归一
WEEK_PATTERN = re.compile(r"^(\d{4})-W(\d{2})$")

#: 允许传入普通日期，命令据此归一到所在周（`--week 2026-10-28` → `2026-W44`）
DAY_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def format_week(day: date) -> str:
    """日期 → 所在 ISO 周标签。"""
    iso = day.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def normalize_week(value: Any) -> str:
    """校验并归一周标签；非法即抛 `ValueError`（调用方据此给退出码 2）。

    接受三种写法：`2026-W44`、`2026-w44`、`2026-10-28`（归一到所在周）。
    """
    text = str(value or "").strip()
    if not text:
        raise ValueError("缺少周标签（需要 YYYY-Www，例如 2026-W44）")
    if DAY_PATTERN.match(text):
        return format_week(date.fromisoformat(text))
    matched = WEEK_PATTERN.match(text.upper())
    if not matched:
        raise ValueError(f"非法周标签：{value!r}（需要 YYYY-Www，例如 2026-W44）")
    year, week = int(matched.group(1)), int(matched.group(2))
    if not 1 <= week <= 53:
        raise ValueError(f"非法周序：{value!r}（ISO 周序范围 W01–W53）")
    try:
        date.fromisocalendar(year, week, 1)
    except ValueError:
        raise ValueError(f"{year} 年没有第 {week} 周：{value!r}") from None
    return f"{year}-W{week:02d}"


def parse_week(value: Any) -> tuple[int, int]:
    """周标签 → `(iso_year, iso_week)`。"""
    week = normalize_week(value)
    year, _, number = week.partition("-W")
    return int(year), int(number)


def week_bounds(value: Any) -> tuple[date, date]:
    """周标签 → `(周一, 周日)`。"""
    year, week = parse_week(value)
    return date.fromisocalendar(year, week, 1), date.fromisocalendar(year, week, 7)


def week_start(value: Any) -> date:
    return week_bounds(value)[0]


def week_days(value: Any) -> list[date]:
    """本周七天（周一 → 周日，升序）。"""
    start, _end = week_bounds(value)
    return [start + timedelta(days=offset) for offset in range(7)]


def weekday_days(value: Any) -> list[date]:
    """本周的工作日（周一 → 周五）。周末没有行情，不必进缺数据清单。"""
    return [day for day in week_days(value) if day.weekday() < 5]


def in_week(value: Any, day: date) -> bool:
    start, end = week_bounds(value)
    return start <= day <= end


def current_week(today: Any = None) -> str:
    """今天所在的周标签；`today` 可注入，便于测试与「补跑上周」。"""
    if today is None:
        return format_week(date.today())
    if isinstance(today, str):
        parsed = date.fromisoformat(today)
    elif isinstance(today, date):
        parsed = today
    else:
        raise TypeError(f"today 需要 date 或 YYYY-MM-DD，收到 {type(today).__name__}")
    return format_week(parsed)


__all__ = [
    "DAY_PATTERN",
    "WEEK_PATTERN",
    "current_week",
    "format_week",
    "in_week",
    "normalize_week",
    "parse_week",
    "week_bounds",
    "week_days",
    "week_start",
    "weekday_days",
]
