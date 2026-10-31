"""TickHub-CN 各栏目返回 → `NewsItem`：形状翻译层（DEC-006 闸门 2 的资讯版）。

只做翻译，不做补救：字段缺失就是缺失（`url` 为空、`published_at` 为 `None`），
不填假值、不猜地址。每个栏目吃哪一种形状都是**实测**（`th-cn tools/list` + 真实返回），
对应关系见 `docs/SOURCES.md` 的资讯段。

已知上游缺口（本层不掩盖，如实留下）：
`eastmoney_global_news` 的条目**没有 url 字段**，所以它进得了资讯流、
进不了事件抽取（事件必须带可点回的 url 作为证据）——见 `events.extract_events`。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ladderstock.news.models import NewsItem, build_item

#: dataset → 解码器
_DECODERS: dict[str, Callable[[Any, dict[str, Any]], list[NewsItem]]] = {}


def decoder(dataset: str) -> Callable[..., Any]:
    def wrap(func: Callable[[Any, dict[str, Any]], list[NewsItem]]) -> Callable[..., Any]:
        _DECODERS[dataset] = func
        return func

    return wrap


def decode(dataset: str, data: Any, *, provenance: dict[str, Any] | None = None) -> list[NewsItem]:
    """把某个栏目的 `data` 翻译成统一条目；没有登记解码器的栏目一律拒绝。

    未登记 = 有人往资讯流里塞了新数据集，但它还没在 `DATASET_CATALOG` 说清归属。
    """
    func = _DECODERS.get(dataset)
    if func is None:
        raise KeyError(f"栏目 {dataset!r} 没有资讯解码器（先登记归属，再写翻译）")
    return func(data, dict(provenance or {}))


def supported_datasets() -> tuple[str, ...]:
    return tuple(sorted(_DECODERS))


def _rows(data: Any, *keys: str) -> tuple[list[dict[str, Any]], str]:
    """取记录列表 + 该次取数的 code 上下文。

    TickHub 的形状分两类：顶层就是 list（cls / 见闻 / 东财全球），
    或 dict 里某个键才是 list（cninfo.announcements / reports / news / items）。
    非 dict 项（字符串之类）不丢弃，包成 `{"title": ...}` 保留原文。
    """
    code_context = ""
    payload = data
    if isinstance(payload, Mapping):
        code_context = str(payload.get("code") or "").strip()
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list):
                payload = value
                break
        else:
            return [], code_context
    if not isinstance(payload, list):
        return [], code_context
    rows: list[dict[str, Any]] = []
    for item in payload:
        if isinstance(item, Mapping):
            rows.append(dict(item))
        elif item is not None:
            rows.append({"title": str(item)})
    return rows, code_context


def _text(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


@decoder("announcement_cninfo")
def _cninfo(data: Any, provenance: dict[str, Any]) -> list[NewsItem]:
    rows, context = _rows(data, "announcements")
    items = []
    for row in rows:
        item = build_item(
            dataset="announcement_cninfo",
            title=_text(row, "title", "name"),
            url=_text(row, "url", "pdf"),
            published_at=row.get("publish_time"),
            summary=_text(row, "type"),
            code=_text(row, "code") or context,
            provenance=provenance,
        )
        if item is not None:
            items.append(item)
    return items


@decoder("announcement_szse")
def _szse(data: Any, provenance: dict[str, Any]) -> list[NewsItem]:
    rows, context = _rows(data, "announcements")
    items = []
    for row in rows:
        item = build_item(
            dataset="announcement_szse",
            title=_text(row, "title", "name"),
            url=_text(row, "url"),
            published_at=row.get("publish_time"),
            summary=_text(row, "format"),
            code=_text(row, "code") or context,
            provenance=provenance,
        )
        if item is not None:
            items.append(item)
    return items


@decoder("flash_cls")
def _cls(data: Any, provenance: dict[str, Any]) -> list[NewsItem]:
    rows, _ = _rows(data)
    items = []
    for row in rows:
        item = build_item(
            dataset="flash_cls",
            title=_text(row, "title"),
            url=_text(row, "url"),
            published_at=row.get("publish_time"),
            summary=_text(row, "content"),
            provenance=provenance,
        )
        if item is not None:
            items.append(item)
    return items


@decoder("flash_wallstreetcn")
def _wallstreetcn(data: Any, provenance: dict[str, Any]) -> list[NewsItem]:
    rows, _ = _rows(data)
    items = []
    for row in rows:
        item = build_item(
            dataset="flash_wallstreetcn",
            title=_text(row, "title"),
            url=_text(row, "uri"),
            published_at=row.get("publish_time"),
            summary=_text(row, "content"),
            provenance=provenance,
        )
        if item is not None:
            items.append(item)
    return items


@decoder("flash_eastmoney")
def _eastmoney_global(data: Any, provenance: dict[str, Any]) -> list[NewsItem]:
    rows, context = _rows(data)
    items = []
    for row in rows:
        item = build_item(
            dataset="flash_eastmoney",
            title=_text(row, "title"),
            url=_text(row, "url"),  # 实测该工具不返回 url：条目留在流里，但不进事件抽取
            published_at=row.get("publish_time"),
            summary=_text(row, "summary"),
            code=_text(row, "code") or context,
            provenance=provenance,
        )
        if item is not None:
            items.append(item)
    return items


@decoder("news_eastmoney_stock")
def _eastmoney_stock_news(data: Any, provenance: dict[str, Any]) -> list[NewsItem]:
    rows, context = _rows(data, "news")
    items = []
    for row in rows:
        item = build_item(
            dataset="news_eastmoney_stock",
            title=_text(row, "title"),
            url=_text(row, "url"),
            published_at=row.get("date"),
            summary=_text(row, "summary"),
            code=context,
            provenance=provenance,
        )
        if item is not None:
            items.append(item)
    return items


@decoder("news_cctv")
def _cctv(data: Any, provenance: dict[str, Any]) -> list[NewsItem]:
    rows, _ = _rows(data, "items")
    day = _text(data if isinstance(data, Mapping) else {}, "date")
    items = []
    for row in rows:
        item = build_item(
            dataset="news_cctv",
            title=_text(row, "title"),
            url=_text(row, "url"),
            # 新闻联播只给到「哪天」，没有时刻——保持日期粒度，不补 00:00
            published_at=row.get("time") or day,
            provenance=provenance,
        )
        if item is not None:
            items.append(item)
    return items


@decoder("research_report")
def _reports(data: Any, provenance: dict[str, Any]) -> list[NewsItem]:
    """东财研报（主源）与新浪研报（同 peer 备源）共用一个数据集，形状各表一次。"""
    rows, context = _rows(data, "reports")
    items = []
    for row in rows:
        summary = _report_summary(row)
        item = build_item(
            dataset="research_report",
            title=_text(row, "title"),
            url=_text(row, "url", "pdf"),
            published_at=row.get("publish_date"),
            summary=summary,
            code=_text(row, "code") or context,
            provenance=provenance,
        )
        if item is not None:
            items.append(item)
    return items


def _report_summary(row: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for label, key in (
        ("机构", "org"),
        ("分析师", "analyst"),
        ("评级", "rating"),
        ("类别", "type"),
    ):
        value = _text(row, key)
        if value:
            parts.append(f"{label}：{value}")
    return "；".join(parts)
