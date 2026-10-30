"""R3 管线接线（QUOT-141 要求 2 + 3）：指标与出处成对进上下文、落盘、渲染、报告段并集。

全部用脚本化假 ChatClient，不拨网络；`root` 指向 `tmp_path`。
对应验收：「数值字段无出处不得进 verified」在产物里可见，「正常报告 100% 数字被引用或标〔未核实〕」
由 `citation.audit_text` 逐段复核。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ladderstock.ai import fallback
from ladderstock.core.boundary import Provenance
from ladderstock.research import artifacts, audit, checkpoint, citation
from ladderstock.research.pipeline import ResearchPipeline

CODE = "600000"
DAY = "2026-10-27"
STAGES = checkpoint.STAGES
URL_ENV = "TEST_PROV_BASE_URL"
KEY_ENV = "TEST_PROV_API_KEY"


def record(metric: str, value: float, *, unit: str = "倍", verified: bool = True) -> Provenance:
    return Provenance(
        value=value,
        unit=unit,
        source="tickhub-cn",
        endpoint=f"{metric}_endpoint",
        fetched_at="2026-10-31T09:00:00+08:00",
        as_of="2026-09-30",
        url="http://127.0.0.1:8000/mcp",
        verified=verified,
        metric=metric,
    )


class FakeClient:
    """按段返回脚本文本，并把每次调用的 system/user 留下来（要求 2 要看上下文里有什么）。"""

    def __init__(self, narratives: dict[str, str] | None = None) -> None:
        self.narratives = narratives or {}
        self.calls: list[tuple[str, str, str]] = []

    def complete(self, model: str, system: str, user: str, *, json_mode: bool) -> str:
        stage = next(
            line.split("：", 1)[1] for line in user.splitlines() if line.startswith("段落标识：")
        )
        self.calls.append((stage, model, user))
        text = self.narratives.get(stage, f"{checkpoint.STAGE_TITLES[stage]}：只引用给定快照。")
        payload = {"narrative": [text], "highlights": ["可复核的定性结论"]}
        return json.dumps(payload, ensure_ascii=False)

    def user_text(self, stage: str) -> str:
        return next(user for name, _model, user in self.calls if name == stage)


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


def document(root: Path, stage: str) -> dict[str, Any]:
    json_path, _md = artifacts.stage_paths(CODE, DAY, stage, root)
    return json.loads(json_path.read_text(encoding="utf-8"))


def markdown(root: Path, stage: str) -> str:
    _json_path, md_path = artifacts.stage_paths(CODE, DAY, stage, root)
    return md_path.read_text(encoding="utf-8")


@pytest.fixture()
def configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(URL_ENV, "http://127.0.0.1:1/v1")
    monkeypatch.setenv(KEY_ENV, "test-key-not-real")


# --------------------------------------------------------------------------- #
# 1. 出处落进结构化件与人读件
# --------------------------------------------------------------------------- #


def test_provenance_backed_metric_lands_in_both_artifacts(
    tmp_path: Path, configured: None
) -> None:
    root = tmp_path / "backed"
    client = FakeClient({"valuation": "市盈率 18.4 倍，市净率 2.1 倍。"})
    run = pipeline(root, client).run(
        DAY,
        facts={"valuation": {"pe": 18.4, "pb": 2.1}},
        provenance={"valuation": {"pe": record("pe", 18.4)}},
    )
    assert run.exit_code == 0, run.research_failures

    slot = document(root, "valuation")["provenance"]
    assert set(slot) == set(artifacts.PROVENANCE_KEYS)
    assert [item["id"] for item in slot["sources"]] == ["S1"]
    assert slot["sources"][0]["endpoint"] == "pe_endpoint"
    assert slot["sources"][0]["as_of"] == "2026-09-30"
    assert slot["sources"][0]["params"] == {"metric": "pe", "stage": "valuation"}
    assert slot["values"] == {"pe": {"sources": ["S1"], "unverified": False}}
    # pb 没有出处 → 整件仍是未核实（要求 2 的落盘形态）
    assert "pb" not in slot["values"]
    assert slot["unverified"] is True

    text = markdown(root, "valuation")
    assert "18.4 倍[1]" in text, "有出处的数字要挂角标"
    assert "2.1" in text and citation.UNVERIFIED_MARK in text
    assert "## 来源" in text
    assert "[1] tickhub-cn · pe_endpoint · 2026-09-30 · http://127.0.0.1:8000/mcp" in text


def test_slot_is_deterministic_for_the_same_inputs(tmp_path: Path, configured: None) -> None:
    """同一份输入渲染两次必须完全一致，否则 `--check` 与产物 diff 都不可信。"""
    root = tmp_path / "determinism"
    facts = {
        "valuation": {"pb": 2.1, "pe": 18.4, "roe": 9.3},
    }
    records = {
        "valuation": {
            "pe": record("pe", 18.4),
            "roe": record("roe", 9.3, unit="%", verified=False),
        }
    }
    pipeline(root).run(DAY, facts=facts, provenance=records)
    first = json.dumps(document(root, "valuation")["provenance"], sort_keys=True)

    other = tmp_path / "determinism2"
    pipeline(other).run(DAY, facts=facts, provenance=records)
    second = json.dumps(document(other, "valuation")["provenance"], sort_keys=True)
    assert first == second
    assert [item["id"] for item in document(root, "valuation")["provenance"]["sources"]] == [
        "S1",
        "S2",
    ]
    assert document(root, "valuation")["provenance"]["values"]["pe"]["unverified"] is False


def test_metrics_and_provenance_enter_the_ai_context_together(
    tmp_path: Path, configured: None
) -> None:
    """要求 2 的字面意思：指标进上下文时同时带出处，模型才有得可引。"""
    root = tmp_path / "context"
    client = FakeClient({"valuation": "市盈率 18.4 倍。"})
    pipeline(root, client).run(
        DAY,
        facts={"valuation": {"pe": 18.4}},
        provenance={"valuation": {"pe": record("pe", 18.4)}},
    )
    user = client.user_text("valuation")
    assert '"pe": 18.4' in user
    assert "数字出处（只读，程序写入）" in user
    assert '"endpoint": "pe_endpoint"' in user
    assert '"as_of": "2026-09-30"' in user

    # 没有出处的段：明说「本段没有任何引用来源」，不留空对象让模型猜
    bare = FakeClient()
    pipeline(tmp_path / "bare", bare).run(DAY)
    assert "本段没有任何引用来源" in bare.user_text("company")


# --------------------------------------------------------------------------- #
# 2. 边界违规只炸本段
# --------------------------------------------------------------------------- #


def test_forged_record_fails_only_that_stage(tmp_path: Path, configured: None) -> None:
    root = tmp_path / "forged"
    client = FakeClient()
    run = pipeline(root, client).run(
        DAY,
        facts={"risk": {"drawdown": 12.5}, "valuation": {"pe": 18.4}},
        provenance={"risk": {"drawdown": record("drawdown", 99.9, unit="%")}},
    )
    assert run.exit_code == 9
    status = {item["name"]: str(item["status"]) for item in run.stages}
    assert status["risk"] == "failed" and status["report"] == "failed"
    assert status["valuation"] == "ok"
    # 违规段与缺它的报告段都不发模型调用（数字都不可信了，叙述不必生成）
    assert [name for name, _model, _user in client.calls] == [
        stage for stage in STAGES if stage not in ("risk", "report")
    ]
    state = checkpoint.stage_state(checkpoint.load(CODE, DAY, root), "risk")
    assert any("不一致" in str(note) for note in state["notes"]), state["notes"]
    directory = artifacts.artifact_dir(CODE, DAY, root)
    assert not (directory / "risk.json").exists(), "违规段不落半截产物"


# --------------------------------------------------------------------------- #
# 3. 报告段：出处并集，指回原段，不复制数值
# --------------------------------------------------------------------------- #


def test_report_stage_unifies_upstream_sources(tmp_path: Path, configured: None) -> None:
    root = tmp_path / "union"
    client = FakeClient(
        {
            "financial": "营收增速 37.2%。",
            "valuation": "市盈率 18.4 倍。",
            "report": "营收增速 37.2%，市盈率 18.4 倍。",
        }
    )
    run = pipeline(root, client).run(
        DAY,
        facts={"financial": {"revenue_yoy": 37.2}, "valuation": {"pe": 18.4}},
        provenance={
            "financial": {"revenue_yoy": record("revenue_yoy", 37.2, unit="%")},
            "valuation": {"pe": record("pe", 18.4)},
        },
    )
    assert run.exit_code == 0, run.research_failures

    report = document(root, "report")
    slot = report["provenance"]
    assert slot["values"] == {}, "数值只有一份，落在各段 facts（DEC-024）"
    assert set(report["facts"]) == {"upstream", "ai"}, "报告段不复制上游指标"
    assert len(slot["sources"]) == 2
    origins = {
        (item["params"]["stage"], item["params"]["upstream_id"]) for item in slot["sources"]
    }
    assert origins == {("financial", "S1"), ("valuation", "S1")}

    text = markdown(root, "report")
    assert "37.2%[1]" in text and "18.4 倍[2]" in text, "报告段的角标要能点回并集里的来源"
    assert slot["unverified"] is False


def test_report_inherits_unverified_when_any_upstream_is_unbacked(
    tmp_path: Path, configured: None
) -> None:
    root = tmp_path / "partial"
    pipeline(root).run(
        DAY,
        facts={"valuation": {"pe": 18.4, "pb": 2.1}},
        provenance={"valuation": {"pe": record("pe", 18.4)}},
    )
    slot = document(root, "report")["provenance"]
    assert slot["unverified"] is True, "有一段没出处，整份报告就不许声称已核实"


# --------------------------------------------------------------------------- #
# 4. 全段复核：每个数字要么有角标，要么标未核实
# --------------------------------------------------------------------------- #


def test_every_stage_passes_the_citation_gate(tmp_path: Path, configured: None) -> None:
    root = tmp_path / "gate"
    client = FakeClient(
        {
            "valuation": "市盈率 18.4 倍，市净率 2.1 倍。",
            "report": "市盈率 18.4 倍，结论日期 2026-10-27。",
        }
    )
    run = pipeline(root, client).run(
        DAY,
        facts={"valuation": {"pe": 18.4, "pb": 2.1}},
        provenance={"valuation": {"pe": record("pe", 18.4)}},
    )
    assert run.exit_code == 0, run.research_failures
    for stage in STAGES:
        result = audit.audit_stage(CODE, DAY, stage=stage, root=root)
        assert result["status"] == "audited", stage
        assert result["ok"] is True, f"{stage}: {result['findings']}"


def test_unconfigured_run_still_produces_a_clean_slot(tmp_path: Path) -> None:
    """非回归：不注入任何指标（R1 骨架的老用法）时，出处件仍是「空来源 + 一律未核实」。"""
    root = tmp_path / "empty"
    run = pipeline(root).run(DAY)
    assert run.exit_code == 9 and run.degraded is True
    for stage in STAGES:
        slot = document(root, stage)["provenance"]
        assert slot == artifacts.empty_slot()
        assert "（本段无引用来源" in markdown(root, stage)
        assert audit.audit_stage(CODE, DAY, stage=stage, root=root)["ok"] is True
