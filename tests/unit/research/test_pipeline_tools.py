"""研究管线接上受控工具通道（R2 / QUOT-140 验收第 2、3、4 条）。

脚本化假 ChatClient，不拨网络；`root` 指向 tmp_path。
重点：越权第 1 次即判该段失败且不重试、被拒的调用要回喂给模型、
不支持 tool-calling 的客户端行为与今天一致。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from ladderstock.ai import fallback, providers, tools
from ladderstock.research import artifacts, checkpoint, stages
from ladderstock.research.pipeline import ResearchPipeline

DAY = "2026-10-27"
CODE = "600000"
URL_ENV = "TEST_RESEARCH_BASE_URL"
KEY_ENV = "TEST_RESEARCH_API_KEY"


class PlainClient:
    """只有 `complete()` 的客户端（五段复盘与既有假客户端的形状）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, bool]] = []
        self.systems: list[str] = []

    @property
    def models(self) -> list[str]:
        return ["fake-model"]

    def complete(self, model: str, system: str, user: str, *, json_mode: bool) -> str:
        del model
        self.calls.append((_stage_of(user), json_mode))
        self.systems.append(system)
        stage = _stage_of(user)
        if not json_mode:
            return f"{checkpoint.STAGE_TITLES[stage]}段：纯文本叙述。"
        return json.dumps({"narrative": [f"{stage} 段叙述。"]}, ensure_ascii=False)


class ToolClient(PlainClient):
    """带 `complete_with_tools()` 的客户端：按脚本回工具调用，用完即出正文。"""

    def __init__(self, script: Mapping[str, Sequence[dict[str, Any]]]) -> None:
        super().__init__()
        self.pending: dict[str, list[dict[str, Any]]] = {
            stage: list(turns) for stage, turns in script.items()
        }
        self.histories: list[list[dict[str, Any]]] = []
        self.requested: list[tuple[str, str]] = []
        self.seen_tools: list[tuple[str, tuple[str, ...]]] = []

    def complete_with_tools(
        self,
        model: str,
        messages: Sequence[dict[str, Any]],
        tools: Sequence[dict[str, Any]],
        *,
        json_mode: bool,
    ) -> providers.ToolTurn:
        del model, json_mode
        stage = _stage_of(next(msg["content"] for msg in messages if msg["role"] == "user"))
        self.histories.append([dict(message) for message in messages])
        self.seen_tools.append((stage, tuple(item["function"]["name"] for item in tools)))
        queue = self.pending.get(stage) or []
        if not queue:
            return providers.ToolTurn(
                text=json.dumps({"narrative": [f"{stage} 段叙述。"]}, ensure_ascii=False)
            )
        calls = tuple(queue.pop(0)["calls"])
        self.requested.extend((stage, call["name"]) for call in calls)
        return providers.ToolTurn(
            tool_calls=tuple(
                providers.ToolCall(
                    id=f"call_{index}_{call['name']}",
                    name=call["name"],
                    arguments=call.get("args", {}),
                )
                for index, call in enumerate(calls)
            )
        )


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


def run_pipeline(tmp_path: Path, client: PlainClient, **kwargs: Any) -> Any:
    pipeline = ResearchPipeline(CODE, config=make_config(), client=client, root=tmp_path)
    return pipeline.run(DAY, **kwargs)


def _stage_of(user: str) -> str:
    return next(
        line.split("：", 1)[1] for line in user.splitlines() if line.startswith("段落标识：")
    )


def audit(tmp_path: Path) -> list[dict[str, Any]]:
    path = artifacts.artifact_dir(CODE, DAY, tmp_path) / tools.AUDIT_FILENAME
    return tools.audit_records(path) if path.is_file() else []


# --------------------------------------------------------------------------- #
# 白名单真的生效：越权第 1 次即判该段失败，不重试、不换模型
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(URL_ENV, "http://127.0.0.1:1/v1")
    monkeypatch.setenv(KEY_ENV, "test-key-not-real")


@pytest.mark.parametrize(
    ("stage", "tool"),
    [
        ("company", "write_report"),  # 只有报告段能写报告
        ("valuation", "compute"),  # DEC-025：估值段不给 compute
        ("risk", "delete_everything"),  # 注册表里根本没有的名字
    ],
)
def test_unauthorized_tool_fails_the_stage_on_first_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str, tool: str
) -> None:
    monkeypatch.setenv(tools.WEB_SEARCH_ENV, "1")
    client = ToolClient({stage: [{"calls": [{"name": tool, "args": {"md": "x"}}]}]})
    run = run_pipeline(tmp_path, client, only=stage)

    assert run.exit_code == 9
    assert run.failed == [stage]
    assert client.requested == [(stage, tool)]  # 只发生一次
    assert client.calls == []  # 不重跑结构化一级，也不落纯文本降级

    entry = next(item for item in run.stages if item["name"] == stage)
    assert entry["status"] == "failed" and entry["mode"] == "failed"
    assert any("越权工具调用" in note for note in entry["notes"])
    failure = run.research_failures[0]
    assert failure["stage"] == stage
    assert failure["kind"] == "internal"
    assert failure["retryable"] is False
    assert tool in failure["message"]
    # 越权的段没有半截产物（失败隔离与 R1 同语义）
    assert not (artifacts.artifact_dir(CODE, DAY, tmp_path) / f"{stage}.json").exists()

    lines = audit(tmp_path)
    assert len(lines) == 1, lines
    assert lines[0]["tool"] == tool
    assert lines[0]["rejected"] is True
    assert lines[0]["reason"] == "unauthorized_tool"


def test_report_stage_cannot_write_a_stage_artifact(tmp_path: Path) -> None:
    """报告段（前五段已落盘，所以走得到工具循环）调 write_stage 同样判越权。"""
    run_pipeline(tmp_path, PlainClient())
    client = ToolClient(
        {
            "report": [
                {
                    "calls": [
                        {
                            "name": "write_stage",
                            "args": {
                                "stage": "report",
                                "name": "draft",
                                "payload": {"narrative": ["模型自己写的段产物"]},
                            },
                        }
                    ]
                }
            ]
        }
    )
    run = run_pipeline(tmp_path, client, only="report", force=True)

    assert run.failed == ["report"]
    assert client.requested == [("report", "write_stage")]
    lines = audit(tmp_path)
    assert [line["tool"] for line in lines if line["rejected"]][-1] == "write_stage"
    assert lines[-1]["reason"] == "unauthorized_tool"


def test_unauthorized_stage_does_not_affect_the_others(tmp_path: Path) -> None:
    """越权只判那一段：前序成果还在，后续段照常跑（report 缺输入是既有语义）。"""
    client = ToolClient(
        {"financial": [{"calls": [{"name": "write_report", "args": {"md": "x"}}]}]}
    )
    run = run_pipeline(tmp_path, client)

    assert run.failed == ["financial", "report"]
    assert run.exit_code == 9
    states = {item["name"]: item["status"] for item in run.stages}
    assert states["company"] == "ok" and states["forecast"] == "ok"
    assert run.research_failures[1]["stage"] == "report"
    # 前序产物没被越权段冲掉
    directory = artifacts.artifact_dir(CODE, DAY, tmp_path)
    assert (directory / "company.json").is_file()
    assert not (directory / "financial.json").exists()


# --------------------------------------------------------------------------- #
# 工具足迹：调用、审计、回喂
# --------------------------------------------------------------------------- #


def test_tool_calls_land_an_audit_line_each(tmp_path: Path) -> None:
    client = ToolClient(
        {
            "company": [
                {
                    "calls": [
                        {"name": "compute", "args": {"op": "add", "a": 0.1, "b": 0.2}},
                        {"name": "list_artifacts", "args": {"stage": "company"}},
                    ]
                }
            ]
        }
    )
    run = run_pipeline(tmp_path, client, only="company")
    assert run.exit_code == 0

    lines = audit(tmp_path)
    assert [line["tool"] for line in lines] == ["compute", "list_artifacts"]
    assert all(line["rejected"] is False for line in lines)
    assert all(line["stage"] == "company" for line in lines)
    for line in lines:
        assert set(line) == {
            "ts",
            "stage",
            "tool",
            "model",
            "args",
            "result",
            "duration_ms",
            "rejected",
            "reason",
        }
    document = artifacts.read_stage(CODE, DAY, "company", tmp_path)
    assert document is not None
    assert document["mode"] == "structured"
    assert any("受控工具 2 次" in note for note in document["notes"])


def test_refused_call_is_fed_back_to_the_model(tmp_path: Path) -> None:
    """路径穿越是被拒，不是越权：拒因回喂，模型改用正文成文，段不失败。"""
    client = ToolClient(
        {
            "company": [
                {
                    "calls": [
                        {
                            "name": "read_artifact",
                            "args": {"stage": "company", "name": "../../state/other.json"},
                        }
                    ]
                }
            ]
        }
    )
    run = run_pipeline(tmp_path, client, only="company")

    assert run.exit_code == 0
    lines = audit(tmp_path)
    assert len(lines) == 1
    assert lines[0]["rejected"] is True
    assert lines[0]["reason"] == "bad_argument"
    # 第二次请求的 messages 里带着 role=tool 的拒因
    assert len(client.histories) == 2
    feedback = [message for message in client.histories[1] if message["role"] == "tool"]
    assert len(feedback) == 1
    assert "工具被拒绝" in feedback[0]["content"]
    assert feedback[0]["tool_call_id"].startswith("call_0_")
    assert not (tmp_path / "state" / "other.json").exists()


def test_model_sees_exactly_its_stage_whitelist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """请求里带去的工具描述符 = 该段白名单，一个不多一个不少。"""
    monkeypatch.delenv(tools.WEB_SEARCH_ENV, raising=False)
    client = ToolClient({})
    run_pipeline(tmp_path, client)
    by_stage: dict[str, set[str]] = {}
    for stage, names in client.seen_tools:
        by_stage.setdefault(stage, set(names))
    assert set(by_stage) == set(checkpoint.STAGES)
    for stage, names in by_stage.items():
        assert names == set(stages.allowed_tools(stage)), stage


# --------------------------------------------------------------------------- #
# 回归：客户端不支持 tool-calling 时，行为与 R2 之前一致
# --------------------------------------------------------------------------- #


def test_plain_client_runs_without_any_tool_channel(tmp_path: Path) -> None:
    client = PlainClient()
    run = run_pipeline(tmp_path, client)

    assert run.exit_code == 0
    assert [model for model, _ in client.calls] == list(checkpoint.STAGES)
    assert all(json_mode for _, json_mode in client.calls)
    assert not (artifacts.artifact_dir(CODE, DAY, tmp_path) / tools.AUDIT_FILENAME).exists()
    assert all("受控工具" not in system for system in client.systems)
    for stage in checkpoint.STAGES:
        document = artifacts.read_stage(CODE, DAY, stage, tmp_path)
        assert document is not None
        assert not any("受控工具" in note for note in document["notes"])


def test_tool_instruction_only_appears_when_tools_will_run(tmp_path: Path) -> None:
    plain = PlainClient()
    run_pipeline(tmp_path, plain, only="risk")
    assert "受控工具" not in plain.systems[0]

    tool_client = ToolClient({})
    run_pipeline(tmp_path, tool_client, only="risk", force=True)
    assert tool_client.histories, "带工具的客户端应走 tool-calling 通道"
    assert "受控工具" in tool_client.histories[0][0]["content"]
    assert "compute" in tool_client.histories[0][0]["content"]
