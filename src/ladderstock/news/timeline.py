"""按日的公司事件时间轴（QUOT-436）：自选 + 持仓 × 未来 7 / 30 / 90 天。

只排**来源写出来的日期**：

- `lockup_calendar`：结构化解禁日历（上游 history / upcoming 两个数组）；
- `earnings_preview`：业绩预告，`notice_date` 是它自己的披露日；
- 本机 `news.sqlite` 里 `date_basis="evidence_text"` 的公告抽取——原文写了几号，
  就是几号（股东会通知、限售上市流通提示这类）。

「财报预约披露日 / 股东大会 / 政策发布窗口」在本仓**没有数据源**，只进 `gaps`，
原样沿用 `news/calendar.py` 的 `NO_SOURCE_SECTIONS` 口径：不估算、不拿
`notice_date` / `report_date` 冒充预约披露日。

取数失败是「没查到」，不是「没有事件」：该来源写进 `gaps`，响应仍是 200，
一条假日期都不补（DEC-018 R6 / DEC-030 §4）。

`overdue` 是本轮同样查过的、日期已经过去的事件（回看窗口与用户选的天数一致）。
本仓没有「已处理」标记，所以它表达的是「日子过了、来源里还是这件事」，
不替用户判断处理没处理。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ladderstock.core.portfolio import replay as replay_portfolio
from ladderstock.data.provider_registry import ProviderRegistry
from ladderstock.errors import LadderStockError
from ladderstock.journal.portfolio import list_trades as list_portfolio_trades
from ladderstock.journal.watchlist import list_watch
from ladderstock.news import store as news_store
from ladderstock.news.calendar import CALENDAR_EVENT_TYPES, NO_SOURCE_SECTIONS
from ladderstock.news.models import normalize_code

#: 页面只给这三档；别的值都是调用方写错（接口层 422）
TIMELINE_DAYS: tuple[int, ...] = (7, 30, 90)
DEFAULT_DAYS = 30

#: 时间轴按北京时间日期排（上游与用户都在 +08）
BEIJING = timezone(timedelta(hours=8))

SOURCE_LOCKUP = "lockup_calendar"
SOURCE_EARNINGS = "earnings_preview"
SOURCE_DISCLOSURE = "announcement_extraction"
DISCLOSURE_DATASET_LABEL = "news_events"

KIND_LOCKUP = "lockup"
KIND_EARNINGS = "earnings"
KIND_DISCLOSURE = "disclosure"

#: 公告抽取的类型 → 时间轴的三类 kind；没有映射的类型不产出
DISCLOSURE_KIND_BY_TYPE: dict[str, str] = {
    "lockup_expiry": KIND_LOCKUP,
    "earnings_preview": KIND_EARNINGS,
    "shareholder_meeting": KIND_DISCLOSURE,
    "policy": KIND_DISCLOSURE,
}

#: 一次最多读回多少条抽取事件（窗口最长 90 天，正常远小于这个数）
EVENT_READ_MAX = 2000
#: 业绩预告没有时间窗参数，取回后本地按 notice_date 过滤（与 R6 日历同一口径）
EARNINGS_PREVIEW_LIMIT = 200

#: 解禁上游按「相对 trade_date 往后 N 天」返回；给窗口留一点余量，上限守住 365
LOCKUP_MARGIN_DAYS = 15
LOCKUP_FORWARD_CAP = 365


@dataclass
class SymbolRef:
    """一只标的：本机自选与持仓账本的并集，`origin` 说清它为什么在列表里。"""

    symbol: str
    name: str = ""
    origin: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "name": self.name, "origin": list(self.origin)}


def parse_timeline_days(raw: Any) -> int:
    """`days` 只允许 7 / 30 / 90；缺省 30，其余（含非整数）由调用方转 422。"""
    if raw is None:
        return DEFAULT_DAYS
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(f"days 只能是 7 / 30 / 90，收到 {raw!r}")
    if raw not in TIMELINE_DAYS:
        raise ValueError(f"days 只能是 7 / 30 / 90，收到 {raw}")
    return raw


def beijing_today() -> date:
    return datetime.now(UTC).astimezone(BEIJING).date()


def build_timeline(
    days: int,
    *,
    today: date | None = None,
    root: Path | None = None,
    registry: ProviderRegistry | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """组装一条按日时间轴。`today` / `now` 只给测试冻结时间用。"""
    if days not in TIMELINE_DAYS:
        raise ValueError(f"days 只能是 7 / 30 / 90，收到 {days}")
    start = today or beijing_today()
    end = start + timedelta(days=days)
    lookback = start - timedelta(days=days)

    refs = _collect_symbols(root)
    gaps = [_gap_text(gap) for gap in NO_SOURCE_SECTIONS]
    failures: list[str] = []

    active = registry if registry is not None else ProviderRegistry()
    events: list[dict[str, Any]] = []
    overdue: list[dict[str, Any]] = []

    if refs:
        lockup_events, lockup_past, lockup_errors = _lockup_events(
            refs, active, start=start, end=end, lookback=lookback
        )
        events.extend(lockup_events)
        overdue.extend(lockup_past)
        if lockup_errors:
            failures.append(_failure_gap("解禁日", SOURCE_LOCKUP, lockup_errors))

        earnings_events, earnings_past, earnings_errors = _earnings_events(
            refs, active, start=start, end=end, lookback=lookback
        )
        events.extend(earnings_events)
        overdue.extend(earnings_past)
        if earnings_errors:
            failures.append(_failure_gap("业绩预告", SOURCE_EARNINGS, earnings_errors))

        disclosure_events, disclosure_past, disclosure_gap, disclosure_failed = _disclosure_events(
            refs, root, start=start, end=end, lookback=lookback
        )
        events.extend(disclosure_events)
        overdue.extend(disclosure_past)
        if disclosure_gap:
            gaps.append(disclosure_gap)
        if disclosure_failed:
            failures.append(disclosure_gap or "公告抽取：本轮读取本机资讯库失败")

    gaps.extend(failures)

    # 稳定排序：按日期升序，同一天保持「解禁 → 业绩预告 → 公告抽取」的来源顺序
    events.sort(key=lambda item: str(item["date"]))
    overdue.sort(key=lambda item: str(item["date"]))

    status = "missing" if not refs else ("partial" if failures else "ok")
    return {
        "local_only": True,
        "store": "local_sqlite",
        "window": {"from": start.isoformat(), "to": end.isoformat(), "days": days},
        "generated_at": (now or datetime.now(UTC)).isoformat(),
        "symbols": [ref.as_dict() for ref in refs],
        "events": events,
        "overdue": overdue,
        "gaps": gaps,
        "data_source_status": status,
        "notice": (
            "只列来源给出的日期；没查到的写进 gaps，时间轴没有不代表真的没有（DEC-018 R6）"
        ),
    }


# --------------------------------------------------------------------------- #
# 标的集合：自选 + 持仓（账本重放后数量 > 0）
# --------------------------------------------------------------------------- #

def _collect_symbols(root: Path | None) -> list[SymbolRef]:
    refs: dict[str, SymbolRef] = {}

    for row in list_watch(root=root):
        code = normalize_code(row.get("code"))
        if not code:
            continue
        ref = refs.setdefault(code, SymbolRef(symbol=code))
        if "自选" not in ref.origin:
            ref.origin.append("自选")

    trading = list_portfolio_trades(root=root)
    result = replay_portfolio(trading, "average")
    for holding in result.holdings:
        code = normalize_code(holding.symbol)
        if not code or holding.shares <= 0:
            continue
        ref = refs.setdefault(code, SymbolRef(symbol=code))
        if holding.name and not ref.name:
            ref.name = str(holding.name)
        if "持有" not in ref.origin:
            ref.origin.append("持有")

    return list(refs.values())


# --------------------------------------------------------------------------- #
# 解禁 / 业绩预告（结构化源）
# --------------------------------------------------------------------------- #

def _lockup_events(
    refs: Sequence[SymbolRef],
    registry: ProviderRegistry,
    *,
    start: date,
    end: date,
    lookback: date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    span = (end - start).days
    forward = max(TIMELINE_DAYS[0], min(span + LOCKUP_MARGIN_DAYS, LOCKUP_FORWARD_CAP))
    events: list[dict[str, Any]] = []
    past: list[dict[str, Any]] = []
    errors: list[str] = []

    for ref in refs:
        try:
            outcome = registry.fetch(
                SOURCE_LOCKUP,
                params={
                    "code": ref.symbol,
                    "trade_date": start.isoformat(),
                    "forward_days": forward,
                },
                trade_date=start,
            )
        except LadderStockError as exc:
            errors.append(str(exc))
            continue

        envelope = outcome.envelope
        fetched_at = envelope.fetched_at.isoformat()
        for row in _lockup_rows(envelope.payload.get("records")):
            day = _day_of(row.get("date"))
            if day is None or not (lookback <= day <= end):
                continue
            lift_type = _text(row, "type") or "未注明类型"
            detail = _lockup_detail(row)
            item = _event(
                date_=day,
                kind=KIND_LOCKUP,
                ref=ref,
                title=f"限售解禁：{lift_type}",
                detail=detail,
                source=SOURCE_LOCKUP,
                fetched_at=fetched_at,
                ref_id=f"{SOURCE_LOCKUP}:{day.isoformat()}:{ref.symbol}:{lift_type}",
            )
            (events if day >= start else past).append(item)

    return events, past, errors


def _lockup_rows(data: Any) -> list[dict[str, Any]]:
    """上游把 history / upcoming 分开返回；两段都是「来源给的日期」。"""
    if isinstance(data, Mapping):
        rows: list[dict[str, Any]] = []
        for key in ("history", "upcoming"):
            value = data.get(key)
            if isinstance(value, list):
                rows.extend(dict(item) for item in value if isinstance(item, Mapping))
        return rows
    if isinstance(data, list):
        return [dict(item) for item in data if isinstance(item, Mapping)]
    return []


def _lockup_detail(row: Mapping[str, Any]) -> list[str]:
    detail: list[str] = []
    for label, key in (("可流通(万股)", "able_shares_10k"), ("占总股本", "ratio_pct")):
        value = row.get(key)
        if value is not None:
            detail.append(f"{label} {value}")
    return detail


def _earnings_events(
    refs: Sequence[SymbolRef],
    registry: ProviderRegistry,
    *,
    start: date,
    end: date,
    lookback: date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    events: list[dict[str, Any]] = []
    past: list[dict[str, Any]] = []
    errors: list[str] = []

    for ref in refs:
        try:
            outcome = registry.fetch(
                SOURCE_EARNINGS,
                params={"code": ref.symbol, "limit": EARNINGS_PREVIEW_LIMIT},
                trade_date=start,
            )
        except LadderStockError as exc:
            errors.append(str(exc))
            continue

        envelope = outcome.envelope
        fetched_at = envelope.fetched_at.isoformat()
        for row in _rows(envelope.payload.get("records")):
            # notice_date 是「业绩预告的披露日」，不是财报预约披露日——两件事不混用
            day = _day_of(row.get("notice_date"))
            if day is None or not (lookback <= day <= end):
                continue
            forecast = _text(row, "forecast_type")
            indicator = _text(row, "indicator")
            content = _text(row, "content")
            title = f"业绩预告：{forecast}" if forecast else "业绩预告"
            detail = [part for part in (indicator, content) if part]
            item = _event(
                date_=day,
                kind=KIND_EARNINGS,
                ref=ref,
                title=title,
                detail=detail,
                source=SOURCE_EARNINGS,
                fetched_at=fetched_at,
                ref_id=f"{SOURCE_EARNINGS}:{day.isoformat()}:{ref.symbol}:{indicator}",
            )
            (events if day >= start else past).append(item)

    return events, past, errors


# --------------------------------------------------------------------------- #
# 公告抽取：只收原文写了日期的条目
# --------------------------------------------------------------------------- #

def _disclosure_events(
    refs: Sequence[SymbolRef],
    root: Path | None,
    *,
    start: date,
    end: date,
    lookback: date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None, bool]:
    """返回 (窗口内, 过去, gap, 是否读取失败)。

    本机还没跑过资讯采集（库不存在）是「还没有公告」，不是取数失败：
    写进 gaps，但不把整条时间轴降级成 partial。
    """
    by_code = {ref.symbol: ref for ref in refs}
    conn = news_store.connect(root, create=False)
    if conn is None:
        return (
            [],
            [],
            (
                f"公告抽取（{DISCLOSURE_DATASET_LABEL}）：本机还没有资讯库，"
                "本轮没有可用的公告事件（先跑一次 ldst news <date>）"
            ),
            False,
        )
    try:
        rows = news_store.list_events(
            conn, start=lookback, end=end, limit=EVENT_READ_MAX
        )
    except sqlite3.Error as exc:
        return (
            [],
            [],
            f"公告抽取（{DISCLOSURE_DATASET_LABEL}）：本机资讯库读取失败，{exc}；没有补日期",
            True,
        )
    finally:
        news_store.close(conn)

    events: list[dict[str, Any]] = []
    past: list[dict[str, Any]] = []
    for row in rows:
        if row["type"] not in CALENDAR_EVENT_TYPES:
            continue
        # 原文没写日期的不进时间轴：published_at 是「什么时候被我们看到」，不是事件日
        if row["date_basis"] != "evidence_text":
            continue
        day = _day_of(row.get("event_date"))
        if day is None or not (lookback <= day <= end):
            continue
        matched = next(
            (code for code in row.get("codes") or [] if code in by_code), None
        )
        if matched is None:
            continue

        kind = DISCLOSURE_KIND_BY_TYPE.get(str(row["type"]), KIND_DISCLOSURE)
        evidence = row.get("evidence") or {}
        snippet = _text(evidence, "snippet")
        dataset = _text(evidence, "dataset") or DISCLOSURE_DATASET_LABEL
        detail = [f"原文：{snippet}"] if snippet else []
        item = _event(
            date_=day,
            kind=kind,
            ref=by_code[matched],
            title=str(row.get("title") or row.get("label") or "公告事件"),
            detail=detail,
            source=dataset,
            # 抽取事件的取数时刻没有落库：如实留空，不拿发布时间顶替
            fetched_at=None,
            ref_id=str(row.get("id") or ""),
        )
        (events if day >= start else past).append(item)

    return events, past, None, False


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #

def _event(
    *,
    date_: date,
    kind: str,
    ref: SymbolRef,
    title: str,
    detail: Sequence[str],
    source: str,
    fetched_at: str | None,
    ref_id: str,
) -> dict[str, Any]:
    return {
        "date": date_.isoformat(),
        "kind": kind,
        "symbol": ref.symbol,
        "name": ref.name,
        "title": title,
        "detail": list(detail),
        "source": source,
        "fetched_at": fetched_at,
        # 三类来源都是实际日期；上游没有「预估」标记，就不把任何一条标成预估
        "estimated": False,
        "ref": ref_id,
    }


def _gap_text(gap: Mapping[str, str]) -> str:
    return f"{gap['label']}：{gap['reason']}（补源：{gap['ask']}）"


def _failure_gap(label: str, dataset: str, errors: Sequence[str]) -> str:
    first = errors[0] if errors else "上游取数失败"
    extra = f"（共 {len(errors)} 只标的失败）" if len(errors) > 1 else ""
    return f"{label}（{dataset}）：本轮取数失败，{first}{extra}；没有补日期"


def _rows(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [dict(item) for item in data if isinstance(item, Mapping)]
    if isinstance(data, Mapping):
        records = data.get("records")
        if isinstance(records, list):
            return [dict(item) for item in records if isinstance(item, Mapping)]
    return []


def _day_of(raw: Any) -> date | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _text(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


__all__ = [
    "DEFAULT_DAYS",
    "TIMELINE_DAYS",
    "build_timeline",
    "beijing_today",
    "parse_timeline_days",
]
