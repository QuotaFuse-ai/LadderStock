"""答案块协议：payload 校验 + §6.4 顺序/数量 + §7 装配规则（阶段 A）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ladderstock.ai import answer_blocks
from ladderstock.ai.answer_blocks import (
    AnswerBlockError,
    build_block,
    build_blocks,
)
from ladderstock.core.boundary import Provenance

ROOT = Path(__file__).resolve().parents[3]
SAMPLES = ROOT / "tests" / "fixtures" / "answer_blocks"

AS_OF = "2026-11-11"
FETCHED_AT = "2026-11-11T15:02:01+08:00"


def _pv(key: str, value: float | int, unit: str = "") -> Provenance:
    return Provenance(
        value=value,
        unit=unit,
        source="computed",
        fetched_at=FETCHED_AT,
        as_of=AS_OF,
        endpoint="review.pipeline.collect_metrics",
        verified=True,
        metric=key,
    )


def _fixed_metrics() -> dict[str, object]:
    return {
        "temperature": _pv("temperature", 62.0),
        "promotion_rate": _pv("promotion_rate", 41.5, "%"),
        "limit_up_count": _pv("limit_up_count", 53, "家"),
        "gap_levels": _pv("gap_levels", [["2026-10-15", 1], ["2026-10-16", -1]]),
        "table": {
            "columns": ["指标", "2026Q3", "2025Q3"],
            "rows": [["营业收入(亿)", 78.2, 71.4]],
            "caption": "近四季度营收",
        },
    }


def _fixed_ai() -> dict[str, object]:
    return {
        "narrative": ["近 20 日震荡偏弱，主力资金连续净流出。", "板块分化明显。"],
        "highlights": ["关注量能变化"],
        "risks": ["跌破 38 元支撑"],
    }


def _fixed_sources() -> list[dict[str, object]]:
    return [{"name": "腾讯行情", "fetched_at": FETCHED_AT, "url": "https://example.com/quote"}]


def _full_blocks() -> list[dict[str, object]]:
    return build_blocks(_fixed_metrics(), _fixed_ai(), _fixed_sources())


# --------------------------------------------------------------------------- #
# §6.4 顺序与数量：五条规则各一个用例
# --------------------------------------------------------------------------- #


def test_rule1_first_block_is_conclusion() -> None:
    blocks = _full_blocks()
    assert blocks[0]["type"] == "conclusion"


def test_rule2_citation_is_last_when_present() -> None:
    blocks = _full_blocks()
    types = [block["type"] for block in blocks]
    assert types.count("citation") == 1
    assert types[-1] == "citation"


def test_rule3_notice_never_first() -> None:
    blocks = build_blocks({}, {}, [])
    types = [block["type"] for block in blocks]
    assert "notice" in types
    assert types[0] != "notice"


def test_rule4_total_within_limit() -> None:
    assert len(_full_blocks()) <= answer_blocks.MAX_BLOCKS
    assert answer_blocks.MAX_BLOCKS == 6


def test_rule5_failure_is_single_notice_and_no_conclusion() -> None:
    failure = {"kind": "network", "message": "AI 请求未送达", "action": "检查网络后重试"}

    blocks = build_blocks(_fixed_metrics(), _fixed_ai(), _fixed_sources(), failure=failure)

    assert blocks == [
        {
            "type": "notice",
            "payload": {
                "kind": "network",
                "message": "AI 请求未送达",
                "actions": ["检查网络后重试"],
            },
        }
    ]
    assert all(block["type"] != "conclusion" for block in blocks)


def test_ordering_rejects_blocks_over_limit() -> None:
    conclusion = {"type": "conclusion", "payload": {"text": "判断", "tone": "neutral"}}
    notice = {"type": "notice", "payload": {"kind": "degraded", "message": "说明"}}
    blocks = [conclusion] + [notice] * (answer_blocks.MAX_BLOCKS - 1)

    answer_blocks.check_ordering(blocks)  # 正好等于上限：通过

    with pytest.raises(AnswerBlockError):
        answer_blocks.check_ordering(blocks + [notice])


def test_ordering_rejects_citation_not_last_and_notice_first() -> None:
    conclusion = {"type": "conclusion", "payload": {"text": "判断", "tone": "neutral"}}
    notice = {"type": "notice", "payload": {"kind": "degraded", "message": "说明"}}
    citation = {
        "type": "citation",
        "payload": {"items": [{"name": "腾讯行情", "fetched_at": FETCHED_AT}]},
    }

    with pytest.raises(AnswerBlockError):
        answer_blocks.check_ordering([notice, conclusion])
    with pytest.raises(AnswerBlockError):
        answer_blocks.check_ordering([conclusion, citation, notice])


def test_validate_blocks_returns_copies_not_aliases() -> None:
    blocks = _full_blocks()
    cleaned = answer_blocks.validate_blocks(blocks)
    assert cleaned == blocks
    cleaned[0]["payload"]["text"] = "改过"
    assert blocks[0]["payload"]["text"] != "改过"


# --------------------------------------------------------------------------- #
# 数字剥离：conclusion 无数字，原数字以数值形态进 metric
# --------------------------------------------------------------------------- #


def test_numbers_are_stripped_from_conclusion_and_kept_in_metric_as_number() -> None:
    metrics = {"temperature": _pv("temperature", 62.0)}
    ai = {"narrative": ["情绪温度 62，市场偏暖。"]}

    blocks = build_blocks(metrics, ai, _fixed_sources())

    conclusion = blocks[0]
    assert conclusion["type"] == "conclusion"
    assert not answer_blocks.contains_number(conclusion["payload"]["text"])

    metric = next(block for block in blocks if block["type"] == "metric")
    first = metric["payload"]["items"][0]
    assert first["value"] == 62.0
    assert isinstance(first["value"], float)
    assert not isinstance(first["value"], str)
    assert first["label"] == "情绪温度"


def test_strip_numbers_removes_digits_but_keeps_words() -> None:
    assert answer_blocks.strip_numbers("近 20 日震荡偏弱") == "近日震荡偏弱"
    assert not answer_blocks.contains_number(answer_blocks.strip_numbers("营收同比 78.2 亿元"))


# --------------------------------------------------------------------------- #
# sources = [] / metrics = {} / 无出处
# --------------------------------------------------------------------------- #


def test_empty_sources_emit_notice_matching_source_note() -> None:
    blocks = build_blocks(_fixed_metrics(), _fixed_ai(), [], page_data=False)

    assert all(block["type"] != "citation" for block in blocks)
    messages = [block["payload"]["message"] for block in blocks if block["type"] == "notice"]
    assert answer_blocks.SOURCE_NOTE_WITHOUT_DATA in messages
    # 与 web/chat.py `_source_note` 逐字一致（同一句、只出一次）
    from ladderstock.web.chat import _source_note

    assert _source_note(has_data=False, sources=[]) == answer_blocks.SOURCE_NOTE_WITHOUT_DATA


def test_empty_sources_with_page_data_use_no_record_note() -> None:
    blocks = build_blocks(_fixed_metrics(), _fixed_ai(), [], page_data=True)

    messages = [block["payload"]["message"] for block in blocks if block["type"] == "notice"]
    assert answer_blocks.SOURCE_NOTE_NO_RECORD in messages
    assert answer_blocks.SOURCE_NOTE_WITHOUT_DATA not in messages

    from ladderstock.web.chat import _source_note

    assert _source_note(has_data=True, sources=[]) == answer_blocks.SOURCE_NOTE_NO_RECORD


def test_sources_missing_fetched_at_fall_back_to_notice() -> None:
    blocks = build_blocks(
        _fixed_metrics(), _fixed_ai(), [{"name": "腾讯行情", "fetched_at": ""}], page_data=False
    )

    assert all(block["type"] != "citation" for block in blocks)
    assert any(
        block["type"] == "notice"
        and block["payload"]["kind"] == "missing_data"
        and block["payload"]["message"] == answer_blocks.SOURCE_NOTE_WITHOUT_DATA
        for block in blocks
    )


def test_source_missing_symbol_and_as_of_stays_empty_not_defaulted() -> None:
    blocks = _full_blocks()
    citation = blocks[-1]
    assert citation["type"] == "citation"
    item = citation["payload"]["items"][0]
    assert item["symbol"] is None
    assert item["as_of"] is None


def test_empty_metrics_emit_missing_data_notice_instead_of_metric() -> None:
    blocks = build_blocks({}, _fixed_ai(), _fixed_sources())

    assert all(block["type"] != "metric" for block in blocks)
    assert any(
        block["type"] == "notice" and block["payload"]["kind"] == "missing_data" for block in blocks
    )
    assert blocks[-1]["type"] == "citation"


def test_metrics_without_provenance_are_not_displayed() -> None:
    blocks = build_blocks({"temperature": 62.0}, _fixed_ai(), _fixed_sources())

    assert all(block["type"] != "metric" for block in blocks)
    missing = [
        block
        for block in blocks
        if block["type"] == "notice" and block["payload"]["kind"] == "missing_data"
    ]
    assert any(block["payload"]["section"] == "指标" for block in missing)


def test_unverified_provenance_is_not_displayed() -> None:
    unverified = Provenance(
        value=62.0,
        unit="",
        source="web",
        fetched_at=FETCHED_AT,
        as_of=AS_OF,
        url="https://example.com",
        verified=False,
        metric="temperature",
    )
    blocks = build_blocks({"temperature": unverified}, _fixed_ai(), _fixed_sources())
    assert all(block["type"] != "metric" for block in blocks)


def test_metric_carries_only_whitelisted_keys() -> None:
    blocks = build_blocks(
        {**_fixed_metrics(), "not_whitelisted": _pv("not_whitelisted", 1.0)},
        _fixed_ai(),
        _fixed_sources(),
    )
    metric = next(block for block in blocks if block["type"] == "metric")
    labels = [item["label"] for item in metric["payload"]["items"]]
    assert labels == ["情绪温度", "晋级率", "涨停家数"]


# --------------------------------------------------------------------------- #
# chart / table
# --------------------------------------------------------------------------- #


def test_series_metric_becomes_chart() -> None:
    chart = next(block for block in _full_blocks() if block["type"] == "chart")
    assert chart["payload"]["kind"] == "line"
    assert chart["payload"]["series"][0]["name"] == "断层档位"


def test_long_series_is_downsampled_and_caption_says_so() -> None:
    points = [[f"2026-01-{index % 28 + 1:02d}", float(index)] for index in range(200)]
    metrics = {"gap_levels": _pv("gap_levels", points)}
    blocks = build_blocks(metrics, _fixed_ai(), _fixed_sources())

    chart = next(block for block in blocks if block["type"] == "chart")
    series = chart["payload"]["series"][0]["points"]
    assert len(series) <= answer_blocks.MAX_CHART_POINTS
    assert series[-1] == points[-1]
    assert "降采样" in chart["payload"]["caption"]


@pytest.mark.parametrize("total", [240, 360, 480, 1200])
def test_downsample_at_multiples_of_120_stays_within_limit(total: int) -> None:
    """回归：n 为 120 整数倍时，保尾补点曾把结果推到 121 点 → validate_chart 崩。"""
    points = [[f"d{index}", float(index)] for index in range(total)]
    metrics = {"gap_levels": _pv("gap_levels", points)}

    blocks = build_blocks(metrics, _fixed_ai(), _fixed_sources())

    chart = next(block for block in blocks if block["type"] == "chart")
    series = chart["payload"]["series"][0]["points"]
    assert len(series) <= answer_blocks.MAX_CHART_POINTS
    assert series[0] == points[0]
    assert series[-1] == points[-1]
    assert "降采样" in chart["payload"]["caption"]
    assert answer_blocks.validate_chart(chart["payload"])["series"][0]["points"] == series


@pytest.mark.parametrize("total", [240, 360, 480])
def test_downsample_helper_never_exceeds_max_points(total: int) -> None:
    points = [[f"d{index}", float(index)] for index in range(total)]
    sampled = answer_blocks._downsample(points)

    assert len(sampled) == answer_blocks.MAX_CHART_POINTS
    assert sampled[0] == points[0]
    assert sampled[-1] == points[-1]
    assert sampled == sorted(sampled, key=lambda point: point[1])


def test_absolute_metrics_carry_no_direction() -> None:
    """绝对量（家数 / 温度）恒为正，不能按符号标成「上涨」。"""
    blocks = build_blocks(_fixed_metrics(), _fixed_ai(), _fixed_sources())
    metric = next(block for block in blocks if block["type"] == "metric")

    assert metric["payload"]["items"]
    assert all("direction" not in item for item in metric["payload"]["items"])


def test_direction_follows_delta_when_present() -> None:
    metrics = {
        **_fixed_metrics(),
        "limit_up_count_delta": _pv("limit_up_count_delta", -3),
        "temperature_delta": _pv("temperature_delta", 0),
        "broken_count_delta": _pv("broken_count_delta", 5),
    }
    blocks = build_blocks(metrics, _fixed_ai(), _fixed_sources())
    metric = next(block for block in blocks if block["type"] == "metric")
    by_label = {item["label"]: item for item in metric["payload"]["items"]}

    assert by_label["涨停家数"]["direction"] == "down"
    assert by_label["情绪温度"]["direction"] == "flat"
    assert "direction" not in by_label["晋级率"]


def test_table_over_five_rows_is_collapsed_with_real_total() -> None:
    rows = [[f"行{index}", float(index)] for index in range(12)]
    metrics = {
        "temperature": _pv("temperature", 62.0),
        "table": {"columns": ["项目", "值"], "rows": rows},
    }
    blocks = build_blocks(metrics, _fixed_ai(), _fixed_sources())

    table = next(block for block in blocks if block["type"] == "table")
    assert table["payload"]["collapsed"] is True
    assert table["payload"]["rows_total"] == 12
    assert len(table["payload"]["rows"]) <= answer_blocks.TABLE_COLLAPSE_ROWS


def test_too_many_blocks_stay_within_limit_and_keep_conclusion_and_citation() -> None:
    ai = {
        "narrative": ["结论一。", "补充一", "补充二", "补充三", "补充四"],
        "highlights": ["要点一", "要点二"],
        "risks": ["风险一", "风险二"],
    }
    metrics = {
        **_fixed_metrics(),
        "table": {"columns": ["项目", "值"], "rows": [["a", 1.0], ["b", 2.0], ["c", 3.0]]},
    }
    blocks = build_blocks(metrics, ai, _fixed_sources())

    assert len(blocks) <= answer_blocks.MAX_BLOCKS
    assert blocks[0]["type"] == "conclusion"
    assert blocks[-1]["type"] == "citation"


# --------------------------------------------------------------------------- #
# payload 校验：多余键 / 错误类型 / 越界枚举 / 超长序列
# --------------------------------------------------------------------------- #


def test_conclusion_rejects_extra_key() -> None:
    with pytest.raises(AnswerBlockError):
        answer_blocks.validate_conclusion({"text": "判断", "tone": "neutral", "value": 1})


def test_conclusion_rejects_number_in_text() -> None:
    with pytest.raises(AnswerBlockError):
        answer_blocks.validate_conclusion({"text": "上涨 3%", "tone": "positive"})


def test_conclusion_rejects_bad_tone() -> None:
    with pytest.raises(AnswerBlockError):
        answer_blocks.validate_conclusion({"text": "判断", "tone": "bullish"})


def test_conclusion_rejects_confidence_out_of_range() -> None:
    with pytest.raises(AnswerBlockError):
        answer_blocks.validate_conclusion({"text": "判断", "tone": "neutral", "confidence": 1.5})


def test_metric_rejects_string_value() -> None:
    with pytest.raises(AnswerBlockError):
        answer_blocks.validate_metric({"items": [{"label": "收盘价", "value": "38.62"}]})


def test_metric_rejects_bad_direction() -> None:
    with pytest.raises(AnswerBlockError):
        answer_blocks.validate_metric(
            {"items": [{"label": "收盘价", "value": 38.62, "direction": "sideways"}]}
        )


def test_metric_rejects_more_than_four_items() -> None:
    items = [{"label": f"项{i}", "value": float(i)} for i in range(5)]
    with pytest.raises(AnswerBlockError):
        answer_blocks.validate_metric({"items": items})


def test_chart_rejects_bad_kind() -> None:
    with pytest.raises(AnswerBlockError):
        answer_blocks.validate_chart(
            {"kind": "pie", "series": [{"name": "收盘价", "points": [["2026-10-15", 1.0]]}]}
        )


def test_chart_rejects_more_than_120_points() -> None:
    points = [[f"i{index}", float(index)] for index in range(answer_blocks.MAX_CHART_POINTS + 1)]
    with pytest.raises(AnswerBlockError):
        answer_blocks.validate_chart(
            {"kind": "line", "series": [{"name": "收盘价", "points": points}]}
        )


def test_table_rejects_column_count_mismatch() -> None:
    with pytest.raises(AnswerBlockError):
        answer_blocks.validate_table({"columns": ["a", "b"], "rows": [["x"]]})


def test_notice_rejects_unknown_kind() -> None:
    with pytest.raises(AnswerBlockError):
        answer_blocks.validate_notice({"kind": "aborted", "message": "中断"})


def test_notice_allows_missing_data_kind() -> None:
    payload = answer_blocks.validate_notice(
        {"kind": "missing_data", "section": "分红", "message": "本页没有取到该标的的分红记录"}
    )
    assert payload["kind"] == "missing_data"


def test_action_rejects_route_outside_registry() -> None:
    with pytest.raises(AnswerBlockError):
        answer_blocks.validate_action(
            {"items": [{"label": "展开完整面板", "action": "open_screen", "target": "/nope"}]}
        )


def test_action_rejects_unknown_block_type() -> None:
    with pytest.raises(AnswerBlockError):
        build_block("chart_v2", {})


def test_ai_payload_rejects_metric_keys() -> None:
    with pytest.raises(AnswerBlockError):
        answer_blocks.normalize_ai({"narrative": ["判断"], "temperature": 62})


# --------------------------------------------------------------------------- #
# 固定样例 JSON：阶段 E 直接当 fixture
# --------------------------------------------------------------------------- #

SAMPLE_NAMES = (
    "conclusion",
    "metric",
    "chart",
    "table",
    "citation",
    "notice",
    "action",
)


@pytest.mark.parametrize("name", SAMPLE_NAMES)
def test_sample_payloads_are_valid(name: str) -> None:
    sample = json.loads((SAMPLES / f"{name}.json").read_text(encoding="utf-8"))

    cleaned = build_block(sample["type"], sample["payload"])

    assert cleaned["type"] == name
    assert cleaned == sample or name == "table"


def test_samples_cover_all_block_types() -> None:
    files = {path.stem for path in SAMPLES.glob("*.json")} - {"full_answer"}
    assert set(SAMPLE_NAMES) == set(answer_blocks.BLOCK_TYPES) == files


def test_recorded_full_answer_rebuilds_identically() -> None:
    recorded = json.loads((SAMPLES / "full_answer.json").read_text(encoding="utf-8"))

    blocks = build_blocks(recorded["metrics"], recorded["ai"], recorded["sources"])

    assert blocks == recorded["blocks"]
