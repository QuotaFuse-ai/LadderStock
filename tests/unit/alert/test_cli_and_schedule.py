"""`ldst alert` CLI 面与调度分发的冒烟（不联网、不真取数）。

命令矩阵里的三条只读叶子由 `tests/fixtures/command_matrix.py` 走全仓契约；
这里钉用法错误、渠道配置与 `alert_check` 分发这三件矩阵覆盖不到的事。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from ladderstock import __main__  # noqa: F401  # 确保包可导入
from ladderstock.alert import store


def test_cli_alert_run_reports_skipped_without_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "ladderstock.alert.run.run_once",
        lambda **kwargs: {
            "status": "skipped",
            "checked": 0,
            "fired": [],
            "detail": "没有启用的提醒规则",
        },
    )
    monkeypatch.setattr(store, "list_rules", lambda **kwargs: [])
    # run_once 已被 monkeypatch，CLI 只是透传
    runner = CliRunner()
    from ladderstock.cli import main as main_mod

    result = runner.invoke(main_mod.app, ["alert", "run"])
    assert result.exit_code == 0, result.output
    assert "skipped" in result.stdout


def test_schedule_dispatches_alert_check(monkeypatch: pytest.MonkeyPatch) -> None:
    from ladderstock.cli.schedule import run_job

    captured: dict = {}

    def fake_run_once(**kwargs):
        captured.update(kwargs)
        return {"status": "ok", "checked": 2, "fired": [], "detail": "stub"}

    monkeypatch.setattr("ladderstock.alert.run.run_once", fake_run_once)
    result = run_job(
        {"id": "alert_close", "name": "收盘终检", "kind": "alert_check", "trigger": "15:05"}
    )
    assert result["status"] == "ok"
    assert result["kind"] == "alert_check"
    # 执行体必须收到 now/registry 透传（调度注入与 CLI/Web 同一个入口）
    assert set(captured) >= {"now", "registry"}


def test_add_channel_config_requires_valid_json_shape() -> None:
    """非法渠道 config 是用法错误：值不落库、报错带人话。"""
    with pytest.raises(Exception) as err:
        store.add_channel("bad", "email", config={"to_addrs": []}, root=Path(".temp/unused"))
    assert "渠道配置缺字段" in str(err.value) or "至少要一个收件人" in str(err.value)
