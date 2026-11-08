"""QUOT-488：档案层核心（状态判定 / valid_days 归一 / 截断 / gate / 边界）。"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from ladderstock.knowledge.gate import GateViolation, find_secret_hits, gate_payload
from ladderstock.knowledge.model import (
    KnowledgeError,
    normalize_market,
    normalize_status,
    normalize_symbol,
    normalize_valid_days,
    parse_frontmatter,
    sh_today,
)
from ladderstock.knowledge.recall import (
    KNOWLEDGE_BEGIN,
    KNOWLEDGE_END,
    truncate_by_section,
    wrap_for_prompt,
)


def _sh_now(hour: int) -> datetime:
    """UTC 时刻 → Asia/Shanghai 当日（16:00 UTC = 次日 00:00 SH）。"""
    return datetime(2026, 11, 8, hour, 0, tzinfo=UTC)


def test_sh_today_rolls_at_utc_16() -> None:
    assert sh_today(_sh_now(15)) == date(2026, 11, 8)
    assert sh_today(_sh_now(16)) == date(2026, 11, 9)


@pytest.mark.parametrize("bad", ["XX", "sh:", "", "US", "hk", None])
def test_market_enum_rejects(bad: str) -> None:
    with pytest.raises(KnowledgeError):
        normalize_market(bad)


@pytest.mark.parametrize("good", ["SH", "sz", "BJ", "SZ"])
def test_market_enum_accepts(good: str) -> None:
    assert normalize_market(good) == good.upper()


@pytest.mark.parametrize(
    "bad", ["../../etc", "60000", "6000000", "AB1234", "600000.SH/..", "", None]
)
def test_symbol_shape_rejects(bad: str) -> None:
    with pytest.raises(KnowledgeError):
        normalize_symbol(bad)


def test_symbol_strips_exchange_suffix() -> None:
    assert normalize_symbol("600000.SH") == "600000"


def test_valid_days_falls_back_on_garbage() -> None:
    assert normalize_valid_days("1e100") == 90
    assert normalize_valid_days(-1) == 90
    assert normalize_valid_days("abc") == 90
    assert normalize_valid_days(3651) == 90
    assert normalize_valid_days(30) == 30
    assert normalize_valid_days(1) == 1


def test_status_strict_rejects_unknown() -> None:
    with pytest.raises(KnowledgeError):
        normalize_status("expired")
    assert normalize_status("STALE") == "stale"


def test_frontmatter_parses_inline_comments_and_quotes() -> None:
    fm = parse_frontmatter('---\nsymbol: "600000"\nmarket: SH  # 注释\nvalid_days: 30\n---\n')
    assert fm["symbol"] == "600000"
    assert fm["market"] == "SH"
    assert fm["valid_days"] == "30"


def test_truncate_caps_at_limit_and_keeps_sections() -> None:
    body = "## 1. 业务\n" + ("甲" * 15000) + "\n## 2. 关键数据\n" + ("乙" * 5000)
    text, truncated = truncate_by_section(body, 12000)
    assert truncated is True
    assert len(text) <= 12000
    assert "## 1. 业务" in text  # 章节结构保留


def test_truncate_short_body_untouched() -> None:
    text, truncated = truncate_by_section("## A\n短文", 12000)
    assert truncated is False
    assert text == "## A\n短文"


def test_truncate_zero_budget_returns_empty() -> None:
    text, truncated = truncate_by_section("## A\n内容", 0)
    assert truncated is True
    assert text == ""


def test_wrap_for_prompt_bounds_untrusted_data() -> None:
    wrapped = wrap_for_prompt("正文")
    assert wrapped.startswith(KNOWLEDGE_BEGIN + "\n")
    assert wrapped.endswith(KNOWLEDGE_END)
    assert "正文" in wrapped


# --------------------------------------------------------------------------- #
# gate
# --------------------------------------------------------------------------- #


def test_gate_rejects_openai_key_form() -> None:
    with pytest.raises(GateViolation):
        gate_payload([], "token = sk-abcdefghij1234567890abcd")


def test_gate_rejects_hex_blob() -> None:
    assert find_secret_hits("凭据 " + "a" * 40)


def test_gate_rejects_private_keys_recursively() -> None:
    with pytest.raises(GateViolation):
        gate_payload({"facts": {"positions": []}}, "")


def test_gate_passes_clean_text() -> None:
    gate_payload({"metric": 1}, "普通投研正文，没有密钥。")
