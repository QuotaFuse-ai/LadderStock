"""资讯流的合并语义：去重生效、幂等重跑、时间窗、降级顺序（R6 要求 1 与验收项）。

验收口径直接对应 issue 原文：

- 「同一 fixture 重跑幂等」→ `test_rerun_is_idempotent`
- 「去重生效（同一条稿子多源只留一条并记录来源列表）」→
  `test_cross_source_story_keeps_one_row_with_all_origins`
- 「资讯流里没有持仓 / 自选 / 交易日志字段」→ `test_no_private_keys_anywhere`

去重的反面同样重要：两份真公告只是标题撞了，不能合并（`_same_title_different_day_`）。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from fixtures.news_samples import CROSS_SOURCE_TITLE, SZSE_DATA, WINDOW_DAY, news_tools

from ladderstock.core.privacy import PRIVATE_KEYS, private_keys_present
from ladderstock.errors import EXIT_OK, EXIT_PARTIAL, EXIT_SOURCE, LadderStockError
from ladderstock.news import normalize
from ladderstock.news import store as news_store
from ladderstock.news.stream import (
    CODE_REQUIRED_DATASETS,
    MAX_WINDOW_DAYS,
    STATUS_EMPTY,
    STATUS_FAILED,
    STATUS_OK,
    STATUS_SKIPPED,
    collect_stream,
    dedupe,
    resolve_datasets,
)

#: 全部资讯工具，含同 peer 备源：只打主源会剩一条备源活着，退出码就成了 8 而不是 4
ALL_NEWS_TOOLS = news_tools()


def _by_title(result, fragment: str):
    return [item for item in result.items if fragment in item.title]


# --------------------------------------------------------------------------- #
# 去重
# --------------------------------------------------------------------------- #

def test_cross_source_story_keeps_one_row_with_all_origins(stream_of) -> None:
    """同一条稿子在三个栏目出现：留一条，`origins` 记下全部来源（不静默丢弃）。"""
    result, _transport = stream_of()
    rows = _by_title(result, CROSS_SOURCE_TITLE)
    assert len(rows) == 1, f"跨源同稿应该收成一条，实际 {len(rows)} 条"
    origins = {str(origin["dataset"]) for origin in rows[0].origins}
    assert origins == {"news_cctv", "flash_cls", "flash_wallstreetcn"}, origins
    assert result.merged > 0


def test_same_title_different_day_is_not_merged() -> None:
    """实测平安银行有两份《董事会决议公告》（10-24 / 09-30）：标题合并必须再看一天。"""
    items = normalize.decode("announcement_szse", SZSE_DATA)
    board = [item for item in items if "董事会决议公告" in item.title]
    assert len(board) == 2
    merged, dropped = dedupe(board)
    assert len(merged) == 2 and dropped == 0
    assert {str(item.day) for item in merged} == {"2026-10-24", "2026-09-30"}


def test_same_url_different_layout_is_merged() -> None:
    """同一地址被两个栏目以不同排版引用：规范化 url 先赢，只留一条。"""
    left = normalize.decode(
        "flash_cls", [{
            "title": "【快讯】央行降准",
            "url": "https://www.cls.cn/detail/9",
            "publish_time": 1_793_323_800,
        }]
    )
    right = normalize.decode(
        "flash_wallstreetcn",
        [{
            "title": "央行 降准",
            "uri": "https://www.cls.cn/detail/9/?utm_source=x",
            "publish_time": 1_793_323_800,
        }],
    )
    merged, dropped = dedupe([*left, *right])
    assert len(merged) == 1 and dropped == 1
    assert {str(origin["dataset"]) for origin in merged[0].origins} == {
        "flash_cls",
        "flash_wallstreetcn",
    }


def test_transitive_duplicates_collapse_into_one_group() -> None:
    """A 与 B 同标题、B 与 C 同地址 → 三条收成一条（字典式去重会留两条，所以用并查集）。"""
    a = normalize.decode("flash_cls", [{
        "title": "铼金镍锆",
        "url": "https://a/1",
        "publish_time": 1_793_323_800,
    }])[0]
    b = normalize.decode("flash_wallstreetcn", [{
        "title": "铼金镍锆",
        "uri": "https://b/2",
        "publish_time": 1_793_323_800,
    }])[0]
    c = normalize.decode("news_cctv", [{"title": "另一个标题", "url": "https://b/2"}])[0]
    merged, dropped = dedupe([a, b, c])
    assert len(merged) == 1 and dropped == 2
    assert len(merged[0].origins) == 3


def test_merged_item_keeps_the_longest_summary_and_union_of_codes() -> None:
    left = normalize.decode(
        "flash_cls", [{
            "title": "同一件事",
            "url": "https://a/1",
            "content": "短",
            "publish_time": 1_793_323_800,
        }]
    )[0]
    right = normalize.decode(
        "news_eastmoney_stock",
        {"code": "600000", "news": [{
            "title": "同一件事",
            "url": "https://b/2",
            "summary": "长得多的一段摘要",
            "date": "2026-10-30T09:30:00",
        }]},
    )[0]
    merged, _dropped = dedupe([left, right])
    assert len(merged) == 1
    assert merged[0].summary == "长得多的一段摘要"
    assert "600000" in merged[0].related_codes


def test_dedupe_on_empty_list() -> None:
    assert dedupe([]) == ([], 0)


# --------------------------------------------------------------------------- #
# 幂等重跑（验收项 1）
# --------------------------------------------------------------------------- #

def test_rerun_is_idempotent(tmp_path: Path, news_registry) -> None:
    """同一 fixture 跑两遍：id 集合、行数、事件数都不变，第二遍「新增 0 条」。"""
    first, _r1 = _persist_run(tmp_path, news_registry)
    assert first.new_items == len(first.items), "第一轮全是新的"
    second, r2 = _persist_run(tmp_path, news_registry)

    assert {item.id for item in first.items} == {item.id for item in second.items}
    assert second.new_items == 0, "重跑不该被当成写入了新东西"
    assert second.known_items == len(
        second.items,
    ), "新增/已知按窗口视图数，第一轮的 15 条第二轮全都认识"
    assert second.stored == first.stored

    conn = news_store.connect(tmp_path)
    try:
        counts = news_store.counts(conn)
        assert counts["news_items"] == len(second.items)
        assert counts["news_events"] == second.events
        assert counts["news_runs"] == 2, "每轮都留一行运行记录：幂等指数据，不是指不记账"
    finally:
        news_store.close(conn)


def _persist_run(root, news_registry):
    transport, registry = news_registry()
    result = collect_stream(
        start=date(2026, 10, 29),
        end=date(2026, 10, 31),
        codes=["600519"],
        registry=registry,
        root=root,
        persist=True,
    )
    return result, transport


def test_ids_are_stable_across_runs_and_processes(tmp_path: Path, news_registry) -> None:
    """id 只由去重键派生：换一次注册表实例（模拟下一次进程启动）还是同一批 id。"""
    first, _t1 = _persist_run(tmp_path, news_registry)
    second, _t2 = _persist_run(tmp_path, news_registry)
    assert [item.id for item in first.items] == [item.id for item in second.items]


# --------------------------------------------------------------------------- #
# 时间窗
# --------------------------------------------------------------------------- #

def test_window_filter_drops_undated_and_out_of_range(stream_of) -> None:
    result, _transport = stream_of(start=WINDOW_DAY, end=WINDOW_DAY)
    assert result.items, "窗口内应该有稿子"
    for item in result.items:
        assert date.fromisoformat(WINDOW_DAY) <= item.day <= date.fromisoformat(WINDOW_DAY)
    assert result.outside_window > 0, "语料里有窗口外的样本（8 月研报、9 月公告）"


def test_multi_day_window_is_inclusive(stream_of) -> None:
    result, _transport = stream_of(start="2026-10-29", end="2026-10-31")
    days = {str(item.day) for item in result.items}
    assert days == {"2026-10-30", "2026-10-31"}
    assert result.window == ("2026-10-29", "2026-10-31")


def test_window_ordering_is_newest_first(stream_of) -> None:
    result, _transport = stream_of()
    stamps = [item.published_at or "" for item in result.items]
    assert stamps == sorted(stamps, reverse=True)


def test_reversed_window_is_a_usage_error(stream_of) -> None:
    with pytest.raises(LadderStockError) as exc:
        stream_of(start="2026-10-31", end="2026-10-29")
    assert exc.value.code == 2


def test_oversized_window_is_refused_to_protect_the_peer(stream_of) -> None:
    with pytest.raises(LadderStockError) as exc:
        stream_of(start="2026-09-01", end="2026-10-31")
    assert exc.value.code == 2
    assert str(MAX_WINDOW_DAYS) in str(exc.value)


def test_window_at_the_limit_is_allowed(stream_of) -> None:
    result, _transport = stream_of(start="2026-10-01", end="2026-10-31")
    assert result.exit_code == EXIT_OK


# --------------------------------------------------------------------------- #
# 栏目选择与 code
# --------------------------------------------------------------------------- #

def test_resolve_datasets_follows_registry_order_not_input_order() -> None:
    picked = resolve_datasets(["flash_cls", "announcement_cninfo"])
    assert picked == ("announcement_cninfo", "flash_cls"), "同一批来源必须每次同序"


def test_resolve_datasets_accepts_comma_and_semicolon() -> None:
    assert resolve_datasets(["flash_cls,announcement_cninfo;news_cctv"]) == (
        "announcement_cninfo",
        "news_cctv",
        "flash_cls",
    )
    assert resolve_datasets([]) == resolve_datasets(None)


def test_unknown_dataset_is_a_usage_error() -> None:
    with pytest.raises(LadderStockError) as exc:
        resolve_datasets(["weibo_hot"])
    assert exc.value.code == 2
    assert "未知资讯栏目" in str(exc.value)


def test_partial_unknown_list_is_rejected_wholesale() -> None:
    """一半合法一半非法也不能「忽略非法的那半」：那会让用户以为取全了。"""
    with pytest.raises(LadderStockError):
        resolve_datasets(["flash_cls", "weibo_hot"])


def test_code_required_columns_are_skipped_not_silently_missing(stream_of) -> None:
    result, transport = stream_of(codes=[])
    skipped = {s.dataset for s in result.statuses if s.status == STATUS_SKIPPED}
    assert skipped == set(CODE_REQUIRED_DATASETS)
    for status in result.statuses:
        if status.status == STATUS_SKIPPED:
            assert "--code" in status.detail
    assert result.exit_code == EXIT_OK, "用法未满足不是取数失败，不该报 4/8"
    assert transport.tools_called, "非 code 栏目照常取数"


def test_code_scoped_columns_are_not_called_once_per_code(stream_of) -> None:
    """快讯类不吃 code：按 code 循环只会打出同样的请求，白白多打 peer 一轮。"""
    _result, transport = stream_of(codes=["600519", "000001", "600000"])
    calls = transport.tools_called
    for tool in ("cls_telegraph", "wallstreetcn_lives", "eastmoney_global_news"):
        assert calls.count(tool) == 1, f"{tool} 被打过 {calls.count(tool)} 次"
    assert calls.count("cninfo_announcements") == 3, "公告类才按 code 逐只取"


def test_cctv_is_fetched_day_by_day(stream_of) -> None:
    _result, transport = stream_of(start="2026-10-29", end="2026-10-31")
    days = [
        call.params["date"]
        for call in transport.calls
        if call.tool == "cctv_news"
    ]
    assert days == ["2026-10-29", "2026-10-30", "2026-10-31"]


# --------------------------------------------------------------------------- #
# 降级与退出码
# --------------------------------------------------------------------------- #

def test_all_columns_ok(stream_of) -> None:
    result, _transport = stream_of()
    assert result.exit_code == EXIT_OK
    assert {status.status for status in result.statuses} == {STATUS_OK}
    assert not any(status.degraded for status in result.statuses)


def test_every_source_down_is_exit_4_not_empty_success(stream_of) -> None:
    result, _transport = stream_of(failing=ALL_NEWS_TOOLS)
    assert result.exit_code == EXIT_SOURCE
    assert result.items == []
    assert {status.status for status in result.statuses} == {STATUS_FAILED}
    for status in result.statuses:
        assert status.detail and status.degraded, "失败行必须自带原因"


def test_some_sources_down_is_exit_8(stream_of) -> None:
    result, _transport = stream_of(failing=["cls_telegraph", "wallstreetcn_lives"])
    assert result.exit_code == EXIT_PARTIAL
    assert result.items, "部分失败不影响其余栏目照常出数据"
    failed = {s.dataset for s in result.statuses if s.status == STATUS_FAILED}
    assert failed == {"flash_cls", "flash_wallstreetcn"}


def test_fallback_same_peer_is_used_and_marked_degraded(stream_of) -> None:
    """主源挂 → 同 peer 备源顶上：栏目照样出数据，但 degraded 与来源轨迹要留痕。"""
    result, transport = stream_of(
        failing=["cninfo_announcements"],
        datasets=["announcement_cninfo"],
    )
    status = next(s for s in result.statuses if s.dataset == "announcement_cninfo")
    assert status.status == STATUS_OK
    assert status.degraded is True
    assert "announcements_backup" in transport.tools_called
    assert "备源" in status.source_detail or "announcements_backup" in status.source_detail


def test_empty_payload_is_distinguished(tmp_path: Path, news_registry) -> None:
    """上游返回 0 条 = empty（今天确实没稿子），与 failed（打不通）是两件事。"""
    from fixtures.fake_peers import FakeTransport, make_news_registry

    transport = FakeTransport(
        handlers={"cls_telegraph": lambda _p: {"ok": True, "data": [], "meta": {"source": "stub"}}},
        tools=["cls_telegraph"],
    )
    registry = make_news_registry(transport, tmp_path)
    result = collect_stream(
        start=date(2026, 10, 30),
        end=date(2026, 10, 30),
        datasets=["flash_cls"],
        registry=registry,
        root=tmp_path,
        persist=False,
    )
    status = result.statuses[0]
    assert status.status == STATUS_EMPTY
    assert "0 条" in status.detail
    assert result.exit_code == EXIT_OK


# --------------------------------------------------------------------------- #
# 输出契约与隐私
# --------------------------------------------------------------------------- #

def test_undated_items_are_counted_not_silently_shown(tmp_path: Path, news_registry) -> None:
    """上游没给时间：条目不进视图，但要计入 `dropped_undated`——「少了」必须能对上账。"""
    from fixtures.fake_peers import FakeTransport, make_news_registry

    transport = FakeTransport(
        handlers={
            "cls_telegraph": lambda _p: {
                "ok": True,
                "data": [
                    {
                        "title": "有时间的一条",
                        "url": "https://www.cls.cn/detail/1",
                        "publish_time": 1_793_323_800,
                    },
                    {"title": "没有时间的一条", "url": "https://www.cls.cn/detail/2"},
                ],
                "meta": {"source": "stub"},
            }
        },
        tools=["cls_telegraph"],
    )
    registry = make_news_registry(transport, tmp_path)
    result = collect_stream(
        start=date(2026, 10, 30),
        end=date(2026, 10, 30),
        datasets=["flash_cls"],
        registry=registry,
        root=tmp_path,
        persist=False,
    )
    assert len(result.items) == 1
    assert result.dropped_undated == 1
    assert result.as_dict()["summary"]["dropped_undated"] == 1


def test_payload_shape_is_the_documented_contract(stream_of) -> None:
    result, _transport = stream_of()
    payload = result.as_dict()
    assert payload["ok"] is True
    assert payload["domain"] == "news"
    assert payload["topic"] == "news_stream"
    assert set(payload["summary"]) == {
        "items",
        "fetched",
        "new_items",
        "known_items",
        "merged_duplicates",
        "dropped_undated",
        "outside_window",
        "stored",
        "events",
        "persisted",
    }
    assert payload["items"] == [item.as_dict() for item in result.items]
    assert json.dumps(payload, ensure_ascii=False)


def test_no_private_keys_anywhere(stream_of) -> None:
    """隐私面（持仓 / 自选 / 交易日志）绝不能出现在资讯流里——反向钉住。"""
    result, transport = stream_of()
    payload = result.as_dict()
    assert private_keys_present(payload) == []
    assert private_keys_present([call.params for call in transport.calls]) == []
    assert private_keys_present(transport.tools_called) == []

    dumped = json.dumps(payload, ensure_ascii=False)
    for key in PRIVATE_KEYS:
        assert key not in dumped, key


def test_privacy_check_covers_nested_items(stream_of) -> None:
    """递归检查真的会往里走：往 origins 里塞一个 positions 键必须被抓到。"""
    result, _transport = stream_of()
    payload = result.as_dict()
    payload["items"][0]["origins"][0]["positions"] = []
    assert private_keys_present(payload) == ["positions"]


def test_events_are_extracted_inside_the_stream_and_exposed_as_a_report(stream_of) -> None:
    result, _transport = stream_of()
    assert result.events == len(result.event_objects)
    assert result.extraction["events"] == result.events
    assert set(result.extraction) == {
        "events",
        "by_type",
        "skipped_no_url",
        "skipped_no_url_ids",
        "skipped_excluded_source",
        "skipped_excluded_source_ids",
        "suppressed_by_negation",
    }
