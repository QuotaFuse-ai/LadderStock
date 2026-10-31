"""盘中盯盘信号：3 分钟涨跌幅越阈、封板 / 开板（纯函数，不联网、不取数）。

DEC-006 边界：本模块只消费**本机已有的分钟级样本**——TickHub 落盘缓存或离线
回放 fixture，不采集、不直连任何行情站点，也不新增数据集登记。指标与事件全部
由程序计算，AI 只读不写（`core/boundary.py:AI_WRITABLE`）。

信号口径（发布前检查 有对应的冒烟步骤）：

| kind | 触发条件 | value |
|---|---|---|
| `threshold_up` | 当前分钟价相对**恰好 3 分钟前**那根 bar 的涨幅 ≥ 阈值 | 涨幅（%） |
| `threshold_down` | 同上，跌幅 ≥ 阈值 | 跌幅（%，负数） |
| `seal` | 由「未封板」转「封板」（`price >= limit_price - 容差`） | 成交价 |
| `open` | 由「封板」转「未封板」 | 成交价 |

三条硬约束：

1. **只在越阈的那一分钟报一次**：持续在阈值之上不重复刷屏（`above` 状态机）；
2. **不跨缺口硬算**：3 分钟基准必须是与当前分钟**恰好相差 3 分钟**的那根 bar，
   午休 / 停牌造成缺口时宁可不报，也不拿隔了很久的价格凑涨跌幅；
3. **同 code 同 kind 同分钟内只报一次**：`dedupe()` 用 `(code, kind, 分钟)` 作键，
   重复样本（同一分钟多路推送）不会产生重复事件。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

KIND_THRESHOLD_UP = "threshold_up"
KIND_THRESHOLD_DOWN = "threshold_down"
KIND_SEAL = "seal"
KIND_OPEN = "open"

KINDS: tuple[str, ...] = (
    KIND_THRESHOLD_UP,
    KIND_THRESHOLD_DOWN,
    KIND_SEAL,
    KIND_OPEN,
)

KIND_LABELS: dict[str, str] = {
    KIND_THRESHOLD_UP: "3 分钟拉升",
    KIND_THRESHOLD_DOWN: "3 分钟跳水",
    KIND_SEAL: "封板",
    KIND_OPEN: "开板",
}

# 3 分钟窗口（分钟级 bar 的口径，不随调用方变化）
WINDOW_MINUTES = 3

# 封板判定容差：A 股报价到分，浮点比较留半个价位
SEAL_TOLERANCE = 0.005

DEFAULT_SOURCE_DETAIL = "local-minute-bars"


class SignalInputError(ValueError):
    """分钟样本不可用（字段缺失、时间戳无法解析等）——属于参数/数据错误。"""


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #

@dataclass(frozen=True, slots=True)
class MinuteBar:
    """一根分钟 bar：价格 + 可选的涨停价（用于封板判定）。"""

    ts: datetime
    code: str
    price: float
    limit_price: float | None = None
    source_detail: str = DEFAULT_SOURCE_DETAIL

    @property
    def sealed(self) -> bool | None:
        """是否封板；没有涨停价时无法判定（返回 None，不猜）。"""
        if self.limit_price is None:
            return None
        return self.price >= self.limit_price - SEAL_TOLERANCE

    @property
    def minute(self) -> datetime:
        return self.ts.replace(second=0, microsecond=0)


@dataclass(frozen=True, slots=True)
class WatchEvent:
    """一条盯盘信号事件（`ts / code / kind / value / source_detail` 五字段契约）。"""

    ts: datetime
    code: str
    kind: str
    value: float | None
    source_detail: str

    @property
    def minute(self) -> datetime:
        return self.ts.replace(second=0, microsecond=0)

    @property
    def key(self) -> tuple[str, str, str]:
        """去重键：同 code 同 kind 同分钟只保留一条。"""
        return (self.code, self.kind, self.minute.isoformat(timespec="minutes"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts.isoformat(timespec="seconds"),
            "code": self.code,
            "kind": self.kind,
            "value": self.value,
            "source_detail": self.source_detail,
        }

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> WatchEvent:
        kind = str(row.get("kind") or "")
        if kind not in KINDS:
            raise SignalInputError(f"未知信号类型 {kind!r}；允许 {', '.join(KINDS)}")
        value = row.get("value")
        return cls(
            ts=_parse_ts(row.get("ts"), field="ts"),
            code=str(row.get("code") or ""),
            kind=kind,
            value=None if value is None else float(value),
            source_detail=str(row.get("source_detail") or DEFAULT_SOURCE_DETAIL),
        )


def _parse_ts(value: Any, *, field: str) -> datetime:
    """解析时间戳：接受 ISO 字符串 / datetime；带时区的转成本机时区后去掉 tzinfo。

    分钟样本统一按交易所本地时间（naive）比较，避免同一份数据里混入时区后
    `ts - 3min` 的字典查找全部落空。
    """
    if isinstance(value, datetime):
        ts = value
    elif isinstance(value, str) and value.strip():
        text = value.strip().replace(" ", "T", 1)
        try:
            ts = datetime.fromisoformat(text)
        except ValueError as exc:
            raise SignalInputError(f"{field} 无法解析为时间：{value!r}") from exc
    else:
        raise SignalInputError(f"{field} 缺失或类型不对：{value!r}")
    if ts.tzinfo is not None:
        ts = ts.astimezone().replace(tzinfo=None)
    return ts.replace(microsecond=0)


# --------------------------------------------------------------------------- #
# 样本装载
# --------------------------------------------------------------------------- #

def _as_float(value: Any, *, field: str, index: int) -> float:
    if isinstance(value, bool) or value is None:
        raise SignalInputError(f"第 {index} 行 {field} 缺失或类型不对：{value!r}")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise SignalInputError(f"第 {index} 行 {field} 不是数字：{value!r}") from exc


def parse_bars(
    rows: Iterable[Mapping[str, Any]], *, source_detail: str = DEFAULT_SOURCE_DETAIL
) -> list[MinuteBar]:
    """把记录列表转成 `MinuteBar`；缺字段直接报错，不静默补 0。"""
    bars: list[MinuteBar] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, Mapping):
            raise SignalInputError(f"第 {index} 行不是对象：{row!r}")
        code = str(row.get("code") or "").strip()
        if not code:
            raise SignalInputError(f"第 {index} 行缺少 code")
        limit_price = row.get("limit_price")
        parsed_limit = (
            None
            if limit_price is None
            else _as_float(limit_price, field="limit_price", index=index)
        )
        bars.append(
            MinuteBar(
                ts=_parse_ts(row.get("ts"), field="ts"),
                code=code,
                price=_as_float(row.get("price"), field="price", index=index),
                limit_price=parsed_limit,
                source_detail=str(row.get("source_detail") or source_detail),
            )
        )
    return bars


def load_bars(path: Path, *, source_detail: str | None = None) -> list[MinuteBar]:
    """读分钟样本：`.jsonl`（每行一条）或 `.json`（数组 / `{"bars": [...]}`）。"""
    if not path.is_file():
        raise FileNotFoundError(f"分钟样本不存在：{path}")
    text = path.read_text(encoding="utf-8")
    detail = source_detail or f"file:{path.name}"
    if path.suffix.lower() == ".jsonl":
        rows: Any = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        rows = json.loads(text or "[]")
        if isinstance(rows, Mapping):
            rows = rows.get("bars") or []
    if not isinstance(rows, list):
        raise SignalInputError(
            f"{path.name} 需要数组或 {{'bars': [...]}}，收到 {type(rows).__name__}"
        )
    return parse_bars(rows, source_detail=detail)


# --------------------------------------------------------------------------- #
# 检测
# --------------------------------------------------------------------------- #

def _event(bar: MinuteBar, kind: str, value: float) -> WatchEvent:
    return WatchEvent(
        ts=bar.ts,
        code=bar.code,
        kind=kind,
        value=round(value, 2),
        source_detail=bar.source_detail,
    )


def dedupe(events: Iterable[WatchEvent]) -> list[WatchEvent]:
    """同 code 同 kind 同分钟只保留一条（保留最先出现的那条）。"""
    seen: set[tuple[str, str, str]] = set()
    unique: list[WatchEvent] = []
    for event in events:
        if event.key in seen:
            continue
        seen.add(event.key)
        unique.append(event)
    return unique


def detect_signals(
    bars: Iterable[MinuteBar],
    *,
    threshold: float,
    window_minutes: int = WINDOW_MINUTES,
) -> list[WatchEvent]:
    """从分钟样本里算出信号事件（按 ts / code / kind 排序，已去重）。

    `threshold` 是百分比（3.0 = 3%），必须为正数；越阈只在「未越 → 越」那一分钟
    报一次，持续越阈不重复。封板第一根就报 `seal`（盯盘开始时已封板也值得知道），
    之后只在状态切换时报 `open` / `seal`。
    """
    if threshold <= 0:
        raise ValueError("threshold 必须大于 0（单位：百分比）")
    if window_minutes <= 0:
        raise ValueError("window_minutes 必须大于 0")

    by_code: dict[str, dict[datetime, MinuteBar]] = {}
    for bar in bars:
        # 同一分钟多路推送：后到的覆盖先到的（最新价格更可信）
        by_code.setdefault(bar.code, {})[bar.minute] = bar

    window = timedelta(minutes=window_minutes)
    events: list[WatchEvent] = []
    for code in sorted(by_code):
        series = by_code[code]
        sealed: bool | None = None
        above_up = False
        above_down = False
        for minute in sorted(series):
            bar = series[minute]
            state = bar.sealed
            if state is not None:
                if state and sealed is not True:
                    events.append(_event(bar, KIND_SEAL, bar.price))
                elif not state and sealed is True:
                    events.append(_event(bar, KIND_OPEN, bar.price))
                sealed = state

            base = series.get(minute - window)
            if base is None or base.price <= 0:
                # 缺口（午休 / 停牌 / 缺样本）：不拿陈旧价格凑涨跌幅
                above_up = above_down = False
                continue
            pct = (bar.price / base.price - 1.0) * 100.0
            if pct >= threshold and not above_up:
                events.append(_event(bar, KIND_THRESHOLD_UP, pct))
            elif pct <= -threshold and not above_down:
                events.append(_event(bar, KIND_THRESHOLD_DOWN, pct))
            above_up = pct >= threshold
            above_down = pct <= -threshold

    return sorted(dedupe(events), key=lambda e: (e.ts, e.code, e.kind))


__all__ = [
    "DEFAULT_SOURCE_DETAIL",
    "KINDS",
    "KIND_LABELS",
    "KIND_OPEN",
    "KIND_SEAL",
    "KIND_THRESHOLD_DOWN",
    "KIND_THRESHOLD_UP",
    "MinuteBar",
    "SEAL_TOLERANCE",
    "SignalInputError",
    "WINDOW_MINUTES",
    "WatchEvent",
    "dedupe",
    "detect_signals",
    "load_bars",
    "parse_bars",
]
