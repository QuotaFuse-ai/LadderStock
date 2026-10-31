"""规则优先的事件抽取（R6 / QUOT-144 要求 2）。

口径：

1. **规则是唯一的事件生产者**。模型可选、默认关闭，且只能给已抽出的事件补一段
   叙述（`ai_enrich`），**不许**新增或改判事件类型、日期、代码——DEC-006 闸门 5
   「AI 只写叙述」在资讯面的落地。
2. **每条事件必须可审计**：`evidence` 至少含原文片段 + url + 来源栏目 + 命中关键词。
   没有 url 的条目**不进抽取**（无证据即不算资料，DEC-018 R4），只计入 `skipped`。
3. **否定语境不抽**：「不减持」「暂无减持计划」「终止重大资产重组」这类公告很常见，
   命中关键词不等于发生该事件；`negations` 在证据窗口内生效。
4. **日期不猜**：能从原文窗口里读到日期就用原文日期（`date_basis=evidence_text`），
   否则退到发布时间（`date_basis=published_at`）；两个都没有就是 `None`，
   宁可日历上少一条，也不排一个假日期。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ladderstock.news.models import NewsItem, normalize_title, publish_day

#: 证据窗口半径（字符）：命中关键词前后各取一段，够人眼复核又不把全文搬进库
EVIDENCE_RADIUS = 60

#: 只有日期粒度的条目也允许出事件，但日历段会按天展示，所以不做时刻推测
_DATE_IN_TEXT_RE = re.compile(
    r"(20\d{2})\s*[-/年]\s*(\d{1,2})\s*[-/月]\s*(\d{1,2})\s*日?"
)


@dataclass(frozen=True)
class EventRule:
    """一条抽取规则：`groups` 是「与」关系，组内任一关键词命中即该组满足。

    拆成「组」是为了能表达复合口径——政策类必须同时出现「发布主体」和
    「政策动作」，只出现「通知」两个字的公司公告不算政策事件。
    """

    key: str
    label: str
    groups: tuple[tuple[str, ...], ...]
    negations: tuple[str, ...] = ()
    #: 限定栏目；None = 全部栏目
    sources: tuple[str, ...] | None = None
    #: 是否尝试从证据窗口读事件日期（解禁 / 股东大会这类「未来某天发生」的事件）
    date_in_text: bool = False
    note: str = ""


# 业绩预告 / 减持 / 增持 / 解禁 / 并购 / 中标 / 政策 —— issue 要求的七类，
# 外加「股东大会」一类：日历需要它，而公告正文本来就写了会议日期（带证据可查）。
EVENT_RULES: tuple[EventRule, ...] = (
    EventRule(
        key="earnings_preview",
        label="业绩预告",
        groups=(("业绩预告", "业绩预增", "业绩预减", "预增", "预减", "扭亏", "首亏", "续亏",
                 "业绩快报", "业绩预计"),),
        note="口径收紧：只认「预告 / 快报」这类字眼。只提「净利润」的业绩新闻不算预告——"
             "真预告另有结构化源 earnings_preview（notice_date 就是它的披露日）",
    ),
    EventRule(
        key="shareholder_reduction",
        label="股东减持",
        groups=(("减持",),),
        negations=("不减持", "未减持", "无减持", "暂无减持", "没有减持", "不会减持",
                   "终止减持", "中止减持", "提前结束减持"),
    ),
    EventRule(
        key="shareholder_increase",
        label="股东增持",
        groups=(("增持",),),
        negations=("不增持", "未增持", "无增持", "暂无增持", "没有增持", "终止增持", "中止增持"),
    ),
    EventRule(
        key="lockup_expiry",
        label="限售解禁",
        groups=(("解禁", "解除限售", "限售股上市", "限售股份上市", "限售股流通"),),
        date_in_text=True,
        note="解禁日历另有结构化源 lockup_calendar，两处都抽以便回溯公告原文",
    ),
    EventRule(
        key="ma_restructuring",
        label="并购重组",
        groups=(("并购", "重大资产重组", "重组", "收购", "发行股份购买资产", "吸收合并",
                 "要约收购", "控制权"),),
        negations=("终止重大资产重组", "不构成重大资产重组", "未构成重大资产重组",
                   "取消本次重组", "暂停重组"),
    ),
    EventRule(
        key="contract_award",
        label="中标合同",
        groups=(("中标", "中选", "重大合同", "签订合同", "签署合同", "框架协议",
                 "日常经营重大合同"),),
        note="「订单」不进关键词：实测「订单充足」是调研描述而非中标事件（漏抽见抽查表）",
    ),
    EventRule(
        key="policy",
        label="政策发布",
        groups=(
            ("国务院", "国务院办公厅", "央行", "中国人民银行", "证监会", "中国证监会",
             "中国证券监督管理委员会", "发改委", "国家发展改革委", "财政部", "工信部",
             "金融监管总局", "银保监会", "上交所", "深交所", "北交所", "交易所",
             "商务部", "市场监管总局", "国资委", "税务总局", "住建", "人社部"),
            ("政策", "新规", "通知", "办法", "规定", "规则", "意见", "规划", "方案",
             "会议", "发布会", "座谈会", "部署", "降息", "降准", "LPR", "印花税", "利率"),
        ),
        note="必须同时命中「发布主体」与「政策动作」，否则公司公告里的『通知』二字会全算成政策",
    ),
    EventRule(
        key="shareholder_meeting",
        label="股东大会",
        groups=(("股东大会", "临时股东大会", "股东年会", "年度股东大会"),),
        date_in_text=True,
        note="TickHub-CN 无「股东大会」独立工具，本类只来自公告正文（DEC-018 R6）",
    ),
)

RULE_KEYS: tuple[str, ...] = tuple(rule.key for rule in EVENT_RULES)
_RULE_BY_KEY: dict[str, EventRule] = {rule.key: rule for rule in EVENT_RULES}

#: 不参与事件抽取的栏目。**研报是二手解读，不是事件本身**：
#: 实测「评级：增持」被 `shareholder_increase` 命中两次（太平洋/万联对茅台的研报），
#: 那是分析师观点，不是股东增持公告。研报照常进资讯流（可阅读），只是不产事件。
EVENT_EXTRACTION_EXCLUDED_DATASETS: frozenset[str] = frozenset({"research_report"})


@dataclass(frozen=True)
class NewsEvent:
    """一条抽取出来的事件。`evidence` 缺失就不该存在——构造函数会拦住。"""

    id: str
    type: str
    label: str
    rule_id: str
    event_date: str | None
    title: str
    codes: tuple[str, ...]
    evidence: dict[str, Any]
    item_id: str = ""
    date_basis: str = "published_at"
    extractor: str = "rule"
    ai_note: str | None = None

    def __post_init__(self) -> None:
        if not self.evidence.get("url"):
            raise ValueError(f"事件 {self.id} 缺 evidence.url：无来源标签的内容不得作为资料")
        if not self.evidence.get("snippet"):
            raise ValueError(f"事件 {self.id} 缺 evidence.snippet：抽取必须留下原文片段")

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "label": self.label,
            "rule_id": self.rule_id,
            "event_date": self.event_date,
            "title": self.title,
            "codes": list(self.codes),
            "evidence": dict(self.evidence),
            "item_id": self.item_id,
            "date_basis": self.date_basis,
            "extractor": self.extractor,
        }
        if self.ai_note:
            payload["ai_note"] = self.ai_note
        return payload

    def with_ai_note(self, note: str) -> NewsEvent:
        return NewsEvent(
            id=self.id, type=self.type, label=self.label, rule_id=self.rule_id,
            event_date=self.event_date, title=self.title, codes=self.codes,
            evidence=dict(self.evidence), item_id=self.item_id, date_basis=self.date_basis,
            extractor=self.extractor, ai_note=note,
        )


@dataclass
class ExtractionReport:
    """一次抽取的账本：抽到什么、为什么没抽。抽查表（准确率/漏抽/误抽）直接用它。"""

    events: list[NewsEvent] = field(default_factory=list)
    skipped_no_url: list[str] = field(default_factory=list)
    skipped_excluded_source: list[str] = field(default_factory=list)
    skipped_negation: list[dict[str, Any]] = field(default_factory=list)
    by_type: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "events": len(self.events),
            "by_type": dict(sorted(self.by_type.items())),
            "skipped_no_url": len(self.skipped_no_url),
            "skipped_no_url_ids": list(self.skipped_no_url),
            "skipped_excluded_source": len(self.skipped_excluded_source),
            "skipped_excluded_source_ids": list(self.skipped_excluded_source),
            "suppressed_by_negation": [
                {"item_id": row["item_id"], "rule": row["rule"], "term": row["term"]}
                for row in self.skipped_negation
            ],
        }


def extract_events(items: list[NewsItem]) -> ExtractionReport:
    """对统一流跑一遍规则。顺序无关、可重跑：同一批 items 必得同一批事件 id。"""
    report = ExtractionReport()
    for item in items:
        if not item.url:
            report.skipped_no_url.append(item.id)
            continue
        if item.source in EVENT_EXTRACTION_EXCLUDED_DATASETS:
            report.skipped_excluded_source.append(item.id)
            continue
        text = f"{item.title}\n{item.summary}"
        for rule in EVENT_RULES:
            if rule.sources is not None and item.source not in rule.sources:
                continue
            match = _match_rule(rule, text)
            if match is None:
                continue
            if match["negation"]:
                report.skipped_negation.append(
                    {"item_id": item.id, "rule": rule.key, "term": match["negation"]}
                )
                continue
            event = _build_event(rule, item, match)
            if event is None:
                continue
            report.events.append(event)
            report.by_type[rule.key] = report.by_type.get(rule.key, 0) + 1
    report.events.sort(key=lambda event: (event.event_date or "", event.id))
    return report


def _match_rule(rule: EventRule, text: str) -> dict[str, Any] | None:
    """返回 {start, end, keyword, negation}；negation 非空表示命中关键词但落在否定语境。"""
    hits: list[tuple[int, int, str]] = []
    for group in rule.groups:
        located = None
        for keyword in group:
            index = text.find(keyword)
            if index < 0:
                continue
            if located is None or index < located[0]:
                located = (index, index + len(keyword), keyword)
        if located is None:
            return None
        hits.append(located)
    start = min(hit[0] for hit in hits)
    end = max(hit[1] for hit in hits)
    window = text[max(0, start - EVIDENCE_RADIUS): end + EVIDENCE_RADIUS]
    negation = next((term for term in rule.negations if term in window), "")
    return {
        "start": start,
        "end": end,
        "keyword": "/".join(sorted({hit[2] for hit in hits})),
        "negation": negation,
        "window": window,
    }


def _build_event(rule: EventRule, item: NewsItem, match: dict[str, Any]) -> NewsEvent | None:
    snippet = str(match["window"]).strip()
    event_date, basis = _event_date(rule, item, snippet)
    title_key = normalize_title(item.title)
    raw = f"{rule.key}|{event_date or ''}|{title_key}|{item.id}"
    event_id = hashlib.sha1(raw.encode()).hexdigest()[:16]
    evidence = {
        "snippet": snippet,
        "url": item.url,
        "source": item.source,
        "matched": match["keyword"],
        "item_title": item.title,
        "published_at": item.published_at,
    }
    provenance = item.provenance if isinstance(item.provenance, Mapping) else {}
    if provenance.get("tool"):
        evidence["tool"] = str(provenance["tool"])
    if provenance.get("trace_id"):
        evidence["trace_id"] = str(provenance["trace_id"])
    return NewsEvent(
        id=event_id,
        type=rule.key,
        label=rule.label,
        rule_id=rule.key,
        event_date=event_date,
        title=item.title,
        codes=item.related_codes,
        evidence=evidence,
        item_id=item.id,
        date_basis=basis,
    )


def _event_date(rule: EventRule, item: NewsItem, snippet: str) -> tuple[str | None, str]:
    if rule.date_in_text:
        found = _DATE_IN_TEXT_RE.search(snippet)
        if found:
            try:
                parsed = date(int(found.group(1)), int(found.group(2)), int(found.group(3)))
            except ValueError:
                parsed = None
            if parsed is not None:
                return parsed.isoformat(), "evidence_text"
    day = item.day or publish_day(item.published_at)
    return (day.isoformat() if day else None), "published_at"


def rule_for(key: str) -> EventRule | None:
    return _RULE_BY_KEY.get(key)
