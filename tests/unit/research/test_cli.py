"""`ldst research <code>` 的 CLI 契约（R1-1）。

全部走「未配置模型凭据」的离线占位路径：不拨网络、不依赖真实模型。
这里钉住三件对下游有契约意义的事：

1. `--output json` 的键集合（R1-2 / R1-3 / R1-8 只消费这份载荷）；
2. 退出码口径：降级 9、用法错误 2、缺配置 3；
3. `--only` / `--retry-failed` 与五段复盘编排一致的选择语义——**续跑只补缺失段**。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from ladderstock.cli.main import app
from ladderstock.research import checkpoint

runner = CliRunner()

CODE = "600000"
DAY = "2026-10-27"
URL_ENV = "LDST_LLM_BASE_URL"
KEY_ENV = "LDST_LLM_API_KEY"

#: `--output json` 的稳定键集合（`ResearchRunResult.as_dict()`）
JSON_KEYS = {
    "code",
    "date",
    "stage_order",
    "configured",
    "models",
    "degraded",
    "failed",
    "generated",
    "reused",
    "stages",
    "research_failures",
    "checkpoint",
    "artifacts",
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


def _run(args: list[str]) -> Any:
    return runner.invoke(app, args)


def _payload(result: Any) -> dict[str, Any]:
    assert result.exit_code in (0, 9), f"{result.exit_code}\n{result.stderr}"
    return json.loads(result.stdout)


# --------------------------------------------------------------------------- #
# 主路径：六段顺序跑通 + 产物落盘
# --------------------------------------------------------------------------- #

def test_first_run_walks_six_stages_and_lands_artifacts(data_dir: Path) -> None:
    result = _run(["research", CODE, "--date", DAY, "--output", "json"])
    payload = _payload(result)

    assert result.exit_code == 9, "未配置凭据按占位降级 -> 9（EXIT_AI_DEGRADED）"
    assert set(payload) == JSON_KEYS
    assert payload["stage_order"] == list(checkpoint.STAGES)
    assert payload["configured"] is False
    assert payload["code"] == CODE and payload["date"] == DAY
    assert [item["name"] for item in payload["stages"]] == list(checkpoint.STAGES)
    assert payload["failed"] == []
    assert payload["degraded"] is True
    assert payload["generated"] == list(checkpoint.STAGES)

    # 产物落盘：六段各一对 json/md，路径来自 payload（不在测试里重算规则）
    for stage in checkpoint.STAGES:
        paths = payload["artifacts"][stage]
        assert Path(paths["json"]).is_file() and Path(paths["md"]).is_file()
        assert json.loads(Path(paths["json"]).read_text(encoding="utf-8"))["stage"] == stage
    assert Path(payload["checkpoint"]).is_file()
    assert Path(payload["checkpoint"]).parent == data_dir / "state" / "research_checkpoint" / CODE


def test_date_is_always_normalized_to_iso(data_dir: Path) -> None:
    """`20261027` 这种紧凑写法 `fromisoformat` 也收，但目录名与 `date` 值只有一种写法。"""
    payload = _payload(_run(["research", CODE, "--date", "20261027", "--output", "json"]))
    assert payload["date"] == "2026-10-27"
    assert (data_dir / "research" / CODE / "2026-10-27" / "company.json").is_file()


# --------------------------------------------------------------------------- #
# 断点续跑：只补缺失段
# --------------------------------------------------------------------------- #

def test_resume_only_fills_missing_stages(data_dir: Path) -> None:
    first = _payload(
        _run(["research", CODE, "--date", DAY, "--only", "company", "--output", "json"])
    )
    assert first["generated"] == ["company"]

    second = _payload(_run(["research", CODE, "--date", DAY, "--output", "json"]))
    assert second["generated"] == list(checkpoint.STAGES[1:]), "已完成段不重跑"
    assert second["reused"] == ["company"]

    # 全段完成后第三次运行：一段都不重跑，续跑是幂等的
    third = _payload(_run(["research", CODE, "--date", DAY, "--output", "json"]))
    assert third["generated"] == []
    assert third["reused"] == list(checkpoint.STAGES)


def test_retry_failed_reruns_only_failed_stages(data_dir: Path) -> None:
    """失败段隔离后，`--retry-failed` 只补失败/未跑的段（与 `ai retry` 同语义）。"""
    _run(["research", CODE, "--date", DAY, "--only", "company", "--output", "json"])

    # 人为把 financial 标成 failed（模拟网络/额度失败后中断），走同一套状态机写入
    document = checkpoint.load(CODE, DAY)
    checkpoint.record(
        document,
        "financial",
        status="failed",
        mode="failed",
        narrative=[],
        failure={
            "kind": "network",
            "message": "连接被重置",
            "action": "稍后重试",
            "retryable": True,
        },
    )
    checkpoint.save(document)

    payload = _payload(
        _run(["research", CODE, "--date", DAY, "--retry-failed", "--output", "json"])
    )
    assert payload["generated"] == ["financial", *checkpoint.STAGES[2:]], (
        "--retry-failed 只补「未完成」的段：失败的 financial 与还没跑过的后四段"
    )
    assert payload["reused"] == ["company"], "已完成段绝不重跑"
    assert payload["failed"] == [], "重跑后 financial 变成就绪"



# --------------------------------------------------------------------------- #
# 用法与配置错误
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "args",
    [
        ["research", "60000"],  # 代码不是 6 位
        ["research", "../etc/passwd"],  # 路径穿越
        ["research", CODE, "--date", "2026-13-45"],  # 日期不存在
        ["research", CODE, "--only", "nope"],  # 未知阶段
    ],
    ids=["bad-code", "path-traversal", "bad-date", "unknown-stage"],
)
def test_usage_errors_exit_2(data_dir: Path, args: list[str]) -> None:
    result = _run([*args, "--output", "json"])
    assert result.exit_code == 2, f"{result.exit_code}\n{result.stdout}\n{result.stderr}"
    assert result.stderr.strip(), "用法错误要在 stderr 说清原因"
    assert not result.stdout.strip(), "stdout 只放结构化结果"


def test_missing_ai_config_exits_3(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LDST_CONFIG_DIR", str(tmp_path / "empty-config"))
    (tmp_path / "empty-config").mkdir()
    result = _run(["research", CODE, "--date", DAY, "--output", "json"])
    assert result.exit_code == 3, result.stdout
    assert "ai.yaml" in result.stderr


# --------------------------------------------------------------------------- #
# 全局 --output 三档（docs/CLI.md §F）
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("fmt", ["json", "table", "csv"])
def test_every_output_format_emits_a_result(data_dir: Path, fmt: str) -> None:
    result = _run(["research", CODE, "--date", DAY, "--output", fmt])
    assert "No such option" not in result.stderr
    assert result.exit_code == 9
    assert result.stdout.strip(), f"--output {fmt} 没有任何输出"
    if fmt == "json":
        assert set(_payload(result)) == JSON_KEYS
