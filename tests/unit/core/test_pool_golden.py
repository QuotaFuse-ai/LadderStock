"""候选池口径黄金值（QUOT-145 R7）：手算值 ↔ core 输出 ↔ CLI 输出，三方必须一致。

夹具在 `tests/fixtures/pool/P*.json`，每份都带 `expected.pool` 块——期望值由人按
`docs/specs/SPEC-CANDIDATE-POOL.md` §4–§5 的算式手算并写进文档，本文件只做比对，
外加两条独立复算（不调用被测实现）：

- **总分**：从输出里的 `parts` + 文档权重（写死在本文件，不 import 实现）重算；
- **分项**：从夹具原始输入按文档算式重算五个 0–100 分。

缓存注入走 `duckdb_store.write_dataset`（与 `test_metrics_golden.py` 同一做法）：
`ldst pool` 读的是真缓存路径，不是打桩。全程离线、只写 tmp，不碰仓库 `data/`。
"""

from __future__ import annotations

import json
from datetime import date as date_type
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from ladderstock.cli.main import app
from ladderstock.core import pool as pool_module
from ladderstock.data import duckdb_store

DAY = "2026-10-27"

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "pool"
CASES = sorted(FIXTURES.glob("P*.json"))
IDS = [path.stem for path in CASES]

runner = CliRunner()

# --- 文档口径（SPEC-CANDIDATE-POOL.md §3–§4）。刻意写死，不从实现 import：--- #
# --- 实现改了口径而忘了改文档时，这里必须变红。                          --- #
DOC_WEIGHTS: dict[str, float] = {
    "strength": 0.30,
    "turnover": 0.10,
    "standing": 0.25,
    "capital": 0.20,
    "theme": 0.15,
}
DOC_TOP_BOARD = 6.0
DOC_TURNOVER_NORM = 20.0
DOC_THEME_NORM = 5.0
#: SPEC-CANDIDATE-POOL.md §4 的算式原文（改动必须双向同步）
DOC_FORMULA = (
    "score = Σ(weight_i × part_i) / Σ(weight_i)，part_i ∈ [0,100]（1 位小数）；"
    "取不到数的分项 part_i = null，其权重从分母里剔除（重归一），不补 0"
)
DOC_INFLOW_KEYS = (
    "net_inflow",
    "main_net_inflow",
    "net_amount",
    "主力净流入",
    "净流入",
    "净额",
)


# --------------------------------------------------------------------------- #
# 夹具与视图
# --------------------------------------------------------------------------- #

def load_case(path: Path) -> dict[str, Any]:
    case: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    assert set(case["datasets"]) == {"limit_pool", "concept_board", "fund_flow"}
    assert {"available", "candidates", "counts"} <= set(case["expected"]["pool"])
    return case


def write_cache(datasets: dict[str, list[dict[str, Any]]], root: Path) -> None:
    """把夹具写进本机缓存；空记录不落库，保持「缓存缺失」语义。"""
    for dataset, rows in datasets.items():
        if rows:
            duckdb_store.write_dataset(
                dataset,
                rows,
                source="golden",
                default_trade_date=date_type.fromisoformat(DAY),
                root=root,
            )


def pool_view(result: dict[str, Any]) -> dict[str, Any]:
    """把打分结果拍平成断言视图（顺序、分项、缺席项、计数、缺失数据集）。"""
    return {
        "available": result["available"],
        "reason": result["reason"],
        "height": result["height"],
        "candidate_count": result["candidate_count"],
        "candidates": [
            {
                "code": row["code"],
                "score": row["score"],
                "parts": row["parts"],
                "absent_parts": row["absent_parts"],
            }
            for row in result["candidates"]
        ],
        "counts": result["counts"],
        "missing_datasets": result["missing"],
    }


def assert_matches(expected: dict[str, Any], actual: dict[str, Any], label: str) -> None:
    """期望块逐键比对；`*_contains` 表示「该字段文本包含此片段」。"""
    expected = dict(expected)
    actual = dict(actual)
    for key in [name for name in expected if name.endswith("_contains")]:
        field = key[: -len("_contains")]
        text = str(actual.pop(field, "") or "")
        fragment = expected.pop(key)
        assert fragment in text, f"{label}.{field}: {text!r} 不含 {fragment!r}"
    for key, value in expected.items():
        assert key in actual, f"{label}.{key} 未出现在输出里"
        assert actual[key] == value, f"{label}.{key}: 程序={actual[key]!r} 手算={value!r}"
    extra = set(actual) - set(expected)
    assert not extra, f"{label} 多出未在夹具声明的键：{sorted(extra)}"


# --------------------------------------------------------------------------- #
# 独立复算（SPEC §4 的直译，不调用被测实现）
# --------------------------------------------------------------------------- #

def _pct(fraction: float) -> float:
    return round(fraction * 100.0, 1)


def _clamped(value: float | None, upper: float) -> float | None:
    if value is None:
        return None
    if value <= 0:
        return 0.0
    return _pct(min(1.0, value / upper))


def doc_inflows(rows: list[dict[str, Any]]) -> dict[str, float]:
    values: dict[str, float] = {}
    for row in rows:
        code = str(row.get("code") or "").strip()
        if not code or code in values:
            continue
        for key in DOC_INFLOW_KEYS:
            raw = row.get(key)
            if raw is not None:
                values[code] = float(raw)
                break
    return values


def documented_parts(
    raw: dict[str, Any], height: int | None, inflows: dict[str, float]
) -> dict[str, float | None]:
    """五个分项按文档算式各算一遍：`raw` 是程序自己报告的输入值。"""
    boards = raw["boards"]
    values = sorted(inflows.values())
    inflow = raw["net_inflow"]
    if inflow is None:
        capital: float | None = None
    elif len(values) == 1:
        capital = 50.0
    else:
        capital = _pct(sum(1 for other in values if other < inflow) / (len(values) - 1))
    return {
        "strength": (
            _pct(min(1.0, boards / DOC_TOP_BOARD))
            if boards is not None and boards > 0
            else None
        ),
        "turnover": _clamped(raw["turnover_rate"], DOC_TURNOVER_NORM),
        "standing": (
            _pct(min(1.0, boards / height)) if boards is not None and height else None
        ),
        "capital": capital,
        "theme": _clamped(raw["theme_board_change_pct"], DOC_THEME_NORM),
    }


def documented_score(parts: dict[str, float | None]) -> float | None:
    usable = [
        (DOC_WEIGHTS[key], value) for key, value in parts.items() if value is not None
    ]
    if not usable:
        return None
    weight = sum(item[0] for item in usable)
    return round(sum(item[0] * item[1] for item in usable) / weight, 1)


# --------------------------------------------------------------------------- #
# 1. core：手算值 ↔ 程序输出
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("path", CASES, ids=IDS)
def test_pool_matches_hand_calculation(path: Path, tmp_path: Path) -> None:
    case = load_case(path)
    write_cache(case["datasets"], tmp_path)

    result = pool_module.compute_pool(DAY, root=tmp_path)

    assert_matches(case["expected"]["pool"], pool_view(result), path.stem)
    # 口径与算式本身也是契约的一部分：改动必须同步 SPEC-CANDIDATE-POOL.md
    assert result["weights"] == DOC_WEIGHTS
    assert result["formula"] == DOC_FORMULA == pool_module.SCORE_FORMULA
    assert result["date"] == DAY


# --------------------------------------------------------------------------- #
# 2. 独立复算：分项与总分都能从报告的原始输入重算
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("path", CASES, ids=IDS)
def test_parts_and_score_recompute_from_reported_inputs(path: Path, tmp_path: Path) -> None:
    case = load_case(path)
    write_cache(case["datasets"], tmp_path)

    result = pool_module.compute_pool(DAY, root=tmp_path)
    if not result["available"]:
        pytest.skip("空池没有可复算的候选")

    inflows = doc_inflows(case["datasets"]["fund_flow"])
    for row in result["candidates"]:
        label = f"{path.stem}.{row['code']}"
        assert row["parts"] == documented_parts(row["raw"], result["height"], inflows), (
            f"{label} 分项与文档算式不一致"
        )
        assert row["score"] == documented_score(row["parts"]), f"{label} 总分不自洽"
        assert row["absent_parts"] == [
            key for key, value in row["parts"].items() if value is None
        ], f"{label} 缺席分项清单与实际分项不一致"


# --------------------------------------------------------------------------- #
# 3. 确定性：同输入两次输出完全一致（ROADMAP R7「diff 为空」）
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("path", CASES, ids=IDS)
def test_scoring_is_deterministic(path: Path, tmp_path: Path) -> None:
    case = load_case(path)
    write_cache(case["datasets"], tmp_path)

    first = pool_module.compute_pool(DAY, root=tmp_path)
    second = pool_module.compute_pool(DAY, root=tmp_path)

    # 打分载荷里没有任何时点字段，所以整块必须逐字节相同
    assert json.dumps(first, ensure_ascii=False) == json.dumps(second, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# 4. CLI 对拍：`ldst pool --date --output json`
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("path", CASES, ids=IDS)
def test_cli_matches_golden(path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case = load_case(path)
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    write_cache(case["datasets"], tmp_path)

    result = runner.invoke(app, ["pool", "--date", DAY, "--output", "json"])
    expected = case["expected"]["pool"]
    # DEC-012：`available=false` 是「无结果」，退出码 6（stderr 给原因，stdout 仍是完整信封）
    assert result.exit_code == (0 if expected["available"] else 6), result.stderr

    envelope = json.loads(result.stdout)
    assert {"domain", "topic", "data_as_of", "payload", "source_detail"} <= set(envelope)
    assert {"cached_at", "backend"} <= set(envelope)
    assert envelope["domain"] == "sentiment"
    assert envelope["topic"] == "candidate_pool"
    assert envelope["source_status"] == ("ok" if expected["available"] else "partial")
    assert envelope["payload"]["data_source_status"] == (
        "ok" if expected["available"] else "missing"
    )
    assert_matches(expected, pool_view(envelope["payload"]), f"{path.stem}.cli")
    for dataset in ("limit_pool", "concept_board", "fund_flow"):
        assert dataset in envelope["source_detail"]


def test_cli_limit_truncates_after_sorting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`--limit` 在打分排序之后截断：留下的是分数最高的前 N 只，不是缓存的前 N 行。"""
    case = load_case(FIXTURES / "P01_standard_full_pool.json")
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    write_cache(case["datasets"], tmp_path)

    result = runner.invoke(app, ["pool", "--date", DAY, "--limit", "2", "--output", "json"])
    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)["payload"]
    assert [row["code"] for row in payload["candidates"]] == ["600001", "600002"]
    assert payload["candidate_count"] == 2
    # 计数仍是全量口径：截断只影响返回条数，不影响「涨停池有几行」
    assert payload["counts"]["limit_pool_rows"] == 3


def test_cli_rejects_non_positive_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    result = runner.invoke(app, ["pool", "--date", DAY, "--limit", "0", "--output", "json"])
    assert result.exit_code == 2, result.stderr
    assert "必须大于 0" in result.stderr


def test_cli_rejects_bad_date(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    result = runner.invoke(app, ["pool", "--date", "2026-13-01", "--output", "json"])
    assert result.exit_code == 2, result.stderr
    assert "YYYY-MM-DD" in result.stderr


# --------------------------------------------------------------------------- #
# 5. 边界：权重和为 1，分项固定五个且「地位」叫 standing
# --------------------------------------------------------------------------- #

def test_weights_sum_to_one_and_sub_scores_are_fixed() -> None:
    assert pool_module.SUB_SCORES == ("strength", "turnover", "standing", "capital", "theme")
    assert pool_module.SUB_SCORE_LABELS == {
        "strength": "强度",
        "turnover": "换手",
        "standing": "地位",
        "capital": "资金",
        "theme": "题材",
    }
    assert round(sum(DOC_WEIGHTS.values()), 2) == 1.0
    assert set(pool_module.WEIGHTS) == set(DOC_WEIGHTS)


def test_rank_percentile_boundaries() -> None:
    """分位是「集合内名次」：最小 0、最大 100、并列同分、单样本给中位 50。"""
    assert pool_module.rank_percentiles({}) == {}
    assert pool_module.rank_percentiles({"A": 1.0}) == {"A": 50.0}
    assert pool_module.rank_percentiles({"A": 1.0, "B": 2.0, "C": 3.0}) == {
        "A": 0.0,
        "B": 50.0,
        "C": 100.0,
    }
    # 并列的两只「严格小于者」都是 0 个，因此同分（不是 0 与 50 的拉开）
    assert pool_module.rank_percentiles({"A": 5.0, "B": 5.0, "C": 9.0}) == {
        "A": 0.0,
        "B": 0.0,
        "C": 100.0,
    }
