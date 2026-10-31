"""盯盘信号 / 通知 / 调度的端到端契约（全部离线，不联网、不取数）。

覆盖 CLI.md 与 RELEASE_CHECKLIST 里的冒烟路径：

- `watch start --source <fixture>` → 信号落盘，重复跑不重复上报，缺样本 4；
- `watch status` / `watch stop` 的如实状态与幂等；
- `notify test` 的三个出口：成功 0、渠道名非法 2、渠道不可用 4（降级不阻塞）；
- `schedule list` 展示默认 09:05 / 15:05，配置缺失回退内置表并告警；
- `schedule run --once` 跑一轮、单任务失败只记 degraded、配置非法退出 3。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ladderstock.cli.main import app
from ladderstock.watch.notifier import DESKTOP_CMD_ENV

DAY = "2026-10-22"

runner = CliRunner()


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #

@pytest.fixture()
def data_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 data_dir() 钉在 tmp，避免测试污染真实缓存（DEC-001）。"""
    target = tmp_path / "data"
    monkeypatch.setenv("LDST_DATA_DIR", str(target))
    return target


@pytest.fixture()
def bars_file(tmp_path: Path) -> Path:
    """09:30 起每根分钟 bar：10.00 → 10.45（09:33 相对 09:30 涨 4.5%），最后封板。"""
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


def _json(result) -> dict:
    return json.loads(result.stdout)


# --------------------------------------------------------------------------- #
# watch
# --------------------------------------------------------------------------- #

def test_watch_start_detects_and_persists(data_home: Path, bars_file: Path) -> None:
    result = runner.invoke(
        app,
        ["watch", "start", "--source", str(bars_file), "--date", DAY, "--output", "json"],
    )

    assert result.exit_code == 0, result.stderr
    payload = _json(result)
    assert payload["ok"] is True
    assert payload["date"] == DAY
    assert payload["bars"] == 5
    assert payload["mode"] == "offline-single-pass"
    assert payload["text_only"] is True
    assert payload["new_events"] == 2

    kinds = [(event["kind"], event["code"]) for event in payload["events"]]
    assert kinds == [("threshold_up", "600000"), ("seal", "600000")]
    assert payload["events"][0]["ts"].startswith(f"{DAY}T09:33")
    assert payload["events"][0]["value"] == 4.5

    events_file = Path(payload["events_file"])
    assert events_file.is_file()
    rows = [json.loads(line) for line in events_file.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert set(rows[0]) == {"ts", "code", "kind", "value", "source_detail"}

    status = json.loads(Path(payload["status_path"]).read_text(encoding="utf-8"))
    assert status["running"] is True
    assert status["event_count"] == 2
    assert status["source"] == str(bars_file)


def test_watch_start_twice_does_not_repeat_events(
    data_home: Path, bars_file: Path
) -> None:
    args = [
        "watch", "start", "--source", str(bars_file), "--date", DAY, "--output", "json"
    ]
    first = runner.invoke(app, args)
    second = runner.invoke(app, args)

    assert first.exit_code == 0
    assert second.exit_code == 0
    payload = _json(second)
    assert payload["new_events"] == 0
    assert payload["known_events"] == 2
    assert payload["event_count"] == 2

    events_file = Path(payload["events_file"])
    lines = events_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2


def test_watch_start_without_local_samples_exits_4(data_home: Path) -> None:
    result = runner.invoke(app, ["watch", "start", "--date", DAY, "--output", "json"])

    assert result.exit_code == 4
    assert "minute_bars" in result.stderr
    assert "DEC-006" in result.stderr


def test_watch_start_rejects_bad_threshold(data_home: Path, bars_file: Path) -> None:
    result = runner.invoke(
        app,
        ["watch", "start", "--threshold", "0", "--source", str(bars_file), "--date", DAY],
    )

    assert result.exit_code == 2
    assert "threshold" in result.stderr


def test_watch_status_is_honest_before_first_run(data_home: Path) -> None:
    result = runner.invoke(app, ["watch", "status", "--output", "json"])

    assert result.exit_code == 0
    payload = _json(result)
    assert payload["running"] is False
    assert "尚未执行" in payload["detail"]
    assert payload["recent_events"] == []


def test_watch_status_and_stop_after_a_run(data_home: Path, bars_file: Path) -> None:
    runner.invoke(
        app,
        ["watch", "start", "--source", str(bars_file), "--date", DAY, "--output", "json"],
    )

    status = runner.invoke(app, ["watch", "status", "--output", "json"])
    assert status.exit_code == 0
    assert _json(status)["event_count"] == 2

    stop = runner.invoke(app, ["watch", "stop", "--output", "json"])
    assert stop.exit_code == 0
    stopped = _json(stop)
    assert stopped["stopped"] is True
    assert stopped["running"] is False

    again = runner.invoke(app, ["watch", "stop", "--output", "json"])
    assert again.exit_code == 0
    assert _json(again)["stopped"] is False  # 幂等


# --------------------------------------------------------------------------- #
# notify
# --------------------------------------------------------------------------- #

def test_notify_test_console_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    result = runner.invoke(app, ["notify", "test", "--output", "json"])

    assert result.exit_code == 0, result.stderr
    payload = _json(result)
    assert payload["ok"] is True
    assert payload["channel"] == "console"
    assert payload["local_only"] is True
    assert payload["available_channels"] == ["console", "desktop"]
    # 通知正文走 stderr，stdout 只有结构化结果
    assert "LadderStock 通知链路冒烟" in result.stderr


def test_notify_test_unknown_channel_exits_2() -> None:
    result = runner.invoke(app, ["notify", "test", "--channel", "telegram"])

    assert result.exit_code == 2
    assert "未知通知渠道" in result.stderr


def test_notify_test_unavailable_channel_exits_4(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        DESKTOP_CMD_ENV, f'"{sys.executable}" -c "import sys; sys.exit(7)"'
    )

    result = runner.invoke(
        app, ["notify", "test", "--channel", "desktop", "--output", "json"]
    )

    assert result.exit_code == 4
    payload = _json(result)
    assert payload["ok"] is False
    assert payload["degraded"] is True
    assert "不阻塞" in result.stderr or "降级" in result.stderr


# --------------------------------------------------------------------------- #
# schedule
# --------------------------------------------------------------------------- #

def test_schedule_list_shows_default_jobs(repo: Path) -> None:
    """仓库自带 config/schedule.yaml：盘前提醒 + 6 时点快照 + 收盘对账。"""
    result = runner.invoke(app, ["schedule", "list", "--output", "json"])

    assert result.exit_code == 0, result.stderr
    payload = _json(result)
    assert payload["job_count"] == 8
    assert payload["timezone"] == "Asia/Shanghai"
    assert [job["trigger"] for job in payload["jobs"]] == [
        "09:05",
        "09:25",
        "09:35",
        "10:00",
        "11:30",
        "14:00",
        "15:00",
        "15:05",
    ]
    assert [job["kind"] for job in payload["jobs"]] == [
        "notify",
        "snapshot",
        "snapshot",
        "snapshot",
        "snapshot",
        "snapshot",
        "snapshot",
        "reconcile",
    ]


def test_schedule_list_table_output(repo: Path) -> None:
    result = runner.invoke(app, ["schedule", "list", "--output", "table"])

    assert result.exit_code == 0
    assert "09:05" in result.stdout
    assert "15:05" in result.stdout
    assert "盘前提醒" in result.stdout


def test_schedule_list_falls_back_to_builtin_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LDST_CONFIG_DIR", str(tmp_path / "nowhere"))

    result = runner.invoke(app, ["schedule", "list", "--output", "json"])

    assert result.exit_code == 0
    payload = _json(result)
    assert payload["config"] == "builtin"
    assert payload["job_count"] == 8
    assert payload["warnings"] and "内置默认表" in payload["warnings"][0]


def test_schedule_run_once_is_offline_and_degrades_gracefully(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """没有验证条件时对账跳过、没有桌面后端时通知降级——整轮仍是 0。"""
    config_home = tmp_path / "config"
    config_home.mkdir()
    (config_home / "schedule.yaml").write_text(
        "scheduler:\n"
        "  timezone: Asia/Shanghai\n"
        "  misfire_grace_seconds: 300\n"
        "jobs:\n"
        "  - id: premarket_reminder\n"
        "    name: 09:05 盘前提醒\n"
        "    kind: notify\n"
        "    trigger: '09:05'\n"
        "    enabled: true\n"
        "    channel: console\n"
        "    message: 核对阈值\n"
        "  - id: close_reconcile\n"
        "    name: 15:05 收盘对账\n"
        "    kind: reconcile\n"
        "    trigger: '15:05'\n"
        "    enabled: true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LDST_CONFIG_DIR", str(config_home))
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path / "data"))

    result = runner.invoke(app, ["schedule", "run", "--once", "--output", "json"])

    assert result.exit_code == 0, result.stderr
    payload = _json(result)
    assert payload["mode"] == "once"
    assert payload["job_count"] == 2
    by_id = {row["id"]: row for row in payload["results"]}
    assert by_id["premarket_reminder"]["status"] == "ok"
    assert by_id["close_reconcile"]["status"] == "skipped"
    assert by_id["close_reconcile"]["detail"]
    assert all("elapsed_ms" in row for row in payload["results"])
    # 盘前提醒真的投递到了 stderr
    assert "核对阈值" in result.stderr


def test_schedule_run_once_marks_failed_delivery_as_degraded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_home = tmp_path / "config"
    config_home.mkdir()
    (config_home / "schedule.yaml").write_text(
        "scheduler:\n"
        "  timezone: Asia/Shanghai\n"
        "  misfire_grace_seconds: 60\n"
        "jobs:\n"
        "  - id: desktop_ping\n"
        "    name: 桌面提醒\n"
        "    kind: notify\n"
        "    trigger: '09:05'\n"
        "    enabled: true\n"
        "    channel: desktop\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LDST_CONFIG_DIR", str(config_home))
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv(
        DESKTOP_CMD_ENV, f'"{sys.executable}" -c "import sys; sys.exit(9)"'
    )

    result = runner.invoke(app, ["schedule", "run", "--once", "--output", "json"])

    # 投递失败只记 degraded，不改变整轮退出码
    assert result.exit_code == 0
    payload = _json(result)
    assert payload["results"][0]["status"] == "degraded"


def test_schedule_run_once_unknown_job_exits_2(repo: Path) -> None:
    result = runner.invoke(
        app, ["schedule", "run", "--once", "--job", "not_a_job", "--output", "json"]
    )

    assert result.exit_code == 2
    assert "not_a_job" in result.stderr


def test_schedule_run_once_with_invalid_config_exits_3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_home = tmp_path / "config"
    config_home.mkdir()
    (config_home / "schedule.yaml").write_text(
        "jobs:\n  - id: Bad Id\n", encoding="utf-8"
    )
    monkeypatch.setenv("LDST_CONFIG_DIR", str(config_home))

    result = runner.invoke(app, ["schedule", "run", "--once", "--output", "json"])

    assert result.exit_code == 3
    assert "schedule.yaml" in result.stderr


def test_schedule_run_once_without_enabled_jobs_is_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_home = tmp_path / "config"
    config_home.mkdir()
    (config_home / "schedule.yaml").write_text(
        "scheduler:\n"
        "  timezone: Asia/Shanghai\n"
        "  misfire_grace_seconds: 60\n"
        "jobs:\n"
        "  - id: premarket_reminder\n"
        "    name: 09:05 盘前提醒\n"
        "    kind: notify\n"
        "    trigger: '09:05'\n"
        "    enabled: false\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LDST_CONFIG_DIR", str(config_home))

    result = runner.invoke(app, ["schedule", "run", "--once", "--output", "json"])

    assert result.exit_code == 0
    payload = _json(result)
    assert payload["results"] == []
    assert "没有启用的任务" in payload["detail"]
