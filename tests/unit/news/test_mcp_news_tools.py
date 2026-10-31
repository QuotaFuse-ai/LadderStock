"""MCP 侧的两个资讯工具：信封契约、只读口径、默认不写库（R6 要求 5）。

工具是给外部 agent 调的，所以这里盯的是**副作用**和**口径诚实**：

- `read_only=true` 必须真的成立：默认 `store=false`，反复调用不留库文件；
- 输出必须是 `LadderEnvelope`（域 `news`），和其它工具同一份契约，不另立形状；
- `event_calendar` 的三个缺口在 `source_status` 上要体现成 `partial`——
  日历不完整是口径事实，不能被包装成「一切正常」。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fixtures.fake_peers import FakeTransport
from fixtures.news_samples import WINDOW_DAY, news_handlers, news_tools

from ladderstock.core.privacy import private_keys_present
from ladderstock.mcp.server import ENVELOPE_OUTPUT_SCHEMA, ToolError, dispatch

ALL_TOOLS = tuple(news_tools())


@pytest.fixture
def failing_tools() -> dict[str, tuple[str, ...]]:
    return {"failing": ()}


@pytest.fixture(autouse=True)
def _offline_transport(
    repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failing_tools
):
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))

    def _build(_peers):
        return FakeTransport(
            handlers=news_handlers(failing=failing_tools["failing"]), tools=list(ALL_TOOLS)
        )

    monkeypatch.setattr("ladderstock.data.provider_registry.build_transport", _build)


@pytest.fixture
def set_failing(failing_tools):
    """让用例点名「这一轮哪些上游工具挂掉」。"""

    def _set(*failing: str) -> None:
        failing_tools["failing"] = failing

    return _set


def _envelope(name: str, arguments: dict, root: Path) -> dict:
    return dispatch(name, arguments, root=root)


# --------------------------------------------------------------------------- #
# news_stream
# --------------------------------------------------------------------------- #

def test_news_stream_returns_the_envelope_contract(tmp_path: Path) -> None:
    result = _envelope(
        "news_stream", {"date": WINDOW_DAY, "codes": ["600519"]}, tmp_path
    )
    assert set(result) >= set(ENVELOPE_OUTPUT_SCHEMA["required"])
    assert result["domain"] == "news"
    assert result["topic"] == "news_stream"
    assert result["data_as_of"] == WINDOW_DAY
    assert result["payload"]["items"], "样本链路应该出条目"
    assert result["source_detail"], "来源标签不能空（DEC-018 R4）"
    assert json.dumps(result, ensure_ascii=False)


def test_news_stream_is_read_only_by_default(tmp_path: Path) -> None:
    """`store` 默认 false：工具被反复调用不该在本地留下库文件。"""
    for _ in range(2):
        _envelope("news_stream", {"date": WINDOW_DAY, "codes": ["600519"]}, tmp_path)
    assert not (tmp_path / "news.sqlite").exists()
    assert not (tmp_path / "data" / "news.sqlite").exists()


def test_news_stream_writes_only_when_asked(tmp_path: Path) -> None:
    result = _envelope(
        "news_stream",
        {"date": WINDOW_DAY, "codes": ["600519"], "store": True},
        tmp_path,
    )
    assert result["payload"]["summary"]["persisted"] is True
    assert (tmp_path / "news.sqlite").is_file()


def test_news_stream_ai_is_off_and_records_why(tmp_path: Path) -> None:
    result = _envelope("news_stream", {"date": WINDOW_DAY, "codes": ["600519"]}, tmp_path)
    ai = result["payload"]["ai"]
    assert ai["requested"] is False and ai["enabled"] is False
    assert "默认关闭" in ai["skipped_reason"]


def test_news_stream_reports_degradation_in_the_envelope(tmp_path: Path, set_failing) -> None:
    set_failing(*ALL_TOOLS)
    result = _envelope("news_stream", {"date": WINDOW_DAY, "codes": ["600519"]}, tmp_path)
    assert result["source_status"] in {"degraded", "partial"}
    assert result["confidence"] == "low"
    assert result["payload"]["exit_code"] == 4


def test_news_stream_accepts_a_window_and_source_filter(tmp_path: Path) -> None:
    result = _envelope(
        "news_stream",
        {"date": "2026-10-29", "until": "2026-10-31", "sources": ["flash_cls"], "limit": 5},
        tmp_path,
    )
    payload = result["payload"]
    assert payload["window"] == {"start": "2026-10-29", "end": "2026-10-31"}
    assert payload["sources"] == ["flash_cls"]
    assert [row["dataset"] for row in payload["sources_status"]] == ["flash_cls"]


@pytest.mark.parametrize("bad_date", ["20261030", "not-a-day", "", None])
def test_news_stream_rejects_bad_dates(tmp_path: Path, bad_date) -> None:
    with pytest.raises(ToolError):
        _envelope("news_stream", {"date": bad_date}, tmp_path)


def test_news_stream_rejects_bad_until(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="until"):
        _envelope("news_stream", {"date": WINDOW_DAY, "until": "2026/10/31"}, tmp_path)


def test_news_stream_rejects_bad_limit(tmp_path: Path) -> None:
    with pytest.raises(ToolError, match="limit"):
        _envelope("news_stream", {"date": WINDOW_DAY, "limit": "abc"}, tmp_path)


def test_news_stream_output_has_no_private_keys(tmp_path: Path) -> None:
    result = _envelope("news_stream", {"date": WINDOW_DAY, "codes": ["600519"]}, tmp_path)
    assert private_keys_present(result) == []


# --------------------------------------------------------------------------- #
# event_calendar
# --------------------------------------------------------------------------- #

def test_event_calendar_returns_the_envelope(tmp_path: Path) -> None:
    result = _envelope("event_calendar", {"month": "2026-11", "codes": ["600519"]}, tmp_path)
    assert result["domain"] == "news" and result["topic"] == "event_calendar"
    assert result["data_as_of"] == "2026-11-01"
    payload = result["payload"]
    assert payload["summary"]["no_source"] == [
        "earnings_disclosure",
        "shareholder_meeting",
        "policy_window",
    ]


def test_event_calendar_marks_the_gaps_in_source_status(tmp_path: Path) -> None:
    """三个缺口是口径事实：信封的 source_status 要说 partial，不能装成 ok。"""
    result = _envelope("event_calendar", {"month": "2026-11", "codes": ["600519"]}, tmp_path)
    assert result["source_status"] == "partial"
    gaps = "暂无数据源：earnings_disclosure、shareholder_meeting、policy_window"
    assert gaps in result["source_detail"]


def test_event_calendar_degrades_when_the_peer_is_down(tmp_path: Path, set_failing) -> None:
    set_failing("lockup_expiry", "earnings_forecast")
    result = _envelope("event_calendar", {"month": "2026-11", "codes": ["600519"]}, tmp_path)
    assert result["source_status"] == "degraded"
    assert result["confidence"] == "low"


def test_event_calendar_no_fetch_is_honest_about_freshness(tmp_path: Path) -> None:
    result = _envelope(
        "event_calendar", {"month": "2026-11", "codes": ["600519"], "fetch": False}, tmp_path
    )
    assert result["freshness"] != "realtime"
    assert result["payload"]["sections"]


@pytest.mark.parametrize("bad_month", ["2026", "2026-13", "abc", None, 202611])
def test_event_calendar_rejects_bad_months(tmp_path: Path, bad_month) -> None:
    with pytest.raises(ToolError):
        _envelope("event_calendar", {"month": bad_month}, tmp_path)


def test_event_calendar_does_not_write_the_db(tmp_path: Path) -> None:
    """日历是只读视图：连调两次不留下任何本机写入（抽取事件由 `news` 命令负责入库）。"""
    for _ in range(2):
        _envelope("event_calendar", {"month": "2026-11", "codes": ["600519"]}, tmp_path)
    assert not (tmp_path / "news.sqlite").exists()


def test_event_calendar_output_has_no_private_keys(tmp_path: Path) -> None:
    result = _envelope("event_calendar", {"month": "2026-11"}, tmp_path)
    assert private_keys_present(result) == []


# --------------------------------------------------------------------------- #
# 分发面
# --------------------------------------------------------------------------- #

def test_both_tools_are_dispatchable() -> None:
    from ladderstock.mcp.server import TOOL_HANDLERS

    assert {"news_stream", "event_calendar"} <= set(TOOL_HANDLERS)


def test_tools_advertise_themselves_as_read_only() -> None:
    from ladderstock.mcp.tools import TOOL_SPECS

    specs = {spec.name: spec for spec in TOOL_SPECS}
    for name in ("news_stream", "event_calendar"):
        assert specs[name].read_only is True
        assert specs[name].domain == "news"
