"""F7 多空对照：归一化、质量门控、块装配。

三条口径锁在这里：
1. 角色输出失败时**不出表**——门控必须拦下「多头 0 条 / 空头 0 条」的报告，
   而不是把空数组渲染成「两边都没话说」；
2. 达标时块必须过 `answer_blocks.build_block` 的契约（结论 / 表格 / 指标 / 出处），
   翻转条件挂到 `conclusion.falsifier`（F5 已有字段，不新开块类型）；
3. 跑合议的子进程可注入，超时/非零退出都只是「没跑成」，不抛异常。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ladderstock.research import debate


def _summary(**overrides) -> dict:
    base = {
        "run_id": "2026-11-20-600519-abcd1234",
        "code": "600519",
        "date": "2026-11-20",
        "generated_at": "2026-11-20T07:00:00+00:00",
        "rating": "buy",
        "action": "买入观察",
        "horizon": "20d",
        "confidence": 0.62,
        "partial": False,
        "low_quality": False,
        "quality_score": 86.0,
        "quality_threshold": 70.0,
        "skipped": [],
    }
    base.update(overrides)
    return base


def _state(**overrides) -> dict:
    base = {
        "debate": {
            "bull_bear": {
                "arguments": [
                    {
                        "side": "bull",
                        "points": ["批价止跌，渠道库存回到一个月以内", "估值处在近五年低位"],
                        "rebuttals": ["空头说的需求下滑已被批价企稳证伪"],
                        "confidence": 0.7,
                        "partial": False,
                    },
                    {
                        "side": "bear",
                        "points": ["动销增速转负，报表端还没体现", "渠道利润被压，压货意愿不足"],
                        "rebuttals": ["多头把批价反弹当成需求反弹，样本只有两周"],
                        "confidence": 0.55,
                        "partial": False,
                    },
                ]
            }
        },
        "decision": {
            "rating": "buy",
            "risks": ["若批价重新跌破前低，或三季报预收账款环比转负，本条结论失效"],
        },
        "trade": {"risks": ["若动销连续两个月负增长，估值修复逻辑作废"]},
        "research": {"unresolved": ["直营渠道占比变化需要下一份季报确认"]},
        "quality": {"low_quality": False, "partial": False},
        "analysts": [{"role": "fundamentals"}, {"role": "news"}],
    }
    base.update(overrides)
    return base


def _payload(*, summary: dict | None = None, state: dict | None = None) -> dict:
    return {
        "ok": True,
        "run_id": "2026-11-20-600519-abcd1234",
        "summary": summary if summary is not None else _summary(),
        "state": state if state is not None else _state(),
        "markdown": "",
    }


def test_gate_blocks_report_with_empty_points() -> None:
    """真实历史报告的形状：角色输出全失败 → 必须拦下，并说清为什么。"""
    summary = _summary(
        rating="hold",
        action="持有",
        confidence=0.3,
        partial=True,
        low_quality=True,
        quality_score=60.0,
        skipped=[{"role": "bull", "reason": "结构化输出校验失败"}] * 6,
    )
    state = {
        "debate": {"bull_bear": {"arguments": []}},
        "decision": {"rating": "hold", "risks": []},
        "trade": {},
        "research": {"unresolved": []},
    }
    normalized = debate.normalize({"summary": summary, "state": state})
    gate = debate.assess(normalized)

    assert gate["passed"] is False
    reasons = "；".join(gate["reasons"])
    assert "质量分低于阈值" in reasons
    assert "多头有效论点不足" in reasons
    assert "空头有效论点不足" in reasons
    assert "没有翻转条件" in reasons
    assert gate["failed_roles"][0]["role"] == "bull"
    # 不达标时只出降级提示 + 指标，绝不出空表
    blocks = debate.to_blocks(normalized, gate)
    kinds = [block["type"] for block in blocks]
    assert kinds == ["notice", "metric"]
    assert "table" not in kinds


def test_gate_passes_and_blocks_meet_contract() -> None:
    normalized = debate.normalize(_payload())
    gate = debate.assess(normalized)

    assert gate["passed"] is True, gate["reasons"]
    assert gate["bull_points"] == 2 and gate["bear_points"] == 2

    blocks = debate.to_blocks(normalized, gate, fetched_at="2026-11-20T15:00:00+08:00")
    kinds = [block["type"] for block in blocks]
    assert kinds == ["conclusion", "table", "metric", "citation"]
    # conclusion 带 F5 的翻转条件；text 不含数字（红线 4）
    conclusion = blocks[0]["payload"]
    assert "跌破" in conclusion["falsifier"]
    assert conclusion["tone"] == "positive"
    # 表格按「立场 | 要点 | 来源角色」三列，多空两方都在
    table = blocks[1]["payload"]
    assert table["columns"] == ["立场", "要点", "来源角色"]
    roles = {row[0] for row in table["rows"]}
    assert {"多头", "空头"} <= roles
    # 引用必须能落到报告 run_id
    assert normalized["run_id"] in blocks[3]["payload"]["items"][0]["name"]


@pytest.mark.parametrize(
    ("quality", "low_quality", "passed"),
    [(70.0, False, True), (69.9, False, False), (90.0, True, False)],
)
def test_threshold_boundary(quality: float, low_quality: bool, passed: bool) -> None:
    normalized = debate.normalize(_payload(summary=_summary(quality_score=quality, low_quality=low_quality)))
    assert debate.assess(normalized)["passed"] is passed


def test_run_debate_reports_timeout_without_raising(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    cli = tmp_path / "venv" / "Scripts"
    cli.mkdir(parents=True)
    (cli / "ssenate.exe").write_text("", encoding="utf-8")

    def fake_runner(cmd, *, cwd, env, timeout):
        return {
            "ok": False,
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "elapsed": 1.2,
            "error": f"超时（>{timeout}s）",
        }

    outcome = debate.run_debate(
        "600519", "2026-11-20", root=tmp_path, timeout=5, runner=fake_runner
    )

    assert outcome["ok"] is False
    assert "超时" in outcome["reason"]
    assert outcome["date"] == "2026-11-20"


def test_list_and_latest_report(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    for run_id, mtime in (("2026-11-17-600519-aaaa", 100), ("2026-11-20-600519-bbbb", 200)):
        folder = reports / run_id
        folder.mkdir(parents=True)
        (folder / "summary.json").write_text(
            json.dumps({"run_id": run_id, "code": "600519", "date": run_id[:10]}), encoding="utf-8"
        )
        import os

        os.utime(folder / "summary.json", (mtime, mtime))

    items = debate.list_reports(tmp_path, limit=10)
    assert [item["run_id"] for item in items] == [
        "2026-11-20-600519-bbbb",
        "2026-11-17-600519-aaaa",
    ]
    assert debate.latest_report("600519", None, tmp_path).name == "2026-11-20-600519-bbbb"
    assert debate.latest_report("600519", "2026-11-17", tmp_path).name == "2026-11-17-600519-aaaa"
    assert debate.latest_report("000001", None, tmp_path) is None


def test_save_result_is_atomic_and_idempotent(tmp_path: Path) -> None:
    payload = {"run_id": "2026-11-20-600519-abcd1234", "date": "2026-11-20", "gate": {"passed": True}}
    first = debate.save_result(tmp_path, payload)
    assert first is not None and first.is_file()
    assert json.loads(first.read_text(encoding="utf-8"))["gate"]["passed"] is True
    second = debate.save_result(tmp_path, payload)
    assert second == first
    assert not list(first.parent.glob("*.tmp"))


def _write_report(tmp_path: Path, payload: dict, run_id: str = "2026-11-20-600519-abcd1234") -> Path:
    """按 Senate 的落盘形状写一份报告（reports/<run_id>/summary.json + state.json）。"""
    folder = tmp_path / "reports" / run_id
    folder.mkdir(parents=True)
    (folder / "summary.json").write_text(
        json.dumps(payload["summary"], ensure_ascii=False), encoding="utf-8"
    )
    (folder / "state.json").write_text(
        json.dumps(payload["state"], ensure_ascii=False), encoding="utf-8"
    )
    return folder


def test_latest_view_returns_none_without_report_and_gate_with_one(tmp_path: Path) -> None:
    assert debate.latest_view("600519", root=tmp_path) is None

    _write_report(tmp_path, _payload())
    view = debate.latest_view("600519", root=tmp_path)

    assert view is not None
    assert view["gate"]["passed"] is True
    assert view["normalized"]["rating"] == "buy"


def test_summary_block_is_one_block_and_never_carries_digits(tmp_path: Path) -> None:
    _write_report(tmp_path, _payload())
    passed = debate.summary_block(debate.latest_view("600519", root=tmp_path))
    assert passed["type"] == "conclusion"
    assert passed["payload"]["falsifier"]
    # 红线 4：主问答里的结论文字不许带数字
    assert not any(ch.isdigit() for ch in passed["payload"]["text"])

    failed_payload = _payload(
        summary=_summary(partial=True, low_quality=True, quality_score=60.0),
        state={
            "debate": {"bull_bear": {"arguments": []}},
            "decision": {"rating": "hold", "risks": []},
            "quality": {"low_quality": True, "partial": True},
        },
    )
    _write_report(tmp_path, failed_payload, run_id="2026-11-21-600519-deadbeef")
    gated = debate.latest_view("600519", root=tmp_path)
    assert gated is not None and gated["gate"]["passed"] is False

    notice = debate.summary_block(gated)
    assert notice["type"] == "notice"
    assert notice["payload"]["kind"] == "degraded"
    assert not any(ch.isdigit() for ch in notice["payload"]["message"])
    # 不会拿空表冒充辩论结果：只说原因，不摆论点
    assert "质量" in notice["payload"]["message"]
