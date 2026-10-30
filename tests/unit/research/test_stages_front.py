"""前三段（R1-2）：提示词 + 三级降级阶梯 + 失败隔离 + 单段重跑。

全部用脚本化假 ChatClient，不拨网络；`root` 指向 tmp_path 子目录，产物不落进仓库 `data/`。
用例名与 QUOT-191 验收条款一一对应（`-k fallback` / `-k failure_isolation` 各命中一条）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ladderstock.ai import fallback
from ladderstock.research import artifacts, checkpoint, stages
from ladderstock.research.pipeline import ResearchPipeline

DAY = "2026-10-27"
URL_ENV = "TEST_FRONT_BASE_URL"
KEY_ENV = "TEST_FRONT_API_KEY"
FRONT = ("company", "financial", "forecast")

#: 三级阶梯每级的假返回：一级给合法 JSON，二级给截断的 JSON，三级给纯文本
GOOD_JSON = {
    "company": json.dumps(
        {
            "narrative": ["公司画像：主营业务为银行，快照未提供分部收入。"],
            "highlights": ["主业单一清晰"],
            "risks": ["快照缺少股东与质押数据"],
        },
        ensure_ascii=False,
    ),
    "financial": json.dumps(
        {"narrative": ["财务：利息收入占比较高。"], "evidence": ["快照未提供同比口径"]}
    ),
    "forecast": json.dumps(
        {"narrative": ["盈利预期：驱动变量为产能投放节奏，具体数字不在本段给出。"]}
    ),
}

#: 合法 JSON 被截断：一级解析失败，二级能从同一段文本里抢救出叙述
TRUNCATED_JSON = (
    '{"narrative": [\n"财务：利息收入占比最高，快照未提供毛利率。",\n'
    '"成长性：快照未提供同比口径，无法判断方向。"'
)

#: 抢救不动的返回（空文本），只能落到第三级纯文本
UNSALVAGEABLE = ""

PLAIN_TEXT = {
    "company": "公司画像：纯文本降级叙述，快照信息不足，只写主营业务的定性判断。",
    "financial": "财务：纯文本降级叙述，利息收入占比最高。",
    "forecast": "盈利预期：纯文本降级叙述，驱动变量为产能投放节奏。",
}


class ScriptedClient:
    """按 `json_mode` 分级返回脚本文本；`fail_on` 里的段抛网络错误。"""

    def __init__(
        self,
        *,
        structured: dict[str, str] | str | None = None,
        plain: dict[str, str] | str | None = None,
        fail_on: tuple[str, ...] = (),
    ) -> None:
        self.calls: list[tuple[str, bool]] = []
        self.fail_on = fail_on
        self._structured = structured if structured is not None else GOOD_JSON
        self._plain = plain if plain is not None else PLAIN_TEXT

    def complete(self, model: str, system: str, user: str, *, json_mode: bool) -> str:
        stage = next(
            line.split("：", 1)[1] for line in user.splitlines() if line.startswith("段落标识：")
        )
        self.calls.append((stage, json_mode))
        if stage in self.fail_on:
            raise RuntimeError("connection reset by peer")
        script = self._structured if json_mode else self._plain
        if isinstance(script, dict):
            return script.get(stage, PLAIN_TEXT.get(stage, "假模型叙述内容足够长可以抢救。"))
        return script

    def stages_called(self, stage: str) -> list[bool]:
        return [json_mode for name, json_mode in self.calls if name == stage]


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
    root: Path, client: ScriptedClient | None = None, **config_overrides: Any
) -> ResearchPipeline:
    return ResearchPipeline(
        "600000",
        config=make_config(**config_overrides),
        client=client or ScriptedClient(),
        root=root,
    )


def state_of(run: Any, stage: str) -> dict[str, Any]:
    return next(item for item in run.stages if item["name"] == stage)


# --------------------------------------------------------------------------- #
# 验收 3：三级降级阶梯
# --------------------------------------------------------------------------- #


def test_three_level_fallback(tmp_path: Path) -> None:
    """同一段（company）依次走「结构化 → 文本补偿 → 纯文本」三级，mode 两两可区分。"""
    levels = {
        "structured": ScriptedClient(structured=GOOD_JSON),
        "parsed": ScriptedClient(structured=TRUNCATED_JSON),
        "text": ScriptedClient(structured=UNSALVAGEABLE),
    }
    runs: dict[str, Any] = {}
    documents: dict[str, dict[str, Any]] = {}
    for index, (expected_mode, client) in enumerate(levels.items()):
        root = tmp_path / f"level-{index}"
        run = pipeline(root, client).run(DAY, only="company")
        document = artifacts.read_stage("600000", DAY, "company", root)
        assert document is not None, expected_mode
        runs[expected_mode] = run
        documents[expected_mode] = document
        # 任何一级成功都不许把该段标 failed
        assert state_of(run, "company")["status"] != "failed", expected_mode
        assert run.failed == [], expected_mode

    modes = [documents[mode]["mode"] for mode in ("structured", "parsed", "text")]
    assert modes == ["structured", "parsed", "text"]
    assert len(set(modes)) == 3

    assert documents["structured"]["degraded"] is False
    assert documents["structured"]["status"] == "ok"
    assert documents["structured"]["ai"]["highlights"] == ["主业单一清晰"]
    assert runs["structured"].exit_code == 0

    # 第二级：一级解析失败后从同一份返回文本里抢救，仍算成功产出
    assert documents["parsed"]["status"] == "ok"
    assert documents["parsed"]["degraded"] is False
    assert any("利息收入占比最高" in line for line in documents["parsed"]["narrative"])
    assert [item["mode"] for item in documents["parsed"]["attempts"]] == [
        "structured",
        "parsed",
    ]
    assert runs["parsed"].exit_code == 0

    # 第三级：纯文本，标 degraded，整次运行退出码 9
    assert documents["text"]["status"] == "degraded"
    assert documents["text"]["degraded"] is True
    assert documents["text"]["attempts"][-1]["mode"] == "text"
    assert documents["text"]["narrative"] == [PLAIN_TEXT["company"]]
    assert runs["text"].exit_code == 9
    assert state_of(runs["text"], "company")["degraded"] is True


# --------------------------------------------------------------------------- #
# 验收 4：失败隔离不吞前序成果
# --------------------------------------------------------------------------- #


def test_failure_isolation(tmp_path: Path) -> None:
    """`financial` 抛网络错误：只死它自己（和缺输入的报告段），前序产物还在，序列不中断。"""
    client = ScriptedClient(fail_on=("financial",))
    run = pipeline(tmp_path, client).run(DAY)
    directory = artifacts.artifact_dir("600000", DAY, tmp_path)

    assert run.exit_code == 9
    assert state_of(run, "financial")["status"] == "failed"
    assert run.failed == ["financial", "report"]
    assert not (directory / "financial.json").exists()
    assert not (directory / "financial.md").exists()
    # 前序成果一件不少
    assert (directory / "company.json").is_file()
    assert (directory / "company.md").is_file()
    # 单段失败不中断序列：forecast 照常执行并落盘
    assert ("forecast", True) in client.calls
    assert state_of(run, "forecast")["status"] == "ok"
    assert (directory / "forecast.json").is_file()

    failure = next(item for item in run.research_failures if item["stage"] == "financial")
    assert failure["kind"] == "network"
    # checkpoint 里也留了尝试轨迹（换模型 / 降级的痕迹）
    document = artifacts.read_stage("600000", DAY, "financial", tmp_path)
    assert document is None
    recorded = checkpoint.stage_state(checkpoint.load("600000", DAY, tmp_path), "financial")
    assert recorded["status"] == "failed"
    assert recorded["attempts"]


# --------------------------------------------------------------------------- #
# 验收 2：三段提示词与产物形状
# --------------------------------------------------------------------------- #


def test_front_stage_prompts_and_artifact_structure(tmp_path: Path) -> None:
    assert stages.FRONT_STAGES == FRONT
    root = tmp_path / "shape"
    client = ScriptedClient()
    run = pipeline(root, client).run(DAY, only="company")
    assert run.exit_code == 0

    prompt_dir = Path(__file__).resolve().parents[3] / "prompts" / "research"
    for stage in FRONT:
        assert (prompt_dir / f"{stage}.md").is_file(), stage
        text = stages.prompt_text(stage)
        assert checkpoint.STAGE_TITLES[stage] in text, stage
        assert len(text) > 50, stage

    # 提示词进了 system，段落标识进了 user
    system_hint = stages.build_messages("600000", "financial", DAY, {})
    assert "只输出一个 JSON 对象" in system_hint[0]
    assert "段落标识：financial" in system_hint[1]
    assert "主营" in system_hint[0]

    document = artifacts.read_stage("600000", DAY, "company", root)
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
    assert document["title"] == "公司画像"
    assert document["mode"] == "structured"
    assert document["ai"]["model"] == "fake-model"
    assert document["provenance"] == {"values": {}, "sources": [], "unverified": True}

    markdown = (artifacts.stage_paths("600000", DAY, "company", root)[1]).read_text(
        encoding="utf-8"
    )
    assert markdown.startswith("# 公司画像")
    assert "## 叙述" in markdown
    assert PLAIN_TEXT["company"] not in markdown  # 走的是结构化一级，不是纯文本


# --------------------------------------------------------------------------- #
# 验收 5：单段重跑只动那一段
# --------------------------------------------------------------------------- #


def test_only_force_rerun_touches_single_stage(tmp_path: Path) -> None:
    root = tmp_path / "rerun"
    pipeline(root).run(DAY)
    directory = artifacts.artifact_dir("600000", DAY, root)
    before = {
        stage: (
            (directory / f"{stage}.json").read_bytes(),
            (directory / f"{stage}.md").read_bytes(),
        )
        for stage in checkpoint.STAGES
    }

    client = ScriptedClient(
        structured={
            **GOOD_JSON,
            "financial": json.dumps({"narrative": ["财务：重跑后的新叙述内容。"]}),
        }
    )
    run = pipeline(root, client).run(DAY, only="financial", force=True)

    assert client.calls == [("financial", True)]
    assert run.generated == ["financial"]
    document = artifacts.read_stage("600000", DAY, "financial", root)
    assert document is not None
    assert document["narrative"] == ["财务：重跑后的新叙述内容。"]
    # 其他段的产物一件都没被改写
    for stage in checkpoint.STAGES:
        if stage == "financial":
            continue
        assert (
            directory / f"{stage}.json"
        ).read_bytes() == before[stage][0], stage
        assert (directory / f"{stage}.md").read_bytes() == before[stage][1], stage
    # 状态也不动
    for stage in ("company", "forecast", "valuation", "risk", "report"):
        assert state_of(run, stage)["status"] == "ok", stage


# --------------------------------------------------------------------------- #
# 边界闸门：越界只死一段
# --------------------------------------------------------------------------- #


def test_boundary_gate_fails_only_the_offending_stage(tmp_path: Path) -> None:
    """facts 里出现叙述键 → 闸门判 company 失败，financial / forecast 不受影响。"""
    root = tmp_path / "gate"
    run = pipeline(root).run(DAY, facts={"company": {"narrative": "程序也算叙述？"}})
    directory = artifacts.artifact_dir("600000", DAY, root)

    assert "company" in run.failed
    assert not (directory / "company.json").exists()
    for stage in ("financial", "forecast"):
        assert state_of(run, stage)["status"] == "ok", stage
        assert (directory / f"{stage}.json").is_file(), stage
    assert run.exit_code == 9
