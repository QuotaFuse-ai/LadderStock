"""六段研究管线（R1-1 骨架）：顺序执行、断点续跑、单段失败隔离。

全部用脚本化假 ChatClient，不拨网络；`root` 指向 tmp_path，产物不落进仓库 `data/`。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ladderstock.ai import fallback
from ladderstock.research import artifacts, checkpoint
from ladderstock.research.pipeline import ResearchPipeline

DAY = "2026-10-27"
URL_ENV = "TEST_RESEARCH_BASE_URL"
KEY_ENV = "TEST_RESEARCH_API_KEY"


class FakeClient:
    """按「段落标识」行返回叙述；`fail_on` 里的段抛网络错误。"""

    def __init__(self, *, fail_on: tuple[str, ...] = (), text: str | None = None) -> None:
        self.calls: list[str] = []
        self.fail_on = fail_on
        self.text = text

    def complete(self, model: str, system: str, user: str, *, json_mode: bool) -> str:
        stage = next(
            line.split("：", 1)[1] for line in user.splitlines() if line.startswith("段落标识：")
        )
        self.calls.append(stage)
        if stage in self.fail_on:
            raise RuntimeError("connection reset by peer")
        return self.text or f"{checkpoint.STAGE_TITLES[stage]}段：假模型叙述。"

    @property
    def models(self) -> list[str]:
        return ["fake-model"]


@pytest.fixture(autouse=True)
def _configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(URL_ENV, "http://127.0.0.1:1/v1")
    monkeypatch.setenv(KEY_ENV, "test-key-not-real")


def make_config(**overrides: Any) -> fallback.AiConfig:
    base: dict[str, Any] = {
        "default_model": "fake-model",
        "fallback_chain": ("fake-backup",),
        "base_url_env": URL_ENV,
        "api_key_env": KEY_ENV,
        "structured_output": "prefer",
        "on_all_failed": "degrade_to_text",
        "sections": {},
    }
    base.update(overrides)
    return fallback.AiConfig(**base)


def pipeline(
    tmp_path: Path, client: FakeClient | None = None, **config_overrides: Any
) -> ResearchPipeline:
    return ResearchPipeline(
        "600000",
        config=make_config(**config_overrides),
        client=client or FakeClient(),
        root=tmp_path,
    )


# --------------------------------------------------------------------------- #
# 顺序执行 + 每段落盘
# --------------------------------------------------------------------------- #


def test_six_stages_run_in_order_and_land_artifacts(tmp_path: Path) -> None:
    client = FakeClient()
    run = pipeline(tmp_path, client).run(DAY)

    assert client.calls == list(checkpoint.STAGES)
    assert run.exit_code == 0
    assert run.failed == []
    assert [item["name"] for item in run.stages] == list(checkpoint.STAGES)
    assert all(item["status"] == "ok" for item in run.stages)

    directory = artifacts.artifact_dir("600000", DAY, tmp_path)
    for stage in checkpoint.STAGES:
        assert (directory / f"{stage}.json").is_file(), stage
        assert (directory / f"{stage}.md").is_file(), stage
    # checkpoint 落在 state/research_checkpoint/<code>/<date>.json
    assert Path(run.checkpoint_path) == tmp_path / "state" / "research_checkpoint" / "600000" / (
        f"{DAY}.json"
    )


def test_stage_artifact_structure(tmp_path: Path) -> None:
    pipeline(tmp_path).run(DAY)
    document = artifacts.read_stage("600000", DAY, "company", tmp_path)
    assert document is not None
    assert set(document) == {
        "code",
        "date",
        "stage",
        "title",
        "status",
        "mode",
        "model",
        "degraded",
        "narrative",
        "notes",
        "facts",
        "ai",
        "provenance",
        "failure",
        "attempts",
    }
    assert document["code"] == "600000"
    assert document["stage"] == "company"
    assert document["narrative"] and document["ai"]["narrative"] == document["narrative"]
    # R3 的字段位：没有 sources 就一律未核实
    assert document["provenance"] == {"values": {}, "sources": [], "unverified": True}

    markdown = (
        artifacts.stage_paths("600000", DAY, "company", tmp_path)[1]
        .read_text(encoding="utf-8")
        .strip()
    )
    assert markdown.startswith("# 公司画像")
    assert "## 叙述" in markdown


# --------------------------------------------------------------------------- #
# 断点续跑 / 单段重跑
# --------------------------------------------------------------------------- #


def test_interrupted_run_only_fills_missing_stages(tmp_path: Path) -> None:
    broken = FakeClient(fail_on=("valuation",))
    first = pipeline(tmp_path, broken).run(DAY)
    assert first.exit_code == 9
    assert first.failed == ["valuation", "report"]
    assert broken.calls == ["company", "financial", "forecast", "valuation", "risk"]

    resume = FakeClient()
    second = pipeline(tmp_path, resume).run(DAY)
    # 只补真正缺的两段，前序成果不重跑
    assert resume.calls == ["valuation", "report"]
    assert second.generated == ["valuation", "report"]
    assert second.reused == ["company", "financial", "forecast", "risk"]
    assert second.exit_code == 0


def test_only_runs_single_stage_and_keeps_others(tmp_path: Path) -> None:
    run = pipeline(tmp_path).run(DAY)
    assert run.generated == list(checkpoint.STAGES)

    single = FakeClient()
    again = pipeline(tmp_path, single).run(DAY, only="risk")
    assert again.generated == []  # risk 已完成 → 与 `ldst ai retry --section` 同语义

    forced = FakeClient()
    third = pipeline(tmp_path, forced).run(DAY, only="risk", force=True)
    assert forced.calls == ["risk"]
    assert third.generated == ["risk"]
    assert [item["status"] for item in third.stages] == ["ok"] * 6


def test_unknown_only_raises_with_stage_list(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="未知阶段"):
        pipeline(tmp_path).run(DAY, only="nope")


def test_retry_failed_only_reruns_failed_stages(tmp_path: Path) -> None:
    broken = FakeClient(fail_on=("financial",))
    pipeline(tmp_path, broken).run(DAY)

    client = FakeClient()
    run = pipeline(tmp_path, client).run(DAY, retry_failed=True)
    # financial 失败 → report 缺输入也失败，两者都要补
    assert client.calls == ["financial", "report"]
    assert run.exit_code == 0


# --------------------------------------------------------------------------- #
# 失败隔离：不吞前序成果
# --------------------------------------------------------------------------- #


def test_stage_failure_keeps_prior_artifacts_and_marks_exit_9(tmp_path: Path) -> None:
    client = FakeClient(fail_on=("forecast",))
    run = pipeline(tmp_path, client).run(DAY)

    assert run.exit_code == 9
    assert "forecast" in run.failed
    directory = artifacts.artifact_dir("600000", DAY, tmp_path)
    # 前序产物还在，失败段没有半截产物
    for stage in ("company", "financial"):
        assert (directory / f"{stage}.json").is_file(), stage
    assert not (directory / "forecast.json").exists()
    assert run.research_failures[0]["stage"] == "forecast"
    assert run.research_failures[0]["kind"] == "network"


def test_all_stages_failed_keeps_checkpoint_readable(tmp_path: Path) -> None:
    client = FakeClient(fail_on=checkpoint.STAGES)
    run = pipeline(tmp_path, client).run(DAY)
    assert run.exit_code == 9
    assert sorted(run.failed) == sorted(checkpoint.STAGES)
    document = checkpoint.load("600000", DAY, tmp_path)
    assert checkpoint.done_stages(document) == []
    assert checkpoint.failed_stages(document) == list(checkpoint.STAGES)


# --------------------------------------------------------------------------- #
# 报告段只消费前五段
# --------------------------------------------------------------------------- #


def test_report_consumes_only_prior_stage_artifacts(tmp_path: Path) -> None:
    pipeline(tmp_path).run(DAY)
    document = artifacts.read_stage("600000", DAY, "report", tmp_path)
    assert document is not None
    assert sorted(document["facts"]["upstream"]) == sorted(checkpoint.REPORT_INPUTS)
    assert document["facts"]["upstream"]["company"]["narrative"]


def test_report_fails_without_inputs_and_says_how_to_fix(tmp_path: Path) -> None:
    run = pipeline(tmp_path).run(DAY, only="report")
    assert run.exit_code == 9
    assert run.failed == ["report"]
    failure = run.research_failures[0]
    assert "前五段产物" in failure["message"]
    assert "--only company" in failure["action"]
    # 前序一段都没有 → 报告段无产物
    assert not (artifacts.artifact_dir("600000", DAY, tmp_path) / "report.json").exists()


# --------------------------------------------------------------------------- #
# 降级与边界
# --------------------------------------------------------------------------- #


def test_unconfigured_client_lands_placeholder_and_degrades(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(KEY_ENV, raising=False)
    client = FakeClient()
    run = pipeline(tmp_path, client).run(DAY)

    assert client.calls == []  # 未配置凭据：一个请求都不发
    assert run.configured is False
    assert run.degraded is True
    assert run.exit_code == 9
    document = artifacts.read_stage("600000", DAY, "valuation", tmp_path)
    assert document is not None
    assert document["mode"] == "placeholder"
    assert document["degraded"] is True
    assert "骨架占位" in document["narrative"][0]


def test_metric_key_in_facts_is_rejected_by_boundary_gate(tmp_path: Path) -> None:
    """facts 里出现叙述键 → 边界闸门判该段失败，不影响其他段。"""
    run = pipeline(tmp_path).run(DAY, facts={"risk": {"narrative": "程序也算叙述？"}})
    assert "risk" in run.failed
    directory = artifacts.artifact_dir("600000", DAY, tmp_path)
    assert (directory / "company.json").is_file()


def test_two_codes_do_not_share_state(tmp_path: Path) -> None:
    pipeline(tmp_path).run(DAY)
    other = ResearchPipeline(
        "000001", config=make_config(), client=FakeClient(), root=tmp_path
    ).run(DAY)
    assert other.generated == list(checkpoint.STAGES)
    assert checkpoint.load("000001", DAY, tmp_path)["code"] == "000001"
    assert checkpoint.failed_stages(checkpoint.load("600000", DAY, tmp_path)) == []


def test_run_result_dict_is_json_serialisable(tmp_path: Path) -> None:
    run = pipeline(tmp_path).run(DAY)
    payload = json.loads(json.dumps(run.as_dict(), ensure_ascii=False, default=str))
    assert payload["code"] == "600000"
    assert payload["stage_order"] == list(checkpoint.STAGES)
    assert payload["artifacts"]["company"]["exists"] is True
