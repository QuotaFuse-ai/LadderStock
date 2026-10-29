"""MCP `ladder_get_watch` 三态：真实状态文件 / 无文件 / 坏文件。

QUANT-109：`_tool_watch()` 曾经是硬编码桩（`available=false` + 「未实现」），
`ldst watch start` 交付后 AI 侧永远拿不到信号。现在它读 `data/watch/status.json`，
判型复用 `watch/status.py`（与 `ldst watch status` 同一份判据）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from ladderstock.cli.main import app
from ladderstock.mcp.server import dispatch
from ladderstock.watch import status as status_file

DAY = "2026-10-22"

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

runner = CliRunner()


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #

@pytest.fixture()
def data_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 `data_dir()` 钉在 tmp（CLI 侧）；MCP 侧用同一个目录当 root。"""
    target = tmp_path / "data"
    monkeypatch.setenv("LDST_DATA_DIR", str(target))
    return target


@pytest.fixture()
def bars_file(tmp_path: Path) -> Path:
    """09:30 起每根分钟 bar：09:33 相对 09:30 +4.5%，09:34 封板。"""
    rows = [
        {"ts": f"{DAY}T09:30:00", "code": "600000", "price": 10.0, "limit_price": 11.0},
        {"ts": f"{DAY}T09:31:00", "code": "600000", "price": 10.1, "limit_price": 11.0},
        {"ts": f"{DAY}T09:32:00", "code": "600000", "price": 10.2, "limit_price": 11.0},
        {"ts": f"{DAY}T09:33:00", "code": "600000", "price": 10.45, "limit_price": 11.0},
        {"ts": f"{DAY}T09:34:00", "code": "600000", "price": 11.0, "limit_price": 11.0},
    ]
    path = tmp_path / f"{DAY}.jsonl"
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    return path


def run_watch_start(bars_file: Path) -> dict[str, Any]:
    result = runner.invoke(
        app,
        ["watch", "start", "--source", str(bars_file), "--date", DAY, "--output", "json"],
    )
    assert result.exit_code == 0, result.stderr
    return json.loads(result.stdout)


def watch_envelope(root: Path) -> dict[str, Any]:
    return dispatch("ladder_get_watch", {"date": DAY}, root=root)


def write_status(root: Path, payload: Any) -> Path:
    path = status_file.status_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _assert_contract(envelope: dict[str, Any]) -> None:
    assert set(envelope) == ENVELOPE_KEYS
    assert envelope["domain"] == "review"
    assert envelope["topic"] == "watch"
    assert envelope["source"] == "ladderstock"
    assert envelope["data_as_of"] == DAY
    assert isinstance(envelope["source_detail"], str) and envelope["source_detail"]
    assert envelope["source_status"] in {"ok", "degraded", "partial"}
    assert isinstance(envelope["payload"], dict)


# --------------------------------------------------------------------------- #
# 三态
# --------------------------------------------------------------------------- #

def test_running_status_exposes_real_events(data_home: Path, bars_file: Path) -> None:
    run_watch_start(bars_file)

    envelope = watch_envelope(data_home)

    _assert_contract(envelope)
    assert envelope["source_status"] == "ok"
    assert envelope["confidence"] == "high"
    payload = envelope["payload"]
    assert payload["data_source_status"] == "ok"
    assert payload["watch_state"] == status_file.STATE_OK

    watch = payload["watch"]
    assert watch["running"] is True
    assert watch["mode"] == "offline-single-pass"
    assert isinstance(watch["detail"], str) and watch["detail"]
    assert watch["watch_date"] == DAY
    assert watch["event_count"] == 2
    assert watch["bars"] == 5
    assert [event["kind"] for event in watch["recent_events"]] == ["threshold_up", "seal"]
    assert watch["recent_events"][0]["value"] == 4.5

    # source_detail 必须指向真实文件
    status_path = status_file.status_path(data_home)
    assert str(status_path) in envelope["source_detail"]
    assert payload["status_path"] == str(status_path)
    assert watch["events_file"] == str(data_home / "watch" / f"events-{DAY}.jsonl")


def test_matches_cli_watch_status(data_home: Path, bars_file: Path) -> None:
    """同一份状态文件：MCP 与 `ldst watch status` 的计数与事件必须一致。"""
    run_watch_start(bars_file)

    cli = runner.invoke(app, ["watch", "status", "--output", "json"])
    assert cli.exit_code == 0
    cli_payload = json.loads(cli.stdout)

    watch = watch_envelope(data_home)["payload"]["watch"]

    assert watch["running"] == cli_payload["running"]
    assert watch["mode"] == cli_payload["mode"]
    assert watch["event_count"] == cli_payload["event_count"]
    assert watch["recent_events"] == cli_payload["recent_events"]


def test_stopped_status_still_reports_events(data_home: Path, bars_file: Path) -> None:
    run_watch_start(bars_file)
    stop = runner.invoke(app, ["watch", "stop", "--output", "json"])
    assert stop.exit_code == 0

    watch = watch_envelope(data_home)["payload"]["watch"]

    assert watch["running"] is False
    assert "已停止" in watch["detail"]
    assert watch["event_count"] == 2  # 停止不清空历史信号


def test_missing_status_file_is_honest(tmp_path: Path) -> None:
    envelope = watch_envelope(tmp_path)

    _assert_contract(envelope)
    assert envelope["source_status"] == "partial"
    assert envelope["confidence"] == "low"
    payload = envelope["payload"]
    assert payload["data_source_status"] == "missing"
    assert payload["watch"] is None
    assert payload["watch_state"] == status_file.STATE_MISSING
    assert "尚未执行 ldst watch start" in payload["reason"]
    assert str(status_file.status_path(tmp_path)) in payload["reason"]
    # 不能再说「未实现」——QUOT-97 已交付
    assert "未实现" not in payload["reason"]
    assert "未实现" not in json.dumps(envelope, ensure_ascii=False)


def test_corrupt_status_file_is_reported_not_raised(tmp_path: Path) -> None:
    write_status(tmp_path, "{not json")

    envelope = watch_envelope(tmp_path)  # 不抛异常

    _assert_contract(envelope)
    payload = envelope["payload"]
    assert payload["data_source_status"] == "missing"
    assert payload["watch"] is None
    assert payload["watch_state"] == status_file.STATE_CORRUPT
    assert str(status_file.status_path(tmp_path)) in payload["reason"]
    assert "JSONDecodeError" in payload["reason"]
    assert "未实现" not in json.dumps(envelope, ensure_ascii=False)


def test_unsupported_schema_is_reported_by_field(tmp_path: Path) -> None:
    write_status(tmp_path, {"running": "yes"})

    envelope = watch_envelope(tmp_path)

    _assert_contract(envelope)
    payload = envelope["payload"]
    assert payload["data_source_status"] == "missing"
    assert payload["watch"] is None
    assert payload["watch_state"] == status_file.STATE_UNSUPPORTED
    assert "schema 不认识" in payload["reason"]
    assert "running" in payload["reason"]
    assert "未实现" not in json.dumps(envelope, ensure_ascii=False)


def test_recent_events_truncated_like_cli(tmp_path: Path) -> None:
    events = [
        {
            "ts": f"{DAY}T09:30:{index:02d}",
            "code": "600000",
            "kind": "threshold_up",
            "value": 3.5,
            "source_detail": "file:test",
        }
        for index in range(status_file.STATUS_RECENT_EVENTS + 10)
    ]
    write_status(
        tmp_path,
        {
            "running": True,
            "mode": "offline-single-pass",
            "date": DAY,
            "threshold": 3.0,
            "started_at": f"{DAY}T09:30:00",
            "last_run_at": f"{DAY}T09:30:00",
            "bars": len(events),
            "event_count": len(events),
            "recent_events": events,
            "events_file": str(tmp_path / "watch" / f"events-{DAY}.jsonl"),
        },
    )

    watch = watch_envelope(tmp_path)["payload"]["watch"]

    assert watch["event_count"] == len(events)
    assert len(watch["recent_events"]) == status_file.STATUS_RECENT_EVENTS
    assert watch["recent_events"] == events[-status_file.STATUS_RECENT_EVENTS:]


def test_declared_tool_still_read_only() -> None:
    from ladderstock.mcp.tools import TOOL_SPECS

    spec = next(item for item in TOOL_SPECS if item.name == "ladder_get_watch")
    assert spec.read_only is True
    assert spec.input_schema.get("required") == ["date"]
