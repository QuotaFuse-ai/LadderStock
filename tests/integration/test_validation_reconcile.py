"""`validate` / `validate reconcile` 的收盘对账链路（CLI 退出码 0 / 6 / 8）。

覆盖文档第五、十节的验收项「明日验证条件可恢复、收盘对账」：

- 条件只能由**当日程序指标**推导（AI 不参与阈值），缺指标的条件不生成；
- 下一交易日数据齐全 → 逐条判定，`status=ok`，退出码 0；
- 下一交易日缺分项指标 → 条件判不了记为 unknown，`status=partial`，退出码 8；
- 本地还没有下一交易日 → 同样 partial + 8，**不伪造判定**；
- 没有验证文档 → 6（先跑 review）。

全程只读临时数据目录，不发网络请求。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from ladderstock.cli.main import app
from ladderstock.core import ladder as ladder_module
from ladderstock.core import sentiment as sentiment_module
from ladderstock.core import validation as validation_module
from ladderstock.data import duckdb_store

DAY1 = date(2026, 10, 22)
DAY2 = date(2026, 10, 23)
runner = CliRunner()


def _limit(day: date, boards: dict[str, int]) -> list[dict[str, Any]]:
    return [
        {
            "code": code,
            "name": f"股{code}",
            "trade_date": day.isoformat(),
            "consecutive_boards": count,
        }
        for code, count in boards.items()
    ]


def _codes(day: date, codes: list[str]) -> list[dict[str, Any]]:
    return [{"code": code, "trade_date": day.isoformat()} for code in codes]


@pytest.fixture()
def data_dir(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    return tmp_path


def _seed(root: Path, day: date, boards: dict[str, int], *, with_broken: bool = True) -> None:
    """写入某交易日的涨停池 / 昨日涨停 / 炸板三张表。"""
    duckdb_store.write_dataset("limit_pool", _limit(day, boards), root=root)
    duckdb_store.write_dataset(
        "yesterday_limit_up", _codes(day, list(boards)), root=root
    )
    if with_broken:
        duckdb_store.write_dataset("broken_board", _codes(day, ["300750"]), root=root)


def _write_conditions(root: Path, day: date) -> dict[str, Any]:
    """用当日指标推导明日条件并落盘（等价于 review 里生成验证文档那一步）。"""
    sentiment = sentiment_module.compute_sentiment(day, root=root)
    ladder = ladder_module.compute_ladder(day, root=root)
    conditions = validation_module.build_conditions(day, sentiment, ladder)
    document = {
        "date": day.isoformat(),
        "generated_at": "2026-10-22T15:10:00+08:00",
        "conditions": conditions,
        "reconcile": None,
    }
    validation_module.write_document(validation_module.validation_path(day, root), document)
    return document


# --------------------------------------------------------------------------- #
# 条件生成
# --------------------------------------------------------------------------- #

def test_conditions_are_derived_from_program_metrics(data_dir: Path) -> None:
    _seed(data_dir, DAY1, {"600000": 3, "000001": 1})
    document = _write_conditions(data_dir, DAY1)

    ids = {item["id"] for item in document["conditions"]}
    assert ids == {"temperature_floor", "promotion_floor", "seal_floor", "height_floor", "gap_fill"}

    by_id = {item["id"]: item for item in document["conditions"]}
    # 阈值全部由当日读数推导，不含 AI 文本
    assert by_id["temperature_floor"]["operator"] == ">="
    assert by_id["temperature_floor"]["metric"] == "temperature"
    assert by_id["height_floor"]["value"] == 2  # 今日 3 板 → 明日不低于 2 板
    assert by_id["gap_fill"]["operator"] == "not_contains"
    assert by_id["gap_fill"]["value"] == [2]  # 3 板与 1 板之间缺 2 板

    for item in document["conditions"]:
        assert isinstance(item["text"], str) and item["text"]


def test_conditions_skip_metrics_that_are_missing(data_dir: Path) -> None:
    """没有连板数字段 → 不生成高度/断层条件，也不生成不存在的阈值。"""
    duckdb_store.write_dataset(
        "limit_pool",
        [{"code": "600000", "trade_date": DAY1.isoformat()}],
        root=data_dir,
    )

    sentiment = sentiment_module.compute_sentiment(DAY1, root=data_dir)
    ladder = ladder_module.compute_ladder(DAY1, root=data_dir)
    conditions = validation_module.build_conditions(DAY1, sentiment, ladder)

    ids = {item["id"] for item in conditions}
    assert "height_floor" not in ids
    assert "gap_fill" not in ids
    # 涨停池存在时封板率仍可算（炸板数按 0 计会失真，所以这里只要求不崩）
    assert "temperature_floor" in ids or "seal_floor" in ids


# --------------------------------------------------------------------------- #
# 对账：三档结果
# --------------------------------------------------------------------------- #

def test_reconcile_all_conditions_judged_exits_zero(data_dir: Path) -> None:
    _seed(data_dir, DAY1, {"600000": 3, "000001": 1})
    _write_conditions(data_dir, DAY1)
    _seed(data_dir, DAY2, {"600000": 3, "000001": 2, "000002": 1})

    result = runner.invoke(
        app, ["validate", "reconcile", "--date", DAY1.isoformat(), "--output", "json"]
    )

    assert result.exit_code == 0, result.output + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "ok"
    assert payload["next_session"] == DAY2.isoformat()
    assert payload["checked"] == 5
    assert payload["unknown"] == 0
    assert payload["passed"] + payload["failed"] == payload["checked"]
    for row in payload["results"]:
        assert row["passed"] is not None
        assert row["metric"] and row["operator"]


def test_reconcile_persists_result_into_document(data_dir: Path) -> None:
    _seed(data_dir, DAY1, {"600000": 3, "000001": 1})
    _write_conditions(data_dir, DAY1)
    _seed(data_dir, DAY2, {"600000": 3, "000001": 2, "000002": 1})

    runner.invoke(app, ["validate", "reconcile", "--date", DAY1.isoformat()])

    document = validation_module.load_validation(DAY1, data_dir)
    assert document is not None
    assert document["reconcile"]["status"] == "ok"
    # 条件本身不被对账结果覆盖
    assert {item["id"] for item in document["conditions"]} >= {"temperature_floor", "gap_fill"}

    # `validate --date` 之后能直接读到对账结果
    shown = runner.invoke(app, ["validate", "--date", DAY1.isoformat(), "--output", "json"])
    assert shown.exit_code == 0
    assert json.loads(shown.stdout)["reconcile"]["status"] == "ok"


def test_reconcile_without_next_session_is_partial(data_dir: Path) -> None:
    _seed(data_dir, DAY1, {"600000": 3, "000001": 1})
    _write_conditions(data_dir, DAY1)

    result = runner.invoke(
        app, ["validate", "reconcile", "--date", DAY1.isoformat(), "--output", "json"]
    )

    assert result.exit_code == 8, result.output + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "partial"
    assert payload["next_session"] is None
    assert payload["results"] == []
    assert "下一交易日" in payload["reason"]


def test_reconcile_missing_metric_marks_unknown_and_returns_partial(data_dir: Path) -> None:
    _seed(data_dir, DAY1, {"600000": 3, "000001": 1})
    _write_conditions(data_dir, DAY1)
    # 下一交易日只有涨停池：晋级率 / 封板率都算不出来
    duckdb_store.write_dataset(
        "limit_pool", _limit(DAY2, {"600000": 3}), root=data_dir
    )

    result = runner.invoke(
        app, ["validate", "reconcile", "--date", DAY1.isoformat(), "--output", "json"]
    )

    assert result.exit_code == 8, result.output + result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "partial"
    assert payload["unknown"] >= 1
    unknown_rows = [row for row in payload["results"] if row["passed"] is None]
    assert unknown_rows, "缺指标的条件必须留成 unknown，而不是判成通过/失败"


def test_reconcile_without_document_exits_not_found(data_dir: Path) -> None:
    result = runner.invoke(app, ["validate", "reconcile", "--date", DAY1.isoformat()])

    assert result.exit_code == 6
    assert "先运行 ldst review" in result.stderr


def test_validate_without_document_exits_not_found(data_dir: Path) -> None:
    result = runner.invoke(app, ["validate", "--date", DAY1.isoformat()])

    assert result.exit_code == 6
    assert "验证条件" in result.stderr


def test_validate_rejects_bad_date(data_dir: Path) -> None:
    result = runner.invoke(app, ["validate", "--date", "2026/10/22"])

    assert result.exit_code == 2
    assert "YYYY-MM-DD" in result.stderr
