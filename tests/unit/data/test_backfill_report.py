"""缺口计算与报表渲染口径（QUOT-501）。

纯函数用例：不碰网络、不碰 DuckDB，锁住三件事——
1. 缺口 = 预期交易日 − 实际有 bar 的交易日，区间内 / 头 / 尾分开计；
2. `target_met` 只看窗口两端是否覆盖，区间内缺口由 `gap_alert` 单独列名；
3. amount 缺失只统计、不补值；CSV / Markdown 都带得出去。
"""

from __future__ import annotations

from ladderstock.data import backfill as gap

EXPECTED = ["2022-01-04", "2022-01-05", "2022-01-06", "2022-01-07"]


def _row(day: str, *, amount: float | None = None, source: str = "tickhub_kline") -> dict:
    record = {
        "code": "600000",
        "trade_date": day,
        "open": 10.0,
        "high": 10.5,
        "low": 9.8,
        "close": 10.2,
        "volume": 1000,
        "_source": source,
    }
    if amount is not None:
        record["amount"] = amount
    return record


def _stats(rows: list[dict], *, expected: list[str] = EXPECTED) -> dict:
    return gap.code_stats(
        code="600000",
        name="浦发银行",
        board="SH主板",
        rows=rows,
        expected=expected,
        start=expected[0],
        end=expected[-1],
        target_years=5,
        gap_threshold=0.02,
    )


def test_full_coverage_has_no_gap_and_meets_target_by_span() -> None:
    stats = _stats([_row(day) for day in EXPECTED])
    assert stats["gap_rate"] == 0.0
    assert stats["missing_days_in_span"] == 0
    assert stats["head_missing"] == 0 and stats["tail_missing"] == 0
    assert stats["actual_days"] == len(EXPECTED)
    assert stats["amount_missing_rate"] == 1.0  # 假数据没带 amount：只统计，不补
    assert stats["target_met"] is False  # 4 天跨度 < 5 年，跨度口径如实判否


def test_middle_gap_is_counted_and_alerted() -> None:
    stats = _stats([_row(day) for day in EXPECTED if day != "2022-01-06"])
    assert stats["missing_days_in_span"] == 1
    assert stats["gap_rate"] == 0.25
    assert stats["gap_alert"] is True
    assert stats["missing_ranges"]["ranges"] == ["2022-01-06"]
    # 两端仍覆盖：target_met 只看窗口两端，中间缺口交给 gap_alert
    assert stats["head_missing"] == 0 and stats["tail_missing"] == 0


def test_head_and_tail_gaps_are_separate_from_in_span() -> None:
    stats = _stats([_row("2022-01-05"), _row("2022-01-06")])
    assert stats["head_missing"] == 1
    assert stats["tail_missing"] == 1
    assert stats["missing_days_in_span"] == 0
    # 缺口率按整个窗口算：两端各缺 1 天 = 2/4，不能因为是「头/尾」就显示成 0%
    assert stats["missing_days_window"] == 2
    assert stats["gap_rate"] == 0.5
    assert stats["gap_alert"] is True
    assert stats["first_date"] == "2022-01-05"
    assert stats["last_date"] == "2022-01-06"


def test_no_rows_means_full_gap_and_zero_coverage() -> None:
    stats = _stats([])
    assert stats["actual_days"] == 0
    assert stats["gap_rate"] == 1.0
    assert stats["coverage_years"] == 0.0
    assert stats["target_met"] is False


def test_amount_present_is_not_counted_missing() -> None:
    stats = _stats([_row(day, amount=1.2e8) for day in EXPECTED])
    assert stats["amount_missing_days"] == 0
    assert stats["amount_missing_rate"] == 0.0
    assert stats["sources"] == ["tickhub_kline"]


def test_compress_ranges_merges_consecutive_days() -> None:
    result = gap.compress_ranges(
        ["2022-01-04", "2022-01-05", "2022-01-07", "2022-02-01", "2022-02-02"]
    )
    assert result["ranges"] == ["2022-01-04~2022-01-05", "2022-01-07", "2022-02-01~2022-02-02"]
    assert result["total_ranges"] == 3
    assert result["truncated"] is False


def test_explain_gap_names_source_window_and_no_fabrication() -> None:
    text = gap.explain_gap(
        {
            "status": "ok",
            "head_missing": 600,
            "tail_missing": 0,
            "first_date": "2024-03-01",
            "last_date": "2026-11-07",
            "missing_days_in_span": 2,
            "amount_missing_rate": 1.0,
        },
        stage="阶段1：腾讯 qfq",
    )
    assert "600 个预期交易日" in text
    assert "未接停牌日历" in text
    assert "amount 全缺" in text
    assert "阶段1：腾讯 qfq" in text


def test_render_csv_has_benchmark_and_one_row_per_code() -> None:
    def base(**overrides: object) -> dict:
        stats = _stats([_row(day) for day in EXPECTED])
        stats.update(kind="stock", status="ok", error="", **overrides)
        return stats

    report = {
        "generated_at": "2026-11-10T12:00:00+08:00",
        "stage": "阶段1",
        "source": "tencent",
        "source_detail": "tencent_kline@tencent",
        "window": {
            "start": EXPECTED[0],
            "end": EXPECTED[-1],
            "target_years": 5,
            "expected_days": 4,
        },
        "benchmark": {
            **_stats([_row(day) for day in EXPECTED]),
            "name": "上证指数",
            "board": "指数",
        },
        "summary": {
            "universe": 2,
            "ok": 2,
            "empty": 0,
            "failed": 0,
            "not_attempted": 0,
            "target_met": 0,
            "gap_alert": 1,
            "gap_threshold": 0.02,
            "amount_all_missing": 2,
        },
        "codes": [
            base(code="600000", name="浦发银行", board="SH主板"),
            base(code="000001", name="平安银行", board="SZ主板"),
        ],
        "top_gaps": [],
        "failures": [],
        "not_attempted": [],
        "dedupe": {
            "before_rows": 0,
            "written": 8,
            "after_rows": 8,
            "new_rows": 8,
            "existing_rows": 0,
            "watermark_before": None,
            "watermark_after": "2022-01-07",
        },
        "index_dedupe": {
            "before_rows": 0,
            "written": 4,
            "after_rows": 4,
            "new_rows": 4,
            "existing_rows": 0,
        },
        "report_dir": "-",
    }
    csv_text = gap.render_csv(report)
    lines = csv_text.strip().splitlines()
    assert len(lines) == 4  # 表头 + 基准 + 2 只票
    assert "benchmark" in lines[1]
    assert "600000" in lines[2] and "000001" in lines[3]

    markdown = gap.render_markdown(report)
    assert "缺口率 > 2%" in markdown
    assert "未接入停牌日历" in markdown
    assert "**新增行数** | 8" in markdown
