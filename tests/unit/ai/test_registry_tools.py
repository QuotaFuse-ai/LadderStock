"""QUOT-598 战略 C2：注册表工具进工具环（read_only 白名单 + 命名空间 × 本段白名单双闸）。

离线钉六件事（注入假传输，全程不触网）：

1. 过滤：只收 `read_only is True`；`None`（未声明）/ `False` 一律不收，取数/实时类
   再显式剔除（`EXCLUDED_TOOLS`，逐条钉住）；
2. 闸门：命名空间不符 / 名字不在白名单 / 编造名字 → 既有越权路径（审计 + 抛
   `UnauthorizedToolCall`，不重试）；执行器自建的第二道闸同样拒绝；
3. 不看未来：`date` / `until` / `month` 晚于分析基准日或不可解析 → 拒绝；
4. 审计：9 键一行，拒绝 `rejected=True` + 真实 reason，成功记真实结果摘要；
5. 事件：每次真实调用一条 `module_class=cog` 的 `StageSample`，拒绝发拒绝态；
6. 开关：`LDST_REGISTRY_TOOLS` 关掉时研究管线与接入前逐字一致（无注册表工具名、
   无审计、无事件）。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from ladderstock.ai import registry_tools, tools
from ladderstock.ai.tool_registry import (
    LOCAL_SOURCE,
    ToolEntry,
    ToolNamespace,
    ToolRegistry,
    ToolSnapshot,
)
from ladderstock.research.pipeline import ResearchPipeline

DAY = "2026-10-27"
CODE = "600000"
URL_ENV = "TEST_RESEARCH_BASE_URL"
KEY_ENV = "TEST_RESEARCH_API_KEY"
NS = ToolNamespace("ladderstock-board", "*")


# --------------------------------------------------------------------------- #
# 假件：条目 / 传输 / 客户端
# --------------------------------------------------------------------------- #


def entry(
    name: str,
    *,
    read_only: bool | None = True,
    source: str = LOCAL_SOURCE,
    description: str = "",
    parameters: dict[str, Any] | None = None,
) -> ToolEntry:
    return ToolEntry(
        name=name,
        source=source,
        product="test",
        description=description or f"{name} 测试工具",
        parameters=parameters or {"type": "object", "properties": {}},
        read_only=read_only,
        domain="test",
    )


class FakeTransport:
    """只记调用、回固定结果的 `provider_registry.Transport.call` 假件。"""

    def __init__(self, *, fail: Exception | None = None) -> None:
        self.calls: list[tuple[str, str, dict[str, Any], float]] = []
        self.fail = fail

    def call(
        self, peer: Any, tool: str, params: dict[str, Any], *, timeout: float
    ) -> dict[str, Any]:
        self.calls.append((peer.key, tool, dict(params), timeout))
        if self.fail is not None:
            raise self.fail
        return {"content": [{"type": "text", "text": json.dumps({"ok": True, "tool": tool})}]}


def snapshot(*entries: ToolEntry, namespace: ToolNamespace = NS) -> ToolSnapshot:
    rows = tuple(entries)
    return ToolSnapshot(
        namespace=namespace,
        generated_at="2026-11-14T08:00:00+00:00",
        sources=(),
        tools=rows,
        total=len(rows),
    )


def run_dir(tmp_path: Path) -> Path:
    path = tmp_path / "run"
    path.mkdir(parents=True, exist_ok=True)
    return path


def ctx(
    tmp_path: Path,
    *,
    allowed: tuple[str, ...],
    registry: registry_tools.RegistryTools | None = None,
    stage: str = "report",
) -> tools.ToolContext:
    return tools.ToolContext(
        stage=stage,
        roots={tools.RUN_ROOT: run_dir(tmp_path)},
        allowed=frozenset(allowed),
        registry=registry,
    )


AUDIT_KEYS = {
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


def audit(tmp_path: Path) -> list[dict[str, Any]]:
    return tools.audit_records(run_dir(tmp_path) / tools.AUDIT_FILENAME)


class RecordingBus:
    def __init__(self) -> None:
        self.samples: list[Any] = []

    def publish_stage(self, sample: Any) -> None:
        self.samples.append(sample)


# --------------------------------------------------------------------------- #
# 1. 过滤：只认明确只读
# --------------------------------------------------------------------------- #


def test_callable_entries_requires_read_only_true() -> None:
    rows = [
        entry("keep_me", read_only=True),
        entry("unknown", read_only=None),
        entry("write", read_only=False),
        entry("keep_me", read_only=False, description="重复名先到先得"),
    ]
    assert [item.name for item in registry_tools.callable_entries(rows)] == ["keep_me"]


def test_realtime_and_fetch_tools_are_excluded_even_when_read_only() -> None:
    assert registry_tools.EXCLUDED_TOOLS == frozenset(
        {"ladder_get_watch", "news_stream", "event_calendar", "proxyfleet_status"}
    )
    rows = [
        entry("ladder_get_review", read_only=True),
        *(entry(name, read_only=True) for name in sorted(registry_tools.EXCLUDED_TOOLS)),
    ]
    assert [item.name for item in registry_tools.callable_entries(rows)] == [
        "ladder_get_review"
    ]


def test_registry_descriptors_match_tools_shape() -> None:
    rows = registry_tools.callable_entries(
        [entry("ladder_get_review", parameters={"type": "object", "properties": {"date": {}}})]
    )
    assert registry_tools.registry_descriptors(rows) == [
        {
            "type": "function",
            "function": {
                "name": "ladder_get_review",
                "description": "ladder_get_review 测试工具",
                "parameters": {"type": "object", "properties": {"date": {}}},
            },
        }
    ]


# --------------------------------------------------------------------------- #
# 2. 命名空间 + 双闸
# --------------------------------------------------------------------------- #


def test_build_for_stage_rejects_foreign_namespace() -> None:
    other = snapshot(entry("ladder_get_review"), namespace=ToolNamespace("someone-else", "*"))
    with pytest.raises(tools.ToolRefusal) as caught:
        registry_tools.build_for_stage(
            other,
            namespace=NS,
            stage="report",
            base_day=DAY,
            transport=FakeTransport(),
            peers={},
            data_root=None,
        )
    assert caught.value.reason == "namespace_mismatch"


def test_namespace_from_env_default_and_malformed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(registry_tools.REGISTRY_NAMESPACE_ENV, raising=False)
    assert registry_tools.namespace_from_env() == ToolNamespace(
        registry_tools.DEFAULT_AGENT_ID, registry_tools.DEFAULT_KB_SCOPE
    )
    monkeypatch.setenv(registry_tools.REGISTRY_NAMESPACE_ENV, "board/ai")
    assert registry_tools.namespace_from_env() == ToolNamespace("board", "ai")
    monkeypatch.setenv(registry_tools.REGISTRY_NAMESPACE_ENV, "no-slash")
    with pytest.raises(tools.ToolRefusal):
        registry_tools.namespace_from_env()


def test_fabricated_name_raises_unauthorized_with_audit_and_no_event(tmp_path: Path) -> None:
    bus = RecordingBus()
    registry = registry_tools.build_for_stage(
        snapshot(entry("ladder_get_review")),
        namespace=NS,
        stage="report",
        base_day=DAY,
        transport=FakeTransport(),
        peers={},
        data_root=None,
        on_call=lambda record: registry_tools.publish_sample(
            bus,
            registry_tools.sample_for(
                stage=str(record["stage"]),
                tool=str(record["tool"]),
                rejected=bool(record["rejected"]),
                reason=record.get("reason"),
                detail=str(record.get("detail") or ""),
            ),
        ),
    )
    context = ctx(tmp_path, allowed=sorted(registry.names()), registry=registry)

    with pytest.raises(tools.UnauthorizedToolCall):
        tools.execute(context, "ladder_delete_everything", {})

    lines = audit(tmp_path)
    assert len(lines) == 1
    assert set(lines[0]) == AUDIT_KEYS
    assert lines[0]["tool"] == "ladder_delete_everything"
    assert lines[0]["rejected"] is True
    assert lines[0]["reason"] == "unauthorized_tool"
    assert lines[0]["result"] is None
    # 越权调用不是「真实调用」：不发事件
    assert bus.samples == []


def test_second_gate_rejects_names_outside_the_executor(tmp_path: Path) -> None:
    registry = registry_tools.build_for_stage(
        snapshot(entry("ladder_get_review")),
        namespace=NS,
        stage="report",
        base_day=DAY,
        transport=FakeTransport(),
        peers={},
        data_root=None,
    )
    # 上游白名单误放了别的名字：执行器仍只认自己的表，拒因回喂，不真实调用
    context = ctx(tmp_path, allowed=("ladder_get_review", "other_tool"), registry=registry)
    result = tools.execute(context, "other_tool", {})

    assert result["ok"] is False
    assert result["reason"] == "unauthorized_tool"
    lines = audit(tmp_path)
    assert len(lines) == 1 and lines[0]["rejected"] is True


# --------------------------------------------------------------------------- #
# 3. 不看未来
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("key", ["date", "until", "month"])
def test_future_argument_is_refused(key: str) -> None:
    with pytest.raises(tools.ToolRefusal) as caught:
        registry_tools.check_not_future(
            {key: "2026-10-28"}, base_day=DAY, today=date(2026, 11, 14)
        )
    assert caught.value.reason == "future_date"


def test_base_day_beyond_today_is_clamped_to_today() -> None:
    with pytest.raises(tools.ToolRefusal) as caught:
        registry_tools.check_not_future(
            {"date": "2026-11-15"}, base_day="2026-12-01", today=date(2026, 11, 14)
        )
    assert caught.value.reason == "future_date"


def test_past_and_equal_dates_pass() -> None:
    registry_tools.check_not_future(
        {"date": DAY, "until": "2026-10-01"}, base_day=DAY, today=date(2026, 11, 14)
    )


@pytest.mark.parametrize(
    ("arguments", "reason"),
    [({"date": "2026/10/27"}, "bad_argument"), ({"month": "nope"}, "bad_argument")],
)
def test_unparsable_dates_are_refused(arguments: dict[str, Any], reason: str) -> None:
    with pytest.raises(tools.ToolRefusal) as caught:
        registry_tools.check_not_future(arguments, base_day=DAY, today=date(2026, 11, 14))
    assert caught.value.reason == reason


def test_future_argument_is_audited_and_never_reaches_transport(tmp_path: Path) -> None:
    tunnel = FakeTransport()
    registry = registry_tools.build_for_stage(
        snapshot(entry("ladder_get_validation")),
        namespace=NS,
        stage="valuation",
        base_day=DAY,
        transport=tunnel,
        peers={"TickHub-CN": type("Peer", (), {"key": "TickHub-CN"})()},
        data_root=None,
    )
    context = ctx(
        tmp_path, allowed=("ladder_get_validation",), registry=registry, stage="valuation"
    )
    result = tools.execute(context, "ladder_get_validation", {"date": "2026-11-20"})

    assert result["ok"] is False
    assert result["reason"] == "future_date"
    assert tunnel.calls == []
    lines = audit(tmp_path)
    assert len(lines) == 1 and lines[0]["reason"] == "future_date"
    assert lines[0]["rejected"] is True


# --------------------------------------------------------------------------- #
# 4/5. 执行 + 审计 + 事件（假传输，完全不触网）
# --------------------------------------------------------------------------- #


def test_peer_tool_executes_through_injected_transport(tmp_path: Path) -> None:
    tunnel = FakeTransport()
    peer = type("Peer", (), {"key": "tickhub-cn"})()
    bus = RecordingBus()
    registry = registry_tools.build_for_stage(
        snapshot(entry("tickhub_candles", source="tickhub-cn")),
        namespace=NS,
        stage="financial",
        base_day=DAY,
        transport=tunnel,
        peers={"tickhub-cn": peer},
        data_root=None,
        on_call=lambda record: registry_tools.publish_sample(
            bus,
            registry_tools.sample_for(
                stage=str(record["stage"]),
                tool=str(record["tool"]),
                rejected=bool(record["rejected"]),
                reason=record.get("reason"),
                detail=str(record.get("detail") or ""),
            ),
        ),
    )
    context = ctx(tmp_path, allowed=("tickhub_candles",), registry=registry, stage="financial")
    result = tools.execute(context, "tickhub_candles", {"date": DAY})

    assert result["ok"] is True
    assert "tickhub_candles" in result["content"]
    assert tunnel.calls and tunnel.calls[0][0] == "tickhub-cn"
    assert tunnel.calls[0][1] == "tickhub_candles"
    assert tunnel.calls[0][3] == registry.timeout

    lines = audit(tmp_path)
    assert len(lines) == 1 and set(lines[0]) == AUDIT_KEYS
    assert lines[0]["rejected"] is False and lines[0]["reason"] is None
    assert lines[0]["result"]

    assert len(bus.samples) == 1
    sample = bus.samples[0]
    assert sample.module_class == "cog"
    assert sample.health == "ok"
    assert sample.stage == "financial/tickhub_candles"


def test_peer_tool_transport_failure_is_fed_back_as_rejection(tmp_path: Path) -> None:
    bus = RecordingBus()
    registry = registry_tools.build_for_stage(
        snapshot(entry("tickhub_candles", source="tickhub-cn")),
        namespace=NS,
        stage="financial",
        base_day=DAY,
        transport=FakeTransport(fail=ConnectionError("peer 未启动")),
        peers={"tickhub-cn": type("Peer", (), {"key": "tickhub-cn"})()},
        data_root=None,
        on_call=lambda record: registry_tools.publish_sample(
            bus,
            registry_tools.sample_for(
                stage=str(record["stage"]),
                tool=str(record["tool"]),
                rejected=bool(record["rejected"]),
                reason=record.get("reason"),
                detail=str(record.get("detail") or ""),
            ),
        ),
    )
    context = ctx(tmp_path, allowed=("tickhub_candles",), registry=registry, stage="financial")
    result = tools.execute(context, "tickhub_candles", {})

    assert result["ok"] is False
    assert result["reason"] == "source_unavailable"
    assert "peer 未启动" in result["content"]
    lines = audit(tmp_path)
    assert len(lines) == 1 and lines[0]["rejected"] is True
    # 真实发生了一次（失败的）调用：拒绝态如实发出，绝不伪造成成功
    assert len(bus.samples) == 1 and bus.samples[0].health != "ok"


def test_unknown_peer_source_is_rejected(tmp_path: Path) -> None:
    registry = registry_tools.build_for_stage(
        snapshot(entry("tickhub_candles", source="tickhub-cn")),
        namespace=NS,
        stage="financial",
        base_day=DAY,
        transport=FakeTransport(),
        peers={},
        data_root=None,
    )
    context = ctx(tmp_path, allowed=("tickhub_candles",), registry=registry)
    result = tools.execute(context, "tickhub_candles", {})
    assert result["ok"] is False
    assert result["reason"] == "source_unavailable"


def test_local_tool_runs_in_process(tmp_path: Path) -> None:
    registry = registry_tools.build_for_stage(
        snapshot(entry("ladder_get_review")),
        namespace=NS,
        stage="report",
        base_day=DAY,
        transport=FakeTransport(),
        peers={},
        data_root=tmp_path,
    )
    context = ctx(tmp_path, allowed=("ladder_get_review",), registry=registry)
    result = tools.execute(context, "ladder_get_review", {"date": DAY})

    assert result["ok"] is True
    envelope = json.loads(result["content"])
    assert envelope["domain"] == "review"
    assert envelope["payload"]["data_source_status"] == "missing"


def test_textify_truncates_long_output() -> None:
    text = registry_tools._textify("x" * (tools.MAX_OUTPUT_CHARS + 50))
    assert len(text) <= tools.MAX_OUTPUT_CHARS + 8
    assert text.endswith("已截断）")


def test_sample_for_is_truthful() -> None:
    ok = registry_tools.sample_for(
        stage="report", tool="ladder_get_review", rejected=False, reason=None, detail="ok"
    )
    rejected = registry_tools.sample_for(
        stage="report",
        tool="ladder_get_review",
        rejected=True,
        reason="future_date",
        detail="晚于基准日",
    )
    assert ok.module_class == "cog" and rejected.module_class == "cog"
    assert ok.health != rejected.health
    assert "被拒" in rejected.reason and "成功" in ok.reason
    # 没有总线：静默跳过，不炸
    registry_tools.publish_sample(None, ok)


def test_load_snapshot_swallows_registry_errors() -> None:
    class Broken:
        def load(self, _ns: Any) -> Any:
            raise RuntimeError("boom")

        def discover(self, _ns: Any) -> Any:
            raise RuntimeError("boom")

    assert registry_tools.load_snapshot(Broken(), NS) is None


# --------------------------------------------------------------------------- #
# 6. 研究管线接线：开关关 ⇒ 零差异；开关开 ⇒ 双闸 + 审计 + 事件
# --------------------------------------------------------------------------- #


class ToolClient:
    """按脚本回工具调用的假客户端（与 `test_pipeline_tools.py` 同形）。"""

    def __init__(self, script: dict[str, list[dict[str, Any]]]) -> None:
        self.pending = {stage: list(turns) for stage, turns in script.items()}
        self.requested: list[tuple[str, str]] = []
        self.seen_tools: list[tuple[str, tuple[str, ...]]] = []
        self.plain_calls = 0

    @property
    def models(self) -> list[str]:
        return ["fake-model"]

    def complete(self, model: str, system: str, user: str, *, json_mode: bool) -> str:
        del model, user
        self.plain_calls += 1
        stage = "report"
        if not json_mode:
            return "纯文本"
        return json.dumps({"narrative": [f"{stage} 段叙述"]}, ensure_ascii=False)

    def complete_with_tools(
        self,
        model: str,
        messages: Any,
        tools_doc: Any,
        *,
        json_mode: bool,
    ) -> Any:
        del model, json_mode
        from ladderstock.ai import providers

        user = next(msg["content"] for msg in messages if msg["role"] == "user")
        stage = next(
            line.split("：", 1)[1]
            for line in user.splitlines()
            if line.startswith("段落标识：")
        )
        self.seen_tools.append((stage, tuple(item["function"]["name"] for item in tools_doc)))
        queue = self.pending.get(stage) or []
        if not queue:
            return providers.ToolTurn(
                text=json.dumps({"narrative": [f"{stage} 段叙述"]}, ensure_ascii=False)
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


def write_snapshot(root: Path, rows: list[ToolEntry]) -> None:
    ToolRegistry(root=root, sources={"peers": {}, "sources": []}).save(snapshot(*rows))


def run_pipeline(tmp_path: Path, client: ToolClient, **kwargs: Any) -> Any:
    from ladderstock.ai import fallback

    config = fallback.AiConfig(
        default_model="fake-model",
        fallback_chain=("fake-backup",),
        base_url_env=URL_ENV,
        api_key_env=KEY_ENV,
        structured_output="prefer",
        on_all_failed="degrade_to_text",
        sections={},
    )
    pipeline = ResearchPipeline(
        CODE,
        config=config,
        client=client,
        root=tmp_path,
        registry_root=tmp_path,
        event_bus=kwargs.pop("event_bus", None),
    )
    return pipeline.run(DAY, **kwargs)


@pytest.fixture(autouse=True)
def _configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(URL_ENV, "http://127.0.0.1:1/v1")
    monkeypatch.setenv(KEY_ENV, "test-key-not-real")
    monkeypatch.delenv(registry_tools.REGISTRY_TOOLS_ENV, raising=False)
    monkeypatch.delenv(registry_tools.REGISTRY_NAMESPACE_ENV, raising=False)
    monkeypatch.delenv(tools.WEB_SEARCH_ENV, raising=False)


def _artifact_dir(tmp_path: Path) -> Path:
    from ladderstock.research import artifacts

    return artifacts.artifact_dir(CODE, DAY, tmp_path)


def pipeline_audit(tmp_path: Path) -> list[dict[str, Any]]:
    return tools.audit_records(_artifact_dir(tmp_path) / tools.AUDIT_FILENAME)


def test_flag_off_keeps_the_pipeline_identical(tmp_path: Path) -> None:
    write_snapshot(tmp_path, [entry("ladder_get_review")])
    bus = RecordingBus()
    client = ToolClient({})
    run_pipeline(tmp_path, client, only="financial", event_bus=bus)

    names = next(names for _stage, names in client.seen_tools)
    assert "ladder_get_review" not in names
    assert not (_artifact_dir(tmp_path) / tools.AUDIT_FILENAME).exists()
    assert bus.samples == []


def test_flag_on_exposes_read_only_registry_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(registry_tools.REGISTRY_TOOLS_ENV, "1")
    write_snapshot(
        tmp_path,
        [
            entry("ladder_get_review"),
            entry("ladder_get_watch"),  # 实时：即使只读也剔除
            entry("unknown_hint", read_only=None),
            entry("ladder_start_research", read_only=False),
        ],
    )
    bus = RecordingBus()
    client = ToolClient(
        {"financial": [{"calls": [{"name": "ladder_get_review", "args": {"date": DAY}}]}]}
    )
    run = run_pipeline(tmp_path, client, only="financial", event_bus=bus)

    assert run.failed == []
    names = next(names for _stage, names in client.seen_tools)
    assert "ladder_get_review" in names
    for dropped in ("ladder_get_watch", "unknown_hint", "ladder_start_research"):
        assert dropped not in names

    lines = pipeline_audit(tmp_path)
    assert len(lines) == 1 and set(lines[0]) == AUDIT_KEYS
    assert lines[0]["tool"] == "ladder_get_review" and lines[0]["rejected"] is False
    assert len(bus.samples) == 1 and bus.samples[0].module_class == "cog"
    assert bus.samples[0].health == "ok"


def test_flag_on_fabricated_name_fails_the_stage_on_first_attempt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(registry_tools.REGISTRY_TOOLS_ENV, "1")
    write_snapshot(tmp_path, [entry("ladder_get_review")])
    client = ToolClient(
        {"financial": [{"calls": [{"name": "ladder_delete_everything", "args": {}}]}]}
    )
    run = run_pipeline(tmp_path, client, only="financial")

    assert run.failed == ["financial"]
    assert client.requested == [("financial", "ladder_delete_everything")]
    lines = pipeline_audit(tmp_path)
    assert len(lines) == 1 and lines[0]["reason"] == "unauthorized_tool"


def test_flag_on_no_calls_emits_no_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(registry_tools.REGISTRY_TOOLS_ENV, "1")
    write_snapshot(tmp_path, [entry("ladder_get_review")])
    bus = RecordingBus()
    client = ToolClient({})
    run = run_pipeline(tmp_path, client, only="financial", event_bus=bus)

    assert run.failed == []
    assert not (_artifact_dir(tmp_path) / tools.AUDIT_FILENAME).exists()
    assert bus.samples == []
