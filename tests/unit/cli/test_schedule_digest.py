"""三类本机摘要（`watch/digest.py`）+ 调度接线（QUOT-432 / DEC-058）。

钉住验收口径：

- 三份摘要都在默认表 / 仓库配置里，并带合法 `summary`；
- 盘前只写有来源的事实：取不到的代码逐条「未取到（原因）」，**不写 0**；
- 过了 11:30 的盘前补跑 `skipped`，且不写收件箱；
- 通知投递失败只把结果记 `degraded`，收件箱那条仍然写入；
- 同一交易日重跑只留一条，已读时间不被清空；
- 自选变化只有一天缓存时明确写「没有上一交易日可比」。
"""

from __future__ import annotations

import copy
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fixtures.fake_peers import SOURCES_DOC, SYNC_DOC, FakeTransport, make_registry, tickhub_payload

from ladderstock.cli import schedule as schedule_cli
from ladderstock.data import duckdb_store
from ladderstock.errors import EXIT_CONFIG, LadderStockError
from ladderstock.journal import inbox, portfolio, watchlist
from ladderstock.watch import digest

DIGEST_JOBS = {
    "premarket_digest": ("premarket", "09:05"),
    "close_digest": ("close", "15:40"),
    "watch_changes": ("watch", "16:15"),
}


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #

def _sources_doc() -> dict[str, Any]:
    sources = copy.deepcopy(SOURCES_DOC)
    sources["sources"].append(
        {
            "id": "tickhub_spot",
            "dataset": "spot_quote",
            "via": "tickhub",
            "tool": "tencent_quote_batch",
            "primary": True,
            "fallback": [],
            "timeout_sec": 1,
            "retries": 0,
            "cooldown_sec": 60,
        }
    )
    return sources


def _quote_row(
    code: str,
    *,
    name: str,
    price: float,
    prev_close: float,
    change_pct: float,
) -> dict[str, Any]:
    return {
        "code": code,
        "name": name,
        "price": price,
        "prev_close": prev_close,
        "change": round(price - prev_close, 2),
        "change_pct": change_pct,
        "timestamp": "20261105090500",
        "pe": 10.0,
        "pb": 1.0,
        "turnover_rate": 1.0,
    }


def _registry(
    root: Path,
    quotes: dict[str, dict[str, Any]],
    *,
    failing: bool = False,
):
    def spot(params: dict[str, Any]) -> dict[str, Any]:
        asked = [code for code in str(params.get("codes", "")).split(",") if code]
        rows = {code: copy.deepcopy(quotes[code]) for code in asked if code in quotes}
        return tickhub_payload(
            {
                "total": len(asked),
                "succeeded": len(rows),
                "failed": len(asked) - len(rows),
                "quotes": rows,
                "failures": [],
            },
            source="tencent",
        )

    transport = FakeTransport(
        handlers={"tencent_quote_batch": spot},
        errors={"tencent_quote_batch": RuntimeError("QFSD-7001 rate limited")}
        if failing
        else {},
        tools=["tencent_quote_batch"],
    )
    return make_registry(
        transport, root, sources=_sources_doc(), sync=copy.deepcopy(SYNC_DOC)
    )


def _seed_daily(root: Path, rows: list[dict[str, Any]]) -> None:
    duckdb_store.write_dataset("daily_k", rows, source="tickhub_kline", root=root)


def _premarket_job() -> dict[str, Any]:
    return next(job for job in schedule_cli.DEFAULT_JOBS if job["id"] == "premarket_digest")


def _holding(root: Path, *, symbol: str = "600000", shares: float = 100.0) -> None:
    portfolio.add_trade(
        {
            "symbol": symbol,
            "name": "浦发银行" if symbol == "600000" else "",
            "type": "buy",
            "trade_date": "2026-10-30",
            "shares": shares,
            "price": 10.0,
            "fee": 0,
        },
        root=root,
    )


# --------------------------------------------------------------------------- #
# 调度表 / schema
# --------------------------------------------------------------------------- #

def test_default_jobs_carry_three_digests() -> None:
    digs = {job["id"]: (job["summary"], job["trigger"]) for job in schedule_cli.DEFAULT_JOBS
            if job["kind"] == "digest"}

    assert digs == DIGEST_JOBS
    assert "premarket_reminder" not in {job["id"] for job in schedule_cli.DEFAULT_JOBS}
    assert len(schedule_cli.DEFAULT_JOBS) == 15


def test_repo_schedule_declares_three_digests(repo: Path) -> None:
    table, warnings, _source = schedule_cli.load_schedule()

    assert warnings == []
    digs = {job["id"]: (job["summary"], job["trigger"]) for job in table["jobs"]
            if job["kind"] == "digest"}
    assert digs == DIGEST_JOBS


def test_schema_requires_summary_for_digest() -> None:
    table = schedule_cli._default_table()
    schedule_cli.validate_table(table)  # 默认表本身合法

    broken = copy.deepcopy(table)
    digest_job = next(job for job in broken["jobs"] if job["kind"] == "digest")
    digest_job.pop("summary")
    with pytest.raises(LadderStockError) as excinfo:
        schedule_cli.validate_table(broken)
    assert excinfo.value.code == EXIT_CONFIG
    assert "summary" in str(excinfo.value)


def test_structural_fallback_requires_summary_for_digest() -> None:
    table = schedule_cli._default_table()
    assert schedule_cli._structural_problems(table) == []

    broken = copy.deepcopy(table)
    digest_job = next(job for job in broken["jobs"] if job["kind"] == "digest")
    digest_job.pop("summary")
    problems = schedule_cli._structural_problems(broken)
    assert any("summary" in problem for problem in problems)


def test_schema_enum_contains_digest() -> None:
    import json

    schema = json.loads(
        (
            Path(__file__).resolve().parents[3]
            / "config"
            / "schema"
            / "schedule.schema.json"
        ).read_text(encoding="utf-8")
    )
    kinds = schema["properties"]["jobs"]["items"]["properties"]["kind"]["enum"]
    assert "digest" in kinds


# --------------------------------------------------------------------------- #
# 盘前
# --------------------------------------------------------------------------- #

def test_premarket_lines_only_sourced_facts(tmp_path: Path) -> None:
    watchlist.add_watch("600000", root=tmp_path)
    watchlist.add_watch("000001", root=tmp_path)
    registry = _registry(
        tmp_path,
        {"600000": _quote_row("600000", name="浦发银行", price=10.5, prev_close=10.0,
                              change_pct=5.0)},
    )

    outcome = digest.build(
        "premarket_digest",
        "premarket",
        now=datetime(2026, 11, 5, 9, 5),
        root=tmp_path,
        registry=registry,
    )

    assert outcome.status == digest.STATUS_OK
    assert outcome.title == "盘前摘要 2026-11-05"
    body = "\n".join(outcome.lines)
    assert "持仓 0 只 · 自选 2 只" in outcome.lines[0]
    assert "600000 浦发银行 现价 10.50 元 昨收 10.00 元 涨跌 +5.00%" in body
    # 取不到的那只写原因，绝不补 0
    assert "000001 未取到" in body
    assert "0.00 元" not in body.split("000001")[1]
    for forbidden in ("建议", "买入", "卖出", "评级", "目标价"):
        assert forbidden not in body


def test_premarket_upstream_failure_is_unavailable_not_zero(tmp_path: Path) -> None:
    watchlist.add_watch("600000", root=tmp_path)
    registry = _registry(tmp_path, {}, failing=True)

    outcome = digest.build(
        "premarket_digest",
        "premarket",
        now=datetime(2026, 11, 5, 9, 5),
        root=tmp_path,
        registry=registry,
    )

    assert outcome.status == digest.STATUS_OK
    line = next(line for line in outcome.lines if line.startswith("600000"))
    assert "未取到" in line
    assert "0.00 元" not in line


def test_premarket_after_1130_is_skipped_and_writes_nothing(tmp_path: Path) -> None:
    watchlist.add_watch("600000", root=tmp_path)
    result = schedule_cli.run_job(
        _premarket_job(),
        now=datetime(2026, 11, 5, 11, 31),
        root=tmp_path,
    )

    assert result["status"] == "skipped"
    assert "11:30" in result["detail"]
    assert inbox.list_items(root=tmp_path) == []
    assert inbox.unread_count(root=tmp_path) == 0


def test_weekend_is_skipped(tmp_path: Path) -> None:
    watchlist.add_watch("600000", root=tmp_path)
    result = schedule_cli.run_job(
        _premarket_job(),
        now=datetime(2026, 11, 8, 9, 5),  # 周日
        root=tmp_path,
    )

    assert result["status"] == "skipped"
    assert "周末" in result["detail"]


def test_notification_failure_still_writes_inbox_and_marks_degraded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    monkeypatch.setenv(
        "LDST_DESKTOP_NOTIFY_CMD", f'"{sys.executable}" -c "import sys; sys.exit(9)"'
    )
    watchlist.add_watch("600000", root=tmp_path)
    registry = _registry(
        tmp_path,
        {"600000": _quote_row("600000", name="浦发银行", price=10.5, prev_close=10.0,
                              change_pct=5.0)},
    )
    job = {**_premarket_job(), "channel": "desktop"}

    result = schedule_cli.run_job(
        job, now=datetime(2026, 11, 5, 9, 5), root=tmp_path, registry=registry
    )

    assert result["status"] == "degraded"
    assert result["delivery"]["ok"] is False
    items = inbox.list_items(root=tmp_path)
    assert len(items) == 1
    assert items[0]["id"] == result["inbox_id"]
    assert items[0]["title"] == "盘前摘要 2026-11-05"


def test_rerun_same_day_updates_single_row_and_preserves_read_at(tmp_path: Path) -> None:
    watchlist.add_watch("600000", root=tmp_path)
    registry = _registry(
        tmp_path,
        {"600000": _quote_row("600000", name="浦发银行", price=10.5, prev_close=10.0,
                              change_pct=5.0)},
    )
    job = _premarket_job()

    first = schedule_cli.run_job(
        job, now=datetime(2026, 11, 5, 9, 5), root=tmp_path, registry=registry
    )
    inbox.mark_read(first["inbox_id"], root=tmp_path, now=datetime(2026, 11, 5, 9, 10))
    second = schedule_cli.run_job(
        job, now=datetime(2026, 11, 5, 9, 6), root=tmp_path, registry=registry
    )

    items = inbox.list_items(root=tmp_path)
    assert second["dedupe_key"] == "premarket_digest:2026-11-05"
    assert len(items) == 1
    assert items[0]["is_read"] is True
    assert items[0]["read_at"] is not None


# --------------------------------------------------------------------------- #
# 收盘
# --------------------------------------------------------------------------- #

def test_close_digest_uses_local_daily_k_and_portfolio(tmp_path: Path) -> None:
    _seed_daily(
        tmp_path,
        [
            {"code": "600000", "trade_date": "2026-11-04", "close": 10.0},
            {"code": "600000", "trade_date": "2026-11-05", "close": 10.5},
            {"code": "000001", "trade_date": "2026-11-04", "close": 11.0},
            {"code": "000001", "trade_date": "2026-11-05", "close": 12.0},
        ],
    )
    _holding(tmp_path)
    watchlist.add_watch("000001", root=tmp_path)

    outcome = digest.build("close_digest", "close", root=tmp_path)

    assert outcome.status == digest.STATUS_OK
    assert outcome.trade_day == "2026-11-05"
    body = "\n".join(outcome.lines)
    assert "持仓 1 只" in body
    assert "600000 浦发银行 收盘 10.50 元 当日 +5.00% × 100 股 = 50.00 元" in body
    assert "持仓当日盈亏合计：50.00 元" in body
    assert "自选涨跌榜" in body and "000001 收盘 12.00 元 +9.09%" in body
    assert "今日没有卖出流水（0 笔）" in body


def test_close_digest_missing_previous_day_says_unavailable(tmp_path: Path) -> None:
    _seed_daily(tmp_path, [{"code": "600000", "trade_date": "2026-11-05", "close": 10.5}])
    _holding(tmp_path)

    outcome = digest.build("close_digest", "close", root=tmp_path)

    assert outcome.status == digest.STATUS_OK
    body = "\n".join(outcome.lines)
    assert "600000 浦发银行 当日盈亏 未取到" in body
    assert "持仓当日盈亏合计：—" in body
    assert "合计：0.00 元" not in body


def test_close_digest_without_ledger_says_so(tmp_path: Path) -> None:
    _seed_daily(tmp_path, [{"code": "600000", "trade_date": "2026-11-05", "close": 10.5}])

    outcome = digest.build("close_digest", "close", root=tmp_path)

    body = "\n".join(outcome.lines)
    assert "本机账本没有持仓" in body
    assert "本机账本为空，无从计算" in body


# --------------------------------------------------------------------------- #
# 自选变化
# --------------------------------------------------------------------------- #

def test_watch_changes_with_a_single_history_day_says_not_comparable(tmp_path: Path) -> None:
    watchlist.add_watch("600000", root=tmp_path)
    _seed_daily(tmp_path, [{"code": "600000", "trade_date": "2026-11-05", "close": 10.5}])

    outcome = digest.build("watch_changes", "watch", root=tmp_path)

    assert outcome.status == digest.STATUS_OK
    assert any("没有上一交易日可比" in line for line in outcome.lines)


def test_watch_changes_compares_previous_trading_day(tmp_path: Path) -> None:
    watchlist.add_watch("600000", root=tmp_path)
    _seed_daily(
        tmp_path,
        [
            {"code": "600000", "trade_date": "2026-11-04", "close": 10.0, "volume": 100000},
            {"code": "600000", "trade_date": "2026-11-05", "close": 11.0, "volume": 150000},
        ],
    )

    outcome = digest.build("watch_changes", "watch", root=tmp_path)

    body = "\n".join(outcome.lines)
    assert "对比 2026-11-04" in body
    assert "600000 收盘 11.00 元（+10.00%）成交量 150,000（+50.00%）" in body


def test_watch_changes_skips_when_watchlist_is_empty(tmp_path: Path) -> None:
    _seed_daily(tmp_path, [{"code": "600000", "trade_date": "2026-11-05", "close": 10.5}])

    outcome = digest.build("watch_changes", "watch", root=tmp_path)

    assert outcome.status == digest.STATUS_SKIPPED
    assert "自选为空" in outcome.detail


# --------------------------------------------------------------------------- #
# 通用
# --------------------------------------------------------------------------- #

def test_unknown_summary_is_a_failed_digest_not_an_exception() -> None:
    outcome = digest.build("x", "nope")

    assert outcome.status == digest.STATUS_FAILED
    assert "不认识" in outcome.detail


def test_dedupe_key_is_job_plus_trade_day(tmp_path: Path) -> None:
    outcome = digest.build("watch_changes", "watch", root=tmp_path)

    assert outcome.dedupe_key == f"watch_changes:{outcome.trade_day}"
