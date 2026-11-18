"""长期偏好沉淀（`ai/preferences.py`）：抽取规则、落盘往返、注入段。

这一层的风险是**错抽**——一条错抽的偏好会污染之后每一轮 prompt，所以用例的重点
不在「抽得多全」，而在「该抽的抽到、不该抽的一律不抽」。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ladderstock.ai import preferences


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    return tmp_path


# --------------------------------------------------------------------------- #
# 抽取：该抽的抽到
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("question", "kind", "text"),
    [
        ("我只看创业板和半导体", "scope", "只看创业板和半导体"),
        ("我主要做港股", "scope", "只做港股"),
        ("以后回答都用短句", "style", "回答用短句"),
        ("回答请用表格", "style", "回答用表格"),
        ("不要给我买卖建议", "constraint", "不要买卖建议"),
        ("别太长了", "style", "回答别太长"),
        ("帮我盯着 600036", "watch", "跟踪 600036"),
        ("记住：我不看北交所", "scope", "我不看北交所"),
    ],
)
def test_candidates_extract(question: str, kind: str, text: str) -> None:
    assert preferences.candidates(question) == [(kind, text)]


# --------------------------------------------------------------------------- #
# 抽取：不该抽的一律不抽（错抽的代价比漏抽高得多）
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "question",
    [
        "浦发银行今天为什么涨了",  # 行情提问，不是设定
        "半导体板块今天怎么样",  # 陈述板块，不是自述偏好
        "我看好白酒",  # 观点，不是偏好
        "帮我查一下贵州茅台最近的公告",  # 动作，不是设定
        "",
        "   ",
    ],
)
def test_candidates_ignore(question: str) -> None:
    assert preferences.candidates(question) == []


def test_candidates_explicit_short_circuit() -> None:
    """「记住 X」是显式要求：整句只收这一条，不再叠加句式匹配。"""
    found = preferences.candidates("记住我不看北交所，另外茅台多少钱")
    assert len(found) == 1
    assert found[0][1].startswith("我不看北交所")


def test_candidates_dedupes_within_sentence() -> None:
    """同一句里两个句式都命中时只留一条。"""
    found = preferences.candidates("以后回答都用短句，回答用短句")
    assert len(found) == 1


# --------------------------------------------------------------------------- #
# 落盘往返
# --------------------------------------------------------------------------- #


def test_remember_persists_and_counts(root: Path) -> None:
    first = preferences.remember("我只看创业板", data_root=root)
    assert [item.text for item in first] == ["只看创业板"]
    assert preferences.load(root)[0].hits == 1

    again = preferences.remember("我只看创业板", data_root=root)
    assert again[0].hits == 2
    # 重复提出不该新增第二条
    assert len(preferences.load(root)) == 1


def test_remember_noop_when_nothing_extracted(root: Path) -> None:
    assert preferences.remember("浦发银行今天多少钱", data_root=root) == []
    assert not preferences.path_for(root).exists()


def test_load_tolerates_corrupt_file(root: Path) -> None:
    path = preferences.path_for(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json", encoding="utf-8")
    assert preferences.load(root) == []


def test_save_is_atomic_leftover_tmp_not_used(root: Path) -> None:
    """落盘走 tmp + replace：正常路径下不该留 .tmp 残留。"""
    preferences.remember("以后回答都用短句", data_root=root)
    tmp = preferences.path_for(root).with_suffix(".json.tmp")
    assert not tmp.exists()


def test_trim_keeps_limit(root: Path) -> None:
    for index in range(preferences.MAX_ITEMS + 5):
        preferences.remember(f"帮我盯着 6000{index:02d}", data_root=root)
    assert len(preferences.load(root)) == preferences.MAX_ITEMS


def test_clear(root: Path) -> None:
    preferences.remember("以后回答都用短句", data_root=root)
    assert preferences.clear(root) is True
    assert preferences.clear(root) is False
    assert preferences.load(root) == []


# --------------------------------------------------------------------------- #
# 注入段
# --------------------------------------------------------------------------- #


def test_render_empty_returns_blank(root: Path) -> None:
    assert preferences.render([], limit=12) == ""


def test_render_contains_header_and_kind_label(root: Path) -> None:
    preferences.remember("我只看创业板", data_root=root)
    preferences.remember("不要给我买卖建议", data_root=root)
    block = preferences.render(preferences.load(root))
    assert preferences.SECTION_HEADER in block
    assert "[关注范围] 只看创业板" in block
    assert "[约束] 不要买卖建议" in block


def test_render_orders_by_hits(root: Path) -> None:
    preferences.remember("以后回答都用短句", data_root=root)
    preferences.remember("我只看创业板", data_root=root)
    preferences.remember("我只看创业板", data_root=root)
    block = preferences.render(preferences.load(root))
    assert block.index("只看创业板") < block.index("回答用短句")


def test_as_dict_shape(root: Path) -> None:
    preferences.remember("我只看创业板", data_root=root)
    payload = preferences.as_dict(preferences.load(root))
    assert payload["count"] == 1
    assert payload["items"][0]["kind"] == "scope"
    assert payload["items"][0]["text"] == "只看创业板"


def test_payload_is_json_serializable(root: Path) -> None:
    preferences.remember("我只看创业板", data_root=root)
    raw = json.loads(preferences.path_for(root).read_text(encoding="utf-8"))
    assert raw["version"] == 1
    assert isinstance(raw["items"], list)
