"""F11 简报（`watch/brief.py`）：条目装配、指纹去重、开关。

四条硬约束（对应 F11 验收）：

1. 每份简报 ≤5 条，超出截断要写在 `detail` 里，不能静默丢；
2. 每条都有 `source`（能顺着回查），取不到数就写原因，不补 0；
3. 指纹只看内容：同一天重复生成 / 跨 job 共用同一段正文，只推一次；
4. `publish` 落 `journal/inbox`，返回**本次真实推送了什么**。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from ladderstock.journal import inbox
from ladderstock.watch import brief

WEEKDAY_MORNING = datetime(2026, 11, 20, 9, 5)
WEEKDAY_CLOSE = datetime(2026, 11, 20, 15, 45)
WEEKEND = datetime(2026, 11, 21, 9, 5)


def test_morning_brief_has_items_with_source(tmp_path: Path) -> None:
    result = brief.build(brief.BRIEF_MORNING, now=WEEKDAY_MORNING, root=tmp_path)
    assert result.status == brief.STATUS_OK
    assert result.trade_day == "2026-11-20"
    assert 1 <= len(result.items) <= brief.MAX_ITEMS
    for item in result.items:
        assert item.title
        assert item.source, f"{item.title} 没有出处"
        assert item.fingerprint == item.fingerprint.strip()
        assert len(item.fingerprint) == 16


def test_limit_truncation_is_reported_not_silent(tmp_path: Path) -> None:
    result = brief.build_morning(now=WEEKDAY_MORNING, root=tmp_path, limit=3)
    assert len(result.items) == 3
    assert "另有" in result.detail
    assert "未推送" in result.detail


def test_unknown_kind_folds_to_failed_without_raising(tmp_path: Path) -> None:
    result = brief.build("nope", now=WEEKDAY_MORNING, root=tmp_path)
    assert result.status == brief.STATUS_FAILED
    assert "不认识的简报类型" in result.detail
    assert result.items == []


def test_fingerprint_depends_on_content_only() -> None:
    left = brief.Item(kind="a", title="同标题", lines=["同一行"], source="x")
    right = brief.Item(kind="b", title="同标题", lines=["同一行"], source="y")
    assert left.fingerprint == right.fingerprint
    changed = brief.Item(kind="a", title="同标题", lines=["换一行"], source="x")
    assert changed.fingerprint != left.fingerprint


def test_weekend_premarket_skips_but_brief_still_builds(tmp_path: Path) -> None:
    result = brief.build(brief.BRIEF_MORNING, now=WEEKEND, root=tmp_path)
    assert result.status == brief.STATUS_OK
    quotes_item = next(item for item in result.items if item.kind == "quotes")
    assert "skipped" in quotes_item.source
    # 周末的盘前摘要按理 skipped；这条要照实写原因，而不是编一个价格
    assert quotes_item.lines


def test_alerts_skip_without_holdings(tmp_path: Path) -> None:
    result = brief.build(brief.BRIEF_ALERTS, now=WEEKDAY_CLOSE, root=tmp_path)
    assert result.status == brief.STATUS_SKIPPED
    assert result.items == []
    assert "持仓" in result.detail or "自选" in result.detail


def test_publish_dedupes_same_day(tmp_path: Path) -> None:
    first = brief.publish(
        brief.build(brief.BRIEF_MORNING, now=WEEKDAY_MORNING, root=tmp_path), root=tmp_path
    )
    assert first["ok"] is True
    fingerprints = {row["fingerprint"] for row in first["pushed"]}
    assert fingerprints

    second = brief.publish(
        brief.build(brief.BRIEF_MORNING, now=WEEKDAY_MORNING, root=tmp_path), root=tmp_path
    )
    assert second["pushed"] == []
    assert len(second["skipped"]) == len(fingerprints)
    state = json.loads(Path(second["state_path"]).read_text(encoding="utf-8"))
    assert set(state["published"]) == fingerprints


def test_publish_writes_inbox_with_brief_kind(tmp_path: Path) -> None:
    outcome = brief.publish(
        brief.build(brief.BRIEF_MORNING, now=WEEKDAY_MORNING, root=tmp_path), root=tmp_path
    )
    items = inbox.list_items(limit=20, root=tmp_path)
    brief_rows = [row for row in items if row["kind"] == "brief"]
    assert len(brief_rows) == len(outcome["pushed"])
    assert all(row["job"].startswith("brief:") for row in brief_rows)


def test_publish_notify_reports_delivery(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    outcome = brief.publish(
        brief.build(brief.BRIEF_MORNING, now=WEEKDAY_MORNING, root=tmp_path),
        root=tmp_path,
        notify=True,
        channel="console",
    )
    assert outcome["delivered"], "打开通知就该有投递记录"
    assert outcome["delivered"][0]["channel"] == "console"
    captured = capsys.readouterr()
    # console 通道写 stderr（与 `notifier` 既有实现一致），不是 stdout
    joined = captured.out + captured.err
    assert "晨报" in joined


def test_settings_roundtrip_and_validation(tmp_path: Path) -> None:
    saved = brief.save_settings(config_root=tmp_path, notify=True, channel="desktop", threshold=3)
    assert saved["notify"] is True
    assert saved["channel"] == "desktop"
    assert saved["threshold"] == 3.0
    assert Path(saved["path"]).is_file()

    loaded = brief.load_settings(tmp_path)
    assert loaded["notify"] is True
    assert loaded["channel"] == "desktop"
    assert loaded["threshold"] == 3.0

    with pytest.raises(ValueError):
        brief.save_settings(config_root=tmp_path, channel="telegram")
    with pytest.raises(ValueError):
        brief.save_settings(config_root=tmp_path, threshold=-1)
    with pytest.raises(ValueError):
        brief.save_settings(config_root=tmp_path, notify="yes")


def test_settings_default_when_file_missing(tmp_path: Path) -> None:
    settings = brief.load_settings(tmp_path / "no-config")
    assert settings == brief.DEFAULT_SETTINGS


def test_history_reads_state_files(tmp_path: Path) -> None:
    brief.publish(brief.build(brief.BRIEF_MORNING, now=WEEKDAY_MORNING, root=tmp_path), root=tmp_path)
    days = brief.history(root=tmp_path, limit=5)
    assert len(days) == 1
    assert days[0]["trade_day"] == "2026-11-20"
    assert days[0]["published"] > 0
