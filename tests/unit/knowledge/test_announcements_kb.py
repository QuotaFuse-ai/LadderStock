"""知识库分类与过滤的单测（不连 qdrant、不连 PG）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ladderstock.knowledge import announcements as ann
from ladderstock.knowledge import fulltext as ft


@pytest.mark.parametrize(
    ("title", "kind", "period"),
    [
        ("贵州茅台2026年第三季度报告", "financial_report", "2026Q3"),
        ("贵州茅台2026年半年度报告摘要", "financial_report", "2026H1"),
        ("某某公司2025年年度报告", "financial_report", "2025A"),
        ("关于2026年第一季度报告的更正公告", "financial_report", "2026Q1"),
        ("贵州茅台关于调整2025年年度利润分配方案每股分红金额的公告", "dividend", ""),
        ("2025年年度权益分派实施公告", "dividend", ""),
        # 「季度利润分配」不是财报：财报只认「季度报告」这类固定叫法
        ("关于2026年第三季度利润分配方案的公告", "dividend", ""),
        ("关于回购股份实施结果暨股份变动的公告", "buyback", ""),
        ("贵州茅台2025年度股东会决议公告", "shareholder_meeting", ""),
        ("关于董事、高级管理人员减持计划的公告", "equity_change", ""),
        ("关于聘任董事会秘书的公告", "personnel", ""),
        ("关于与关联方签订商标许可协议暨关联交易的公告", "related_party", ""),
        ("北京市金杜律师事务所关于公司股东会的法律意见书", "legal", ""),
        ("关于重大诉讼进展的公告", "risk_event", ""),
        ("章程修订案", "governance", ""),
        ("ST椰岛第八届董事会第五十八次会议决议公告", "governance", ""),
        ("资产减值准备管理制度（2026年8月修订）", "governance", ""),
        ("关于公司核心技术人员退休离任的公告", "personnel", ""),
        ("投资者关系管理信息", "investor_relations", ""),
        ("003032传智教育投资者关系管理信息20260831", "investor_relations", ""),
        ("2026年半年度业绩说明会召开情况的公告", "investor_relations", ""),
        # 中期报告/审阅报告也属于财报，period 走 H1
        ("中国平安2026年中期报告", "financial_report", "2026H1"),
        ("潍柴动力股份有限公司2026年中期审阅报告", "financial_report", "2026H1"),
        # 再融资：募集说明书、预案、招股书
        ("2026年度向特定对象发行股票募集说明书（注册稿）", "refinancing", ""),
        ("江苏长电科技股份有限公司2026年度向特定对象发行A股股票预案", "refinancing", ""),
        ("傲拓科技首次公开发行股票并在科创板上市招股意向书", "refinancing", ""),
        ("国信证券股份有限公司2026年面向专业投资者公开发行公司债券募集说明书", "refinancing", ""),
        ("关于暂不向下修正“隆22转债”转股价格的公告", "refinancing", ""),
        # 审核问询函归风险类，不能被再融资吃掉
        ("关于向特定对象发行股票申请文件审核问询函的回复", "risk_event", ""),
        # 并购重组优先于再融资
        ("通威股份有限公司发行股份及支付现金购买资产并募集配套资金预案", "merger", ""),
        ("关于重大资产重组进展的公告", "merger", ""),
        # 担保
        ("中国巨石为控股子公司提供担保的公告", "guarantee", ""),
        ("四川长虹关于2026年度对外担保的进展公告", "guarantee", ""),
        # 中介机构核查/督导文件
        ("东吴证券关于江苏澳洋健康产业股份有限公司详式权益变动报告书之持续督导意见", "agency_report", ""),
        # 股权激励与员工持股
        ("2026年股票期权激励计划（草案）", "stock_incentive", ""),
        ("2026年员工持股计划管理办法", "stock_incentive", ""),
        # ESG 报告
        ("北方稀土2025年度可持续发展报告（英文版）", "esg_report", ""),
        ("2025年度环境、社会与公司治理（ESG）报告", "esg_report", ""),
        # 投资者关系活动
        ("2026年5月8日投资者关系活动记录表", "investor_relations", ""),
    ],
)
def test_classify(title: str, kind: str, period: str) -> None:
    assert ann.classify(title) == (kind, period)


def test_classify_is_deterministic() -> None:
    title = "2026年第三季度报告"
    assert ann.classify(title) == ann.classify(title)


def test_row_exposes_kind_and_period() -> None:
    row = ann.AnnouncementRow(
        id=1,
        symbol="600519",
        ann_date="2026-10-31",
        title="贵州茅台2026年第三季度报告",
        body="正文",
        event_id="evt",
    )
    assert row.doc_kind == "financial_report"
    assert row.period == "2026Q3"


def test_all_doc_kinds_have_labels() -> None:
    # 值域固定才能当 keyword 索引；这里守住"新增分类必须同时进 DOC_KINDS"
    assert set(ann.DOC_KINDS) | {ann.DOC_KIND_FALLBACK} == {
        "financial_report",
        "earnings_preview",
        "dividend",
        "buyback",
        "risk_event",
        "merger",
        "refinancing",
        "stock_incentive",
        "esg_report",
        "investor_relations",
        "guarantee",
        "agency_report",
        "legal",
        "related_party",
        "equity_change",
        "personnel",
        "governance",
        "shareholder_meeting",
        "notice",
    }


def _capture_search(monkeypatch: pytest.MonkeyPatch, **kwargs) -> dict:
    captured: dict = {}

    def fake_request(method, path, *, json_body=None):
        captured["path"] = path
        captured["body"] = json_body
        return {"result": []}

    monkeypatch.setattr("ladderstock.knowledge.vectors._request", fake_request)
    monkeypatch.setattr("ladderstock.knowledge.vectors.embed_one", lambda text: [0.1] * 512)
    monkeypatch.setattr("ladderstock.knowledge.vectors.ensure_collection", lambda dim: "")
    monkeypatch.setattr("ladderstock.knowledge.vectors.collection_name", lambda: "kb")
    ann.search("分红", top_k=3, **kwargs)
    return captured["body"]


def test_search_default_filter_only_source(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _capture_search(monkeypatch)
    must = body["filter"]["must"]
    assert must == [{"key": "source", "match": {"value": "announcement"}}]


def test_search_adds_kind_and_period_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _capture_search(monkeypatch, doc_kinds=["financial_report"], periods=["2026Q3"])
    must = body["filter"]["must"]
    keys = [item["key"] for item in must]
    assert keys == ["source", "doc_kind", "period"]
    assert must[1]["match"] == {"any": ["financial_report"]}
    assert must[2]["match"] == {"any": ["2026Q3"]}


def test_search_adds_date_range(monkeypatch: pytest.MonkeyPatch) -> None:
    body = _capture_search(monkeypatch, ann_from="2026-01-01", ann_to="2026-09-30")
    must = body["filter"]["must"]
    date_clause = next(item for item in must if item["key"] == "ann_date")
    assert date_clause["range"] == {"gte": "2026-01-01", "lte": "2026-09-30"}


def test_search_empty_query_returns_empty() -> None:
    assert ann.search("   ") == []


def test_catalog_reports_kinds_and_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_request(method, path, *, json_body=None):
        if path.endswith("/points/count"):
            return {"result": {"count": 42}}
        if path.endswith("/facet"):
            key = json_body["key"]
            return {
                "result": {
                    "hits": [{"value": "notice" if key == "doc_kind" else "2026Q3", "count": 7}]
                }
            }
        return {"result": {"status": "acknowledged"}}

    monkeypatch.setattr("ladderstock.knowledge.vectors._request", fake_request)
    monkeypatch.setattr("ladderstock.knowledge.vectors.collection_name", lambda: "kb")
    monkeypatch.setattr(ann, "symbols", lambda: ["600519", "000001"])

    info = ann.catalog()
    assert info["ok"] is True
    assert info["points"] == 42
    assert info["doc_kinds"] == {"notice": 7}
    assert info["periods"] == {"2026Q3": 7}
    assert info["source_symbols"] == 2
    field_keys = {field["key"] for field in info["fields"]}
    assert {"symbol", "doc_kind", "period", "ann_date", "content_chars"} <= field_keys


def test_apply_local_fulltext_replaces_short_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LDST_FULLTEXT_DIR", str(tmp_path))
    key = "2026-10-31|贵州茅台2026年第三季度报告"
    (tmp_path / "600519.jsonl").write_text(
        json.dumps({"key": key, "dwd_id": 1, "chars": 500, "text": "真" * 500}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    row = ann.AnnouncementRow(
        id=1,
        symbol="600519",
        ann_date="2026-10-31",
        title="贵州茅台2026年第三季度报告",
        body="标题",
        event_id="evt",
        content_chars=3,
    )
    patched = ann._apply_local_fulltext([row], "600519")
    assert patched[0].content_chars == 500
    assert patched[0].body == "真" * 500


def test_apply_local_fulltext_keeps_long_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LDST_FULLTEXT_DIR", str(tmp_path))
    row = ann.AnnouncementRow(
        id=2,
        symbol="600519",
        ann_date="2026-10-31",
        title="某公告",
        body="已" * 900,
        event_id="evt",
        content_chars=900,
    )
    assert ann._apply_local_fulltext([row], "600519")[0].content_chars == 900


def test_fulltext_store_roundtrip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("LDST_FULLTEXT_DIR", str(tmp_path))
    assert ft.load("000001") == {}
    ft._append("000001", [{"key": "2026-10-31|标题", "text": "正文"}])
    assert ft.load("000001") == {"2026-10-31|标题": "正文"}


def test_fulltext_key_normalizes_none() -> None:
    assert ft._key(None, None) == "|"
    assert ft._key("2026-10-31", " 标题 ") == "2026-10-31|标题"
