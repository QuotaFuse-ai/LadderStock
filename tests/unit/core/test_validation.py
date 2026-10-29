"""明日验证条件与收盘对账（core/validation.py）。

阈值由程序从当日指标推导，AI 不参与；这里逐条锁定阈值公式、判定算子与
"下一交易日缺数据就 partial"的口径，防止以后有人拿默认值冒充判定结果。
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import pytest

from ladderstock.core import sentiment as sentiment_module
from ladderstock.core import validation as validation_module
from ladderstock.core.validation import (
    build_conditions,
    evaluate,
    load_validation,
    reconcile,
    session_days,
    validation_path,
)

DAY = date(2026, 10, 27)
NEXT = date(2026, 10, 28)


# --------------------------------------------------------------------------- #
# 条件生成
# --------------------------------------------------------------------------- #

def test_build_conditions_uses_program_thresholds() -> None:
    sentiment = {"temperature": 62.5, "promotion_rate": 43.2, "seal_rate": 71.4}
    ladder = {"height": 5, "gap_levels": [4]}

    conditions = build_conditions(DAY, sentiment, ladder)
    by_id = {item["id"]: item for item in conditions}

    assert list(by_id) == [
        "temperature_floor",
        "promotion_floor",
        "seal_floor",
        "height_floor",
        "gap_fill",
    ]
    assert by_id["temperature_floor"]["value"] == 47.5
    assert by_id["promotion_floor"]["value"] == 28.2
    assert by_id["seal_floor"]["value"] == 56.4
    assert by_id["height_floor"]["value"] == 4
    assert by_id["height_floor"]["metric"] == "max_board"
    assert by_id["gap_fill"]["operator"] == "not_contains"
    assert by_id["gap_fill"]["value"] == [4]
    assert all(item["text"] for item in conditions)


def test_build_conditions_clamps_to_floors() -> None:
    sentiment = {"temperature": 20.0, "promotion_rate": 5.0, "seal_rate": 30.0}
    ladder = {"height": 1}

    by_id = {item["id"]: item for item in build_conditions(DAY, sentiment, ladder)}

    assert by_id["temperature_floor"]["value"] == 30.0
    assert by_id["promotion_floor"]["value"] == 10.0
    assert by_id["seal_floor"]["value"] == 40.0
    assert by_id["height_floor"]["value"] == 2


def test_build_conditions_skips_missing_metrics() -> None:
    assert build_conditions(DAY, {"temperature": None}, {"height": None}) == []
    assert build_conditions(DAY, {}, {}) == []


# --------------------------------------------------------------------------- #
# 判定
# --------------------------------------------------------------------------- #

def _cond(operator: str, value: Any) -> dict[str, Any]:
    return {"metric": "temperature", "operator": operator, "value": value}


def test_evaluate_numeric_operators() -> None:
    actual = {"temperature": 50}
    assert evaluate(_cond(">=", 50), actual) is True
    assert evaluate(_cond(">=", 50.1), actual) is False
    assert evaluate(_cond("<=", 50), actual) is True
    assert evaluate(_cond(">", 50), actual) is False
    assert evaluate(_cond("<", 51), actual) is True
    assert evaluate(_cond("==", 50), actual) is True
    # 未知算子不猜：返回 None 而不是 False
    assert evaluate(_cond("??", 50), actual) is None


def test_evaluate_returns_none_when_metric_missing() -> None:
    condition = {"metric": "temperature", "operator": ">=", "value": 30}
    assert evaluate(condition, {}) is None
    assert evaluate(condition, {"temperature": "high"}) is None
    assert evaluate(condition, {"temperature": True}) is None
    # 条件本身缺 metric / value 也不能瞎判
    assert evaluate({"operator": ">=", "value": 30}, {"temperature": 40}) is None
    assert evaluate({"metric": "temperature", "operator": ">="}, {"temperature": 40}) is None


def test_evaluate_gap_levels() -> None:
    condition = {"metric": "gap_levels", "operator": "not_contains", "value": [4]}

    assert evaluate(condition, {"gap_levels": [1, 3, 5]}) is True
    assert evaluate(condition, {"gap_levels": [3, 4, 5]}) is False
    assert evaluate(condition, {"gap_levels": []}) is True
    assert evaluate(condition, {}) is None


# --------------------------------------------------------------------------- #
# 读写
# --------------------------------------------------------------------------- #

def test_validation_path_and_roundtrip(tmp_path: Any) -> None:
    path = validation_path(DAY, tmp_path)
    assert path == tmp_path / "state" / "validation" / f"{DAY}.json"
    assert load_validation(DAY, tmp_path) is None

    document = {"date": DAY.isoformat(), "conditions": [{"id": "x"}]}
    validation_module.write_document(path, document)

    loaded = load_validation(DAY, tmp_path)
    assert loaded == document
    # 原子写不留下临时文件
    assert list(path.parent.glob("*.tmp")) == []
    # 落盘是合法 JSON（UTF-8 中文不转义）
    assert json.loads(path.read_text(encoding="utf-8")) == document


def test_load_validation_ignores_corrupt_file(tmp_path: Any) -> None:
    path = validation_path(DAY, tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    assert load_validation(DAY, tmp_path) is None


# --------------------------------------------------------------------------- #
# 交易日与下一交易日
# --------------------------------------------------------------------------- #

def test_session_days_reads_limit_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    records = [
        {"trade_date": "2026-10-27", "code": "600000"},
        {"trade_date": "2026-10-24", "code": "600001"},
        {"trade_date": "2026-10-24", "code": "600002"},
        {"code": "600003"},  # 没有日期：丢弃，不猜
    ]
    monkeypatch.setattr(
        validation_module.ladder_module, "dataset_snapshot", lambda *_a, **_k: (records, "parquet")
    )

    assert session_days(root="/ignored") == [date(2026, 10, 24), date(2026, 10, 27)]
    assert validation_module.next_session(date(2026, 10, 24), root="/ignored") == date(2026, 10, 27)
    assert validation_module.next_session(date(2026, 10, 27), root="/ignored") is None


# --------------------------------------------------------------------------- #
# 对账
# --------------------------------------------------------------------------- #

def _write_conditions(tmp_path: Any, conditions: list[dict[str, Any]]) -> None:
    validation_module.write_document(
        validation_path(DAY, tmp_path),
        {"date": DAY.isoformat(), "conditions": conditions, "reconcile": None},
    )


def test_reconcile_marks_passed_and_failed(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_conditions(
        tmp_path,
        [
            {
                "id": "temperature_floor",
                "text": "温度不低于 50",
                "metric": "temperature",
                "operator": ">=",
                "value": 50,
            },
            {
                "id": "gap_fill",
                "text": "回补 4 板",
                "metric": "gap_levels",
                "operator": "not_contains",
                "value": [4],
            },
        ],
    )
    monkeypatch.setattr(validation_module, "next_session", lambda *_a, **_k: NEXT)
    monkeypatch.setattr(
        sentiment_module,
        "compute_sentiment",
        lambda *_a, **_k: {"available": True, "temperature": 55.0, "gap_levels": [4, 5]},
    )

    result = reconcile(DAY, root=tmp_path)

    assert result["status"] == "ok"
    assert result["next_session"] == NEXT.isoformat()
    assert result["checked"] == 2
    assert result["passed"] == 1
    assert result["failed"] == 1
    assert result["unknown"] == 0
    assert [row["passed"] for row in result["results"]] == [True, False]
    assert result["results"][0]["actual"] == 55.0

    # 对账结果写回同一份文档，条件本身不被改写
    stored = load_validation(DAY, tmp_path)
    assert stored is not None
    assert stored["reconcile"]["status"] == "ok"
    assert len(stored["conditions"]) == 2


def test_reconcile_partial_without_next_session(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_conditions(
        tmp_path,
        [{"id": "temperature_floor", "metric": "temperature", "operator": ">=", "value": 30}],
    )
    monkeypatch.setattr(validation_module, "next_session", lambda *_a, **_k: None)

    result = reconcile(DAY, root=tmp_path)

    assert result["status"] == "partial"
    assert result["next_session"] is None
    assert result["results"] == []
    assert "下一交易日" in result["reason"]
    assert load_validation(DAY, tmp_path)["reconcile"]["status"] == "partial"


def test_reconcile_partial_when_sentiment_unavailable(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_conditions(
        tmp_path,
        [{"id": "temperature_floor", "metric": "temperature", "operator": ">=", "value": 30}],
    )
    monkeypatch.setattr(validation_module, "next_session", lambda *_a, **_k: NEXT)
    monkeypatch.setattr(
        sentiment_module,
        "compute_sentiment",
        lambda *_a, **_k: {"available": False, "reason": "涨停池为空"},
    )

    result = reconcile(DAY, root=tmp_path)

    assert result["status"] == "partial"
    assert "不可用" in result["reason"]
    assert "涨停池为空" in result["reason"]
    assert result["results"] == []


def test_reconcile_marks_unknown_when_metric_missing(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_conditions(
        tmp_path,
        [
            {"id": "temperature_floor", "metric": "temperature", "operator": ">=", "value": 30},
            {"id": "seal_floor", "metric": "seal_rate", "operator": ">=", "value": 40},
        ],
    )
    monkeypatch.setattr(validation_module, "next_session", lambda *_a, **_k: NEXT)
    monkeypatch.setattr(
        sentiment_module,
        "compute_sentiment",
        lambda *_a, **_k: {"available": True, "temperature": 70.0, "seal_rate": None},
    )

    result = reconcile(DAY, root=tmp_path)

    assert result["status"] == "partial"
    assert result["unknown"] == 1
    assert result["passed"] == 1
    assert [row["passed"] for row in result["results"]] == [True, None]
    assert "1 条条件缺指标" in result["reason"]


def test_reconcile_requires_conditions_document(tmp_path: Any) -> None:
    with pytest.raises(FileNotFoundError):
        reconcile(DAY, root=tmp_path)
