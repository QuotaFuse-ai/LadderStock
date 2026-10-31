"""本机存储：库文件独立、写入幂等、读回来形状不变（DEC-006「数据只落本机」+ DEC-007 幂等）。

`news.sqlite` 与隐私库 `journal.sqlite` 分家不是整理癖：分库之后
「资讯流里没有隐私数据」这句话是可以结构证明的——它根本不与隐私库共用连接。
这里同时钉住这点，防止有人图省事把资讯写回 journal。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from ladderstock.news import store as news_store
from ladderstock.news.events import extract_events
from ladderstock.news.models import NewsItem, build_item


def _item(index: int = 1, **overrides) -> NewsItem:
    params = {
        "dataset": "flash_cls",
        "title": f"某公司发布业绩预告 {index}",
        "url": f"https://www.cls.cn/detail/{index}",
        "published_at": 1_793_323_800,
        "summary": "预计前三季度净利润同比增长。",
        "code": "600519",
        "provenance": {"tool": "cls_telegraph", "confidence": "high"},
    }
    params.update(overrides)
    return build_item(**params)


def _events(items):
    return extract_events(items).events


# --------------------------------------------------------------------------- #
# 库文件与schema
# --------------------------------------------------------------------------- #

def test_db_lives_in_its_own_file_next_to_nothing_privacy(tmp_path: Path) -> None:
    assert news_store.DB_FILENAME == "news.sqlite"
    assert "journal" not in news_store.DB_FILENAME
    assert news_store.db_path(tmp_path) == tmp_path / "news.sqlite"


def test_connect_creates_the_three_tables(tmp_path: Path) -> None:
    conn = news_store.connect(tmp_path)
    assert conn is not None
    try:
        names = {
            str(row["name"])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert set(news_store.TABLES) <= names
        assert "journal" not in names and "trades" not in names and "positions" not in names
        assert news_store.SCHEMA_VERSION == 1
    finally:
        news_store.close(conn)


def test_read_only_connect_does_not_create_an_empty_db(tmp_path: Path) -> None:
    assert news_store.connect(tmp_path, create=False) is None
    assert not news_store.db_path(tmp_path).exists()


def test_reconnect_is_cheap_and_keeps_rows(tmp_path: Path) -> None:
    conn = news_store.connect(tmp_path)
    try:
        news_store.upsert_items(conn, [_item()])
    finally:
        news_store.close(conn)
    again = news_store.connect(tmp_path)
    try:
        assert news_store.counts(again)["news_items"] == 1
    finally:
        news_store.close(again)


def test_a_newer_db_on_disk_is_refused_not_silently_downgraded(tmp_path: Path) -> None:
    """库版本高于程序：直接报错让用户升级程序，而不是把 v2 的库当 v1 读写。"""
    conn = news_store.connect(tmp_path)
    assert conn is not None
    try:
        conn.execute("PRAGMA user_version = 99")
    finally:
        news_store.close(conn)
    with pytest.raises(RuntimeError, match="高于当前程序支持"):
        news_store.connect(tmp_path)


def test_paths_follow_the_data_dir_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    from ladderstock.cli.paths import data_dir

    assert data_dir() == tmp_path
    assert news_store.db_path() == tmp_path / "news.sqlite"


# --------------------------------------------------------------------------- #
# 幂等写
# --------------------------------------------------------------------------- #

def test_upsert_is_idempotent_on_rows_and_ids(tmp_path: Path) -> None:
    items = [_item(1), _item(2)]
    conn = news_store.connect(tmp_path)
    try:
        assert news_store.upsert_items(conn, items) == 2
        assert news_store.upsert_items(conn, items) == 2, "写两次都返回处理条数，不谎报"
        assert news_store.counts(conn)["news_items"] == 2, "但库里还是两行"
        ids = {str(row["id"]) for row in conn.execute("SELECT id FROM news_items")}
        assert ids == {item.id for item in items}
    finally:
        news_store.close(conn)


def test_first_seen_at_survives_an_update(tmp_path: Path) -> None:
    """覆盖写只更新内容，不改首次入库时间——那是「这条稿子什么时候进的库」的唯一凭据。"""
    item = _item()
    conn = news_store.connect(tmp_path)
    try:
        news_store.upsert_items(conn, [item])
        conn.execute(
            "UPDATE news_items SET first_seen_at = ? WHERE id = ?",
            ("2020-01-01T00:00:00+08:00", item.id),
        )
        news_store.upsert_items(conn, [_item(title="改了标题的同一条")])
        row = conn.execute(
            "SELECT first_seen_at, updated_at, title FROM news_items WHERE id = ?", (item.id,)
        ).fetchone()
        assert str(row["first_seen_at"]) == "2020-01-01T00:00:00+08:00"
        assert "改了标题" in str(row["title"])
        assert str(row["updated_at"]).startswith("20"), "updated_at 每轮刷新"
        assert not str(row["updated_at"]).startswith("2020"), "刷新时间不该被写成首次入库时间"
    finally:
        news_store.close(conn)


def test_events_upsert_is_idempotent(tmp_path: Path) -> None:
    events = _events([_item()])
    conn = news_store.connect(tmp_path)
    try:
        news_store.upsert_events(conn, events)
        news_store.upsert_events(conn, events)
        assert news_store.counts(conn)["news_events"] == len(events)
    finally:
        news_store.close(conn)


def test_ai_note_is_persisted_by_the_same_upsert(tmp_path: Path) -> None:
    """模型叙述只是一列，不是另一张表：重跑规则后带着 note 一起覆盖即可。"""
    event = _events([_item()])[0]
    enriched = event.with_ai_note("一句叙述")
    conn = news_store.connect(tmp_path)
    try:
        news_store.upsert_events(conn, [event])
        news_store.upsert_events(conn, [enriched])
        rows = news_store.list_events(conn)
        assert len(rows) == 1
        assert rows[0]["ai_note"] == "一句叙述"
    finally:
        news_store.close(conn)


def test_empty_upserts_are_no_ops(tmp_path: Path) -> None:
    conn = news_store.connect(tmp_path)
    try:
        assert news_store.upsert_items(conn, []) == 0
        assert news_store.upsert_events(conn, []) == 0
    finally:
        news_store.close(conn)


# --------------------------------------------------------------------------- #
# 读回来的形状
# --------------------------------------------------------------------------- #

def test_round_trip_preserves_the_item_exactly(tmp_path: Path) -> None:
    item = _item()
    conn = news_store.connect(tmp_path)
    try:
        news_store.upsert_items(conn, [item])
        loaded = news_store.load_items(conn)
    finally:
        news_store.close(conn)
    assert [row.as_dict() for row in loaded] == [item.as_dict()]


def test_load_items_filters_by_window_and_source(tmp_path: Path) -> None:
    conn = news_store.connect(tmp_path)
    try:
        news_store.upsert_items(
            conn,
            [
                _item(1, published_at="2026-10-30"),
                _item(2, published_at="2026-09-01"),
                _item(3, published_at="2026-11-20", dataset="news_cctv"),
            ],
        )
        october = news_store.load_items(
            conn, start=date(2026, 10, 1), end=date(2026, 10, 31), sources=["flash_cls"]
        )
        assert [item.title for item in october] == [_item(1).title]
        everything = news_store.load_items(conn)
        assert len(everything) == 3
        by_source = news_store.load_items(conn, sources=["news_cctv"])
        assert len(by_source) == 1
    finally:
        news_store.close(conn)


def test_load_items_can_filter_by_code(tmp_path: Path) -> None:
    conn = news_store.connect(tmp_path)
    try:
        news_store.upsert_items(conn, [_item(1, code="600519"), _item(2, code="000001")])
        rows = news_store.load_items(conn, code="000001")
        assert len(rows) == 1
        assert rows[0].related_codes == ("000001",)
    finally:
        news_store.close(conn)


def test_undated_items_stay_in_the_candidate_set(tmp_path: Path) -> None:
    """时间未知 ≠ 内容无效：窗口读把它们带上，跨轮次才可能按标题合成一条。
    真正的「视图里没有它们」由 `collect_stream` 的 `dropped_undated` 负责。
    """
    conn = news_store.connect(tmp_path)
    try:
        news_store.upsert_items(conn, [_item(published_at=None)])
        assert len(news_store.load_items(conn)) == 1
        windowed = news_store.load_items(
            conn, start=date(2026, 10, 1), end=date(2026, 10, 31), sources=["flash_cls"]
        )
        assert len(windowed) == 1, "无日期条目不能从候选集里掉出去"
    finally:
        news_store.close(conn)


def test_list_events_filters_by_month_and_type(tmp_path: Path) -> None:
    items = [_item(1), _item(
        2,
        url="https://www.cls.cn/detail/2",
        title="国务院常务会议部署稳经济",
    )]
    conn = news_store.connect(tmp_path)
    try:
        news_store.upsert_events(conn, _events(items))
        rows = news_store.list_events(conn, month="2026-10")
        assert rows
        assert all(str(row["event_date"]).startswith("2026-10") for row in rows)
        only_policy = news_store.list_events(conn, event_type="policy")
        assert {row["type"] for row in only_policy} == {"policy"}
        assert news_store.list_events(conn, month="2020-01") == []
    finally:
        news_store.close(conn)


def test_event_rows_carry_their_evidence_back(tmp_path: Path) -> None:
    event = _events([_item()])[0]
    conn = news_store.connect(tmp_path)
    try:
        news_store.upsert_events(conn, [event])
        row = news_store.list_events(conn)[0]
        assert row["evidence"]["url"] == event.evidence["url"]
        assert row["evidence"]["snippet"] == event.evidence["snippet"]
        assert row["extractor"] == "rule"
        assert row["date_basis"] == event.date_basis
    finally:
        news_store.close(conn)


def test_limit_is_respected(tmp_path: Path) -> None:
    conn = news_store.connect(tmp_path)
    try:
        news_store.upsert_items(conn, [_item(index) for index in range(1, 6)])
        assert len(news_store.load_items(conn, limit=2)) == 2
        news_store.upsert_events(conn, _events([_item()]))
        assert len(news_store.list_events(conn, limit=1)) <= 1
    finally:
        news_store.close(conn)


# --------------------------------------------------------------------------- #
# 运行记录
# --------------------------------------------------------------------------- #

def test_record_run_keeps_the_source_ledger(tmp_path: Path) -> None:
    conn = news_store.connect(tmp_path)
    try:
        news_store.record_run(
            conn,
            window=(date(2026, 10, 29), date(2026, 10, 31)),
            datasets=["flash_cls"],
            codes=["600519"],
            fetched=4,
            stored=3,
            deduped=1,
            events=2,
            detail={"sources_status": [{"dataset": "flash_cls", "status": "ok"}]},
        )
        row = conn.execute("SELECT * FROM news_runs").fetchone()
        payload = json.loads(str(row["detail"]))
        assert payload["sources_status"][0]["dataset"] == "flash_cls"
        assert json.loads(str(row["datasets"])) == ["flash_cls"]
        assert int(row["fetched"]) == 4 and int(row["stored"]) == 3
    finally:
        news_store.close(conn)


def test_runs_accumulate_across_reruns(tmp_path: Path) -> None:
    """幂等指数据不重复，不指不记账：每轮都要留一行，否则「今天跑过没有」无从判断。"""
    conn = news_store.connect(tmp_path)
    try:
        for _ in range(3):
            news_store.record_run(
                conn,
                window=(date(2026, 10, 30), date(2026, 10, 30)),
                datasets=["flash_cls"],
                codes=[],
                fetched=1,
                stored=1,
                deduped=0,
                events=0,
            )
        assert news_store.counts(conn)["news_runs"] == 3
    finally:
        news_store.close(conn)


# --------------------------------------------------------------------------- #
# 隐私：库里没有隐私列
# --------------------------------------------------------------------------- #

def test_no_column_name_collides_with_private_keys(tmp_path: Path) -> None:
    from ladderstock.core.privacy import PRIVATE_KEYS

    conn = news_store.connect(tmp_path)
    try:
        columns: list[str] = []
        for table in news_store.TABLES:
            columns.extend(
                str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")
            )
    finally:
        news_store.close(conn)
    lowered = {name.lower() for name in columns}
    assert not lowered & {key.lower() for key in PRIVATE_KEYS}, columns


def test_provenance_json_does_not_smuggle_private_keys(tmp_path: Path) -> None:
    from ladderstock.core.privacy import private_keys_present

    conn = news_store.connect(tmp_path)
    try:
        news_store.upsert_items(conn, [_item()])
        rows = conn.execute("SELECT provenance, origins FROM news_items").fetchall()
        for row in rows:
            assert private_keys_present(json.loads(str(row["provenance"]))) == []
            assert private_keys_present(json.loads(str(row["origins"]))) == []
    finally:
        news_store.close(conn)


def test_connection_is_not_shared_with_the_journal_db(tmp_path: Path) -> None:
    """结构证明：news 的连接只指向 news.sqlite，隐私库压根没被打开。"""
    conn = news_store.connect(tmp_path)
    assert conn is not None
    try:
        (tmp_path / "journal.sqlite").write_bytes(b"")
        attached = conn.execute("PRAGMA database_list").fetchall()
        files = [str(row["file"]) for row in attached]
        assert all("journal" not in f for f in files), files
        assert files and files[-1].replace("\\", "/").endswith("news.sqlite")
    finally:
        news_store.close(conn)


def test_close_tolerates_none() -> None:
    news_store.close(None)
    news_store.close(sqlite3.connect(":memory:"))
