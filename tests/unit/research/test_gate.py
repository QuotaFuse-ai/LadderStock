"""R1-4 门禁收口：断点续跑 / 单段重跑 / 连续失败退出码 9 / 产物结构 / JSON 契约 / 只读工具。

六个用例名与 QUOT-193 验收条款一一对应，一条一款，不合并：

1. `test_resume_after_interruption`  跑到第 3 段中断 → 重跑只补 3~6 段，前两段产物未被重写
2. `test_only_single_stage`           `--only risk` 只动 risk 段，其余状态与 mtime 不变
3. `test_all_failed_exit_9`           某段连续失败 → 该段 failed、其余不变、退出码 9
4. `test_artifact_shape`              json 键形状 / md 非空 / checkpoint 每段一条
5. `test_json_contract`               `--output json` 字段名与类型固定
6. `test_research_status_read_only`   `research_status` 读得对，且不产生任何写操作

全用脚本化假 ChatClient（不拨网络、不依赖真实模型），`root` 指向 `tmp_path`，产物不落仓库 `data/`。
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from ladderstock.ai import fallback
from ladderstock.cli.main import app
from ladderstock.mcp.server import dispatch
from ladderstock.mcp.tools import TOOL_SPECS
from ladderstock.research import artifacts, checkpoint
from ladderstock.research.pipeline import ResearchPipeline

runner = CliRunner()

CODE = "600000"
DAY = "2026-10-27"
STAGES = checkpoint.STAGES
#: 状态词表与五段复盘共用一套（`ai/checkpoint.STATUSES`），这里只按同一词表校验
STATUSES = ("pending", "ok", "degraded", "failed")
URL_ENV = "TEST_GATE_BASE_URL"
KEY_ENV = "TEST_GATE_API_KEY"

#: `--output json` 顶层契约：键名与类型都钉死（R3 / R8 只消费这一份载荷）
JSON_CONTRACT: dict[str, type] = {
    "code": str,
    "date": str,
    "stage_order": list,
    "configured": bool,
    "models": list,
    "degraded": bool,
    "failed": list,
    "generated": list,
    "reused": list,
    "stages": list,
    "research_failures": list,
    "checkpoint": str,
    "artifacts": dict,
    "exit_code": int,
}
#: `stages[]` 单条与 `artifacts.<stage>` 单条的键集合
STAGE_ENTRY_KEYS = {
    "name",
    "title",
    "status",
    "mode",
    "model",
    "degraded",
    "narrative",
    "notes",
    "facts",
}
ARTIFACT_ENTRY_KEYS = {"json": str, "md": str, "exists": bool}
FAILURE_ENTRY_KEYS = {"stage", "kind", "message", "action", "retryable"}

#: 阶段产物 `<stage>.json` 的顶层键（R1-1 定稿的 15 个键，不得增减）
STAGE_KEYS = {
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
#: checkpoint 里每条段状态的键（复用 `ai/checkpoint.record` 的写入形状）
STATE_KEYS = {
    "status",
    "mode",
    "model",
    "degraded",
    "narrative",
    "notes",
    "failure",
    "attempts",
    "completed_at",
}

#: 一级（结构化 JSON）各段脚本返回
def _scripted(stage: str) -> str:
    title = checkpoint.STAGE_TITLES[stage]
    return json.dumps(
        {
            "narrative": [f"{title}：假模型叙述，只引用给定快照，口径为静态快照。"],
            "highlights": [f"{title}段可复核的定性结论"],
        },
        ensure_ascii=False,
    )


SCRIPTED: dict[str, str] = {stage: _scripted(stage) for stage in STAGES}

#: 模型链：`fake-model` + `fake-backup`，连续失败要把两级都试完
MODELS = ["fake-model", "fake-backup"]


class Interrupted(BaseException):
    """模拟进程中断（不是 `Exception`，管线不许吞掉它）。"""


class FakeClient:
    """按段返回脚本文本；`fail_on` 里的段每次都抛网络错误，`interrupt_on` 的段直接中断。"""

    def __init__(self, *, fail_on: tuple[str, ...] = (), interrupt_on: str | None = None) -> None:
        self.calls: list[tuple[str, str, bool]] = []
        self.fail_on = set(fail_on)
        self.interrupt_on = interrupt_on

    def complete(self, model: str, system: str, user: str, *, json_mode: bool) -> str:
        stage = next(
            line.split("：", 1)[1] for line in user.splitlines() if line.startswith("段落标识：")
        )
        self.calls.append((stage, model, json_mode))
        if stage == self.interrupt_on:
            raise Interrupted(f"模拟进程在 {stage} 段被中断")
        if stage in self.fail_on:
            raise RuntimeError("connection reset by peer")
        return SCRIPTED[stage]

    def stages(self, stage: str) -> list[tuple[str, bool]]:
        return [(model, json_mode) for name, model, json_mode in self.calls if name == stage]


@pytest.fixture()
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    """造出「凭据齐全」的假环境：走三级阶梯而不是占位分支。"""
    monkeypatch.setenv(URL_ENV, "http://127.0.0.1:1/v1")
    monkeypatch.setenv(KEY_ENV, "test-key-not-real")


def make_config() -> fallback.AiConfig:
    return fallback.AiConfig(
        default_model="fake-model",
        fallback_chain=("fake-backup",),
        base_url_env=URL_ENV,
        api_key_env=KEY_ENV,
        structured_output="prefer",
        on_all_failed="degrade_to_text",
        sections={},
    )


def pipeline(root: Path, client: FakeClient | None = None) -> ResearchPipeline:
    return ResearchPipeline(CODE, config=make_config(), client=client or FakeClient(), root=root)


def status_map(run: Any) -> dict[str, str]:
    return {item["name"]: str(item["status"]) for item in run.stages}


def digest(root: Path) -> dict[str, tuple[str, int]]:
    """整棵数据目录指纹：相对路径 -> (内容 sha256, mtime_ns)。"""
    return {
        path.relative_to(root).as_posix(): (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_mtime_ns,
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def artifact_pair(stage: str, root: Path) -> tuple[dict[str, Any], str]:
    """读某段的两件产物（结构化件 + Markdown 叙述件）。"""
    json_path, md_path = artifacts.stage_paths(CODE, DAY, stage, root)
    return json.loads(json_path.read_text(encoding="utf-8")), md_path.read_text(encoding="utf-8")


def pair_paths(stage: str) -> list[str]:
    directory = artifacts.ARTIFACT_SUBDIR + f"/{CODE}/{DAY}/"
    return [f"{directory}{stage}.json", f"{directory}{stage}.md"]


def checkpoint_file() -> str:
    return "/".join(checkpoint.CHECKPOINT_SUBDIR) + f"/{CODE}/{DAY}.json"


# --------------------------------------------------------------------------- #
# 1. 断点续跑：跑到第 3 段中断，重跑只补 3~6 段
# --------------------------------------------------------------------------- #


def test_resume_after_interruption(tmp_path: Path, configured: None) -> None:
    root = tmp_path / "resume"
    broken = FakeClient(interrupt_on="forecast")

    with pytest.raises(Interrupted):
        pipeline(root, broken).run(DAY)

    # 中断只可能停在段与段之间：前两段已各自原子落盘，第 3 段起一件都没有
    document = checkpoint.load(CODE, DAY, root)
    assert checkpoint.done_stages(document) == ["company", "financial"]
    assert checkpoint.pending_stages(document) == list(STAGES[2:])
    directory = artifacts.artifact_dir(CODE, DAY, root)
    for stage in ("company", "financial"):
        assert (directory / f"{stage}.json").is_file() and (directory / f"{stage}.md").is_file()
    for stage in STAGES[2:]:
        assert not (directory / f"{stage}.json").exists(), stage

    before = digest(root)
    time.sleep(1.1)

    resumed = FakeClient()
    run = pipeline(root, resumed).run(DAY)

    assert run.exit_code == 0, run.research_failures
    assert run.generated == list(STAGES[2:]), "重跑只补第 3~6 段"
    assert run.reused == ["company", "financial"]
    assert [stage for stage, _model, _json_mode in resumed.calls] == list(STAGES[2:])

    after = digest(root)
    # checkpoint 会被续跑更新；前两段产物必须逐字节 + mtime 都不动
    for stage in ("company", "financial"):
        for path in pair_paths(stage):
            assert path in before and path in after, path
            assert before[path] == after[path], f"{stage} 段产物被重写了：{path}"

    # 续跑之后六段齐全，报告段拿到的上游是五段
    report, _md = artifact_pair("report", root)
    assert list(report["facts"]["upstream"]) == list(checkpoint.REPORT_INPUTS)


# --------------------------------------------------------------------------- #
# 2. 单段重跑：--only risk 只动 risk 段
# --------------------------------------------------------------------------- #


def test_only_single_stage(tmp_path: Path, configured: None) -> None:
    root = tmp_path / "only"
    first = pipeline(root).run(DAY)
    assert first.exit_code == 0 and first.failed == []

    before = digest(root)
    states_before = json.loads(
        Path(checkpoint.checkpoint_path(CODE, DAY, root)).read_text(encoding="utf-8")
    )["stages"]

    # (a) 不加 --force：risk 已完成，`--only risk` 一段都不跑，整棵树一个字节都不动
    idle = FakeClient()
    run_idle = pipeline(root, idle).run(DAY, only="risk")
    assert run_idle.generated == [] and run_idle.exit_code == 0
    assert idle.calls == [], "已完成的段不许被 --only 重跑"
    assert digest(root) == before, "整棵数据目录应完全不变"

    time.sleep(1.1)

    # (b) 加 --force：只重写 risk，其余五段的状态与文件 mtime 一律不变
    forced = FakeClient()
    run = pipeline(root, forced).run(DAY, only="risk", force=True)
    assert run.generated == ["risk"]
    assert [stage for stage, _model, _json_mode in forced.calls] == ["risk"], (
        "单段重跑不得触碰其他段的模型调用"
    )
    assert status_map(run) == {name: "ok" for name in STAGES}

    after = digest(root)
    for path in pair_paths("risk"):
        assert after[path][1] > before[path][1], f"risk 段产物应被重写：{path}"
    for stage in tuple(name for name in STAGES if name != "risk"):
        for path in pair_paths(stage):
            assert after[path] == before[path], f"{stage} 段产物被动了：{path}"

    states_after = json.loads(
        Path(checkpoint.checkpoint_path(CODE, DAY, root)).read_text(encoding="utf-8")
    )["stages"]
    assert {name: value for name, value in states_after.items() if name != "risk"} == (
        {name: value for name, value in states_before.items() if name != "risk"}
    ), "--only 不改其他段状态"


# --------------------------------------------------------------------------- #
# 3. 连续失败：该段 failed、其余不变、退出码 9、前序产物仍在
# --------------------------------------------------------------------------- #


def test_all_failed_exit_9(tmp_path: Path, configured: None) -> None:
    # 场景 A：报告段两级阶梯全部失败（模型链每个模型都被试过）
    root = tmp_path / "report-failed"
    client = FakeClient(fail_on=("report",))
    run = pipeline(root, client).run(DAY)

    assert run.exit_code == 9, "有段 failed -> EXIT_AI_DEGRADED(9)"
    assert run.failed == ["report"]
    assert status_map(run) == {
        **{name: "ok" for name in checkpoint.REPORT_INPUTS},
        "report": "failed",
    }
    assert client.stages("report") == [(model, True) for model in MODELS] + [
        (model, False) for model in MODELS
    ], "连续失败要把结构化一级与纯文本三级都试完才判 failed"

    state = checkpoint.stage_state(checkpoint.load(CODE, DAY, root), "report")
    assert state["status"] == "failed" and state["mode"] == "failed"
    assert state["failure"]["kind"] == "network" and state["failure"]["retryable"] is True
    assert len(state["attempts"]) == len(MODELS) * 2

    report_json, report_md = artifacts.stage_paths(CODE, DAY, "report", root)
    assert not report_json.exists() and not report_md.exists(), "失败段不落半截产物"
    directory = artifacts.artifact_dir(CODE, DAY, root)
    for stage in checkpoint.REPORT_INPUTS:
        assert (directory / f"{stage}.json").is_file(), stage
        assert (directory / f"{stage}.md").is_file(), stage

    # 场景 B：中段（risk）连续失败 → 该段 failed，前四段照常 ok，序列不中断，退出码 9
    other = tmp_path / "risk-failed"
    client_b = FakeClient(fail_on=("risk",))
    run_b = pipeline(other, client_b).run(DAY)

    assert run_b.exit_code == 9
    statuses_b = status_map(run_b)
    assert statuses_b["risk"] == "failed"
    for stage in ("company", "financial", "forecast", "valuation"):
        assert statuses_b[stage] == "ok", stage
    # report 段因缺 risk 而失败，且点名要补哪一段
    assert run_b.failed == ["risk", "report"]
    report_failure = next(item for item in run_b.research_failures if item["stage"] == "report")
    assert "--only risk" in report_failure["action"]
    assert set(run_b.research_failures[0]) == FAILURE_ENTRY_KEYS
    for stage in ("company", "financial", "forecast", "valuation"):
        assert (artifacts.artifact_dir(CODE, DAY, other) / f"{stage}.json").is_file(), stage
        assert (artifacts.artifact_dir(CODE, DAY, other) / f"{stage}.md").is_file(), stage
    assert not (artifacts.artifact_dir(CODE, DAY, other) / "risk.json").exists()

    # 场景 C：六段全失败 → 整条管线以 9 退出，一件半成品都不留
    worst = tmp_path / "all-failed"
    client_c = FakeClient(fail_on=STAGES)
    run_c = pipeline(worst, client_c).run(DAY)

    assert run_c.exit_code == 9
    assert run_c.failed == list(STAGES)
    assert status_map(run_c) == {name: "failed" for name in STAGES}
    assert run_c.degraded is False and run_c.models == [] and run_c.generated == []
    # 前五段没有产物，报告段直接判「缺输入」而不是重新取数（一次模型调用都不发）
    assert client_c.stages("report") == []
    directory_c = artifacts.artifact_dir(CODE, DAY, worst)
    assert not directory_c.exists() or not list(directory_c.glob("*")), list(
        directory_c.glob("*")
    )

    checkpoint_c = json.loads(
        Path(checkpoint.checkpoint_path(CODE, DAY, worst)).read_text(encoding="utf-8")
    )
    assert list(checkpoint_c["stages"]) == list(STAGES), "六段全失败也要每段一条状态"
    for name in STAGES:
        entry = checkpoint_c["stages"][name]
        assert entry["status"] == "failed" and entry["completed_at"], name
        assert entry["failure"]["retryable"] is True, name

    # 全失败后续跑：`--retry-failed` 会把六段重新选中（失败段绝不复用）
    retry_client = FakeClient()
    run_d = pipeline(worst, retry_client).run(DAY, retry_failed=True)
    assert run_d.generated == list(STAGES)
    assert run_d.exit_code == 0
    assert status_map(run_d) == {name: "ok" for name in STAGES}
    assert set(run_d.as_dict()["artifacts"]) == set(STAGES)


# --------------------------------------------------------------------------- #
# 4. 产物结构：json 键形状 + md 非空 + checkpoint 每段一条
# --------------------------------------------------------------------------- #


def test_artifact_shape(tmp_path: Path, configured: None) -> None:
    root = tmp_path / "shape"
    run = pipeline(root).run(DAY)
    assert run.exit_code == 0

    for stage in STAGES:
        document, markdown = artifact_pair(stage, root)
        assert set(document) == STAGE_KEYS, stage
        assert document["code"] == CODE and document["date"] == DAY
        assert document["stage"] == stage
        assert document["title"] == checkpoint.STAGE_TITLES[stage]
        assert document["status"] in {"ok", "degraded"}
        assert document["model"] in MODELS
        assert document["degraded"] is False and document["failure"] is None
        assert document["narrative"] and all(document["narrative"])
        assert document["ai"]["narrative"] == document["narrative"]
        assert set(document["provenance"]) == set(artifacts.PROVENANCE_KEYS)
        assert document["provenance"]["unverified"] is True, "R3 定稿前一律未核实"

        title = checkpoint.STAGE_TITLES[stage]
        assert markdown.startswith(f"# {title}"), stage
        assert "## 叙述" in markdown
        body = markdown.split("## 叙述", 1)[1]
        assert document["narrative"][0] in body, f"{stage}.md 叙述段为空"
        assert len(markdown.strip()) > len(f"# {title}") + len("## 叙述")

    # checkpoint：六段一条不少不多，顺序即 STAGES，每条状态字段形状固定
    raw = json.loads(
        Path(checkpoint.checkpoint_path(CODE, DAY, root)).read_text(encoding="utf-8")
    )
    assert list(raw["stages"]) == list(STAGES)
    assert raw["code"] == CODE and raw["date"] == DAY and raw["version"] == 1
    for name, state in raw["stages"].items():
        assert set(state) == STATE_KEYS, name
        assert state["status"] in STATUSES, name
        assert state["completed_at"], name
        assert isinstance(state["attempts"], list) and isinstance(state["notes"], list)

    # 落盘位置与 --output json 报的路径一致（路径规则只有一处定义）
    payload = run.as_dict()
    for stage in STAGES:
        entry = payload["artifacts"][stage]
        assert Path(entry["json"]).is_file() and Path(entry["md"]).is_file()
        assert entry["exists"] is True
        assert Path(entry["json"]) == artifacts.stage_paths(CODE, DAY, stage, root)[0]
    assert Path(payload["checkpoint"]) == Path(checkpoint.checkpoint_path(CODE, DAY, root))


# --------------------------------------------------------------------------- #
# 5. --output json 契约：字段名与类型固定
# --------------------------------------------------------------------------- #


def test_json_contract(
    tmp_path: Path, repo: Path, configured: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "data"
    monkeypatch.setenv("LDST_DATA_DIR", str(data_dir))
    monkeypatch.delenv("LDST_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LDST_LLM_API_KEY", raising=False)

    result = runner.invoke(app, ["research", CODE, "--date", DAY, "--output", "json"])
    assert result.exit_code == 9, result.stdout
    payload = json.loads(result.stdout)

    assert set(payload) == set(JSON_CONTRACT)
    for key, expected in JSON_CONTRACT.items():
        assert type(payload[key]) is expected, f"{key} 类型应为 {expected.__name__}"
    assert payload["code"] == CODE and payload["date"] == DAY
    assert payload["stage_order"] == list(STAGES)
    assert payload["configured"] is False
    assert payload["exit_code"] == result.exit_code == 9
    assert payload["failed"] == [] and payload["degraded"] is True
    assert payload["generated"] == list(STAGES) and payload["reused"] == []
    assert payload["research_failures"] == []
    assert [item["name"] for item in payload["stages"]] == list(STAGES)
    for item in payload["stages"]:
        assert set(item) == STAGE_ENTRY_KEYS
        assert type(item["status"]) is str and type(item["narrative"]) is list
    assert set(payload["artifacts"]) == set(STAGES)
    for entry in payload["artifacts"].values():
        assert set(entry) == set(ARTIFACT_ENTRY_KEYS)
        for key, expected in ARTIFACT_ENTRY_KEYS.items():
            assert type(entry[key]) is expected, key

    # 同一条命令的 table/csv 档不能改载荷形状，只能换渲染
    table = runner.invoke(app, ["research", CODE, "--date", DAY, "--output", "table"])
    assert table.exit_code == 9 and table.stdout.strip()

    # 配好凭据走真实阶梯后，载荷键集合与类型不变；失败段只往既有键里填内容
    root = tmp_path / "configured"
    run = pipeline(root, FakeClient(fail_on=("risk",))).run(DAY)
    armed = run.as_dict()
    assert set(armed) == set(JSON_CONTRACT)
    for key, expected in JSON_CONTRACT.items():
        assert type(armed[key]) is expected, key
    assert armed["configured"] is True
    assert armed["failed"] == ["risk", "report"]
    assert armed["exit_code"] == 9
    for item in armed["research_failures"]:
        assert set(item) == FAILURE_ENTRY_KEYS
        assert type(item["retryable"]) is bool


# --------------------------------------------------------------------------- #
# 6. research_status：读得对，且不产生写操作
# --------------------------------------------------------------------------- #


def test_research_status_read_only(tmp_path: Path, configured: None) -> None:
    spec = {item.name: item for item in TOOL_SPECS}["research_status"]
    assert spec.read_only is True

    root = tmp_path / "status"
    run = pipeline(root).run(DAY)
    assert run.exit_code == 0

    before = digest(root)
    arguments = {"code": CODE, "date": DAY}

    envelope = dispatch("research_status", arguments, root=root)
    research = envelope["payload"]["research"]
    assert research["exists"] is True and research["complete"] is True
    assert research["done"] == list(STAGES)
    assert research["failed"] == [] and research["pending"] == []
    assert [item["name"] for item in research["stages"]] == list(STAGES)
    assert [item["status"] for item in research["stages"]] == [
        str(item["status"]) for item in run.stages
    ]
    assert research["checkpoint"] == str(checkpoint.checkpoint_path(CODE, DAY, root))
    assert envelope["source_status"] == "ok"

    # 再读一次：整棵数据目录（含 checkpoint）内容与 mtime 完全一致，没有新文件
    second = dispatch("research_status", arguments, root=root)
    assert second == envelope or second["payload"]["research"] == research
    after = digest(root)
    assert after == before, "research_status 是只读工具，不得产生任何写操作"
    assert after[checkpoint_file()][1] == before[checkpoint_file()][1]
    assert not list(root.rglob("*.tmp"))
