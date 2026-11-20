"""F1 覆盖判定层的单测（不连真库）。"""

from datetime import date

import pytest

from ladderstock.data import coverage


class _FakeCursor:
    """按表名分派返回值的假游标；每个表给一个「行列表」。"""

    def __init__(self, rows: dict[str, list | Exception]):
        self._rows = rows
        self._current: list | None = None

    def execute(self, sql: str, params=None):
        for table, row in self._rows.items():
            if table in sql:
                if isinstance(row, Exception):
                    raise row
                self._current = row
                return
        self._current = None

    def fetchone(self):
        if not self._current:
            return None
        return self._current[0]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch(monkeypatch, rows):
    monkeypatch.setattr(coverage, "_connect", lambda: _FakeConn(_FakeCursor(rows)))


TODAY = date(2026, 11, 20)


def test_missing_when_nothing_local(monkeypatch):
    _patch(monkeypatch, {
        "stock_agent_analysis_wide": [],
        "stock_financial_snapshot": [(None, 0)],
        "stock_quote_snapshot": [(None,)],
        "stock_kline": [(None,)],
        "stock_announcement": [(0, None)],
    })
    result = coverage.check_coverage("600000", today=TODAY)
    assert result["complete"] is False
    assert result["kinds"]["financial"]["state"] == coverage.STATE_MISSING
    assert result["kinds"]["financial"]["reason"] == coverage.REASON_NO_ROW
    assert set(result["missing"]) == {"financial", "quote", "announcement", "kline"}


def test_ready_from_wide_table(monkeypatch):
    metrics = {"years": [{"report_date": "2026-09-30", "data_status": "present"}]}
    _patch(monkeypatch, {
        "stock_agent_analysis_wide": [(date(2026, 11, 19), metrics, None)],
        "stock_quote_snapshot": [(date(2026, 11, 19),)],
        "stock_kline": [(date(2026, 11, 19),)],
        "stock_announcement": [(3, date(2026, 11, 10))],
    })
    result = coverage.check_coverage("600000", today=TODAY)
    assert result["kinds"]["financial"]["state"] == coverage.STATE_READY
    assert result["kinds"]["financial"]["source"] == "dws"
    assert result["kinds"]["financial"]["as_of"] == "2026-09-30"
    assert result["complete"] is True


def test_wide_table_too_old_is_stale(monkeypatch):
    metrics = {"years": [{"report_date": "2025-03-31", "data_status": "present"}]}
    _patch(monkeypatch, {
        "stock_agent_analysis_wide": [(date(2025, 4, 1), metrics, None)],
    })
    result = coverage.check_coverage("600000", kinds=["financial"], today=TODAY)
    info = result["kinds"]["financial"]
    assert info["state"] == coverage.STATE_STALE
    assert info["reason"] == coverage.REASON_OUT_OF_WINDOW
    assert info["age_days"] > coverage.FINANCIAL_FRESH_DAYS


def test_dwd_only_needs_wide_row(monkeypatch):
    """有明细没宽表：只需 build，不该当成"没数据"去重新采集。"""

    _patch(monkeypatch, {
        "stock_agent_analysis_wide": [],
        "stock_financial_snapshot": [(date(2026, 9, 30), 24)],
    })
    result = coverage.check_coverage("600000", kinds=["financial"], today=TODAY)
    info = result["kinds"]["financial"]
    assert info["state"] == coverage.STATE_STALE
    assert info["reason"] == coverage.REASON_NO_WIDE_ROW
    assert info["source"] == "dwd"


def test_dwd_ignores_ods_etl_status(monkeypatch):
    """判定只看 DWD 的实际行；ods.etl_status 停留在 pending 不影响结论。"""

    _patch(monkeypatch, {
        "stock_agent_analysis_wide": [],
        "stock_financial_snapshot": [(date(2026, 9, 30), 24)],
    })
    result = coverage.check_coverage("600000", kinds=["financial"], today=TODAY)
    assert result["kinds"]["financial"]["state"] != coverage.STATE_MISSING


def test_quote_stale_after_three_days(monkeypatch):
    _patch(monkeypatch, {"stock_quote_snapshot": [(date(2026, 11, 16),)]})
    result = coverage.check_coverage("600000", kinds=["quote"], today=TODAY)
    info = result["kinds"]["quote"]
    assert info["state"] == coverage.STATE_STALE
    assert info["age_days"] == 4


def test_announcement_window(monkeypatch):
    _patch(monkeypatch, {"stock_announcement": [(2, date(2026, 11, 12))]})
    result = coverage.check_coverage("600000", kinds=["announcement"], today=TODAY)
    info = result["kinds"]["announcement"]
    assert info["state"] == coverage.STATE_READY
    assert info["rows"] == 2


def test_expand_kinds_adds_quote_for_financial():
    assert coverage.expand_kinds(["financial"]) == ["financial", "quote"]
    assert coverage.expand_kinds(["kline"]) == ["kline", "quote"]
    assert coverage.expand_kinds(["announcement"]) == ["announcement"]


def test_normalize_symbol():
    assert coverage.normalize_symbol("sh600000") == "600000"
    assert coverage.normalize_symbol("600519.SH") == "600519"
    assert coverage.normalize_symbol("NVDA") == "NVDA"


def test_unknown_kind_raises():
    with pytest.raises(ValueError):
        coverage.check_coverage("600000", kinds=["bogus"])


def test_db_error_surfaces(monkeypatch):
    _patch(monkeypatch, {"stock_agent_analysis_wide": RuntimeError("conn refused")})
    with pytest.raises(coverage.CoverageUnavailable):
        coverage.check_coverage("600000", kinds=["financial"])
