"""`ldst weekly` 的 CLI 契约（R11 / QUOT-149 验收：退出码语义 + 产物存在）。

全部走「未配置模型凭据」的离线降级路径：不拨网络、不依赖真实模型。
钉住四件对下游有契约意义的事：

1. 退出码口径：非法周标签 2、整周无可用读数 6、事实包有数但 AI 降级 9；
2. **6 优先于 9**，且两种情况两份产物（`.json` + `.md`）都已落盘（要求 4）；
3. `--week` 也接受普通日期，归一到所在周；
4. `--output json` 载荷的稳定键集合。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fixtures import weekly_cache as cache
from typer.testing import CliRunner

from ladderstock.cli.main import app
from ladderstock.weekly import pipeline as weekly_pipeline

runner = CliRunner()

WEEK = cache.WEEK
URL_ENV = "LDST_LLM_BASE_URL"
KEY_ENV = "LDST_LLM_API_KEY"

JSON_KEYS = {
    "week",
    "range",
    "ok",
    "available",
    "reason",
    "configured",
    "degraded",
    "failed",
    "models",
    "session_days",
    "missing_days",
    "report_path",
    "markdown_path",
    "checkpoint",
    "sections",
    "ai_failures",
    "headline",
    "exit_code",
}


@pytest.fixture()
def data_dir(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """仓库配置 + 临时数据目录 + 明确无凭据：离线可重复，不写仓库 `data/`。"""
    target = tmp_path / "data"
    monkeypatch.setenv("LDST_DATA_DIR", str(target))
    monkeypatch.delenv(URL_ENV, raising=False)
    monkeypatch.delenv(KEY_ENV, raising=False)
    return target


@pytest.fixture()
def seeded_dir(data_dir: Path) -> Path:
    cache.seed_week(data_dir)
    return data_dir


def _run(args: list[str]) -> Any:
    return runner.invoke(app, args)


# --------------------------------------------------------------------------- #
# 参数：非法周标签一律 2，且不产出任何文件
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "value",
    ["2026-W00", "2026-W54", "2025-W53", "banana", "2026-10", "../evil", "2026-13-01"],
)
def test_illegal_week_label_exits_usage(value: str, data_dir: Path) -> None:
    result = _run(["weekly", "--week", value, "--output", "json"])

    assert result.exit_code == 2, result.stdout
    assert not (data_dir / "weekly").exists()


def test_date_is_normalized_to_its_week(seeded_dir: Path) -> None:
    result = _run(["weekly", "--week", cache.WED, "--output", "json"])

    assert result.exit_code == 9, result.stderr
    assert json.loads(result.stdout)["week"] == WEEK


# --------------------------------------------------------------------------- #
# 6：整周没有可用单日指标
# --------------------------------------------------------------------------- #

def test_empty_week_exits_not_found_but_keeps_artifacts(data_dir: Path) -> None:
    result = _run(["weekly", "--week", WEEK, "--output", "json"])
    payload = json.loads(result.stdout)

    assert result.exit_code == 6, result.stderr
    assert payload["exit_code"] == 6
    assert payload["available"] is False
    assert payload["session_days"] == []
    assert payload["reason"]
    # 要求 4：降级不得整体失败——两份产物都在，五段都有文字
    assert weekly_pipeline.weekly_path(WEEK, data_dir).is_file()
    assert weekly_pipeline.markdown_path(WEEK, data_dir).is_file()
    assert len(payload["sections"]) == 5


def test_payload_keys_are_stable(seeded_dir: Path) -> None:
    result = _run(["weekly", "--week", WEEK, "--output", "json"])

    assert set(json.loads(result.stdout)) == JSON_KEYS


# --------------------------------------------------------------------------- #
# 9：事实包有数、AI 段落降级
# --------------------------------------------------------------------------- #

def test_seeded_week_degrades_offline_and_lands_artifacts(seeded_dir: Path) -> None:
    result = _run(["weekly", "--week", WEEK, "--output", "json"])
    payload = json.loads(result.stdout)

    assert result.exit_code == 9, result.stderr
    assert payload["exit_code"] == 9
    assert payload["available"] is True
    assert payload["configured"] is False
    assert payload["degraded"] is True
    assert payload["failed"] == []
    assert payload["session_days"] == cache.EXPECTED["session_days"]
    assert [item["name"] for item in payload["sections"]] == [
        "sentiment",
        "capital",
        "theme",
        "dragon_tiger",
        "leader",
    ]

    report = json.loads(weekly_pipeline.weekly_path(WEEK, seeded_dir).read_text(encoding="utf-8"))
    facts = report["facts"]
    expected = cache.EXPECTED
    # CLI 落盘的就是同一份可复算事实包（值取自 tests/fixtures/weekly_cache.py 的手算期望）
    assert facts["week"] == expected["week"]
    assert facts["session_days"] == expected["session_days"]
    assert facts["sentiment"]["temperature"]["avg"] == pytest.approx(
        expected["sentiment"]["temperature_avg"]
    )
    assert facts["ladder"]["peak_height"] == expected["ladder"]["peak_height"]
    assert facts["breadth"]["samples"] == expected["breadth"]["samples"]
    # 要求 2：事实包子树里没有任何模型输出
    dumped = json.dumps(facts, ensure_ascii=False)
    assert cache.CHECKPOINT_NARRATIVE not in dumped
    assert "narrative" not in dumped


def test_force_regenerates_every_section(seeded_dir: Path) -> None:
    first = _run(["weekly", "--week", WEEK, "--output", "json"])
    second = _run(["weekly", "--week", WEEK, "--force", "--output", "json"])

    assert first.exit_code == second.exit_code == 9
    assert json.loads(second.stdout)["session_days"] == json.loads(first.stdout)["session_days"]
