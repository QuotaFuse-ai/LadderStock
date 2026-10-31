"""`ldst news` / `ldst events` 的 CLI 表面：退出码、JSON 契约、开关默认值。

数据层用真实 `config/sources.yaml` + `config/sync.yaml`（源链、降级顺序、冷却都是真的），
只把传输层换成离线桩——`build_transport` 就是这个项目留出来的官方测试缝。
数据目录钉在 tmp：这两条命令都会写 `data/news.sqlite`，不能落在仓库里。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fixtures.fake_peers import FakeTransport
from fixtures.news_samples import WINDOW_DAY, news_handlers, news_tools
from typer.testing import CliRunner

from ladderstock.cli.main import app
from ladderstock.core.privacy import private_keys_present
from ladderstock.errors import EXIT_AI_DEGRADED, EXIT_OK, EXIT_PARTIAL, EXIT_SOURCE

runner = CliRunner()

DATE_ARGS = [WINDOW_DAY]
ALL_TOOLS = tuple(news_tools())


@pytest.fixture(autouse=True)
def _offline_news_layer(repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    state: dict[str, object] = {"failing": ()}

    def _build(_peers):
        return FakeTransport(
            handlers=news_handlers(failing=tuple(state["failing"])), tools=list(ALL_TOOLS)
        )

    monkeypatch.setattr("ladderstock.data.provider_registry.build_transport", _build)

    def set_failing(*failing: str) -> None:
        state["failing"] = failing

    yield set_failing


def _json(result) -> dict:
    assert result.exit_code in {EXIT_OK, EXIT_PARTIAL, EXIT_SOURCE, EXIT_AI_DEGRADED}, (
        f"stdout={result.stdout}\nstderr={result.stderr}"
    )
    return json.loads(result.stdout)


# --------------------------------------------------------------------------- #
# `ldst news`
# --------------------------------------------------------------------------- #

def test_news_default_writes_and_reports_zero(repo) -> None:
    result = runner.invoke(app, ["news", *DATE_ARGS, "--code", "600519", "--output", "json"])
    payload = _json(result)
    assert result.exit_code == EXIT_OK
    assert payload["domain"] == "news" and payload["topic"] == "news_stream"
    assert payload["summary"]["persisted"] is True
    assert payload["items"], "样本链路应该出条目"
    assert (Path(repo) / "..").exists()  # repo fixture 生效，配置文件被找到


def test_news_writes_only_to_the_configured_data_dir(repo, tmp_path: Path) -> None:
    runner.invoke(app, ["news", *DATE_ARGS, "--code", "600519", "--output", "json"])
    assert (tmp_path / "news.sqlite").is_file(), "默认写本机数据目录"
    assert not (Path(repo).parent / "data" / "news.sqlite").exists()


def test_news_no_store_does_not_touch_the_db(repo, tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["news", *DATE_ARGS, "--code", "600519", "--no-store", "--output", "json"],
    )
    payload = _json(result)
    assert payload["summary"]["persisted"] is False
    assert not (tmp_path / "news.sqlite").exists()


def test_news_is_idempotent_at_the_command_level(repo, tmp_path: Path) -> None:
    """连跑两次同一命令：id 集合与库里行数都不变（验收项 1 的用户视角）。"""
    argv = ["news", "2026-10-29", "--until", "2026-10-31", "--code", "600519", "--output", "json"]
    first = _json(runner.invoke(app, argv))
    second = _json(runner.invoke(app, argv))
    assert [item["id"] for item in first["items"]] == [item["id"] for item in second["items"]]
    assert second["summary"]["new_items"] == 0
    assert second["summary"]["stored"] == first["summary"]["stored"]


def test_news_until_makes_a_window(repo) -> None:
    result = runner.invoke(
        app, ["news", "2026-10-29", "--until", "2026-10-31", "--code", "600519", "--output", "json"]
    )
    payload = _json(result)
    assert payload["window"] == {"start": "2026-10-29", "end": "2026-10-31"}


def test_news_source_filter_only_fetches_the_named_columns(repo) -> None:
    result = runner.invoke(
        app, ["news", *DATE_ARGS, "--source", "flash_cls", "--output", "json"]
    )
    payload = _json(result)
    assert payload["sources"] == ["flash_cls"]
    assert [row["dataset"] for row in payload["sources_status"]] == ["flash_cls"]


def test_news_rejects_unknown_source(repo) -> None:
    result = runner.invoke(app, ["news", *DATE_ARGS, "--source", "weibo_hot"])
    assert result.exit_code == 2
    assert "未知资讯栏目" in result.stderr


def test_news_rejects_loose_dates(repo) -> None:
    for bad in ("20261030", "2026-10-30 ", "yesterday", "2026-13-01"):
        result = runner.invoke(app, ["news", bad])
        assert result.exit_code == 2, bad
        assert "YYYY-MM-DD" in result.stderr


def test_news_rejects_oversized_window(repo) -> None:
    result = runner.invoke(app, ["news", "2026-08-01", "--until", WINDOW_DAY])
    assert result.exit_code == 2
    assert "31" in result.stderr


def test_news_without_code_marks_the_three_columns_skipped(repo) -> None:
    result = runner.invoke(app, ["news", *DATE_ARGS, "--output", "json"])
    payload = _json(result)
    skipped = [row["dataset"] for row in payload["sources_status"] if row["status"] == "skipped"]
    assert skipped == ["announcement_cninfo", "announcement_szse", "news_eastmoney_stock"]
    assert result.exit_code == EXIT_OK, "用法未满足不该伪装成源故障"


def test_news_all_sources_down_is_exit_4(repo, _offline_news_layer) -> None:
    _offline_news_layer(*ALL_TOOLS)
    result = runner.invoke(app, ["news", *DATE_ARGS, "--code", "600519", "--output", "json"])
    assert result.exit_code == EXIT_SOURCE
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert {row["status"] for row in payload["sources_status"]} == {"failed"}


def test_news_partial_failure_is_exit_8(repo, _offline_news_layer) -> None:
    _offline_news_layer("cls_telegraph", "wallstreetcn_lives", "eastmoney_global_news")
    result = runner.invoke(app, ["news", *DATE_ARGS, "--code", "600519", "--output", "json"])
    assert result.exit_code == EXIT_PARTIAL
    payload = json.loads(result.stdout)
    assert payload["items"], "部分失败时其余栏目照常出数据"


def test_news_humans_get_a_table_and_json_gets_the_contract(repo) -> None:
    text = runner.invoke(app, ["news", *DATE_ARGS, "--code", "600519"])
    assert text.exit_code == EXIT_OK
    assert WINDOW_DAY in text.stdout
    payload = _json(runner.invoke(app, [
        "news",
        *DATE_ARGS,
        "--code",
        "600519",
        "--output",
        "json",
    ]))
    assert set(payload) >= {
        "ok",
        "window",
        "sources",
        "items",
        "sources_status",
        "extraction",
        "ai",
    }


# --------------------------------------------------------------------------- #
# 模型开关（默认关闭）
# --------------------------------------------------------------------------- #

def test_news_ai_is_off_by_default(repo, monkeypatch: pytest.MonkeyPatch) -> None:
    """不加 `--ai` 时，模型这条路的账本必须写着「没请求」，且真的没构造客户端。"""

    def explode(*_args, **_kwargs):
        raise AssertionError("默认路径不该碰模型")

    monkeypatch.setattr("ladderstock.news.ai_enrich._default_chat", explode)
    result = runner.invoke(app, ["news", *DATE_ARGS, "--code", "600519", "--output", "json"])
    payload = _json(result)
    assert payload["ai"] == {
        "requested": False,
        "enabled": False,
        "skipped_reason": "默认关闭：需要模型叙述请加 --ai / ai=true",
    }


def test_news_ai_requested_without_config_degrades_to_exit_9(
    repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--ai` 开了但模型没配：事件一条不丢，退出码 9 说明是叙述失败而非取数失败。"""
    from ladderstock.ai import fallback

    class _Cfg:
        pass

    monkeypatch.setattr(fallback, "load_config", lambda *a, **k: _Cfg())
    monkeypatch.setattr(fallback, "is_configured", lambda _c: False)
    monkeypatch.setattr(fallback, "missing_env", lambda _c: ["LDST_TEST_KEY"])

    result = runner.invoke(
        app, ["news", *DATE_ARGS, "--code", "600519", "--no-store", "--ai", "--output", "json"]
    )
    assert result.exit_code == EXIT_AI_DEGRADED, result.stdout
    payload = json.loads(result.stdout)
    assert payload["ai"]["requested"] is True
    assert payload["ai"]["model"] == ""
    assert "模型不可用" in payload["ai"]["skipped_reason"]


# --------------------------------------------------------------------------- #
# `ldst events`
# --------------------------------------------------------------------------- #

def test_events_requires_a_month(repo) -> None:
    result = runner.invoke(app, ["events"])
    assert result.exit_code == 2
    # rich 会把选项名拆成两个着色片段（`-` + `-month`），先去掉 ANSI 再断言。
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.stderr)
    assert "--month" in " ".join(plain.split())


def test_events_validates_the_month_format(repo) -> None:
    for bad in ("2026", "202611", "2026-13"):
        result = runner.invoke(app, ["events", "--month", bad])
        assert result.exit_code == 2, bad


def test_events_declares_the_three_gaps(repo) -> None:
    result = runner.invoke(
        app, ["events", "--month", "2026-11", "--code", "600519", "--output", "json"]
    )
    payload = _json(result)
    assert payload["topic"] == "event_calendar"
    assert payload["summary"]["no_source"] == [
        "earnings_disclosure",
        "shareholder_meeting",
        "policy_window",
    ]
    for section in payload["sections"]:
        if section["status"] == "no_source":
            assert section["entries"] == []
            assert section["reason"] and section["ask"]
    assert "不估算、不编日期" in payload["notice"]


def test_events_lockup_needs_a_code(repo) -> None:
    result = runner.invoke(app, ["events", "--month", "2026-11", "--output", "json"])
    payload = _json(result)
    lockup = next(s for s in payload["sections"] if s["key"] == "lockup_expiry")
    assert lockup["status"] == "no_source"
    assert "code" in lockup["reason"]
    assert result.exit_code == EXIT_OK, "三个真缺口 + 一个用法未满足，不算源故障"


def test_events_fetches_the_sourced_sections(repo) -> None:
    result = runner.invoke(
        app, ["events", "--month", "2026-11", "--code", "600519", "--output", "json"]
    )
    payload = _json(result)
    lockup = next(s for s in payload["sections"] if s["key"] == "lockup_expiry")
    assert [entry["date"] for entry in lockup["entries"]] == ["2026-11-16"]
    assert lockup["data_source"] == "lockup_calendar"


def test_events_no_fetch_does_not_call_the_peer(repo) -> None:
    result = runner.invoke(
        app, ["events", "--month", "2026-11", "--code", "600519", "--no-fetch", "--output", "json"]
    )
    payload = _json(result)
    assert result.exit_code == EXIT_OK
    assert all(not section["entries"] for section in payload["sections"])


def test_events_all_sources_down_is_exit_4(repo, _offline_news_layer) -> None:
    _offline_news_layer("lockup_expiry", "earnings_forecast")
    result = runner.invoke(
        app, ["events", "--month", "2026-11", "--code", "600519", "--output", "json"]
    )
    assert result.exit_code == EXIT_SOURCE


def test_events_code_list_is_normalised(repo) -> None:
    result = runner.invoke(
        app, ["events", "--month", "2026-10", "--code", "600519.SH,000001", "--output", "json"]
    )
    assert _json(result)["codes"] == ["600519", "000001"]


# --------------------------------------------------------------------------- #
# 隐私：两个命令的出口都不带隐私面
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "argv",
    [
        ["news", WINDOW_DAY, "--code", "600519", "--output", "json"],
        ["events", "--month", "2026-11", "--code", "600519", "--output", "json"],
    ],
    ids=["news", "events"],
)
def test_command_output_carries_no_private_keys(repo, argv) -> None:
    result = runner.invoke(app, argv)
    payload = _json(result)
    assert private_keys_present(payload) == []
    dumped = json.dumps(payload, ensure_ascii=False)
    for banned in ("持仓", "自选", "交易日志", "watchlist", "positions"):
        assert banned not in dumped, banned


def test_commands_are_listed_in_help(repo) -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "news" in result.stdout and "events" in result.stdout
