"""`news_stream(date_range, sources, codes?)`：统一资讯流的取数与去重（R6 要求 1）。

链路只有一条：`ProviderRegistry.fetch(dataset)` → adapter 翻译 → 本模块去重合并。
没有「换个地方抓」的分支（DEC-006 闸门 1/2/4），peer 全挂就是 `EXIT_SOURCE`，
不编内容、不拿旧数据顶替当日。

上游能力差异如实保留在 `DatasetStatus.detail` 里，不偷偷抹平：

- `cls_telegraph` / `wallstreetcn_lives` / `eastmoney_global_news` **没有时间窗参数**，
  只有 `limit`：本模块按 limit 取回后在本地按 `published_at` 过滤窗口，
  所以「窗口内为空」有两种含义——真没有，或落在 limit 之外，detail 会说明。
- 巨潮 / 深交所 / 东财个股新闻三个栏目 `code` 必填，不给 `--code` 就不取（记 skipped）。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from ladderstock.core.boundary import NEWS_STREAM_DATASETS, owner_of
from ladderstock.data.provider_registry import ProviderRegistry
from ladderstock.errors import EXIT_OK, EXIT_PARTIAL, EXIT_SOURCE, LadderStockError
from ladderstock.news import normalize
from ladderstock.news import store as news_store
from ladderstock.news.events import NewsEvent, extract_events
from ladderstock.news.models import NewsItem, normalize_title

#: 上游要求 code 的栏目：不给代码就不取（不是取失败，是用法未满足）
CODE_REQUIRED_DATASETS: frozenset[str] = frozenset(
    {"announcement_cninfo", "announcement_szse", "news_eastmoney_stock"}
)

#: code 有意义的栏目：快讯 / 新闻联播按 code 取会打出**完全相同**的请求，
#: 白白多打 peer 一轮，所以这几个只调一次（合并去重照样把跨源同稿收成一条）。
CODE_SCOPED_DATASETS: frozenset[str] = CODE_REQUIRED_DATASETS | {"research_report"}

DEFAULT_LIMIT = 100
MAX_LIMIT = 500
#: 逐日取数的栏目（cctv 只接受单日）最多回溯多少天，避免一条命令打穿 peer
MAX_WINDOW_DAYS = 31

STATUS_OK = "ok"
STATUS_EMPTY = "empty"
STATUS_SKIPPED = "skipped"
STATUS_FAILED = "failed"


@dataclass(frozen=True)
class DatasetStatus:
    """一个栏目这一轮的取数结论。`ok=false` 的行必须自带原因。"""

    dataset: str
    tool: str
    status: str
    items: int
    detail: str
    source: str = ""
    source_detail: str = ""
    freshness: str = ""
    confidence: str = ""
    degraded: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "tool": self.tool,
            "status": self.status,
            "items": self.items,
            "detail": self.detail,
            "source": self.source,
            "source_detail": self.source_detail,
            "freshness": self.freshness,
            "confidence": self.confidence,
            "degraded": self.degraded,
        }


@dataclass
class StreamResult:
    start: date
    end: date
    datasets: tuple[str, ...]
    codes: tuple[str, ...]
    items: list[NewsItem] = field(default_factory=list)
    statuses: list[DatasetStatus] = field(default_factory=list)
    fetched: int = 0
    merged: int = 0
    dropped_undated: int = 0
    outside_window: int = 0
    stored: int = 0
    new_items: int = 0
    known_items: int = 0
    events: int = 0
    extraction: dict[str, Any] = field(default_factory=dict)
    #: 抽取出的事件对象：给 `--ai`（默认关闭）留的入口，不进 `as_dict()`
    event_objects: list[NewsEvent] = field(default_factory=list)
    persisted: bool = False
    exit_code: int = EXIT_OK

    @property
    def window(self) -> tuple[str, str]:
        return (self.start.isoformat(), self.end.isoformat())

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.exit_code == EXIT_OK,
            "domain": "news",
            "topic": "news_stream",
            "window": {"start": self.start.isoformat(), "end": self.end.isoformat()},
            "sources": list(self.datasets),
            "codes": list(self.codes),
            "summary": {
                "items": len(self.items),
                "fetched": self.fetched,
                "new_items": self.new_items,
                "known_items": self.known_items,
                "merged_duplicates": self.merged,
                "dropped_undated": self.dropped_undated,
                "outside_window": self.outside_window,
                "stored": self.stored,
                "events": self.events,
                "persisted": self.persisted,
            },
            "sources_status": [status.as_dict() for status in self.statuses],
            "extraction": dict(self.extraction),
            "items": [item.as_dict() for item in self.items],
            "exit_code": self.exit_code,
        }


# --------------------------------------------------------------------------- #
# 参数装配
# --------------------------------------------------------------------------- #

def _tool_params(
    dataset: str, *, start: date, end: date, code: str | None, limit: int
) -> list[dict[str, Any]]:
    """一个栏目 → 若干次工具调用参数。逐日栏目（cctv）返回多天参数。"""
    if dataset in CODE_REQUIRED_DATASETS and not code:
        return []
    if dataset == "news_cctv":
        days = []
        cursor = start
        while cursor <= end:
            days.append({"date": cursor.isoformat()})
            cursor += timedelta(days=1)
        return days
    if dataset == "announcement_cninfo":
        return [{"code": code, "start": start.isoformat(), "end": end.isoformat(), "limit": limit}]
    if dataset == "announcement_szse":
        return [
            {"code": code, "start": start.isoformat(), "end": end.isoformat(), "page_size": limit}
        ]
    if dataset == "research_report":
        params: dict[str, Any] = {"page_size": limit}
        if code:
            params["code"] = code
        return [params]
    if dataset == "news_eastmoney_stock":
        return [{"code": code, "limit": limit}]
    if dataset == "flash_wallstreetcn":
        return [{"channel": "a-stock", "limit": limit}]
    return [{"limit": limit}]


def resolve_datasets(values: list[str] | None) -> tuple[str, ...]:
    """`--source` 取值必须是登记过的资讯栏目；非法取值是用法错误，不是「忽略」。"""
    if not values:
        return NEWS_STREAM_DATASETS
    resolved: list[str] = []
    unknown: list[str] = []
    for raw in values:
        for part in str(raw).replace(";", ",").split(","):
            name = part.strip()
            if not name:
                continue
            if name not in NEWS_STREAM_DATASETS:
                unknown.append(name)
            elif name not in resolved:
                resolved.append(name)
    if unknown:
        raise LadderStockError(
            f"未知资讯栏目：{', '.join(sorted(set(unknown)))}；"
            f"可选：{', '.join(NEWS_STREAM_DATASETS)}",
            2,
        )
    # 输出顺序按登记顺序，与用户输入顺序无关：同一批来源必须每次同序
    return tuple(name for name in NEWS_STREAM_DATASETS if name in resolved)


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #

def collect_stream(
    *,
    start: date,
    end: date,
    datasets: list[str] | None = None,
    codes: list[str] | None = None,
    registry: ProviderRegistry | None = None,
    root: Path | None = None,
    persist: bool = True,
    limit: int = DEFAULT_LIMIT,
) -> StreamResult:
    """取一个时间窗的资讯，去重合并，（可选）落本机库并抽事件。"""
    if end < start:
        raise LadderStockError(f"时间窗非法：{start} 晚于 {end}", 2)
    if (end - start).days + 1 > MAX_WINDOW_DAYS:
        raise LadderStockError(
            f"时间窗 {start}→{end} 超过单次上限 {MAX_WINDOW_DAYS} 天（不打穿 peer）", 2
        )
    selected = list(resolve_datasets(datasets))
    code_list = [str(c).strip() for c in (codes or []) if str(c).strip()]
    active = registry if registry is not None else ProviderRegistry()
    capped = max(1, min(int(limit), MAX_LIMIT))

    result = StreamResult(start=start, end=end, datasets=tuple(selected), codes=tuple(code_list))
    fetched: list[NewsItem] = []
    for dataset in selected:
        items, status = _collect_one(
            active, dataset, start=start, end=end, codes=code_list, limit=capped
        )
        result.statuses.append(status)
        fetched.extend(items)

    result.fetched = len(fetched)
    existing = _load_existing(active, result, root) if persist else []
    # 「库里已有」按 id 判定：id 只由去重键派生，所以跨轮次可比（DEC-007 的资讯版）
    existing_ids = {item.id for item in existing}
    merged_items, merged_away = dedupe([*existing, *fetched])
    result.merged = merged_away

    in_window: list[NewsItem] = []
    for item in merged_items:
        day = item.day
        if day is None:
            result.dropped_undated += 1
            continue
        if start <= day <= end:
            in_window.append(item)
        else:
            result.outside_window += 1
    in_window.sort(key=lambda item: (item.published_at or "", item.id), reverse=True)
    result.items = in_window

    # 新增 / 已知按**窗口视图**数，不按 `fetched`：fetched 里既有跨源重复、也有窗口外的稿子，
    # 而窗口外的稿子根本不入库——拿它当分母，幂等重跑每轮都会报出「新增 N 条」的假象。
    result.new_items = sum(1 for item in in_window if item.id not in existing_ids)
    result.known_items = len(in_window) - result.new_items

    if persist:
        result.persisted = True
        result.stored = _persist(active, result, in_window, root)
    else:
        report = extract_events(in_window)
        result.events = len(report.events)
        result.event_objects = list(report.events)
        result.extraction = report.as_dict()

    result.exit_code = _exit_code(result.statuses)
    return result


def _collect_one(
    registry: ProviderRegistry,
    dataset: str,
    *,
    start: date,
    end: date,
    codes: list[str],
    limit: int,
) -> tuple[list[NewsItem], DatasetStatus]:
    binding = owner_of(dataset)
    # 非 code 栏目只调一次：`--code` 对它们没有语义，按 code 循环只会打出同样的请求
    targets: list[str | None] = [None]
    if dataset in CODE_SCOPED_DATASETS and codes:
        targets = [code for code in codes]
    items: list[NewsItem] = []
    errors: list[str] = []
    degraded = False
    last: DatasetStatus | None = None

    for code in targets:
        for params in _tool_params(dataset, start=start, end=end, code=code, limit=limit):
            try:
                outcome = registry.fetch(dataset, params=dict(params), trade_date=end)
            except LadderStockError as exc:
                errors.append(str(exc))
                continue
            envelope = outcome.envelope
            provenance = _provenance(envelope, dataset, binding.tool)
            try:
                decoded = normalize.decode(
                    dataset, envelope.payload.get("records"), provenance=provenance
                )
            except KeyError as exc:  # 登记了归属却没有翻译器：施工期错误，如实报错
                errors.append(str(exc))
                continue
            items.extend(decoded)
            degraded = degraded or outcome.degraded
            last = DatasetStatus(
                dataset=dataset,
                tool=binding.tool,
                status=STATUS_OK,
                items=len(items),
                detail="",
                source=outcome.source,
                source_detail=envelope.source_detail,
                freshness=envelope.freshness,
                confidence=envelope.confidence,
                degraded=degraded,
            )

    if last is not None:
        if not items:
            return [], replace(
                last,
                status=STATUS_EMPTY,
                items=0,
                detail="上游返回 0 条；该栏目无时间窗参数时也可能是 limit 之外的窗口没稿子",
            )
        if errors:
            return items, replace(
                last,
                status=STATUS_OK,
                items=len(items),
                degraded=True,
                detail=f"部分调用失败：{errors[0]}"[:200],
            )
        return items, replace(last, items=len(items))

    if errors:
        return [], DatasetStatus(
            dataset=dataset,
            tool=binding.tool,
            status=STATUS_FAILED,
            items=0,
            detail=errors[0][:200],
            source=binding.peer,
            degraded=True,
        )
    return [], DatasetStatus(
        dataset=dataset,
        tool=binding.tool,
        status=STATUS_SKIPPED,
        items=0,
        detail=f"{dataset} 需要指定 code（--code <code>[,<code>]）",
    )


def _provenance(envelope: Any, dataset: str, tool: str) -> dict[str, Any]:
    """来源标签（DEC-018 R4 硬门槛）：栏目、工具、置信度、抓取时间、trace_id。"""
    return {
        "dataset": dataset,
        "tool": tool,
        "peer": owner_of(dataset).peer,
        "confidence": envelope.confidence,
        "freshness": envelope.freshness,
        "fetched_at": envelope.fetched_at.isoformat(),
        "data_as_of": envelope.data_as_of.isoformat() if envelope.data_as_of else None,
        "trace_id": envelope.event_id,
        "source_detail": envelope.source_detail,
    }


def _load_existing(
    registry: ProviderRegistry, result: StreamResult, root: Path | None
) -> list[NewsItem]:
    conn = news_store.connect(root, create=False)
    if conn is None:
        return []
    try:
        return news_store.load_items(
            conn,
            start=result.start,
            end=result.end,
            sources=list(result.datasets),
        )
    finally:
        news_store.close(conn)


def _persist(
    registry: ProviderRegistry,
    result: StreamResult,
    items: list[NewsItem],
    root: Path | None,
) -> int:
    conn = news_store.connect(root)
    assert conn is not None  # create=True
    try:
        written = news_store.upsert_items(conn, items)
        report = extract_events(items)
        news_store.upsert_events(conn, report.events)
        result.events = len(report.events)
        result.event_objects = list(report.events)
        result.extraction = report.as_dict()
        news_store.record_run(
            conn,
            window=(result.start, result.end),
            datasets=list(result.datasets),
            codes=list(result.codes),
            fetched=result.fetched,
            stored=written,
            deduped=result.merged,
            events=result.events,
            detail={"sources_status": [s.as_dict() for s in result.statuses]},
        )
        return written
    finally:
        news_store.close(conn)


def persist_events(events: list[NewsEvent], *, root: Path | None = None) -> int:
    """把（可能被 `--ai` 补过叙述的）事件写回本机库。幂等：同 id 覆盖，不新增行。"""
    if not events:
        return 0
    conn = news_store.connect(root)
    assert conn is not None
    try:
        return news_store.upsert_events(conn, events)
    finally:
        news_store.close(conn)


def _exit_code(statuses: list[DatasetStatus]) -> int:
    counts = Counter(status.status for status in statuses)
    failed = counts.get(STATUS_FAILED, 0)
    succeeded = counts.get(STATUS_OK, 0) + counts.get(STATUS_EMPTY, 0)
    if failed and succeeded:
        return EXIT_PARTIAL
    if failed:
        return EXIT_SOURCE
    return EXIT_OK


# --------------------------------------------------------------------------- #
# 去重合并
# --------------------------------------------------------------------------- #

def dedupe(items: list[NewsItem]) -> tuple[list[NewsItem], int]:
    """按「规范化 url 优先，其次归一化标题 + 同一天」合并同稿；返回合并后列表与被并掉的条数。

    用并查集而不是字典：同一篇稿子可能 A 与 B 同标题、B 与 C 同地址，
    三次两两匹配要收成一组，否则留两条。

    标题匹配必须**再加一天**：实测平安银行有两份《董事会决议公告》，
    2026-10-24 与 2026-09-30 各一份，url 不同、标题归一化后完全一样——
    只按标题合并就会吃掉一份真公告。跨源同稿总是同天转发，
    所以「同标题 + 同发布日」既保住合并率又不吞公告。
    """
    if not items:
        return [], 0
    parent = list(range(len(items)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            # 组代表取靠前者 = 登记顺序靠前的栏目（公告优先于快讯）
            parent[max(root_left, root_right)] = min(root_left, root_right)

    by_url: dict[str, int] = {}
    by_title: dict[tuple[str, str], int] = {}
    for index, item in enumerate(items):
        if item.url:
            key = item.dedup_key if item.dedup_key.startswith("url:") else f"url:{item.url}"
            slot = by_url.get(key)
            if slot is None:
                by_url[key] = index
            else:
                union(slot, index)
        if item.title:
            title_key = normalize_title(item.title)
            slot = by_title.get((title_key, item.day.isoformat() if item.day else ""))
            if slot is None:
                by_title[(title_key, item.day.isoformat() if item.day else "")] = index
            else:
                union(slot, index)

    groups: dict[int, list[int]] = {}
    for index in range(len(items)):
        groups.setdefault(find(index), []).append(index)

    merged: list[NewsItem] = []
    for root_index, members in groups.items():
        members.sort(key=lambda index: (index != root_index, index))
        merged.append(_merge_group([items[index] for index in members]))
    merged.sort(key=lambda item: (item.published_at or "", item.id), reverse=True)
    return merged, len(items) - len(merged)


def _merge_group(group: list[NewsItem]) -> NewsItem:
    head = group[0]
    origins: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    codes: list[str] = []
    for item in group:
        fallback_origin = {"dataset": item.source, "title": item.title, "url": item.url}
        for origin in item.origins or (fallback_origin,):
            marker = (str(origin.get("dataset") or ""), str(origin.get("url") or ""))
            if marker in seen:
                continue
            seen.add(marker)
            origins.append(dict(origin))
        for code in item.related_codes:
            if code not in codes:
                codes.append(code)
    summary = head.summary
    for item in group[1:]:
        if len(item.summary) > len(summary):
            summary = item.summary
    published_at = head.published_at
    if published_at is None:
        published_at = next((item.published_at for item in group[1:] if item.published_at), None)
    return replace(
        head,
        origins=tuple(origins),
        related_codes=tuple(codes),
        summary=summary,
        published_at=published_at,
    )
