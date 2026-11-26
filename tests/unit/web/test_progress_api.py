"""采集进度视图的单测：口径与"为什么是 0"的证据链。

守的三件事：
1. 分母取代码池，不取全市场；
2. 采集器每轮 skipped 时，公告必须是 blocked 而不是"没数据"；
3. 采到了但没落库要能和"没采"分开（lost vs never）。
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

from ladderstock.web import progress_api


def _project(tmp_path: Path, pool: list[str]) -> tuple[Path, Path]:
    project = tmp_path / "LadderStock"
    codes = ", ".join(f"'{code}'" for code in pool)
    (project / "config").mkdir(parents=True)
    (project / "config" / "sync.yaml").write_text(
        "sync:\n  datasets:\n    daily_k:\n      primary: tickhub_kline\n"
        f"      codes: [{codes}]\n",
        encoding="utf-8",
    )
    data = project / "data"
    data.mkdir()
    return project, data


def _news_db(data: Path, cells: list[dict], *, announcements: int = 0) -> None:
    conn = sqlite3.connect(str(data / "news.sqlite"))
    conn.execute(
        "create table news_items (id integer primary key, source text, published_at text, published_day text)"
    )
    conn.execute(
        "create table news_runs (id integer primary key, started_at text, window_start text,"
        " window_end text, datasets text, codes text, fetched int, stored int, deduped int,"
        " events int, detail text)"
    )
    conn.execute(
        "insert into news_items (source, published_at, published_day) values"
        " ('flash_eastmoney', '2026-11-19T18:00:00+08:00', '2026-11-19')"
    )
    conn.execute(
        "insert into news_runs (started_at, window_start, window_end, fetched, stored, detail)"
        " values ('2026-11-19T18:29:43+08:00', '2026-11-19', '2026-11-20', 300, 225, ?)",
        (json.dumps({"sources_status": cells}, ensure_ascii=False),),
    )
    for index in range(announcements):
        conn.execute(
            "insert into news_items (source, published_at, published_day) values (?, ?, ?)",
            ("announcement_cninfo", f"2026-11-19T0{index % 10}:00:00+08:00", "2026-11-19"),
        )
    conn.commit()
    conn.close()


def test_base_pool_takes_longest_codes(tmp_path: Path) -> None:
    project, _ = _project(tmp_path, ["600000", "600519", "000001"])
    assert progress_api._base_pool(project) == ["600000", "600519", "000001"]
    assert len(progress_api._base_pool(tmp_path / "nothing")) == 0


def test_announcement_is_blocked_with_reason(tmp_path: Path) -> None:
    project, data = _project(tmp_path, ["600000"])
    _news_db(
        data,
        [
            {
                "dataset": "announcement_cninfo",
                "tool": "cninfo_announcements",
                "status": "skipped",
                "items": 0,
                "detail": "announcement_cninfo 需要指定 code（--code <code>[,<code>]）",
            },
            {"dataset": "research_report", "tool": "eastmoney_reports", "status": "ok", "items": 100, "detail": ""},
            {"dataset": "flash_eastmoney", "tool": "eastmoney_global_news", "status": "ok", "items": 100, "detail": ""},
        ],
    )
    view = progress_api._progress_view(project, data, dt.datetime(2026, 11, 20, 22, 0))
    by_key = {row["key"]: row for row in view["domains"]}

    assert by_key["announcement"]["status"] == "blocked"
    assert "需要指定 code" in by_key["announcement"]["detail"]
    assert by_key["announcement"]["got"] == 0
    # 研报：采集器 ok 但一条归档分区都没有 → lost，不是 never
    assert by_key["research_report"]["status"] in {"lost", "never"}
    # 快讯最新在 T-1，不算断档
    assert by_key["flash"]["status"] == "ok"
    # 没有 duckdb 时给出原因，而不是整页 500
    assert view["duckdb_error"] is not None
    assert view["counts"]["broken"] >= 1


def test_announcement_lost_when_fetched_but_not_stored(tmp_path: Path) -> None:
    """采到没落库：items>0 但 news_items 为 0 → lost（与「没采到」分开）。"""
    project, data = _project(tmp_path, ["600000"])
    _news_db(
        data,
        [
            {"dataset": "announcement_cninfo", "status": "ok", "items": 120, "detail": ""},
            {"dataset": "announcement_szse", "status": "ok", "items": 40, "detail": ""},
        ],
    )
    view = progress_api._progress_view(project, data, dt.datetime(2026, 11, 20, 22, 0))
    row = {item["key"]: item for item in view["domains"]}["announcement"]

    assert row["status"] == "lost"
    assert "160" in row["detail"]
    assert "采到没落库" in row["detail"]


def test_announcement_partial_when_history_exists_but_last_run_skipped(tmp_path: Path) -> None:
    """有历史数据、最近一轮却 skipped：不装成「一条都没有」。"""
    project, data = _project(tmp_path, ["600000"])
    _news_db(
        data,
        [
            {
                "dataset": "announcement_cninfo",
                "status": "skipped",
                "items": 0,
                "detail": "announcement_cninfo 需要指定 code（--code <code>[,<code>]）",
            },
        ],
        announcements=3,
    )
    view = progress_api._progress_view(project, data, dt.datetime(2026, 11, 20, 22, 0))
    row = {item["key"]: item for item in view["domains"]}["announcement"]

    assert row["status"] == "partial"
    assert row["got"] == 3
    assert row["latest"] == "2026-11-19"
    assert "历史数据还在" in row["detail"]
    assert "需要指定 code" in row["detail"]


def test_announcement_ok_once_sources_report_ok(tmp_path: Path) -> None:
    project, data = _project(tmp_path, ["600000"])
    _news_db(
        data,
        [
            {"dataset": "announcement_cninfo", "status": "ok", "items": 120, "detail": ""},
            {"dataset": "announcement_szse", "status": "ok", "items": 40, "detail": ""},
        ],
        announcements=5,
    )
    view = progress_api._progress_view(project, data, dt.datetime(2026, 11, 20, 22, 0))
    row = {item["key"]: item for item in view["domains"]}["announcement"]

    assert row["status"] == "ok"
    assert row["got"] == 5
    assert "announcement_cninfo=ok" in row["detail"]


def test_announcement_denominator_is_pool_union_outside_movers(
    tmp_path: Path, monkeypatch
) -> None:
    """A+B 口径：分母 = 池 ∪ 池外异动；未采的票标「按策略未采」，不是缺口。"""
    project, data = _project(tmp_path, ["600000", "600519"])
    _news_db(
        data,
        [
            {"dataset": "announcement_cninfo", "status": "ok", "items": 10, "detail": ""},
            {"dataset": "announcement_szse", "status": "ok", "items": 2, "detail": ""},
        ],
        announcements=3,
    )

    from ladderstock.news import candidates as news_candidates

    monkeypatch.setattr(
        news_candidates,
        "announcement_candidates",
        lambda pool, **kwargs: {
            "pool_size": len(pool),
            "extra": ["300750"],
            "union": sorted([*pool, "300750"]),
            "movers": [
                {"code": pool[0], "name": "浦发银行", "pct": 6.1, "in_pool": True},
                {"code": "300750", "name": "宁德时代", "pct": 7.2, "in_pool": False},
            ],
            "snapshot_day": "2026-11-20",
            "threshold": 5.0,
            "fallback": False,
            "fallback_reason": "",
            "note": "分母 = 池 2 只 ∪ 池外异动 1 只 = 3 只",
        },
    )
    monkeypatch.setattr(
        news_candidates,
        "read_candidates",
        lambda data_root, day: {
            "day": day,
            "covered": 2,
            "union_size": 3,
            "requested": 2,
        },
    )

    view = progress_api._progress_view(project, data, dt.datetime(2026, 11, 20, 22, 0))
    row = {item["key"]: item for item in view["domains"]}["announcement"]

    assert row["denominator"] == 3
    assert row["pool_size"] == 2
    assert row["extra_total"] == 1
    assert row["extra_movers"] == ["300750"]
    assert row["movers_day"] == "2026-11-20"
    assert row["covered"] == 2
    assert row["requested"] == 2
    assert row["requested_missing"] == 0
    assert row["strategy_skipped"] == 1
    assert "按策略未采 1 只" in row["detail"]
    assert "不算缺口" in row["detail"]


def test_announcement_denominator_previews_before_first_run(tmp_path: Path, monkeypatch) -> None:
    """还没跑过前置筛采集：分母照算（预告），但不谎报「已采」。"""
    project, data = _project(tmp_path, ["600000"])
    _news_db(data, [{"dataset": "announcement_cninfo", "status": "skipped", "items": 0, "detail": "no code"}])

    from ladderstock.news import candidates as news_candidates

    monkeypatch.setattr(
        news_candidates,
        "announcement_candidates",
        lambda pool, **kwargs: {
            "pool_size": len(pool),
            "extra": [],
            "union": list(pool),
            "movers": [],
            "snapshot_day": "2026-11-20",
            "threshold": 5.0,
            "fallback": False,
            "fallback_reason": "",
            "note": "分母 = 池 1 只 ∪ 池外异动 0 只 = 1 只",
        },
    )
    monkeypatch.setattr(news_candidates, "read_candidates", lambda data_root, day: None)

    view = progress_api._progress_view(project, data, dt.datetime(2026, 11, 20, 22, 0))
    row = {item["key"]: item for item in view["domains"]}["announcement"]

    assert row["denominator"] == 1
    assert row["covered"] is None
    assert row["strategy_skipped"] is None
    assert "分母是预告" in row["detail"]


def test_sidecar_wins_over_live_candidates(tmp_path: Path, monkeypatch) -> None:
    """当天已经有采集记录：分母/拆解以 sidecar 为准，快照更新不许改当轮口径。"""
    project, data = _project(tmp_path, ["600000"])
    _news_db(
        data,
        [{"dataset": "announcement_cninfo", "status": "ok", "items": 3, "detail": ""}],
        announcements=3,
    )

    from ladderstock.news import candidates as news_candidates

    monkeypatch.setattr(
        news_candidates,
        "announcement_candidates",
        lambda pool, **kwargs: {
            "pool_size": len(pool),
            "extra": [],
            "union": list(pool),
            "movers": [],
            "movers_total": 0,
            "movers_in_pool": 0,
            "snapshot_day": "2026-11-21",
            "threshold": 5.0,
            "fallback": False,
            "fallback_reason": "",
            "note": "现算口径（不该被采用）",
        },
    )
    monkeypatch.setattr(
        news_candidates,
        "read_candidates",
        lambda data_root, day: {
            "day": day,
            "pool_size": 1,
            "union_size": 4,
            "extra": ["300750", "688981"],
            "extra_total": 2,
            "requested": 2,
            "covered": 1,
            "snapshot_day": "2026-11-20",
            "note": "当轮口径",
        },
    )

    view = progress_api._progress_view(project, data, dt.datetime(2026, 11, 21, 9, 0))
    row = {item["key"]: item for item in view["domains"]}["announcement"]

    assert row["denominator"] == 4
    assert row["extra_total"] == 2
    assert row["extra_movers"] == ["300750", "688981"]
    assert row["movers_day"] == "2026-11-20"
    assert row["covered"] == 1
    assert row["requested"] == 2
    assert row["requested_missing"] == 1
    assert row["strategy_skipped"] == 2
    assert "当轮口径" in row["candidates_note"]


def test_schedule_runs_are_ranked_and_hinted(tmp_path: Path) -> None:
    project, data = _project(tmp_path, ["600000"])
    runs = data / "schedule_runs"
    runs.mkdir()
    (runs / "close_sync.json").write_text(
        json.dumps({"job_id": "close_sync", "status": "ok", "started_at": "2026-11-20T15:35:00", "detail": "ok"}),
        encoding="utf-8",
    )
    (runs / "close_digest.json").write_text(
        json.dumps(
            {
                "job_id": "close_digest",
                "status": "failed",
                "started_at": "2026-11-20T15:40:00",
                "detail": "摘要生成异常：历史库打不开",
            }
        ),
        encoding="utf-8",
    )
    rows = progress_api._schedule_runs(data, dt.datetime(2026, 11, 20, 22, 0))
    assert [row["job_id"] for row in rows][0] == "close_digest"  # 失败排最前
    assert rows[0]["age_hours"] == 6.3
    assert "真失败" in rows[0]["hint"]
    assert rows[1]["hint"].startswith("正常完成")


def test_skipped_runs_explain_themselves(tmp_path: Path) -> None:
    project, data = _project(tmp_path, ["600000"])
    runs = data / "schedule_runs"
    runs.mkdir()
    (runs / "fund_flow_fast.json").write_text(
        json.dumps(
            {
                "job_id": "fund_flow_fast",
                "status": "skipped",
                "started_at": "2026-11-20T22:40:14",
                "detail": "不在连续竞价时段（09:30–11:30 / 13:00–15:00），不跑",
            }
        ),
        encoding="utf-8",
    )
    rows = progress_api._schedule_runs(data, dt.datetime(2026, 11, 20, 22, 45))
    assert rows[0]["status"] == "skipped"
    assert "不是故障" in rows[0]["hint"]
    assert "不在连续竞价时段" in rows[0]["detail"]
