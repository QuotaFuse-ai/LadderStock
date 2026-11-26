"""资讯批量采集：公告/研报按代码池分批传 code，快讯整批一次，不重复打 peer。

起因是一条真实缺口：公告栏目上游要求 `code` 必填，排班里的批量入口从来不传，
`news_runs` 每轮都是 `skipped`，`news_items` 里一条公告都没有。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from ladderstock.cli import schedule as schedule_module
from ladderstock.news import candidates as news_candidates
from ladderstock.news import stream as news_stream


class _Registry:
    def __init__(self, pool: list[str]) -> None:
        self.datasets = {"daily_k": {"codes": pool}}


def _result(datasets, *, items=1, stored=1, written=1, errors=None, per_code=None):
    return SimpleNamespace(
        items=list(range(items)),
        stored=stored,
        statuses=[
            SimpleNamespace(dataset=d, status="ok", detail="", per_code=dict(per_code or {}))
            for d in datasets
        ],
        archive={"written": written, "datasets": {}, "errors": list(errors or [])},
    )


@pytest.fixture(autouse=True)
def _candidates_offline(monkeypatch):
    """前置筛在本文件里默认不生效（fallback=回退全池），并且不写盘。

    这让原有的「按池分批」断言保持稳定：筛选本身另有专门用例。
    """
    monkeypatch.setattr(
        news_candidates,
        "write_candidates",
        lambda data_root, payload: None,
    )
    monkeypatch.setattr(
        news_candidates,
        "announcement_candidates",
        lambda pool, **kwargs: {
            "pool_size": len(pool),
            "extra": [],
            "union": [str(code) for code in pool],
            "movers": [],
            "snapshot_day": None,
            "threshold": 5.0,
            "fallback": True,
            "fallback_reason": "test",
            "note": f"分母 = 池 {len(pool)} 只（test）",
        },
    )
    yield


@pytest.fixture()
def captured(monkeypatch):
    calls: list[dict] = []

    def fake_collect_stream(**kwargs):
        calls.append(kwargs)
        return _result(kwargs["datasets"], items=2, stored=2, written=2)

    monkeypatch.setattr(news_stream, "collect_stream", fake_collect_stream)
    return calls


def test_code_datasets_are_batched_by_100(captured) -> None:
    pool = [f"{index:06d}" for index in range(250)]
    outcome = schedule_module._run_news_collect(
        {
            "id": "news_radar",
            "datasets": "announcement_cninfo,announcement_szse",
            "batch_size": 100,
        },
        registry=_Registry(pool),
    )

    assert outcome["status"] == "ok", outcome
    assert outcome["batches"] == 3
    assert [len(call["codes"]) for call in captured] == [100, 100, 50]
    assert all(call["datasets"] == ["announcement_cninfo", "announcement_szse"] for call in captured)
    # 250 只 × 2 个栏目都要在批次里传下去，没有丢掉池子尾部的 50 只
    assert sum(len(call["codes"]) for call in captured) == 250


def test_plain_datasets_run_once_without_codes(captured) -> None:
    outcome = schedule_module._run_news_collect(
        {"id": "news_radar", "datasets": ["flash_cls", "flash_eastmoney"]},
        registry=_Registry(["600000"]),
    )

    assert outcome["status"] == "ok", outcome
    assert len(captured) == 1
    assert captured[0]["codes"] == []
    assert captured[0]["datasets"] == ["flash_cls", "flash_eastmoney"]


def test_mixed_datasets_split_into_code_and_plain(captured) -> None:
    """公告与研报分开切批：公告要吃前置筛，研报仍按全池，不能共用一个批次。"""
    pool = [f"{index:06d}" for index in range(120)]
    outcome = schedule_module._run_news_collect(
        {
            "id": "news_radar",
            "datasets": "announcement_cninfo,research_report,flash_cls",
            "batch_size": 100,
        },
        registry=_Registry(pool),
    )

    assert outcome["status"] == "ok", outcome
    assert outcome["batches"] == 5
    assert [call["datasets"] for call in captured] == [
        ["announcement_cninfo"],
        ["announcement_cninfo"],
        ["research_report"],
        ["research_report"],
        ["flash_cls"],
    ]
    assert captured[-1]["codes"] == []


def test_empty_pool_skips_instead_of_calling(captured) -> None:
    outcome = schedule_module._run_news_collect(
        {"id": "news_radar", "datasets": "announcement_cninfo"},
        registry=_Registry([]),
    )

    assert outcome["status"] == "skipped"
    assert "代码池为空" in outcome["detail"]
    assert captured == []


def test_unknown_dataset_fails_fast(captured) -> None:
    outcome = schedule_module._run_news_collect(
        {"id": "news_radar", "datasets": "not_a_dataset"}, registry=_Registry(["600000"])
    )

    assert outcome["status"] == "failed"
    assert "未登记的资讯栏目" in outcome["detail"]
    assert captured == []


def test_archive_errors_are_reported_not_swallowed(monkeypatch) -> None:
    def fake_collect_stream(**kwargs):
        return _result(kwargs["datasets"], errors=["research_report: 历史库被占用"])

    monkeypatch.setattr(news_stream, "collect_stream", fake_collect_stream)
    outcome = schedule_module._run_news_collect(
        {"id": "news_radar", "datasets": "flash_cls"}, registry=_Registry(["600000"])
    )

    assert outcome["status"] == "degraded"
    assert any("归档失败" in item for item in outcome["problems"])


# --------------------------------------------------------------------------- #
# 公告前置筛（分母 = 池 ∪ 当日异动）：只查候选，不查全池；结果逐票留痕
# --------------------------------------------------------------------------- #


def _with_union(monkeypatch, pool, extra, insiders=None):
    """池内异动默认取 pool[0]：它进请求名单，但不撑大分母。"""
    insiders = list(insiders if insiders is not None else pool[:1])
    union = sorted([*pool, *extra])
    movers = [
        {"code": code, "name": "x", "pct": 7.0, "in_pool": True} for code in insiders
    ] + [{"code": code, "name": "y", "pct": -6.0, "in_pool": False} for code in extra]
    sent: dict = {}

    def _cand(p, **kwargs):
        return {
            "pool_size": len(p),
            "extra": list(extra),
            "union": union,
            "movers": movers,
            "snapshot_day": "2026-11-20",
            "threshold": 5.0,
            "fallback": False,
            "fallback_reason": "",
            "note": f"分母 = 池 {len(p)} 只 ∪ 池外异动 {len(extra)} 只 = {len(union)} 只",
        }

    monkeypatch.setattr(news_candidates, "announcement_candidates", _cand)
    monkeypatch.setattr(
        news_candidates, "write_candidates", lambda data_root, payload: sent.update(payload)
    )
    return union, sorted([*insiders, *extra]), sent


def test_candidate_filter_only_requests_movers(captured, monkeypatch) -> None:
    """请求名单是异动票（池内异动 + 池外异动），不是整个并集。

    这是前置筛真正省请求的地方：池内当天没异动的票不请求，但仍留在分母里。
    """
    pool = ["600000", "600009"]
    union, requested, sent = _with_union(monkeypatch, pool, ["300750"])

    def fake(**kwargs):
        captured.append(kwargs)
        return _result(
            kwargs["datasets"],
            per_code={"600000": 2, "300750": 1},
        )

    monkeypatch.setattr(news_stream, "collect_stream", fake)
    outcome = schedule_module._run_news_collect(
        {"id": "news_radar", "datasets": "announcement_cninfo", "batch_size": 100},
        registry=_Registry(pool),
        root=None,
    )

    assert outcome["status"] == "ok", outcome
    assert sorted(captured[0]["codes"]) == requested
    assert "600009" not in captured[0]["codes"]
    ann = outcome["ann_candidates"]
    assert ann["covered"] == 2
    assert ann["union_size"] == len(union) == 3
    assert ann["requested"] == 2
    assert ann["skipped"] == 1  # 600009 按策略未采，仍算在分母里
    assert ann["extra"] == ["300750"]
    assert ann["filtered"] is True
    # 逐票结果落 sidecar：covered 两只、0 条也算"采过"
    assert sent["collected"] == {"600000": 2, "300750": 1}
    assert sent["covered"] == 2
    assert "公告候选" in outcome["detail"]


def test_candidate_filter_no_movers_requests_nothing(captured, monkeypatch) -> None:
    """当天一只异动都没有：0 请求，但分母照旧，status 记 skipped 且有解释。"""
    pool = ["600000", "600009"]
    _with_union(monkeypatch, pool, [], insiders=[])
    outcome = schedule_module._run_news_collect(
        {"id": "news_radar", "datasets": "announcement_cninfo"}, registry=_Registry(pool)
    )

    assert captured == []
    assert outcome["status"] == "skipped"
    assert "0 请求" in outcome["detail"]
    assert outcome["ann_candidates"]["union_size"] == 2
    assert outcome["ann_candidates"]["covered"] == 0


def test_candidate_filter_off_falls_back_to_pool(captured, monkeypatch) -> None:
    pool = ["600000", "600009"]
    _with_union(monkeypatch, pool, ["300750"])
    outcome = schedule_module._run_news_collect(
        {
            "id": "news_radar",
            "datasets": "announcement_cninfo",
            "candidate_filter": False,
        },
        registry=_Registry(pool),
    )

    assert captured[0]["codes"] == pool
    assert "前置筛已关闭" in outcome["detail"]


def test_snapshot_missing_falls_back_to_pool(captured, monkeypatch) -> None:
    """没有行情快照时不能把采集变成 0 只：candidates 的 fallback 要回退全池。"""
    monkeypatch.setattr(
        news_candidates,
        "announcement_candidates",
        lambda pool, **kwargs: {
            "pool_size": len(pool),
            "extra": [],
            "union": [],
            "movers": [],
            "snapshot_day": None,
            "threshold": 5.0,
            "fallback": True,
            "fallback_reason": "本机没有报价快照",
            "note": f"分母 = 池 {len(pool)} 只（回退全池）",
        },
    )
    pool = ["600000", "600009"]
    outcome = schedule_module._run_news_collect(
        {"id": "news_radar", "datasets": "announcement_cninfo"}, registry=_Registry(pool)
    )

    assert captured[0]["codes"] == pool
    assert "回退全池" in outcome["detail"]
