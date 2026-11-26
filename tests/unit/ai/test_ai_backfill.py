"""F3 缺口自补：只判定、不越权；文案不许带数字（notice 红线）。"""

from __future__ import annotations

import pytest

from ladderstock.ai import backfill
from ladderstock.ai.answer_blocks import contains_number
from ladderstock.data import coverage as real_coverage


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("帮我看下 600519 的财报", "600519"),
        ("000001 怎么样", "000001"),
        ("688981 的公告", "688981"),
        ("2026 年三季报", None),  # 四位年份不是代码
        ("20261120 的数据", None),  # 八位日期
        ("123456 是什么", None),  # 前缀不认识，宁可漏认
        ("没有代码，只说茅台", None),
        ("", None),
    ],
)
def test_detect_symbol(text: str, expected: str | None) -> None:
    assert backfill.detect_symbol(text) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("600519 的财报怎么样", ["financial"]),
        ("最近有分红公告吗", ["announcement"]),
        ("看看日线走势", ["kline"]),
        ("现在什么价格", ["quote"]),
        ("财报和公告都看看", ["financial", "announcement"]),
        # 上限：最多两类
        ("财报、公告、K线、行情都要", ["financial", "announcement"]),
        ("今天大盘怎么样", []),
    ],
)
def test_kinds_for_question(text: str, expected: list[str]) -> None:
    assert backfill.kinds_for_question(text) == expected


def _stub_coverage(monkeypatch: pytest.MonkeyPatch, *, ready: list[str], missing: list[str]) -> None:
    def fake(symbol: str, kinds=None, **_: object) -> dict:
        return {
            "symbol": symbol,
            "ready": list(ready),
            "missing": list(missing),
            "kinds": {k: {"state": "ready"} for k in kinds or []},
        }

    monkeypatch.setattr(real_coverage, "check_coverage", fake)


def test_no_symbol_means_no_action() -> None:
    decision = backfill.plan_backfill("今天大盘怎么样")
    assert decision["action"] == backfill.ACTION_NONE
    assert decision["task_id"] is None


def test_symbol_without_kind_hint_does_not_collect() -> None:
    """只给了代码、没说要哪类数据：不猜、不跑采集。"""
    decision = backfill.plan_backfill("600519 怎么样")
    assert decision["action"] == backfill.ACTION_NONE


def test_ready_when_local_has_data(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_coverage(monkeypatch, ready=["financial", "quote"], missing=[])
    decision = backfill.plan_backfill("600519 的财报", data_root=None)
    assert decision["action"] == backfill.ACTION_READY
    assert decision["missing"] == []


def test_confirm_under_default_policy_does_not_submit(monkeypatch: pytest.MonkeyPatch) -> None:
    from ladderstock.data import tasks

    _stub_coverage(monkeypatch, ready=[], missing=["financial"])
    submitted: list[tuple] = []
    monkeypatch.setattr(tasks, "submit", lambda *a, **k: submitted.append((a, k)) or {})
    # 默认档 ask_on_risk：采集要确认
    decision = backfill.plan_backfill("600519 的财报", data_root=None)
    assert decision["action"] == backfill.ACTION_CONFIRM
    assert decision["task_id"] is None
    assert submitted == []


def test_auto_under_allow_all_submits_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from ladderstock.core import policy
    from ladderstock.data import tasks

    _stub_coverage(monkeypatch, ready=[], missing=["financial", "quote", "kline"])
    # 权限档拨到 allow_all：采集不需要确认，才允许自动起任务
    monkeypatch.setattr(
        policy, "load_policy", lambda root=None: {"confirm_policy": policy.POLICY_ALLOW_ALL}
    )
    calls: list[dict] = []

    def fake_submit(symbol, kinds, **kwargs):
        calls.append({"symbol": symbol, "kinds": list(kinds), "kwargs": kwargs})
        return {"task_id": "ens_600519_test"}

    monkeypatch.setattr(tasks, "submit", fake_submit)
    decision = backfill.plan_backfill(
        "600519 的财报和公告",
        data_root=None,
        kinds_hint=["financial", "quote", "kline"],
    )
    assert decision["action"] == backfill.ACTION_AUTO
    assert decision["task_id"] == "ens_600519_test"
    assert len(calls) == 1
    # 一次最多补两类
    assert len(calls[0]["kinds"]) == backfill.MAX_AUTO_KINDS


def test_unavailable_coverage_is_not_a_data_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> dict:
        raise real_coverage.CoverageUnavailable("no db")

    monkeypatch.setattr(real_coverage, "check_coverage", boom)
    decision = backfill.plan_backfill("600519 的财报", data_root=None)
    assert decision["action"] == backfill.ACTION_UNAVAILABLE


@pytest.mark.parametrize("action", [backfill.ACTION_AUTO, backfill.ACTION_CONFIRM, backfill.ACTION_UNAVAILABLE])
def test_notice_text_has_no_digits(action: str) -> None:
    """notice 块禁止携带数字（红线 4）：代码、任务号都不能进这句文案。"""
    decision = {"action": action, "symbol": "600519", "missing": ["financial"], "task_id": "ens_600519_test"}
    note = backfill.notice_for(decision)
    assert note is not None
    assert not contains_number(note)


def test_notice_absent_when_nothing_to_say() -> None:
    assert backfill.notice_for({"action": backfill.ACTION_NONE}) is None
    assert backfill.notice_for({"action": backfill.ACTION_READY}) is None
