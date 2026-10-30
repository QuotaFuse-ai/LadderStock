"""受控工具集（R2 / QUOT-140）：白名单执行、路径边界、确定性算术、审计留痕。

全部用 tmp_path 当落盘根目录，不碰仓库 `data/`，也不拨网络。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ladderstock.ai import providers, tools

DAY = "2026-10-27"
CODE = "600000"
FULL_SET = (
    tools.LIST_ARTIFACTS,
    tools.READ_ARTIFACT,
    tools.COMPUTE,
    tools.WRITE_STAGE,
    tools.WRITE_REPORT,
    tools.SEARCH_WEB,
)


def make_ctx(
    tmp_path: Path,
    *,
    stage: str = "company",
    allowed: tuple[str, ...] = FULL_SET,
    backend: Any = None,
) -> tools.ToolContext:
    run_dir = tmp_path / "research" / CODE / DAY
    run_dir.mkdir(parents=True, exist_ok=True)
    return tools.ToolContext(
        stage=stage,
        roots={tools.RUN_ROOT: run_dir},
        allowed=frozenset(allowed),
        model="fake-model",
        search_backend=backend,
    )


def land(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / "research" / CODE / DAY / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def audit_lines(tmp_path: Path) -> list[dict[str, Any]]:
    return tools.audit_records(tools.audit_path(make_ctx(tmp_path)))


# --------------------------------------------------------------------------- #
# 注册表本身
# --------------------------------------------------------------------------- #


def test_registry_is_the_constant_tool_set() -> None:
    assert set(tools.TOOLS) == set(tools.TOOL_NAMES)
    for name, spec in tools.TOOLS.items():
        assert spec.name == name
        assert spec.description, name
        assert callable(spec.handler), name
        # 每项都声明允许的输入根目录；本任务只允许研究落盘目录这一个
        assert set(spec.roots) <= {tools.RUN_ROOT}, name
        assert spec.note, name


def test_every_tool_schema_is_a_closed_object() -> None:
    """入参 Schema 必须是封闭对象：不允许多余键，模型塞不进额外意图。"""
    for name, spec in tools.TOOLS.items():
        schema = dict(spec.parameters)
        assert schema["type"] == "object", name
        assert schema["additionalProperties"] is False, name
        assert schema["required"], name


def test_only_controlled_search_may_reach_the_network() -> None:
    """触网是例外不是常态：只有 search_web 标了 network，且默认被闸门拦掉。"""
    assert [name for name, spec in tools.TOOLS.items() if spec.network] == [tools.SEARCH_WEB]


def test_tool_descriptors_are_openai_shaped() -> None:
    descriptors = tools.tool_descriptors([tools.COMPUTE, tools.READ_ARTIFACT])
    assert [item["function"]["name"] for item in descriptors] == [
        tools.COMPUTE,
        tools.READ_ARTIFACT,
    ]
    assert all(item["type"] == "function" for item in descriptors)
    assert descriptors[0]["function"]["parameters"]["properties"]["op"]["enum"] == list(
        tools.COMPUTE_OPS
    )


def test_tool_instruction_lists_exactly_the_allowed_tools() -> None:
    text = tools.tool_instruction([tools.COMPUTE])
    assert tools.COMPUTE in text
    assert tools.WRITE_STAGE not in text
    assert "未授权工具本段直接判失败" in tools.tool_instruction([tools.COMPUTE]) or "判失败" in text
    assert tools.tool_instruction([]) == ""


# --------------------------------------------------------------------------- #
# 越权：第 1 次即拒，且留痕
# --------------------------------------------------------------------------- #


def test_unauthorized_tool_raises_and_audits_once(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path, allowed=(tools.LIST_ARTIFACTS,))
    with pytest.raises(tools.UnauthorizedToolCall, match="未授权工具"):
        tools.execute(ctx, tools.WRITE_STAGE, {"stage": "company", "name": "d", "payload": {}})
    assert ctx.call_count == 1
    lines = audit_lines(tmp_path)
    assert len(lines) == 1
    assert lines[0]["rejected"] is True
    assert lines[0]["reason"] == "unauthorized_tool"
    assert lines[0]["tool"] == tools.WRITE_STAGE
    assert lines[0]["stage"] == "company"


def test_invented_tool_name_is_unauthorized_too(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path, allowed=(tools.COMPUTE,))
    with pytest.raises(tools.UnauthorizedToolCall):
        tools.execute(ctx, "fetch_eastmoney_snapshot", {})
    assert audit_lines(tmp_path)[-1]["tool"] == "fetch_eastmoney_snapshot"


def test_audit_record_shape(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path, allowed=(tools.COMPUTE,))
    tools.execute(ctx, tools.COMPUTE, {"op": "add", "a": 1, "b": 2})
    line = audit_lines(tmp_path)[-1]
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
    assert line["model"] == "fake-model"
    assert line["rejected"] is False
    assert line["reason"] is None
    assert line["duration_ms"] >= 0


def test_no_audit_file_without_any_call(tmp_path: Path) -> None:
    """一次工具都没调 → 不产生审计文件，管线落盘与 R2 之前完全一致。"""
    make_ctx(tmp_path)
    assert not (tmp_path / "research" / CODE / DAY / tools.AUDIT_FILENAME).exists()


def test_call_budget_stops_a_tool_looping_model(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path, allowed=(tools.COMPUTE,))
    for index in range(tools.MAX_CALLS_PER_STAGE):
        assert tools.execute(ctx, tools.COMPUTE, {"op": "add", "a": index, "b": 1})["ok"]
    outcome = tools.execute(ctx, tools.COMPUTE, {"op": "add", "a": 1, "b": 1})
    assert outcome["ok"] is False
    assert outcome["reason"] == "call_budget_exceeded"
    assert audit_lines(tmp_path)[-1]["reason"] == "call_budget_exceeded"


# --------------------------------------------------------------------------- #
# 读产物：路径边界
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "name",
    [
        "../600519.json",
        "../../etc/passwd",
        "..\\..\\windows\\system32\\x.json",
        "/etc/passwd",
        "C:/Windows/x.json",
        "C:\\research\\600000\\company.json",
        "sub/company.json",
        "company.json/../../../x",
        "~/company.json",
        "company\u200b.json",
        "company.JSON",
        "600519.json",
        "company",
        "company.exe",
        "c" * 80 + ".json",
    ],
)
def test_read_artifact_rejects_any_path_writing(tmp_path: Path, name: str) -> None:
    ctx = make_ctx(tmp_path)
    land(tmp_path, "company.json", '{"narrative": ["x"]}')
    outcome = tools.execute(ctx, tools.READ_ARTIFACT, {"stage": "company", "name": name})
    assert outcome["ok"] is False
    assert outcome["reason"] in {
        "bad_argument",
        "bad_artifact_name",
        "foreign_artifact",
        "path_escape",
    }
    assert "content" not in outcome["content"].replace("工具被拒绝：", "")
    assert audit_lines(tmp_path)[-1]["rejected"] is True


def test_read_artifact_rejects_symlink_escape(tmp_path: Path) -> None:
    run_dir = tmp_path / "research" / CODE / DAY
    run_dir.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "outside.json"
    outside.write_text('{"secret": "别的目录的内容"}', encoding="utf-8")
    link = run_dir / "company__link.json"
    try:
        link.symlink_to(outside)
    except OSError:  # Windows 无软链权限：换一条等价的「解析后越界」路径
        pytest.skip("本平台不允许创建符号链接")
    ctx = make_ctx(tmp_path)
    outcome = tools.execute(
        ctx, tools.READ_ARTIFACT, {"stage": "company", "name": "company__link.json"}
    )
    assert outcome["ok"] is False
    assert outcome["reason"] == "symlink_rejected"
    assert "secret" not in outcome["content"]


def test_read_artifact_rejects_other_stage_file(tmp_path: Path) -> None:
    """别的段的产物读不到：名字必须属于本段，链进来也一样。"""
    run_dir = tmp_path / "research" / CODE / DAY
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "risk__note.json").write_text('{"narrative": ["风险"]} ', encoding="utf-8")
    ctx = make_ctx(tmp_path, stage="company")
    outcome = tools.execute(
        ctx, tools.READ_ARTIFACT, {"stage": "company", "name": "risk__note.json"}
    )
    assert outcome["ok"] is False
    assert outcome["reason"] == "foreign_artifact"


def test_read_and_list_canonical_artifact(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    land(tmp_path, "company.json", '{"narrative": ["公司画像叙述"]}')
    listing = json.loads(tools.execute(ctx, tools.LIST_ARTIFACTS, {"stage": "company"})["content"])
    assert listing["count"] == 1
    assert listing["artifacts"][0]["name"] == "company.json"
    read = json.loads(
        tools.execute(ctx, tools.READ_ARTIFACT, {"stage": "company", "name": "company.json"})[
            "content"
        ]
    )
    assert read["content"] == '{"narrative": ["公司画像叙述"]}'


def test_list_artifacts_hides_the_audit_log(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path, allowed=(tools.COMPUTE, tools.LIST_ARTIFACTS))
    tools.execute(ctx, tools.COMPUTE, {"op": "add", "a": 1, "b": 1})
    listing = json.loads(tools.execute(ctx, tools.LIST_ARTIFACTS, {"stage": "company"})["content"])
    assert [row["name"] for row in listing["artifacts"]] == []
    assert (tmp_path / "research" / CODE / DAY / tools.AUDIT_FILENAME).is_file()


# --------------------------------------------------------------------------- #
# compute：模型不许心算
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("op", "a", "b", "result"),
    [
        ("add", 0.1, 0.2, "0.300000"),
        ("sub", 100, 0.5, "99.500000"),
        ("mul", 3, 2.5, "7.500000"),
        ("div", 2, 3, "0.666667"),
        ("pct_change", 10, 12, "20.000000"),
    ],
)
def test_compute_is_deterministic(
    tmp_path: Path, op: str, a: float, b: float, result: str
) -> None:
    ctx = make_ctx(tmp_path, allowed=(tools.COMPUTE,))
    outcome = tools.execute(ctx, tools.COMPUTE, {"op": op, "a": a, "b": b})
    assert json.loads(outcome["content"])["result"] == result


def test_compute_refuses_unsafe_numbers(tmp_path: Path) -> None:
    cases = [
        ({"op": "div", "a": 1, "b": 0}, "division_by_zero"),
        ({"op": "pct_change", "a": 0, "b": 1}, "division_by_zero"),
        ({"op": "average", "a": 1, "b": 2}, "bad_arguments"),
        ({"op": "add", "a": "1", "b": 2}, "bad_arguments"),
        ({"op": "add", "a": float("nan"), "b": 2}, "bad_number"),
        ({"op": "add", "a": 1e30, "b": 2}, "bad_number"),
    ]
    for args, reason in cases:
        outcome = tools.execute(make_ctx(tmp_path, allowed=(tools.COMPUTE,)), tools.COMPUTE, args)
        assert outcome["ok"] is False, args
        assert outcome["reason"] == reason, (args, outcome["reason"])


def test_compute_arguments_as_json_text_are_accepted(tmp_path: Path) -> None:
    """OpenAI 把入参给成 JSON 字符串，也要照收。"""
    ctx = make_ctx(tmp_path, allowed=(tools.COMPUTE,))
    outcome = tools.execute(ctx, tools.COMPUTE, '{"op": "add", "a": 1, "b": 2}')
    assert outcome["ok"] is True
    assert json.loads(outcome["content"])["result"] == "3.000000"


def test_malformed_arguments_are_refused_not_crashing(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path, allowed=(tools.COMPUTE,))
    outcome = tools.execute(ctx, tools.COMPUTE, '{"op": "add", ')
    assert outcome["ok"] is False
    assert outcome["reason"] == "bad_arguments"
    assert audit_lines(tmp_path)[-1]["rejected"] is True


# --------------------------------------------------------------------------- #
# 写：只写本段、只写叙述
# --------------------------------------------------------------------------- #


def test_write_stage_lands_a_draft_next_to_canonical(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path)
    outcome = tools.execute(
        ctx,
        tools.WRITE_STAGE,
        {"stage": "company", "name": "draft_1", "payload": {"narrative": ["主营与行业"]}},
    )
    assert outcome["ok"] is True
    written = tmp_path / "research" / CODE / DAY / "company__draft_1.json"
    assert written.is_file()
    assert json.loads(written.read_text(encoding="utf-8"))["narrative"] == ["主营与行业"]
    listing = json.loads(tools.execute(ctx, tools.LIST_ARTIFACTS, {"stage": "company"})["content"])
    assert [row["name"] for row in listing["artifacts"]] == ["company__draft_1.json"]


def test_write_stage_refuses_other_stage_and_metric_keys(tmp_path: Path) -> None:
    cases = [
        ({"stage": "risk", "name": "d", "payload": {"narrative": ["x"]}}, "cross_stage_write"),
        ({"stage": "company", "name": "d", "payload": {"temperature": 62}}, "narrative_only"),
        ({"stage": "company", "name": "d", "payload": {"narrative": []}}, "narrative_only"),
        # Schema 的 pattern 先拦下路径写法，`_safe_segment` 是第二道
        ({"stage": "company", "name": "../escape", "payload": {}}, "bad_arguments"),
    ]
    for payload, reason in cases:
        outcome = tools.execute(make_ctx(tmp_path), tools.WRITE_STAGE, payload)
        assert outcome["ok"] is False, payload
        assert outcome["reason"] == reason, (payload, outcome["reason"])


def test_write_report_only_in_report_stage(tmp_path: Path) -> None:
    outside = tools.execute(
        make_ctx(tmp_path, stage="company"), tools.WRITE_REPORT, {"md": "# 报告"}
    )
    assert outside["reason"] == "not_report_stage"

    ctx = make_ctx(tmp_path, stage="report")
    outcome = tools.execute(ctx, tools.WRITE_REPORT, {"md": "# 报告\n\n结论。"})
    assert outcome["ok"] is True
    written = tmp_path / "research" / CODE / DAY / f"report{tools.DRAFT_SEPARATOR}model.md"
    assert written.read_text(encoding="utf-8").startswith("# 报告")
    # canonical 文件不属于工具可写的范围：程序才是 15 键产物的作者
    assert not (tmp_path / "research" / CODE / DAY / "report.md").exists()


def test_write_report_rejects_empty_and_oversized(tmp_path: Path) -> None:
    ctx = make_ctx(tmp_path, stage="report")
    assert tools.execute(ctx, tools.WRITE_REPORT, {"md": "   "})["reason"] == "empty_report"
    too_long = tools.execute(ctx, tools.WRITE_REPORT, {"md": "字" * 9000})
    assert too_long["ok"] is False


# --------------------------------------------------------------------------- #
# 受控检索（DEC-018）：默认关闭 + 来源标签硬门槛
# --------------------------------------------------------------------------- #


def test_search_web_refused_until_env_gate_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(tools.WEB_SEARCH_ENV, raising=False)
    calls: list[str] = []

    def backend(query: str, hosts: frozenset[str]) -> list[dict[str, Any]]:
        calls.append(query)
        return []

    ctx = make_ctx(tmp_path, backend=backend)
    outcome = tools.execute(ctx, tools.SEARCH_WEB, {"query": "行业景气"})
    assert outcome["ok"] is False
    assert outcome["reason"] == "web_search_disabled"
    assert calls == []  # 闸门没开：一次外呼都不许发生


def test_search_web_requires_hosts_allowlist_and_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(tools.WEB_SEARCH_ENV, "1")
    monkeypatch.delenv(tools.WEB_SEARCH_HOSTS_ENV, raising=False)
    outcome = tools.execute(make_ctx(tmp_path), tools.SEARCH_WEB, {"query": "x"})
    assert outcome["reason"] == "web_search_hosts_empty"

    monkeypatch.setenv(tools.WEB_SEARCH_HOSTS_ENV, "example.com")
    outcome = tools.execute(make_ctx(tmp_path), tools.SEARCH_WEB, {"query": "x"})
    assert outcome["reason"] == "web_search_backend_missing"


def test_search_web_labels_sources_and_marks_unverified(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(tools.WEB_SEARCH_ENV, "1")
    monkeypatch.setenv(tools.WEB_SEARCH_HOSTS_ENV, "example.com")
    seen: list[str] = []

    def backend(query: str, hosts: frozenset[str]) -> list[dict[str, Any]]:
        seen.append(query)
        return [
            {
                "url": "https://example.com/a",
                "title": "行业月度景气",
                "fetched_at": "2026-10-27T09:00:00+00:00",
                "snippet": "…",
            }
        ]

    ctx = make_ctx(tmp_path, backend=backend)
    outcome = tools.execute(ctx, tools.SEARCH_WEB, {"query": "行业景气"})
    assert outcome["ok"] is True
    rows = json.loads(outcome["content"])["results"]
    assert rows[0]["verified"] is False
    assert rows[0]["source_host"] == "example.com"
    assert seen == ["行业景气"]


def test_search_web_rejects_unlabelled_and_offlist_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(tools.WEB_SEARCH_ENV, "1")
    monkeypatch.setenv(tools.WEB_SEARCH_HOSTS_ENV, "example.com")

    def no_source(query: str, hosts: frozenset[str]) -> list[dict[str, Any]]:
        return [{"url": "https://example.com/a", "snippet": "没有标题与抓取时间"}]

    def off_list(query: str, hosts: frozenset[str]) -> list[dict[str, Any]]:
        return [
            {
                "url": "https://not-allowed.example.org/a",
                "title": "t",
                "fetched_at": "2026-10-27",
            }
        ]

    assert (
        tools.execute(make_ctx(tmp_path, backend=no_source), tools.SEARCH_WEB, {"query": "x"})[
            "reason"
        ]
        == "source_label_missing"
    )
    assert (
        tools.execute(make_ctx(tmp_path, backend=off_list), tools.SEARCH_WEB, {"query": "x"})[
            "reason"
        ]
        == "host_not_allowed"
    )


def test_search_web_rate_limited_per_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(tools.WEB_SEARCH_ENV, "1")
    monkeypatch.setenv(tools.WEB_SEARCH_HOSTS_ENV, "example.com")

    def backend(query: str, hosts: frozenset[str]) -> list[dict[str, Any]]:
        return [{"url": "https://example.com/a", "title": "t", "fetched_at": "2026-10-27"}]

    ctx = make_ctx(tmp_path, backend=backend)
    for _ in range(tools.WEB_SEARCH_CALL_BUDGET):
        assert tools.execute(ctx, tools.SEARCH_WEB, {"query": "x"})["ok"] is True
    assert (
        tools.execute(ctx, tools.SEARCH_WEB, {"query": "x"})["reason"] == "web_search_rate_limited"
    )


# --------------------------------------------------------------------------- #
# 根目录：调用方没交出来的目录，工具够不着
# --------------------------------------------------------------------------- #


def test_missing_run_root_raises(tmp_path: Path) -> None:
    """审计日志就落在这个目录里：给不出来即调用方接线错误，一次都不许放行。"""
    for name in (tools.LIST_ARTIFACTS, tools.COMPUTE):
        ctx = tools.ToolContext(stage="company", roots={}, allowed=frozenset({name}))
        with pytest.raises(tools.ToolRefusal) as excinfo:
            tools.execute(ctx, name, {"stage": "company", "op": "add", "a": 1, "b": 2})
        assert excinfo.value.reason == "root_unavailable"


def test_compute_declares_no_input_root(tmp_path: Path) -> None:
    """compute 是纯算术，不声明任何输入根目录（只有审计需要运行目录）。"""
    assert tools.TOOLS[tools.COMPUTE].roots == ()
    ctx = make_ctx(tmp_path, allowed=(tools.COMPUTE,))
    assert tools.execute(ctx, tools.COMPUTE, {"op": "add", "a": 1, "b": 2})["ok"] is True


def test_turn_types_are_protocol_neutral() -> None:
    """providers 的 ToolTurn/ToolCall 是编排层唯一的形状，两个协议都得产得出。"""
    turn = providers.ToolTurn(
        text="",
        tool_calls=(providers.ToolCall(id="c1", name=tools.COMPUTE, arguments='{"a":1}'),),
    )
    assert turn.wants_tools is True
    assert providers.ToolTurn(text="正文").wants_tools is False
