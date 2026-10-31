"""资讯雷达（R6 / QUOT-144）：统一资讯流 + 规则事件抽取 + 事件日历。

数据边界（DEC-006 / DEC-018）：

- 本包**不采集**。所有原文只从 `core.boundary.DATASET_CATALOG` 登记过的
  TickHub-CN 工具经 `ProviderRegistry` 进来，`news.normalize` 只做形状翻译。
- 只落本机（`data/news.sqlite`），不上传、不进 `LadderEnvelope` 之外的对外契约。
- 资讯流里**没有**任何持仓 / 自选 / 交易日志字段（`core.privacy` 口径，测试反向钉住）。
- 事件抽取以规则为主，模型可选且**默认关闭**；模型只能补叙述，不能新增事件。
"""

from __future__ import annotations

from ladderstock.news.calendar import NO_SOURCE_SECTIONS, CalendarSection, build_calendar
from ladderstock.news.events import EVENT_RULES, NewsEvent, extract_events
from ladderstock.news.models import NewsItem, normalize_title, parse_published_at
from ladderstock.news.stream import StreamResult, collect_stream

__all__ = [
    "CalendarSection",
    "EVENT_RULES",
    "NO_SOURCE_SECTIONS",
    "NewsEvent",
    "NewsItem",
    "StreamResult",
    "build_calendar",
    "collect_stream",
    "extract_events",
    "normalize_title",
    "parse_published_at",
]
