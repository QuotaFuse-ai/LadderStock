"""后三段（R1-3）：valuation / risk 接三级阶梯，report 只消费前五段产物。

全部用脚本化假 ChatClient，不拨网络；`root` 指向 tmp_path，产物不落进仓库 `data/`。
用例名与 QUOT-192 验收条款一一对应（`-k zero_data_calls` 命中验收 2）。
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from ladderstock.ai import fallback
from ladderstock.data import duckdb_store, provider_registry
from ladderstock.research import artifacts, checkpoint, stages
from ladderstock.research.pipeline import ResearchPipeline

DAY = "2026-10-27"
URL_ENV = "TEST_BACK_BASE_URL"
KEY_ENV = "TEST_BACK_API_KEY"
BACK = ("valuation", "risk", "report")

#: 一级（结构化 JSON）：后三段各给一份合法返回
GOOD_JSON = {
    "valuation": json.dumps(
        {
            "narrative": ["估值：快照给出 PE(TTM) 与 PB，口径为静态快照。"],
            "highlights": ["定价口径以 PB 更稳"],
            "risks": ["快照未提供同业样本"],
        },
        ensure_ascii=False,
    ),
    "risk": json.dumps(
        {
            "narrative": ["风险：收入集中度与质押情况都需要补数据源才能判断。"],
            "risks": ["快照未提供股东质押"],
        },
        ensure_ascii=False,
    ),
    "report": json.dumps(
        {
            "narrative": ["报告：公司画像与财务段一致，估值段缺同业口径。"],
            "highlights": ["主业清晰但定价口径待核实"],
            "evidence": ["来自 upstream 的 valuation 段"],
        },
        ensure_ascii=False,
    ),
}

#: 二级：合法 JSON 被截断，从同一段返回文本里抢救
TRUNCATED_JSON = '{"narrative": [\n"估值：快照给出 PE(TTM)，口径为静态快照。"'

#: 三级（纯文本）用的返回
PLAIN_TEXT = {
    "valuation": "估值：纯文本降级叙述，快照只给了 PB，同业口径缺失。",
    "risk": "风险：纯文本降级叙述，质押与诉讼均无数据。",
    "report": "报告：纯文本降级叙述，综合前五段的定性判断。",
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
# 验收 1 + 3：后三段各自跑通三级阶梯，缺输入时报告段点名缺失段
# --------------------------------------------------------------------------- #


def test_back_stages_run_three_level_ladder(tmp_path: Path) -> None:
    """valuation 依次走「结构化 → 文本补偿 → 纯文本」三级，mode 两两可区分。"""
    levels = {
        "structured": ScriptedClient(structured=GOOD_JSON),
        "parsed": ScriptedClient(structured=TRUNCATED_JSON),
        "text": ScriptedClient(structured=""),
    }
    documents: dict[str, dict[str, Any]] = {}
    for index, (expected_mode, client) in enumerate(levels.items()):
        root = tmp_path / f"level-{index}"
        run = pipeline(root, client).run(DAY, only="valuation")
        document = artifacts.read_stage("600000", DAY, "valuation", root)
        assert document is not None, expected_mode
        documents[expected_mode] = document
        assert state_of(run, "valuation")["status"] != "failed", expected_mode
        assert run.failed == [], expected_mode

    modes = [documents[mode]["mode"] for mode in ("structured", "parsed", "text")]
    assert modes == ["structured", "parsed", "text"]
    assert documents["structured"]["ai"]["risks"] == ["快照未提供同业样本"]
    assert documents["structured"]["model"] == "fake-model"
    # 第三级：纯文本，标 degraded，整次运行退出码 9
    assert documents["text"]["status"] == "degraded"
    assert documents["text"]["degraded"] is True
    assert documents["text"]["narrative"] == [PLAIN_TEXT["valuation"]]

    # 后三段都有实际提示词，六段共用一条阶梯
    assert stages.BACK_STAGES == BACK
    prompt_dir = Path(__file__).resolve().parents[3] / "prompts" / "research"
    for stage in BACK:
        assert (prompt_dir / f"{stage}.md").is_file(), stage
        text = stages.prompt_text(stage)
        assert checkpoint.STAGE_TITLES[stage] in text, stage
        assert len(text) > 50, stage
    system, user = stages.build_messages("600000", "report", DAY, {})
    assert "只输出一个 JSON 对象" in system
    assert "段落标识：report" in user

    # `--only report` 在前五段都没跑过时点名缺失段，且不落半截产物
    root = tmp_path / "missing"
    run = pipeline(root).run(DAY, only="report")
    assert run.exit_code == 9
    assert run.failed == ["report"]
    failure = run.research_failures[0]
    assert "--only company" in failure["action"]
    assert not (artifacts.artifact_dir("600000", DAY, root) / "report.json").exists()


# --------------------------------------------------------------------------- #
# 验收 2：report 段 0 次取数
# --------------------------------------------------------------------------- #


def _install_data_layer_tripwires(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """把数据层入口换成「一调用即抛异常」的桩，返回被调用次数记录。"""
    hits: list[str] = []

    def make_stub(name: str) -> Any:
        def stub(*_args: Any, **_kwargs: Any) -> Any:
            hits.append(name)
            raise AssertionError(f"report 段触碰了数据层入口：{name}")

        return stub

    targets: list[tuple[Any, str, Any]] = [
        (provider_registry.ProviderRegistry, "fetch", provider_registry.ProviderRegistry.fetch),
        (provider_registry.ProviderRegistry, "chain", provider_registry.ProviderRegistry.chain),
        (duckdb_store, "connect", duckdb_store.connect),
        (duckdb_store, "query_dataset", duckdb_store.query_dataset),
        (duckdb_store, "dataset_rows", duckdb_store.dataset_rows),
    ]
    for module in (provider_registry, duckdb_store):
        for name, member in inspect.getmembers(module, inspect.isfunction):
            if not name.startswith("_"):
                targets.append((module, name, member))

    for owner, name, member in targets:
        if callable(member) and getattr(member, "__module__", "").startswith("ladderstock.data"):
            monkeypatch.setattr(owner, name, make_stub(f"{owner.__name__}.{name}"), raising=False)
    return hits


def test_report_makes_zero_data_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """六段全跑一遍：报告段运行时数据层桩一次都没被调用，假模型只收到一次 report 请求。"""
    hits = _install_data_layer_tripwires(monkeypatch)

    client = ScriptedClient()
    root = tmp_path / "zero"
    run = pipeline(root, client).run(DAY)

    assert run.failed == [], run.research_failures
    assert hits == []
    assert client.stages_called("report") == [True]
    assert [name for name, _json_mode in client.calls].count("report") == 1

    document = artifacts.read_stage("600000", DAY, "report", root)
    assert document is not None
    # 报告段的唯一输入 = 前五段已落盘的结构化产物
    assert list(document["facts"]["upstream"]) == list(checkpoint.REPORT_INPUTS)


# --------------------------------------------------------------------------- #
# 验收 4：provenance 字段位与上游输入固定
# --------------------------------------------------------------------------- #


def test_report_keeps_provenance_fields_and_upstream_order(tmp_path: Path) -> None:
    root = tmp_path / "provenance"
    run = pipeline(root).run(DAY)
    assert run.exit_code == 0

    document = artifacts.read_stage("600000", DAY, "report", root)
    assert document is not None
    assert set(document["provenance"]) == {"values", "sources", "unverified"}
    # R3 定稿前：只留字段不渲染，没有 sources 就一律未核实
    assert document["provenance"]["values"] == {}
    assert document["provenance"]["sources"] == []
    assert document["provenance"]["unverified"] is True
    assert list(document["facts"]["upstream"]) == list(checkpoint.REPORT_INPUTS)
    for stage in checkpoint.REPORT_INPUTS:
        upstream = document["facts"]["upstream"][stage]
        assert set(upstream) == {"title", "status", "narrative", "provenance"}, stage


# --------------------------------------------------------------------------- #
# 失败隔离：后三段失败不吞前序产物
# --------------------------------------------------------------------------- #


def test_back_stage_failure_keeps_prior_artifacts(tmp_path: Path) -> None:
    client = ScriptedClient(fail_on=("valuation",))
    root = tmp_path / "isolation"
    run = pipeline(root, client).run(DAY)
    directory = artifacts.artifact_dir("600000", DAY, root)

    assert run.exit_code == 9
    assert state_of(run, "valuation")["status"] == "failed"
    assert run.failed == ["valuation", "report"]
    assert not (directory / "valuation.json").exists()
    # 前序成果一件不少，序列不中断：risk 照常执行
    for stage in ("company", "financial", "forecast"):
        assert (directory / f"{stage}.json").is_file(), stage
        assert (directory / f"{stage}.md").is_file(), stage
    assert ("risk", True) in client.calls
    assert state_of(run, "risk")["status"] == "ok"
    assert (directory / "risk.json").is_file()


def test_risk_and_report_land_structured_products(tmp_path: Path) -> None:
    """risk / report 走一级结构化：产物顶层键集合仍是 R1-1 定稿的 15 个键。"""
    root = tmp_path / "shape"
    run = pipeline(root).run(DAY)
    assert run.exit_code == 0

    for stage in ("valuation", "risk", "report"):
        document = artifacts.read_stage("600000", DAY, stage, root)
        assert document is not None, stage
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
        }, stage
        assert document["mode"] == "structured", stage
        assert document["ai"]["narrative"] == document["narrative"], stage
        markdown = (artifacts.stage_paths("600000", DAY, stage, root)[1]).read_text(
            encoding="utf-8"
        )
        assert markdown.startswith(f"# {checkpoint.STAGE_TITLES[stage]}"), stage
