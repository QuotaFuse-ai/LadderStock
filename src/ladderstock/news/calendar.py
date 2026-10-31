"""`event_calendar(month)`：财报披露日 / 解禁日 / 股东大会 / 政策发布窗口（R6 要求 3）。

**这一模块最重要的一条是「没有源就明说」**（DEC-018 R6，总经理 2026-10-31 复核口径）：

- 解禁日：TickHub-CN 有 `lockup_expiry`，是**真数据源**，但上游要求 `code`；
- 业绩预告：TickHub-CN 有 `earnings_forecast`，是真数据源，**但它不是「财报预约披露日」**——
  `notice_date` 是业绩预告自己的披露日，`report_date` 是报告期，两者都不能当成财报预约日期用；
- 财报预约披露日 / 股东大会 / 政策发布窗口：**当前没有数据源**，对应小节
  `status="no_source"`、`entries=[]`，只写「暂无数据源」和补齐路径，绝不估算、不排日期。
  `macro_calendar` 是宏观指标日历，与「政策发布窗口」不是一回事，不拿它顶包。

公告正文里真写了日期的那部分（股东大会通知、解禁提示），走**规则抽取**这条路，
落在 `extracted_events` 区块，每条自带原文片段 + url（`events.NewsEvent` 的硬约束）。
它是「可审计的抽取结果」，不是日历数据源，所以不会出现在上面三个小节里冒充数据。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from ladderstock.core.boundary import NEWS_CALENDAR_DATASETS, owner_of
from ladderstock.data.provider_registry import ProviderRegistry
from ladderstock.errors import EXIT_OK, EXIT_PARTIAL, EXIT_SOURCE, LadderStockError
from ladderstock.news import store as news_store
from ladderstock.news.models import normalize_code

#: 有数据源的小节：dataset → 上游要求
SOURCED_SECTIONS: dict[str, str] = {
    "lockup_expiry": "lockup_calendar",
    "earnings_preview": "earnings_preview",
}

#: DEC-018 R6 明确的三个缺口：只能写「暂无数据源」
NO_SOURCE_SECTIONS: tuple[dict[str, str], ...] = (
    {
        "key": "earnings_disclosure",
        "label": "财报预约披露日",
        "reason": (
            "TickHub-CN 没有「预约披露时间表」工具；"
            "业绩预告的 notice_date 不是预约披露日，不能替代"
        ),
        "ask": (
            "上游需求：th-cn 增加 em_income_summary / "
            "交易所预约披露时间表（earnings_appointment_calendar）"
        ),
    },
    {
        "key": "shareholder_meeting",
        "label": "股东大会",
        "reason": (
            "TickHub-CN 没有股东大会工具；公告正文里写了会议日期的，"
            "只作为带证据的抽取结果出现在 extracted_events"
        ),
        "ask": "上游需求：th-cn 增加 cninfo 股东大会通知专项（category=股东大会）",
    },
    {
        "key": "policy_window",
        "label": "政策发布窗口",
        "reason": "没有官方发布日程源；macro_calendar 是宏观指标日历，口径不同，不做顶包",
        "ask": "上游需求：国务院/证监会「政策发布日程」类工具，或由 Wire 侧提供带来源的日程条目",
    },
)

SECTION_ORDER: tuple[str, ...] = (
    "earnings_disclosure",
    "lockup_expiry",
    "shareholder_meeting",
    "policy_window",
    "earnings_preview",
)

STATUS_SOURCED = "sourced"
STATUS_NO_SOURCE = "no_source"

#: 抽取事件里与日历口径相关、可以进日历的类型
CALENDAR_EVENT_TYPES: tuple[str, ...] = (
    "lockup_expiry",
    "shareholder_meeting",
    "earnings_preview",
    "policy",
)

DEFAULT_LIMIT = 200
MAX_LIMIT = 1000


@dataclass
class CalendarSection:
    """日历的一节。`no_source` 与「有源但为空」是两件事，不能混成一个空列表。"""

    key: str
    label: str
    status: str
    data_source: str = ""
    tool: str = ""
    entries: list[dict[str, Any]] = field(default_factory=list)
    note: str = ""
    reason: str = ""
    ask: str = ""
    degraded: bool = False

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "key": self.key,
            "label": self.label,
            "status": self.status,
            "entries": list(self.entries),
            "note": self.note,
        }
        if self.status == STATUS_SOURCED:
            payload["data_source"] = self.data_source
            payload["tool"] = self.tool
            payload["degraded"] = self.degraded
        else:
            # 暂无数据源：把「为什么没有」和「怎么补上」一起交给页面（R10 要显式标注）
            payload["reason"] = self.reason
            payload["ask"] = self.ask
        return payload


@dataclass
class CalendarResult:
    month: str
    window: tuple[str, str]
    codes: tuple[str, ...]
    sections: list[CalendarSection] = field(default_factory=list)
    extracted_events: list[dict[str, Any]] = field(default_factory=list)
    exit_code: int = EXIT_OK

    @property
    def no_source_sections(self) -> list[str]:
        return [s.key for s in self.sections if s.status == STATUS_NO_SOURCE]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.exit_code == EXIT_OK,
            "domain": "news",
            "topic": "event_calendar",
            "month": self.month,
            "window": {"start": self.window[0], "end": self.window[1]},
            "codes": list(self.codes),
            "summary": {
                "sections": len(self.sections),
                "entries": sum(len(s.entries) for s in self.sections),
                "extracted_events": len(self.extracted_events),
                "no_source": self.no_source_sections,
            },
            "sections": [s.as_dict() for s in self.sections],
            "extracted_events": list(self.extracted_events),
            "notice": "no_source 小节为「暂无数据源」：不估算、不编日期（DEC-018 R6）",
            "exit_code": self.exit_code,
        }


def parse_month(value: str) -> tuple[date, date]:
    """`YYYY-MM` → 该月首末日。非法输入是用法错误（exit 2），不是「当作今天」。"""
    text = str(value or "").strip()
    parts = text.split("-")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise LadderStockError(f"月份格式应为 YYYY-MM，收到 {value!r}", 2)
    year, month = int(parts[0]), int(parts[1])
    if not 1 <= month <= 12:
        raise LadderStockError(f"月份超出范围：{value!r}", 2)
    start = date(year, month, 1)
    following = date(year + month // 12, month % 12 + 1, 1)
    return start, following - timedelta(days=1)


def _clean_codes(codes: list[str] | None) -> list[str]:
    out: list[str] = []
    for raw in codes or []:
        for part in str(raw).replace(";", ",").split(","):
            code = normalize_code(part)
            if code and code not in out:
                out.append(code)
    return out


def build_calendar(
    month: str,
    *,
    codes: list[str] | None = None,
    registry: ProviderRegistry | None = None,
    root: Path | None = None,
    fetch: bool = True,
    limit: int = DEFAULT_LIMIT,
) -> CalendarResult:
    """组装一个月的日历：真源小节 + 三个「暂无数据源」小节 + 带证据的抽取事件。"""
    start, end = parse_month(month)
    code_list = _clean_codes(codes)
    result = CalendarResult(
        month=start.strftime("%Y-%m"),
        window=(start.isoformat(), end.isoformat()),
        codes=tuple(code_list),
    )
    active = registry if registry is not None else ProviderRegistry()
    capped = max(1, min(int(limit), MAX_LIMIT))

    for key in SECTION_ORDER:
        missing = next((s for s in NO_SOURCE_SECTIONS if s["key"] == key), None)
        if missing is not None:
            result.sections.append(
                CalendarSection(
                    key=missing["key"],
                    label=missing["label"],
                    status=STATUS_NO_SOURCE,
                    reason=missing["reason"],
                    ask=missing["ask"],
                    note="暂无数据源",
                )
            )
            continue
        dataset = SOURCED_SECTIONS[key]
        section = CalendarSection(
            key=key,
            label=_section_label(key),
            status=STATUS_SOURCED,
            data_source=dataset,
            tool=owner_of(dataset).tool,
        )
        if fetch:
            _fill_section(
                section, active, dataset, start=start, end=end, codes=code_list, limit=capped
            )
        else:
            section.note = "本轮未取数（--no-fetch）"
        result.sections.append(section)

    result.extracted_events = _extracted_events(
        root, month=start.strftime("%Y-%m"), codes=code_list
    )
    result.exit_code = _exit_code(result.sections)
    return result


def _section_label(key: str) -> str:
    return {"lockup_expiry": "解禁日", "earnings_preview": "业绩预告"}[key]


# --------------------------------------------------------------------------- #
# 有源小节
# --------------------------------------------------------------------------- #

def _fill_section(
    section: CalendarSection,
    registry: ProviderRegistry,
    dataset: str,
    *,
    start: date,
    end: date,
    codes: list[str],
    limit: int,
) -> None:
    if dataset == "lockup_calendar" and not codes:
        # 上游 code 必填：没有代码就没有「全市场解禁」这条路，如实说明而不是留个空节
        section.status = STATUS_NO_SOURCE
        section.reason = "lockup_expiry 上游要求 code：本轮未指定 --code，无法给出全市场解禁日"
        section.ask = "指定 --code <code>[,<code>] 后重试；全市场解禁日历需要上游加无 code 分支"
        section.note = "暂无数据源（未指定 code）"
        return

    params: list[dict[str, Any]]
    if dataset == "lockup_calendar":
        params = [
            {
                "code": code,
                "trade_date": start.isoformat(),
                "forward_days": _forward_days(start, end),
            }
            for code in codes
        ]
    else:
        targets = codes or [""]
        params = [{"code": code, "limit": limit} for code in targets]

    errors: list[str] = []
    for call in params:
        try:
            outcome = registry.fetch(dataset, params=dict(call), trade_date=end)
        except LadderStockError as exc:
            errors.append(str(exc))
            continue
        records = outcome.envelope.payload.get("records")
        section.entries.extend(
            _translate(
                dataset,
                records,
                start=start,
                end=end,
                provenance=_provenance(outcome, dataset),
                # 上游把 code 放在信封上、行里没有（实测 lockup 的 history/upcoming 就是这样）：
                # 请求是谁的代码，条目就是谁的代码，否则日历上会出现「不知哪只票的解禁」
                requested_code=str(call.get("code") or ""),
            )
        )
        section.degraded = section.degraded or bool(outcome.degraded)

    if errors and not section.entries:
        section.status = STATUS_NO_SOURCE
        section.reason = f"取数失败，且没有可用的历史结果：{errors[0]}"[:200]
        section.ask = "确认 TickHub-CN 在线（ldst sources validate）"
        section.note = "暂无数据源（本轮取数失败）"
        section.degraded = True
        return
    if errors:
        section.degraded = True
        section.note = f"部分 code 取数失败：{errors[0]}"[:200]
    elif dataset == "earnings_preview":
        section.note = (
            "earnings_forecast 无时间窗参数：取回前 N 条后按 notice_date 本地过滤，"
            "窗口内为空不等于当月没有预告"
        )
    section.entries.sort(
        key=lambda row: (
            str(row.get("date") or ""),
            str(row.get("code") or ""),
            str(row.get("id") or ""),
        )
    )


def _forward_days(start: date, end: date) -> int:
    span = (end - start).days + 1
    # 上游按「相对 trade_date 往后 N 天」返回；覆盖整月再加一点余量，上限守住 365
    return max(30, min(span + 15, 365))


def _provenance(outcome: Any, dataset: str) -> dict[str, Any]:
    envelope = outcome.envelope
    return {
        "dataset": dataset,
        "tool": owner_of(dataset).tool,
        "peer": owner_of(dataset).peer,
        "confidence": envelope.confidence,
        "fetched_at": envelope.fetched_at.isoformat(),
        "trace_id": envelope.event_id,
        "source_detail": envelope.source_detail,
    }


def _translate(
    dataset: str,
    data: Any,
    *,
    start: date,
    end: date,
    provenance: dict[str, Any],
    requested_code: str = "",
) -> list[dict[str, Any]]:
    """上游记录 → 日历条目：只保留落在本月的、且日期是上游写出来的。"""
    rows = _rows_for_calendar(dataset, data)
    entries: list[dict[str, Any]] = []
    for row in rows:
        day = _entry_date(dataset, row)
        if day is None or not (start <= day <= end):
            continue
        code = _text(row, "code") or requested_code
        kind = _text(row, "type", "indicator", "forecast_type")
        entry: dict[str, Any] = {
            "id": f"{dataset}:{day.isoformat()}:{code}:{kind}",
            "date": day.isoformat(),
            "code": code,
            "section": "lockup_expiry" if dataset == "lockup_calendar" else "earnings_preview",
            "label": _entry_label(dataset, row),
            "detail": _entry_detail(dataset, row),
            "source": provenance["dataset"],
            "provenance": dict(provenance),
        }
        entries.append(entry)
    return entries


def _rows_for_calendar(dataset: str, data: Any) -> list[dict[str, Any]]:
    if isinstance(data, Mapping):
        keys = ("history", "upcoming") if dataset == "lockup_calendar" else ()
        rows: list[dict[str, Any]] = []
        for key in keys:
            value = data.get(key)
            if isinstance(value, list):
                rows.extend(dict(item) for item in value if isinstance(item, Mapping))
        return rows
    if isinstance(data, list):
        return [dict(item) for item in data if isinstance(item, Mapping)]
    return []


def _entry_date(dataset: str, row: Mapping[str, Any]) -> date | None:
    key = "date" if dataset == "lockup_calendar" else "notice_date"
    raw = str(row.get(key) or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _entry_label(dataset: str, row: Mapping[str, Any]) -> str:
    if dataset == "lockup_calendar":
        return f"限售解禁：{_text(row, 'type') or '未注明类型'}"
    name = _text(row, "name")
    forecast = _text(row, "forecast_type")
    indicator = _text(row, "indicator")
    return " ".join(part for part in (name, forecast, indicator) if part)


def _entry_detail(dataset: str, row: Mapping[str, Any]) -> str:
    if dataset == "lockup_calendar":
        parts = []
        for label, key in (("可流通(万股)", "able_shares_10k"), ("占总股本", "ratio_pct")):
            value = row.get(key)
            if value is not None:
                parts.append(f"{label} {value}")
        return "；".join(parts)
    return _text(row, "content")


def _text(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


# --------------------------------------------------------------------------- #
# 带证据的抽取事件
# --------------------------------------------------------------------------- #

def _extracted_events(root: Path | None, *, month: str, codes: list[str]) -> list[dict[str, Any]]:
    conn = news_store.connect(root, create=False)
    if conn is None:
        return []
    try:
        rows = news_store.list_events(conn, month=month)
    finally:
        news_store.close(conn)
    out: list[dict[str, Any]] = []
    for row in rows:
        if row["type"] not in CALENDAR_EVENT_TYPES:
            continue
        if codes and not set(codes) & set(row["codes"]):
            continue
        out.append(
            {
                "id": row["id"],
                "type": row["type"],
                "label": row["label"],
                "date": row["event_date"],
                "title": row["title"],
                "codes": row["codes"],
                "evidence": row["evidence"],
                "date_basis": row["date_basis"],
                "extractor": row["extractor"],
                "basis": "rule_extracted",
                "note": "规则抽取，非日历数据源；日期取自原文或发布时间，见 date_basis",
            }
        )
    return out


def _exit_code(sections: list[CalendarSection]) -> int:
    failed = [s for s in sections if s.status == STATUS_NO_SOURCE and s.degraded]
    sourced = [s for s in sections if s.status == STATUS_SOURCED]
    if failed and sourced:
        return EXIT_PARTIAL
    if failed and not sourced:
        return EXIT_SOURCE
    return EXIT_OK


def calendar_datasets() -> tuple[str, ...]:
    return NEWS_CALENDAR_DATASETS
