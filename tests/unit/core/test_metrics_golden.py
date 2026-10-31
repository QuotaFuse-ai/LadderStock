"""指标口径黄金值（QUOT-100）：手算值 ↔ core 输出 ↔ CLI 输出，三方差异必须为 0。

夹具在 `tests/fixtures/metrics/S*.json`，每份都带 `expected` 块——期望值由人按
指标口径 §5 的算式手算并写入文档，本文件只做比对与独立复算。

约定：
- 期望值改动只允许来自口径变更（METRICS.md 更新 + 夹具同步），不允许为了让实现
  变绿而改夹具；
- 全程离屏离线：core 用例用内存记录，CLI 用例把 `LDST_DATA_DIR` 钉到 tmp 并只用
  `duckdb_store.write_dataset` 造缓存，不触发任何 peer 调用。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date as date_type
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from ladderstock.cli.main import app
from ladderstock.core import boundary
from ladderstock.core import ladder as ladder_module
from ladderstock.core import sentiment as sentiment_module
from ladderstock.data import duckdb_store

DAY = "2026-10-27"

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "metrics"
CASES = sorted(FIXTURES.glob("S*.json"))
IDS = [path.stem for path in CASES]

runner = CliRunner()


# --------------------------------------------------------------------------- #
# 夹具装载
# --------------------------------------------------------------------------- #

def load_case(path: Path) -> dict[str, Any]:
    case: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    assert case["datasets"].keys() >= {"limit_pool", "broken_board", "yesterday_limit_up"}
    assert {"sentiment", "ladder"} <= case["expected"].keys()
    return case


def as_snapshot(
    datasets: dict[str, list[dict[str, Any]]],
) -> Callable[..., tuple[list[dict[str, Any]], str]]:
    """`compute_sentiment` 读三个数据集的替身：只返回内存夹具。"""

    def snapshot(dataset: str, day: Any, root: Any = None):
        return datasets.get(dataset, []), "parquet"

    return snapshot


@pytest.fixture
def patch_snapshot(monkeypatch: pytest.MonkeyPatch) -> Callable[[dict[str, Any]], None]:
    def apply(datasets: dict[str, list[dict[str, Any]]]) -> None:
        monkeypatch.setattr(sentiment_module, "dataset_snapshot", as_snapshot(datasets))

    return apply


def ladder_view(ladder: dict[str, Any]) -> dict[str, Any]:
    """把天梯输出拍平成可断言的视图（层序、每股、龙头、断层）。"""
    return {
        "available": ladder["available"],
        "reason": ladder["reason"],
        "height": ladder["height"],
        "count": ladder["count"],
        "levels": [group["boards"] for group in ladder["ladder"]],
        "stocks": {
            str(group["boards"]): [row["code"] for row in group["stocks"]]
            for group in ladder["ladder"]
        },
        "gap": ladder["gap"],
        "gap_levels": ladder["gap_levels"],
        "unknown_board": ladder["unknown_board"],
        "leaders": [row["code"] for row in ladder["leader_candidates"]],
    }


def assert_matches(expected: dict[str, Any], actual: dict[str, Any], label: str) -> None:
    """期望块逐键比对；`*_contains` 表示「原因文本包含该片段」。"""
    for key, value in expected.items():
        if key.endswith("_contains"):
            text = str(actual.get("reason") or "")
            assert value in text, f"{label}.{key}: {value!r} 不在 {text!r} 里"
            continue
        assert key in actual, f"{label}.{key} 未出现在输出里"
        assert actual[key] == value, f"{label}.{key}: 程序={actual[key]!r} 手算={value!r}"


# --------------------------------------------------------------------------- #
# 1. 情绪指标：手算值 vs core
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("path", CASES, ids=IDS)
def test_sentiment_matches_hand_calculation(
    path: Path, patch_snapshot: Callable[[dict[str, Any]], None]
) -> None:
    case = load_case(path)
    patch_snapshot(case["datasets"])

    result = sentiment_module.compute_sentiment(DAY)

    assert_matches(case["expected"]["sentiment"], result, f"{path.stem}.sentiment")
    # 公式文本也属于口径的一部分：改动它必须同步 METRICS.md
    assert result["formula"] == sentiment_module.TEMPERATURE_FORMULA
    assert result["date"] == DAY


# --------------------------------------------------------------------------- #
# 2. 连板天梯：手算值 vs core（含层序与龙头顺序）
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("path", CASES, ids=IDS)
def test_ladder_matches_hand_calculation(path: Path) -> None:
    case = load_case(path)
    ladder = ladder_module.compute_ladder(DAY, records=case["datasets"]["limit_pool"])

    assert_matches(case["expected"]["ladder"], ladder_view(ladder), f"{path.stem}.ladder")


# --------------------------------------------------------------------------- #
# 3. 温度公式独立复算：只用文档口径的输入复算，不再调 core
# --------------------------------------------------------------------------- #

def documented_temperature(
    promotion: float | None, seal: float | None, height: int | None
) -> float | None:
    """METRICS.md §3.3 的直译：分项已是 0–100 百分数，按可用权重重归一。"""
    parts: list[tuple[float, float]] = []
    if promotion is not None:
        parts.append((0.45, promotion))
    if seal is not None:
        parts.append((0.35, seal))
    if height is not None and height > 0:
        parts.append((0.20, min(1.0, height / sentiment_module.TOP_BOARD_NORM) * 100.0))
    if not parts:
        return None
    weight = sum(weight for weight, _ in parts)
    return round(sum(weight * value for weight, value in parts) / weight, 1)


@pytest.mark.parametrize("path", CASES, ids=IDS)
def test_temperature_recomputes_from_reported_components(
    path: Path, patch_snapshot: Callable[[dict[str, Any]], None]
) -> None:
    case = load_case(path)
    patch_snapshot(case["datasets"])

    result = sentiment_module.compute_sentiment(DAY)
    if not result["available"]:
        assert result["temperature"] is None
        return

    assert result["temperature"] == documented_temperature(
        result["promotion_rate"], result["seal_rate"], result["max_board"]
    ), f"{path.stem}: 温度与「晋级率/封板率/最高板」三者在文档口径下不自洽"


# --------------------------------------------------------------------------- #
# 4. 断层定义独立复算：gap_levels 必须是 1..height-1 里缺席的层级
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("path", CASES, ids=IDS)
def test_gap_levels_are_absent_interior_levels(path: Path) -> None:
    case = load_case(path)
    ladder = ladder_module.compute_ladder(DAY, records=case["datasets"]["limit_pool"])
    height = ladder["height"]
    if height is None:
        assert ladder["gap_levels"] == []
        return

    present = {group["boards"] for group in ladder["ladder"]}
    expected = [level for level in range(1, height) if level not in present]
    assert ladder["gap_levels"] == expected
    assert ladder["gap"] is bool(expected)


# --------------------------------------------------------------------------- #
# 5. CLI 对拍：`ldst sentiment show` / `ldst ladder show` 必须与手算值一致
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("path", CASES, ids=IDS)
def test_cli_matches_golden(
    path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = load_case(path)
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    for dataset, rows in case["datasets"].items():
        if rows:  # 空记录不落库，保持「缓存缺失」语义
            duckdb_store.write_dataset(
                dataset,
                rows,
                source="golden",
                default_trade_date=date_type.fromisoformat(DAY),
                root=tmp_path,
            )

    # D-03：数据不可用时退出码 6（DEC-003「6 = 无结果」），stderr 仍给原因。
    expected_sentiment_code = 0 if case["expected"]["sentiment"]["available"] else 6
    sentiment_run = runner.invoke(
        app, ["sentiment", "show", "--date", DAY, "--output", "json"]
    )
    assert sentiment_run.exit_code == expected_sentiment_code, sentiment_run.stderr
    envelope = json.loads(sentiment_run.stdout)
    # D-02：外层是 `LadderEnvelope`，溯源键必须存在（缺数据时为 null，不是省略）。
    assert {"domain", "topic", "data_as_of", "payload", "source_detail"} <= set(envelope)
    assert {"data_as_of", "cached_at", "backend"} <= set(envelope)
    assert envelope["domain"] == "sentiment"
    sentiment_payload = envelope["payload"]
    assert sentiment_payload["date"] == DAY
    assert "limit_pool" in sentiment_payload["source_detail"]
    if case["expected"]["sentiment"]["available"]:
        # 有数据时 `data_as_of` 指向当日分区的缓存时点
        assert envelope["data_as_of"] == DAY
        assert envelope["backend"] in {"parquet", "duckdb"}
    else:
        assert envelope["data_as_of"] is None
        assert envelope["backend"] == "none"
    assert_matches(
        case["expected"]["sentiment"], sentiment_payload, f"{path.stem}.cli.sentiment"
    )

    expected_ladder_code = 0 if case["expected"]["ladder"]["available"] else 6
    ladder_run = runner.invoke(
        app, ["ladder", "show", "--date", DAY, "--output", "json"]
    )
    assert ladder_run.exit_code == expected_ladder_code, ladder_run.stderr
    ladder_envelope = json.loads(ladder_run.stdout)
    assert {"data_as_of", "cached_at", "backend"} <= set(ladder_envelope)
    assert ladder_envelope["domain"] == "sentiment"
    assert_matches(
        case["expected"]["ladder"],
        ladder_view(ladder_envelope["payload"]),
        f"{path.stem}.cli.ladder",
    )


# --------------------------------------------------------------------------- #
# 6. AI 不改数：指标快照只读扁平，AI 写指标键必须抛错
# --------------------------------------------------------------------------- #

KEYS = (
    "temperature",
    "promotion_rate",
    "seal_rate",
    "limit_up_count",
    "broken_count",
    "yesterday_limit_up_count",
    "max_board",
    "gap",
    "gap_levels",
    "missing",
)


@pytest.mark.parametrize("path", CASES, ids=IDS)
def test_narrative_snapshot_is_flat_and_complete(
    path: Path, patch_snapshot: Callable[[dict[str, Any]], None]
) -> None:
    case = load_case(path)
    patch_snapshot(case["datasets"])

    snapshot = sentiment_module.narrative_metrics(sentiment_module.compute_sentiment(DAY))
    assert set(snapshot) == set(KEYS)
    # 扁平：没有嵌套记录，AI 看不到个股明细
    for value in snapshot.values():
        assert not isinstance(value, dict)
        if isinstance(value, list):
            assert all(not isinstance(item, dict) for item in value)


def test_ai_cannot_overwrite_metric_keys() -> None:
    metrics = {"temperature": 71.4, "promotion_rate": 75.0, "missing": []}

    with pytest.raises(boundary.BoundaryViolation):
        boundary.merge_narrative(metrics, {"temperature": 99.0})
    with pytest.raises(boundary.BoundaryViolation):
        boundary.merge_narrative(metrics, {"commentary": "情绪回暖", "seal_rate": 88.0})
    with pytest.raises(boundary.BoundaryViolation):
        boundary.merge_narrative(metrics, {"summary": "自由发挥"})

    merged = boundary.merge_narrative(
        metrics, {"narrative": "情绪回暖", "degraded": False}
    )
    assert merged["temperature"] == 71.4  # 指标原样保留
    assert merged["ai"] == {"narrative": "情绪回暖", "degraded": False}
