"""R6 资讯雷达的离线样本语料（形状对齐真实 TickHub-CN 返回，内容手工整理）。

为什么要自己写一份而不是把线上抓的 11 份 payload 直接搬进来：

1. 语料要**能被断言**——每一条都对应 `SPOT_CHECK` 里的一行（正确抽取 / 漏抽 / 误抽防护），
   真实抓取的稿件每天都在变，钉不住回归；
2. 仓库里不该沉淀别人的正文（样本只留标题 + 一句摘要 + 一个占位 url）；
3. 形状是实测的：cninfo 的 `{"code","announcements"}`、cls 的顶层数组 + epoch 秒、
   东财全球快讯**没有 url**、研报只有日期没有时刻——这几种差异都得有样本压着。

`news_handlers()` 产出给 `FakeTransport.handlers` 的表；`failing=` 用来点名「哪一路挂了」。
"""

from __future__ import annotations

from typing import Any

from fixtures.fake_peers import (
    NEWS_FALLBACK_TOOLS,
    NEWS_TOOL_BY_DATASET,
    tickhub_payload,
)

from ladderstock.core.boundary import NEWS_STREAM_DATASETS
from ladderstock.news import normalize
from ladderstock.news.models import NewsItem

WINDOW_DAY = "2026-10-30"
FLASH_TS = 1_793_323_800  # 2026-10-30T09:30:00+08:00（epoch 秒，cls/见闻的真实形态）
FLASH_TS_LATER = 1_793_410_200  # 2026-10-31T09:30:00+08:00（跨天：用来压住时间窗过滤）

#: 跨源同稿的标题（cls 与见闻都发过，url 不同、同一天）→ 必须合成一条并留下两个来源
CROSS_SOURCE_TITLE = "我国铼、金、镍、锆等保有资源量大幅增加"

CNINFO_DATA: dict[str, Any] = {
    "code": "600519",
    "total": 6,
    "page": 1,
    "announcements": [
        {
            "code": "600519",
            "name": "贵州茅台",
            "title": "贵州茅台关于召开2026年第三次临时股东会的通知",
            "type": "股东大会通知",
            "publish_time": f"{WINDOW_DAY} 00:00:00",
            "url": "https://static.cninfo.com.cn/finalpage/2026-10-30/900001.PDF",
        },
        {
            "code": "600519",
            "name": "贵州茅台",
            "title": "贵州茅台关于限售股份上市流通的提示公告（2026年11月16日）",
            "type": "股本变动",
            "publish_time": f"{WINDOW_DAY} 00:00:00",
            "url": "https://static.cninfo.com.cn/finalpage/2026-10-30/900002.PDF",
        },
        {
            "code": "600519",
            "name": "贵州茅台",
            "title": "贵州茅台2026年第三季度报告",
            "type": "定期报告",
            "publish_time": f"{WINDOW_DAY} 00:00:00",
            "url": "https://static.cninfo.com.cn/finalpage/2026-10-30/900003.PDF",
        },
        {
            "code": "600519",
            "name": "贵州茅台",
            "title": "贵州茅台关于会计政策变更的公告",
            "type": "其他",
            "publish_time": "2026-08-15 00:00:00",
            "url": "https://static.cninfo.com.cn/finalpage/2026-08-15/900004.PDF",
        },
        {
            "code": "600519",
            "name": "贵州茅台",
            "title": "贵州茅台关于股东减持股份计划的公告",
            "type": "股东持股变动",
            "publish_time": "2026-09-30 00:00:00",
            "url": "https://static.cninfo.com.cn/finalpage/2026-09-30/900005.PDF",
        },
        {
            "code": "600519",
            "name": "贵州茅台",
            "title": "贵州茅台关于控股股东增持公司股份进展的公告",
            "type": "股东持股变动",
            "publish_time": f"{WINDOW_DAY} 00:00:00",
            "url": "https://static.cninfo.com.cn/finalpage/2026-10-30/900006.PDF",
        },
    ],
}

SZSE_DATA: dict[str, Any] = {
    "code": "000001",
    "announcements": [
        # 同标题、不同发布日：两份真公告，去重绝不能合并（实测平安银行就是这样）
        {
            "code": "000001",
            "name": "平安银行",
            "title": "平安银行：董事会决议公告",
            "format": "PDF",
            "publish_time": "2026-10-24 00:00:00",
            "url": "https://www.szse.cn/disclosure/000001-aaaa.PDF",
        },
        {
            "code": "000001",
            "name": "平安银行",
            "title": "平安银行：董事会决议公告",
            "format": "PDF",
            "publish_time": "2026-09-30 00:00:00",
            "url": "https://www.szse.cn/disclosure/000001-bbbb.PDF",
        },
        {
            "code": "000001",
            "name": "平安银行",
            "title": "平安银行：关于股东未减持公司股份暨实施完毕的公告",
            "format": "PDF",
            "publish_time": f"{WINDOW_DAY} 00:00:00",
            "url": "https://www.szse.cn/disclosure/000001-cccc.PDF",
        },
        {
            "code": "000001",
            "name": "平安银行",
            "title": "平安银行：关于终止重大资产重组的公告",
            "format": "PDF",
            "publish_time": f"{WINDOW_DAY} 00:00:00",
            "url": "https://www.szse.cn/disclosure/000001-dddd.PDF",
        },
    ],
}

CLS_DATA: list[dict[str, Any]] = [
    {
        "id": "c-1",
        "title": "国务院常务会议部署稳经济一揽子措施 会议审议通过相关政策文件",
        "content": "会议指出，要抓紧研究细化具体举措，确保政策落地见效。",
        "publish_time": FLASH_TS,
        "level": "A",
        "url": "https://www.cls.cn/detail/100001",
    },
    {
        "id": "c-2",
        "title": "某科技公司发布业绩预告 预计前三季度净利润同比增长120%",
        "content": "公司预计2026年1-9月归属于上市公司股东的净利润同比增长约120%。",
        "publish_time": FLASH_TS,
        "level": "B",
        "url": "https://www.cls.cn/detail/100002",
    },
    {
        "id": "c-3",
        "title": CROSS_SOURCE_TITLE,
        "content": "自然资源部今日发布消息，新一轮找矿突破战略行动取得新成果。",
        "publish_time": FLASH_TS,
        "level": "C",
        "url": "https://www.cls.cn/detail/100003",
    },
    {
        "id": "c-4",
        "title": "某基建公司中标12亿元高速公路项目 构成日常经营重大合同",
        "content": "公司收到中标通知书。",
        "publish_time": FLASH_TS_LATER,
        "level": "B",
        "url": "https://www.cls.cn/detail/100004",
    },
]

WSC_DATA: list[dict[str, Any]] = [
    {
        "id": "w-1",
        "title": CROSS_SOURCE_TITLE,
        "content": "自然资源部：新一轮找矿突破战略行动取得新成果，铼、金等储量大幅增长。",
        "publish_time": FLASH_TS,
        "score": 8,
        "uri": "https://wallstreetcn.com/livenews/200001",
    },
    {
        "id": "w-2",
        "title": "麦格米特：明年AI电源板块订单充足 产能持续爬坡",
        "content": "公司近日接受机构调研时表示，订单充足。",
        "publish_time": FLASH_TS,
        "score": 5,
        "uri": "https://wallstreetcn.com/livenews/200002",
    },
]

#: 实测：eastmoney_global_news 的条目**没有 url 字段**——进得了资讯流，进不了事件抽取
EM_GLOBAL_DATA: list[dict[str, Any]] = [
    {
        "code": "",
        "title": "证监会就上市公司监管规则公开征求意见",
        "summary": "进一步完善退市与信息披露制度安排。",
        "publish_time": f"{WINDOW_DAY} 08:00:00",
    },
    {
        "code": "",
        "title": "央行开展逆回购操作 利率维持不变",
        "summary": "本周净投放 2000 亿元。",
        "publish_time": f"{WINDOW_DAY} 09:00:00",
    },
]

EM_STOCK_DATA: dict[str, Any] = {
    "code": "600519",
    "news": [
        {
            "date": f"{WINDOW_DAY} 23:04:44",
            "media": "东方财富",
            "title": "贵州茅台(600519.SH)发布前三季度业绩 归母净利润644.67亿元",
            "summary": "单季度净利润重回增长。",
            "url": "https://finance.eastmoney.com/a/20261030300001.html",
        },
        {
            "date": f"{WINDOW_DAY} 06:02:58",
            "media": "东方财富",
            "title": "贵州茅台：拟收购茅台集团持有的某项资产 构成重大资产重组",
            "summary": "公司股票明日复牌。",
            "url": "https://finance.eastmoney.com/a/20261030300002.html",
        },
    ],
}

CCTV_DATA: dict[str, Any] = {
    "date": WINDOW_DAY,
    "items": [
        {
            "title": "[视频]李强主持召开国务院常务会议 部署稳经济一揽子措施",
            "url": "https://tv.cctv.com/2026/10/30/VIDE0001.shtml",
        },
        {
            "title": "我国铼、金、镍、锆等保有资源量大幅增加",
            "url": "https://tv.cctv.com/2026/10/30/VIDE0002.shtml",
        },
    ],
}

REPORTS_DATA: dict[str, Any] = {
    "code": "600519",
    "page": 1,
    "reports": [
        {
            "title": "市场化改革重塑渠道和价格体系，业绩保持韧性",
            "code": "600519",
            "name": "贵州茅台",
            "org": "太平洋",
            "analyst": "郭梦婕",
            "rating": "增持",
            "publish_date": "2026-08-18",
            "info_code": "AP202608180001",
            "pdf": "https://pdf.dfcfw.com/pdf/H3_AP202608180001_1.pdf",
        },
        {
            "title": "2026年中期业绩点评报告：提价与直销双轮驱动",
            "code": "600519",
            "name": "贵州茅台",
            "org": "万联证券",
            "analyst": "陈雯",
            "rating": "买入",
            "publish_date": "2026-08-19",
            "info_code": "AP202608190002",
            "pdf": "https://pdf.dfcfw.com/pdf/H3_AP202608190002_1.pdf",
        },
    ],
}

SINA_REPORTS_DATA: dict[str, Any] = {
    "code": "600519",
    "page": 1,
    "reports": [
        {
            "title": "贵州茅台深度报告：渠道改革红利释放",
            "type": "公司研究",
            "org": "某券商",
            "analyst": "张三",
            "publish_date": "2026-10-20",
            "url": "https://stock.finance.sina.com.cn/report/2026/1020/1.html",
        }
    ],
}

LOCKUP_DATA: dict[str, Any] = {
    "code": "600519",
    "trade_date": "2026-10-31",
    "forward_days": 45,
    "history": [
        {
            "date": "2025-11-16",
            "type": "股权激励限售股份",
            "shares_10k": 125227.0,
            "able_shares_10k": 320.8,
            "ratio_pct": 0.0256,
            "lift_market_cap_10k": 63369.7,
        }
    ],
    "upcoming": [
        {
            "date": "2026-11-16",
            "type": "股权激励限售股份",
            "shares_10k": 125227.0,
            "able_shares_10k": 411.2,
            "ratio_pct": 0.0328,
            "lift_market_cap_10k": 71000.0,
        }
    ],
}

EARNINGS_DATA: list[dict[str, Any]] = [
    {
        "code": "600519",
        "name": "贵州茅台",
        "notice_date": "2026-10-14",
        "report_date": "2026-09-30",
        "indicator": "归属于上市公司股东的净利润",
        "forecast_type": "预增",
        "amount_lower": 64_467_000_000.0,
        "amount_upper": 64_467_000_000.0,
        "change_pct_lower": 8.5,
        "change_pct_upper": 8.5,
        "prior_year_amount": 59_400_000_000.0,
        "content": "预计2026年1-9月归属于上市公司股东的净利润同比增长约8.5%。",
        "reason": "销量增长",
    },
    {
        "code": "600519",
        "name": "贵州茅台",
        "notice_date": "2026-07-14",
        "report_date": "2026-06-30",
        "indicator": "营业收入",
        "forecast_type": "预增",
        "amount_lower": 90_000_000_000.0,
        "amount_upper": 90_000_000_000.0,
        "change_pct_lower": 15.0,
        "change_pct_upper": 15.0,
        "prior_year_amount": 78_000_000_000.0,
        "content": "预计2026年1-6月营业收入同比增长约15%。",
        "reason": None,
    },
]

#: tool → 上游 `data` 形状
SAMPLE_DATA: dict[str, Any] = {
    "cninfo_announcements": CNINFO_DATA,
    "szse_announcements": SZSE_DATA,
    "cls_telegraph": CLS_DATA,
    "wallstreetcn_lives": WSC_DATA,
    "eastmoney_global_news": EM_GLOBAL_DATA,
    "eastmoney_stock_news": EM_STOCK_DATA,
    "cctv_news": CCTV_DATA,
    "eastmoney_reports": REPORTS_DATA,
    "sina_research_reports": SINA_REPORTS_DATA,
    "announcements_backup": CNINFO_DATA,
    "lockup_expiry": LOCKUP_DATA,
    "earnings_forecast": EARNINGS_DATA,
}


def news_handlers(*, failing: tuple[str, ...] = (), cache_hit: bool = False) -> dict[str, Any]:
    """给 `FakeTransport.handlers` 用的分派表；`failing` 里的工具改由 errors 制造失败。"""
    return {
        tool: (lambda _params, payload=data, tool=tool: tickhub_payload(payload, source=tool))
        for tool, data in SAMPLE_DATA.items()
        if tool not in failing
    }


def news_tools() -> list[str]:
    """资讯链路会用到的工具名（主源 + 同 peer 备源）：「全挂」的用例必须连备源一起打。"""
    return sorted(
        set(NEWS_TOOL_BY_DATASET.values()) | {tool for _id, tool in NEWS_FALLBACK_TOOLS.values()}
    )


#: 语料里属于资讯流的工具（备源 / 日历结构化源不算：它们各有专门的用例）
CORPUS_TOOLS: tuple[str, ...] = tuple(
    tool
    for dataset, tool in NEWS_TOOL_BY_DATASET.items()
    if dataset in NEWS_STREAM_DATASETS
)

_TOOL_TO_DATASET: dict[str, str] = {
    tool: dataset for dataset, tool in NEWS_TOOL_BY_DATASET.items()
}


def corpus_items() -> list[NewsItem]:
    """整个语料 → 统一条目（不取数、不过时间窗、不跨源合并）。

    事件抽取的抽查基准用它：抽取的正确性只取决于「条目长什么样 + 规则」，
    把时间窗和落库掺进来只会让失败原因难以判断。
    """
    items: list[NewsItem] = []
    for tool in CORPUS_TOOLS:
        dataset = _TOOL_TO_DATASET[tool]
        items.extend(
            normalize.decode(
                dataset,
                SAMPLE_DATA[tool],
                provenance={"dataset": dataset, "tool": tool, "trace_id": f"trace-{tool}"},
            )
        )
    return items


#: 抽查表：(样本来源工具, 标题里的可定位片段, 期望事件类型或 None, 说明)。
#: `test_events.py` 逐行钉住这张表——它和 `docs/tasks/QUOT-144` 的抽查表同源，
#: 表里改一行，测试就红一行，两者不允许各说各话。
SPOT_CHECK: tuple[tuple[str, str, str | None, str], ...] = (
    (
        "cls_telegraph",
        "国务院常务会议部署稳经济一揽子措施",
        "policy",
        "正确抽取：发布主体（国务院）+ 政策动作（部署）同时命中",
    ),
    (
        "cls_telegraph",
        "某科技公司发布业绩预告",
        "earnings_preview",
        "正确抽取：命中「业绩预告」字样",
    ),
    (
        "cls_telegraph",
        "某基建公司中标12亿元高速公路项目",
        "contract_award",
        "正确抽取：中标 + 日常经营重大合同",
    ),
    (
        "cninfo_announcements",
        "贵州茅台关于召开2026年第三次临时股东会的通知",
        "shareholder_meeting",
        "正确抽取：公告标题写明会议（日历缺口只能靠它，见 DEC-018 R6）",
    ),
    (
        "cninfo_announcements",
        "贵州茅台关于限售股份上市流通的提示公告（2026年11月16日）",
        "lockup_expiry",
        "正确抽取：日期取自原文 → date_basis=evidence_text",
    ),
    (
        "cninfo_announcements",
        "贵州茅台关于股东减持股份计划的公告",
        "shareholder_reduction",
        "正确抽取：减持计划",
    ),
    (
        "cninfo_announcements",
        "贵州茅台关于控股股东增持公司股份进展的公告",
        "shareholder_increase",
        "正确抽取：增持进展",
    ),
    (
        "eastmoney_stock_news",
        "贵州茅台：拟收购茅台集团持有的某项资产 构成重大资产重组",
        "ma_restructuring",
        "正确抽取：收购 + 重大资产重组",
    ),
    (
        "szse_announcements",
        "平安银行：关于股东未减持公司股份暨实施完毕的公告",
        None,
        "误抽防护：否定语境（未减持）不抽，且计入 suppressed_by_negation",
    ),
    (
        "szse_announcements",
        "平安银行：关于终止重大资产重组的公告",
        None,
        "误抽防护：终止 ≠ 发生，且计入 suppressed_by_negation",
    ),
    (
        "cninfo_announcements",
        "贵州茅台关于会计政策变更的公告",
        None,
        "误抽防护：只有「政策」二字、没有发布主体，不算政策事件",
    ),
    (
        "eastmoney_reports",
        "市场化改革重塑渠道和价格体系，业绩保持韧性",
        None,
        "误抽防护：研报「评级：增持」是分析师观点，不是股东增持（栏目整体不进抽取）",
    ),
    (
        "cninfo_announcements",
        "贵州茅台2026年第三季度报告",
        None,
        "已知漏抽：定期报告不含「预告」字样（真预告另有结构化源 earnings_preview）",
    ),
    (
        "wallstreetcn_lives",
        "麦格米特：明年AI电源板块订单充足",
        None,
        "已知漏抽：「订单充足」是调研描述，不是中标事件",
    ),
    (
        "eastmoney_stock_news",
        "贵州茅台(600519.SH)发布前三季度业绩 归母净利润644.67亿元",
        None,
        "已知漏抽：业绩新闻 ≠ 业绩预告",
    ),
    (
        "eastmoney_global_news",
        "证监会就上市公司监管规则公开征求意见",
        None,
        "已知漏抽：上游不给 url，无证据即不作为资料（DEC-018 R4）",
    ),
)

#: 已知残留：同一件事、标题措辞不同 → 去重合并不了，会出两条同类事件。
#: 抽查表把它单列出来，不假装干净（页面按 type + 日期聚合时可以合并展示）。
KNOWN_DUPLICATE_TITLE = "李强主持召开国务院常务会议"
