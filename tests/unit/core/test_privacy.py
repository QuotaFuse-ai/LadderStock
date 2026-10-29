"""隐私出口唯一实现（audit R-2 / R-3）：递归剥离与地址脱敏。"""

from __future__ import annotations

from ladderstock.core.privacy import (
    PRIVATE_KEYS,
    UNPARSEABLE_URL,
    mask_url,
    private_keys_present,
    strip_private,
)


def test_strip_private_recurses_into_nested_structures() -> None:
    payload = {
        "date": "2026-07-01",
        "sections": [
            {"title": "纪律", "journal": {"note": "私货"}, "items": [{"positions": [1, 2]}]},
        ],
        "meta": {"交易日志": [{"note": "也不许出"}]},
        "keep": 1,
    }
    cleaned = strip_private(payload)
    assert cleaned == {
        "date": "2026-07-01",
        "sections": [{"title": "纪律", "items": [{}]}],
        "meta": {},
        "keep": 1,
    }
    assert "journal" not in str(cleaned)
    assert "交易日志" not in str(cleaned)


def test_strip_private_is_case_insensitive_on_keys() -> None:
    assert strip_private({"Journal": 1, "TRADES": 2, "ok": 3}) == {"ok": 3}


def test_strip_private_leaves_non_container_values_alone() -> None:
    assert strip_private("journal") == "journal"
    assert strip_private(None) is None
    assert strip_private(7) == 7


def test_private_keys_present_lists_each_hit_once() -> None:
    found = private_keys_present(
        {"journal": 1, "nested": {"trades": 2, "again": {"journal": 3}}, "ok": 4}
    )
    assert found == ["journal", "trades"]
    assert private_keys_present({"ok": 1}) == []


def test_every_private_key_is_stripped() -> None:
    for key in PRIVATE_KEYS:
        assert strip_private({key: {"x": 1}}) == {}


def test_mask_url_drops_userinfo_query_and_fragment() -> None:
    masked = mask_url("http://user:pa55@127.0.0.1:8000/mcp?token=secret#frag")
    assert masked == "http://127.0.0.1:8000/mcp"
    assert "secret" not in masked
    assert "pa55" not in masked


def test_mask_url_keeps_bracketed_ipv6_and_port() -> None:
    assert mask_url("http://[::1]:8791/mcp?key=x") == "http://[::1]:8791/mcp"


def test_mask_url_handles_empty_and_unparseable() -> None:
    assert mask_url("") == ""
    assert mask_url(None) == ""
    assert mask_url("not-a-url") == UNPARSEABLE_URL
    assert mask_url("http://host:bad/mcp") == "http://host/mcp"
