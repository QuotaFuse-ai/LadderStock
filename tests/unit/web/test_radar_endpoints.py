"""R12 收口（QUOT-279）：四个只读接口的契约测试。

字段表在 `frontend/src/api/radar.ts` 与 `docs/specs/SPEC-RADAR-PAGES.md` §3–§6.1，
这里按那份契约逐条钉住，**每个接口都覆盖成功 / 空态 / 非法参数三条路径**：

| 接口 | 成功 | 空态 |
|---|---|---|
| `GET /api/news` | 投影 `news.sqlite` + 账本回放 | 没有资讯库 / 窗口无行 → 200+missing |
| `GET /api/events` | 五节固定 + 有源条目带溯源 | 有源全挂 → 200+missing 并说清 reason/ask |
| `GET /api/pool` | 打分结果原样投影（`null` 不补 0） | 缓存没同步 → 200+missing |
| `GET /api/positions` | 平面 JSON、与 CLI 同一份数字 | 没有持仓库 → 200 全零且不建空库 |

非法参数各有一组 422 用例（news 11 例、events 7 例、pool 4 例、positions 3 例）。

另钉四条口径：`GET` 不写库（账本行数不变、磁盘上不冒出新文件）；参数非法是
**422**（不是控制面的 400）；持仓**回环独占**（非回环 / 坏 `Host` 一律 403，
即使开了远程访问 + Token）；三个公开面是数据面，非回环照常 200。

全程离线：资讯与日历都用 `tests/fixtures` 的假 peer，候选池的快照被打桩，
行情缓存在 tmp 里本来就没有（现价取不到就是 `null`，这正是 §6.1 要的场景）。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fixtures.fake_peers import FakeTransport, make_news_registry
from fixtures.news_samples import news_handlers, news_tools

from ladderstock.core import pool as pool_module
from ladderstock.core.boundary import NEWS_STREAM_DATASETS
from ladderstock.core.privacy import private_keys_present, strip_private
from ladderstock.data.provider_registry import ProviderRegistry
from ladderstock.journal import position as position_module
from ladderstock.news import store as news_store
from ladderstock.news.calendar import NO_SOURCE_SECTIONS, SECTION_ORDER
from ladderstock.news.models import NewsItem
from ladderstock.news.stream import collect_stream
from ladderstock.web import control as control_plane
from ladderstock.web import radar
from ladderstock.web.radar import (
    NEWS_STORE_MISSING,
    NEWS_WINDOW_EMPTY,
    NO_ROWS_DETAIL,
    POSITIONS_DENIED,
    RadarPlane,
)
from ladderstock.web.server import create_app

LOOPBACK = ("127.0.0.1", 54321)
REMOTE = ("10.1.2.3", 54321)

START = "2026-10-29"
END = "2026-10-31"
MONTH = "2026-10"
CODE = "600519"
SENTINEL = "隐私哨兵-RADAR-9f2b"

#: 信封顶层键（`LadderEnvelope`），三个公开面共用
ENVELOPE_KEYS = {
    "domain",
    "topic",
    "event_id",
    "data_as_of",
    "payload",
    "source",
    "source_detail",
    "fetched_at",
    "freshness",
    "confidence",
    "source_status",
}


# --------------------------------------------------------------------------- #
# 桩
# --------------------------------------------------------------------------- #

def _registry(root: Path, *, failing: tuple[str, ...] = ()) -> ProviderRegistry:
    transport = FakeTransport(handlers=news_handlers(failing=failing), tools=news_tools())
    return make_news_registry(transport, root)


def _client(
    root: Path,
    *,
    client: tuple[str, int] = LOOPBACK,
    failing: tuple[str, ...] = (),
) -> TestClient:
    plane = RadarPlane(data_root=root, registry=_registry(root, failing=failing))
    app = create_app(data_root=root, dist_dir=root / "no-dist", radar_plane=plane)
    return TestClient(app, client=client)


def _seed_news(root: Path, *, failing: tuple[str, ...] = ()) -> Any:
    """按真实源链跑一次采集并落库：接口读的就是这个库（GET 自己不取数）。"""
    return collect_stream(
        start=date.fromisoformat(START),
        end=date.fromisoformat(END),
        codes=[CODE],
        registry=_registry(root, failing=failing),
        root=root,
        persist=True,
    )


def _pool_snapshot(dataset: str, day: Any, root: Any = None) -> tuple[list[dict[str, Any]], str]:
    """候选池三份快照打桩：给涨停池 + 题材板，**故意不给资金流**（考 `null` 不补 0）。"""
    if dataset == "limit_pool":
        return (
            [
                {"code": CODE, "name": "贵州茅台", "boards": 3, "turnover_rate": 8.0,
                 "hot_topic": "白酒"},
                {"code": "000001", "name": "平安银行", "boards": 1, "turnover_rate": 2.0},
            ],
            "fixture",
        )
    if dataset == "concept_board":
        return [{"name": "白酒", "change_pct": 4.0}], "fixture"
    return [], "fixture"


# --------------------------------------------------------------------------- #
# §3 GET /api/news
# --------------------------------------------------------------------------- #

#: 语料里 research_report 的稿子落在 10-29→10-31 之外：库里这一栏 0 行，投影要说 `empty`
#: 所以「全绿」的用例按页面自己的栏目筛选把那一路排除掉，另有一例专门钉 empty。
ALL_SOURCES = list(NEWS_STREAM_DATASETS)
OK_SOURCES = [dataset for dataset in ALL_SOURCES if dataset != "research_report"]


def test_news_success_envelope_and_item_shape(tmp_path: Path) -> None:
    _seed_news(tmp_path)
    body = _client(tmp_path).get(
        "/api/news", params={"date": START, "until": END, "source": OK_SOURCES}
    ).json()

    assert set(body) == ENVELOPE_KEYS
    assert body["domain"] == "news" and body["topic"] == "news_stream"
    payload = body["payload"]
    # 前端 `unwrap()` 只下钻 payload，信封顶层读不到 → 状态与时点必须在载荷里有一份副本
    assert payload["data_source_status"] == "ok"
    assert payload["data_as_of"] == body["data_as_of"] == END
    assert payload["window"] == {"start": START, "end": END}
    assert payload["sources"] == OK_SOURCES

    assert payload["items"], "语料里 10-29→10-31 有稿子，投影不该给空数组"
    item = payload["items"][0]
    assert set(item) == {
        "id", "source", "title", "url", "published_at", "related_codes", "summary",
        "content_hash", "origins", "provenance", "industries",
    }
    assert item["origins"] and "dataset" in item["origins"][0]
    assert item["industries"] == []  # SPEC §7 缺口 2：R6 不产出行业标签，键给空数组

    # events[] 是 §3 的页面字段，且必须指回这一屏的条目
    assert payload["events"], "语料里有股东大会通知 / 解禁提示，抽取不该为空"
    for event in payload["events"]:
        assert event["item_id"] in {row["id"] for row in payload["items"]}
        assert event["evidence"]["url"]

    assert [row["dataset"] for row in payload["sources_status"]] == OK_SOURCES
    assert {row["status"] for row in payload["sources_status"]} == {"ok"}
    assert payload["exit_code"] == 0


def test_news_column_without_rows_in_window_is_empty_not_failed(tmp_path: Path) -> None:
    """库里这一栏本窗口 0 行 = `empty` + 去哪个命令补；SPEC §3：有非 ok 栏目 → partial。"""
    _seed_news(tmp_path)
    payload = _client(tmp_path).get("/api/news", params={"date": START, "until": END}).json()[
        "payload"
    ]

    rows = {row["dataset"]: row for row in payload["sources_status"]}
    assert [row["dataset"] for row in payload["sources_status"]] == ALL_SOURCES
    assert rows["research_report"]["status"] == "empty"
    assert rows["research_report"]["items"] == 0
    assert rows["research_report"]["detail"] == NO_ROWS_DETAIL
    assert rows["research_report"]["degraded"] is False
    assert rows["announcement_cninfo"]["status"] == "ok"
    assert payload["data_source_status"] == "partial"
    assert payload["items"], "某一路没行不等于整页没数据"
    assert payload["exit_code"] == 0, "empty 不是取数失败，不能报 8"


def test_news_summary_matches_the_ledger_and_keeps_unanswerable_nulls(tmp_path: Path) -> None:
    """`summary` 里能问到的数取自取数账本，问不到的**置 null**，不拿窗口条数冒充。"""
    seeded = _seed_news(tmp_path)
    payload = _client(tmp_path).get("/api/news", params={"date": START, "until": END}).json()[
        "payload"
    ]
    summary = payload["summary"]

    assert summary["fetched"] == seeded.fetched
    assert summary["merged_duplicates"] == seeded.merged
    assert summary["stored"] == seeded.stored
    assert summary["persisted"] is True
    assert summary["items"] == len(payload["items"])
    assert summary["events"] == len(payload["events"]) == seeded.events
    # 新增 / 已知 / 窗口外是「取数那一刻」才知道的数，投影问不出来
    for key in ("new_items", "known_items", "outside_window"):
        assert summary[key] is None, key


def test_news_get_does_not_write(tmp_path: Path) -> None:
    _seed_news(tmp_path)
    conn = news_store.connect(tmp_path)
    assert conn is not None
    try:
        before = news_store.counts(conn)
    finally:
        news_store.close(conn)

    client = _client(tmp_path)
    for _ in range(3):
        assert client.get("/api/news", params={"date": START, "until": END}).status_code == 200
        assert client.get("/api/events", params={"month": MONTH, "code": CODE}).status_code == 200

    conn = news_store.connect(tmp_path)
    assert conn is not None
    try:
        assert news_store.counts(conn) == before, "GET 把页面变成了写入方"
    finally:
        news_store.close(conn)


def test_news_replays_failed_columns_from_the_ledger(tmp_path: Path) -> None:
    """某一路挂了：条目照常出，栏目账本如实回放 `failed` + 原因，状态降级 partial。"""
    _seed_news(tmp_path, failing=("cls_telegraph", "wallstreetcn_lives"))
    payload = _client(tmp_path).get("/api/news", params={"date": START, "until": END}).json()[
        "payload"
    ]

    rows = {row["dataset"]: row for row in payload["sources_status"]}
    failed = [key for key, row in rows.items() if row["status"] == "failed"]
    assert set(failed) == {"flash_cls", "flash_wallstreetcn"}
    for key in failed:
        assert rows[key]["detail"] and rows[key]["degraded"] is True, "失败行必须自带原因"
        assert rows[key]["items"] == 0
    assert payload["items"], "部分栏目失败不影响其余栏目照常出数据"
    assert payload["data_source_status"] == "partial"
    assert payload["exit_code"] == 8  # 沿用 R6：部分栏目挂 → 8


def test_news_empty_columns_say_no_rows_not_failed(tmp_path: Path) -> None:
    """查过但本窗口没有行 = `empty`（说清去哪个命令补），与打不通 = `failed` 是两件事。"""
    _seed_news(tmp_path)
    payload = _client(tmp_path).get(
        "/api/news", params={"date": "2026-01-05", "until": "2026-01-06"}
    ).json()["payload"]

    assert payload["items"] == [] and payload["events"] == []
    assert payload["data_source_status"] == "missing"
    assert payload["reason"] == NEWS_WINDOW_EMPTY
    assert {row["status"] for row in payload["sources_status"]} == {"empty"}
    assert all(row["items"] == 0 for row in payload["sources_status"])
    assert NO_ROWS_DETAIL in payload["sources_status"][0]["detail"]
    # 没有覆盖这个窗口的取数账本 → 无从得知，置 null 不补 0
    assert payload["summary"]["fetched"] is None
    assert payload["exit_code"] == 0


def test_news_without_store_is_200_missing_not_404(tmp_path: Path) -> None:
    body = _client(tmp_path).get("/api/news").json()

    assert body["data_as_of"] is None and body["confidence"] == "low"
    assert body["source_status"] == "partial"  # 外壳词表没有 missing
    payload = body["payload"]
    assert payload["data_source_status"] == "missing"
    assert payload["reason"] == NEWS_STORE_MISSING
    assert payload["items"] == [] and payload["events"] == []
    assert payload["sources_status"] == [], "空库的真相是「从来没取过」，不是 8 栏都查过"
    assert payload["summary"]["persisted"] is False
    assert not (tmp_path / news_store.DB_FILENAME).exists(), "只读查询不该建空库"


def test_news_filters_by_source_code_and_limit(tmp_path: Path) -> None:
    _seed_news(tmp_path)
    client = _client(tmp_path)

    payload = client.get(
        "/api/news",
        params={"date": START, "until": END, "source": "announcement_cninfo", "code": CODE},
    ).json()["payload"]
    assert payload["sources"] == ["announcement_cninfo"]
    assert [row["dataset"] for row in payload["sources_status"]] == ["announcement_cninfo"]
    assert payload["codes"] == [CODE]
    assert payload["items"], "公告栏里有 600519 的稿子"
    for item in payload["items"]:
        assert item["source"] == "announcement_cninfo"
        assert CODE in item["related_codes"]

    whole = client.get("/api/news", params={"date": START, "until": END}).json()["payload"]
    limited = client.get(
        "/api/news", params={"date": START, "until": END, "limit": 2}
    ).json()["payload"]
    assert len(limited["items"]) == 2
    assert [row["id"] for row in limited["items"]] == [r["id"] for r in whole["items"][:2]]
    # 展示条数变了，栏目账本的计数不变（那是「库里有多少」，不是「这一屏给几条」）
    assert limited["summary"]["items"] == 2
    assert limited["sources_status"] == whole["sources_status"]


def test_news_undated_rows_are_counted_not_shown(tmp_path: Path) -> None:
    """没有发布时间的条目是跨轮次合并候选，不是这一天的视图：计数、不进 items。"""
    _seed_news(tmp_path)
    conn = news_store.connect(tmp_path)
    assert conn is not None
    try:
        news_store.upsert_items(
            conn,
            [
                NewsItem(
                    id="undated-1",
                    source="flash_cls",
                    title="没有发布时间的稿子",
                    url="https://example.com/undated-1",
                    published_at=None,
                    related_codes=(CODE,),
                    summary="",
                    content_hash="h",
                    dedup_key="url:https://example.com/undated-1",
                )
            ],
        )
    finally:
        news_store.close(conn)

    payload = _client(tmp_path).get("/api/news", params={"date": START, "until": END}).json()[
        "payload"
    ]
    assert payload["summary"]["dropped_undated"] == 1
    assert "undated-1" not in {item["id"] for item in payload["items"]}


@pytest.mark.parametrize(
    "params",
    [
        {"date": "2026-13-01"},  # 月份越界
        {"date": "20261029"},  # 不是 YYYY-MM-DD
        {"date": "昨天"},
        {"date": "2026-02-30"},  # 形状对、日子不存在
        {"date": END, "until": START},  # until 早于 date
        {"date": START, "until": "2026-12-31"},  # 跨度 > 31 天
        {"source": "no_such_column"},
        {"code": "abc"},
        {"limit": "0"},
        {"limit": "1001"},
        {"limit": "-3"},
    ],
)
def test_news_invalid_params_are_422(tmp_path: Path, params: dict[str, str]) -> None:
    response = _client(tmp_path).get("/api/news", params=params)
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert isinstance(detail, str) and detail, "422 必须说清哪个参数错在哪"


# --------------------------------------------------------------------------- #
# §4 GET /api/events
# --------------------------------------------------------------------------- #

def test_events_success_keeps_the_five_fixed_sections(tmp_path: Path) -> None:
    body = _client(tmp_path).get("/api/events", params={"month": MONTH, "code": CODE}).json()

    assert body["domain"] == "news" and body["topic"] == "event_calendar"
    assert body["data_as_of"] == "2026-10-01"

    payload = body["payload"]
    assert payload["data_source_status"] == "ok"
    assert [section["key"] for section in payload["sections"]] == list(SECTION_ORDER)
    assert payload["window"] == {"start": "2026-10-01", "end": "2026-10-31"}
    assert payload["codes"] == [CODE]

    sourced = {s["key"] for s in payload["sections"] if s["status"] == "sourced"}
    assert sourced == {"lockup_expiry", "earnings_preview"}
    for section in payload["sections"]:
        if section["status"] != "no_source":
            continue
        declared = next(s for s in NO_SOURCE_SECTIONS if s["key"] == section["key"])
        assert section["entries"] == []
        assert section["reason"] == declared["reason"] and section["ask"] == declared["ask"]
    earnings = next(s for s in payload["sections"] if s["key"] == "earnings_preview")
    assert [entry["date"] for entry in earnings["entries"]] == ["2026-10-14"]
    entry = earnings["entries"][0]
    assert entry["code"] == CODE and entry["provenance"]["trace_id"]
    assert entry["id"].startswith("earnings_preview:2026-10-14:")


def test_events_extracted_events_are_evidence_backed_rows_from_the_store(
    tmp_path: Path,
) -> None:
    """公告正文里真写了日期的那部分走规则抽取：每条带原文片段 + url，落款 rule_extracted。"""
    _seed_news(tmp_path)
    payload = _client(tmp_path).get(
        "/api/events", params={"month": MONTH, "code": CODE}
    ).json()["payload"]

    assert payload["extracted_events"], "语料里有股东会通知，抽取不该为空"
    for event in payload["extracted_events"]:
        assert event["basis"] == "rule_extracted"
        assert event["evidence"]["snippet"] and event["evidence"]["url"]
        assert event["date_basis"]
        assert event["date"].startswith(MONTH)
        assert CODE in event["codes"]


def test_events_all_sources_down_is_200_with_declared_gaps(tmp_path: Path) -> None:
    body = _client(tmp_path, failing=("lockup_expiry", "earnings_forecast")).get(
        "/api/events", params={"month": MONTH, "code": CODE}
    ).json()

    assert body["data_as_of"] is None
    payload = body["payload"]
    assert payload["data_source_status"] == "missing"
    assert set(payload["summary"]["no_source"]) == set(SECTION_ORDER)
    assert payload["exit_code"] == 4  # 有源小节一个都没成
    for key in ("lockup_expiry", "earnings_preview"):
        section = next(s for s in payload["sections"] if s["key"] == key)
        assert section["status"] == "no_source" and section["entries"] == []
        assert "取数失败" in section["reason"]


@pytest.mark.parametrize(
    "params",
    [
        {},  # 缺 month
        {"month": ""},
        {"month": "2026-13"},
        {"month": "202610"},
        {"month": "2026-10-01"},
        {"month": "2026-10", "code": "not-a-code"},
        {"month": "2026-10", "limit": "0"},
    ],
)
def test_events_invalid_params_are_422(tmp_path: Path, params: dict[str, str]) -> None:
    response = _client(tmp_path).get("/api/events", params=params)
    assert response.status_code == 422, response.text
    assert isinstance(response.json()["detail"], str)


# --------------------------------------------------------------------------- #
# §5 GET /api/pool
# --------------------------------------------------------------------------- #

def test_pool_success_projects_scores_without_recomputing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pool_module, "dataset_snapshot", _pool_snapshot)
    body = _client(tmp_path).get("/api/pool", params={"date": END}).json()

    assert body["domain"] == "sentiment" and body["topic"] == "candidate_pool"
    assert body["data_as_of"] == END

    payload = body["payload"]
    assert payload["data_source_status"] == "ok"
    assert payload["available"] is True and payload["candidate_count"] == 2
    assert payload["formula"] == pool_module.SCORE_FORMULA
    assert payload["weights"] == pool_module.WEIGHTS
    assert payload["missing"] == ["fund_flow"], "没给资金流就要在 missing 里说清"

    top = payload["candidates"][0]
    assert set(top) == {"code", "name", "score", "parts", "absent_parts", "raw"}
    assert set(top["parts"]) == set(pool_module.SUB_SCORES)
    assert top["parts"]["capital"] is None  # 缺数据的分项是 null，不是 0
    assert "capital" in top["absent_parts"]
    assert top["score"] is not None


def test_pool_without_cache_is_200_missing(tmp_path: Path) -> None:
    body = _client(tmp_path).get("/api/pool", params={"date": END}).json()

    assert body["data_as_of"] is None and body["confidence"] == "low"
    payload = body["payload"]
    assert payload["data_source_status"] == "missing"
    assert payload["available"] is False and payload["candidates"] == []
    assert payload["reason"]
    assert payload["counts"]["limit_pool_rows"] == 0


@pytest.mark.parametrize("params", [{"date": "2026-13-01"}, {"date": "x"}, {"limit": "0"},
                                    {"limit": "1001"}])
def test_pool_invalid_params_are_422(tmp_path: Path, params: dict[str, str]) -> None:
    response = _client(tmp_path).get("/api/pool", params=params)
    assert response.status_code == 422, response.text
    assert isinstance(response.json()["detail"], str)


# --------------------------------------------------------------------------- #
# §6.1 GET /api/positions
# --------------------------------------------------------------------------- #

def _seed_positions(root: Path) -> None:
    position_module.add_position(
        CODE, 12.34, 1000.0, entry_date="2026-09-01", industry="白酒",
        note=SENTINEL, root=root,
    )
    position_module.add_position(
        "000001", 10.0, 500.0, entry_date="2026-09-02", industry="银行",
        note="另一条", root=root,
    )


def test_positions_is_flat_json_not_an_envelope(tmp_path: Path) -> None:
    _seed_positions(tmp_path)
    response = _client(tmp_path).get("/api/positions")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"

    body = response.json()
    assert "payload" not in body and "domain" not in body and "fetched_at" not in body
    assert body["local_only"] is True and body["store"] == "local_sqlite"
    assert body["data_source_status"] == "ok"
    assert body["position_count"] == 2

    top = next(item for item in body["items"] if item["code"] == CODE)
    assert top["note"] == SENTINEL  # 本机终端 / 本机页面能看到，隐私在闸门而不在投影
    assert top["latest_price"] is None  # 缓存里没有 daily_k：不猜价
    assert top["market_value"] is None and top["unrealized_pnl"] is None
    assert body["valuation"]["total_cost"] == round(12.34 * 1000 + 10.0 * 500, 2)
    assert body["valuation"]["market_value"] is None
    assert body["valuation"]["unpriced_cost"] == body["valuation"]["total_cost"]
    assert body["concentration"]["basis"] == "cost"
    assert [row["code"] for row in body["concentration"]["holdings"]]
    assert "daily_k" in body["price_lookup"]["reason"]


def test_positions_are_the_cli_numbers_not_a_reimplementation(tmp_path: Path) -> None:
    """接口层只做投影：与 `ldst position list` 调的同一个 `compute_metrics()`，逐键相等。"""
    _seed_positions(tmp_path)
    body = _client(tmp_path).get("/api/positions").json()

    expected = position_module.compute_metrics(
        position_module.list_positions(root=tmp_path), {}, as_of=None
    )
    assert {key: value for key, value in body.items() if key != "price_lookup"} == expected


def test_empty_positions_store_is_200_without_creating_a_file(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/api/positions")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["data_source_status"] == "missing"
    assert body["position_count"] == 0 and body["items"] == []
    assert body["valuation"]["total_cost"] == 0.0
    assert body["valuation"]["coverage_pct"] is None  # 无样本不拿 0 冒充
    assert body["concentration"]["available"] is False and body["concentration"]["holdings"] == []
    assert body["price_lookup"]["reason"] == "本机没有持仓，无需查价"
    assert list(tmp_path.iterdir()) == [], "只读查询连空库都不该建"


def test_positions_are_loopback_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_positions(tmp_path)
    # Token 管「谁连得进来」，不管「隐私数据该不该出去」：开了远程也不能读持仓
    monkeypatch.setenv("LDST_WEB_ALLOW_REMOTE", "1")
    monkeypatch.setenv("LDST_WEB_TOKEN", "0123456789abcdef0123")

    denied = _client(tmp_path, client=REMOTE).get("/api/positions")
    assert denied.status_code == 403
    assert denied.json()["detail"] == POSITIONS_DENIED
    assert SENTINEL not in denied.text and "12.34" not in denied.text

    rebinding = _client(tmp_path).get("/api/positions", headers={"host": "evil.example.com"})
    assert rebinding.status_code == 403
    assert "Host" in rebinding.json()["detail"]

    assert _client(tmp_path).get("/api/positions").status_code == 200


@pytest.mark.parametrize("params", [{"date": "2026-13-01"}, {"date": "20261029"}, {"date": "abc"}])
def test_positions_invalid_date_is_422(tmp_path: Path, params: dict[str, str]) -> None:
    _seed_positions(tmp_path)
    response = _client(tmp_path).get("/api/positions", params=params)
    assert response.status_code == 422, response.text
    assert "YYYY-MM-DD" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# 只读 / 数据面 / 隐私面：四条共用
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "path", ["/api/news", "/api/events", "/api/pool", "/api/positions"]
)
def test_write_methods_are_not_registered(tmp_path: Path, path: str) -> None:
    """§6.2 的写接口按 §7 缺口 6 另议：这里连方法都不提供。"""
    client = _client(tmp_path)
    for method in ("post", "put", "patch", "delete"):
        response = getattr(client, method)(path)
        assert response.status_code == 405, (path, method, response.status_code)
        assert response.headers["allow"] == "GET"


@pytest.mark.parametrize(
    ("path", "params"),
    [("/api/news", {"date": START, "until": END}), ("/api/pool", {"date": END})],
)
def test_public_surfaces_stay_on_the_data_plane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str, params: dict[str, str]
) -> None:
    """资讯 / 候选池是公开数据，和 `/api/sentiment` 同层：非回环来源照常 200。

    这条与 `test_positions_are_loopback_only` 是同一个决定的两面，写在这里是为了让
    「哪些面加了闸门」这件事在测试里可读，而不是只活在一段注释里。
    """
    _seed_positions(tmp_path)
    if path == "/api/news":
        _seed_news(tmp_path)
    monkeypatch.setenv(control_plane.CONTROL_ENABLED_ENV, "0")  # 只关写面

    assert _client(tmp_path, client=REMOTE).get(path, params=params).status_code == 200


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/api/news", {"date": START, "until": END}),
        ("/api/events", {"month": MONTH, "code": CODE}),
        ("/api/pool", {"date": END}),
    ],
)
def test_enveloped_payloads_are_strip_private_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str, params: dict[str, str]
) -> None:
    """公开面的载荷必须是 `strip_private` 的不动点：持仓字段一个都不许在里面。"""
    _seed_positions(tmp_path)
    monkeypatch.setattr(pool_module, "dataset_snapshot", _pool_snapshot)
    if path == "/api/news":
        _seed_news(tmp_path)
    body = _client(tmp_path).get(path, params=params).json()

    assert strip_private(body) == body
    assert private_keys_present(body) == []
    assert SENTINEL not in json.dumps(body, ensure_ascii=False, default=str)
    assert "entry_date" not in json.dumps(body, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# 内部错误 → 500（不冒成 200 空态，也不冒成 422）
# --------------------------------------------------------------------------- #

def _boom(error: Exception) -> Any:
    def call(*_args: Any, **_kwargs: Any) -> Any:
        raise error

    return call


@pytest.mark.parametrize(
    ("path", "holder", "attribute", "error", "params"),
    [
        ("/api/news", news_store, "load_items", sqlite3.OperationalError("database is locked"),
         {"date": START, "until": END}),
        ("/api/events", radar, "build_calendar", OSError("日历读不动"), {"month": MONTH}),
        ("/api/pool", pool_module, "compute_pool", RuntimeError("archive damaged"),
         {"date": END}),
        ("/api/positions", radar, "list_positions", sqlite3.DatabaseError("journal corrupt"), {}),
    ],
)
def test_read_failures_are_500(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    holder: Any,
    attribute: str,
    error: Exception,
    params: dict[str, str],
) -> None:
    """库读不动 / 上游抛系统错误 → 500，并且不能把内部异常文本原样吐回去。"""
    if path == "/api/news":
        _seed_news(tmp_path)
    monkeypatch.setattr(holder, attribute, _boom(error))

    response = _client(tmp_path).get(path, params=params)
    assert response.status_code == 500, response.text
    detail = response.json()["detail"]
    assert detail.startswith(radar.READ_FAILURE)
    assert str(error) in detail
