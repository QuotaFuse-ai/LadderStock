from __future__ import annotations

import pytest

from ladderstock.data import sectors


ENVELOPES = {
    "board_fund_flow": {
        "scope": "industry",
        "items": [
            {
                "code": "new_ysjs",
                "name": "有色金属",
                "change_pct": -1.49,
                "main_net": 930660131.22,
                "leader_code": "sh600111",
                "leader_name": "北方稀土",
            },
            {
                "code": "new_dlhy",
                "name": "电力行业",
                "change_pct": -0.25,
                "main_net": 567128843.84,
                "leader_code": "sh600863",
                "leader_name": "华能蒙电",
            },
        ],
    },
    "stock_concept_blocks": {
        "600111": {
            "boards": [
                {"name": "稀土", "code": "BK1626", "change_pct": 1.69, "lead_stock": "北方稀土"},
                {"name": "有色金属", "code": "BK0478", "change_pct": -1.89, "lead_stock": "西部黄金"},
            ]
        },
        "600863": {
            "boards": [
                # 名字对不上、领涨股却在别处也当 lead_stock：这条必须被判成错配
                {"name": "交通运输", "code": "BK1670", "change_pct": -0.20, "lead_stock": "华能蒙电"},
            ]
        },
    },
    "board_members": {
        "BK0478": {
            "items": [
                {"code": "000060", "name": "中金岭南", "change_pct": -0.63, "pe": 15.62},
                {"code": "000612", "name": "焦作万方", "change_pct": -1.40, "pe": 5.25},
                {"code": "000426", "name": "兴业银锡", "change_pct": -1.54, "pe": 12.26},
            ]
        }
    },
}


@pytest.fixture
def fake_call(monkeypatch):
    calls: list[tuple[str, dict]] = []

    def _call(tool, params, *, timeout=30.0):
        calls.append((tool, dict(params)))
        if tool == "board_fund_flow":
            return dict(ENVELOPES["board_fund_flow"])
        if tool == "stock_concept_blocks":
            code = str(params.get("code"))
            return dict(ENVELOPES["stock_concept_blocks"][code])
        if tool == "board_members":
            code = str(params.get("board_code"))
            return dict(ENVELOPES["board_members"][code])
        raise AssertionError(f"未预期的工具：{tool}")

    monkeypatch.setattr(sectors, "_call", _call)
    return calls


def test_board_list_uses_real_leader_codes(fake_call):
    rows = sectors.board_list("industry", limit=2)
    assert [r["board_name"] for r in rows] == ["有色金属", "电力行业"]
    assert rows[0]["leader_code"] == "600111"  # sh 前缀被剥掉
    assert rows[0]["main_net"] == 930660131.22


def test_resolve_exact_match(fake_call):
    code, how = sectors.resolve_board_code("有色金属", hint_code="600111")
    assert (code, how) == ("BK0478", "exact")


def test_resolve_accepts_bk_passthrough(fake_call):
    code, how = sectors.resolve_board_code("BK0478")
    assert (code, how) == ("BK0478", "exact")


def test_resolve_refuses_wrong_board_by_name(fake_call):
    # 名字不同词 + 默认不放开模糊匹配 → 如实空缺，而不是错配到交通运输
    code, how = sectors.resolve_board_code("电力行业", hint_code="600863", leader_name="华能蒙电")
    assert code is None
    assert how == "unresolved"


def test_resolve_fuzzy_is_opt_in_and_still_gated(fake_call):
    # 放开模糊匹配也要过涨幅校验（这里差 0.05 个点，允许）
    code, how = sectors.resolve_board_code(
        "电力行业", hint_code="600863", leader_name="华能蒙电", board_change_pct=-0.25, allow_fuzzy=True
    )
    assert (code, how) == ("BK1670", "leader")


def test_resolve_fuzzy_rejects_when_change_pct_far(fake_call):
    code, how = sectors.resolve_board_code(
        "电力行业", hint_code="600863", leader_name="华能蒙电", board_change_pct=+2.5, allow_fuzzy=True
    )
    assert code is None


def test_board_members_requires_bk_code(fake_call):
    with pytest.raises(sectors.SectorError):
        sectors.board_members("new_ysjs")


def test_board_members_parses_rows(fake_call):
    rows = sectors.board_members("BK0478", limit=3)
    assert [r["code"] for r in rows] == ["000060", "000612", "000426"]
    assert rows[0]["pe"] == 15.62


def test_sector_digest_marks_covered_and_sorted(fake_call, monkeypatch):
    monkeypatch.setattr(
        sectors, "covered_symbols", lambda symbols=None: {"000612": "2026-09-30"}
    )
    # 码表：一个真板块 + 一个状态榜噪音（必须被剔掉）
    monkeypatch.setattr(
        sectors,
        "board_code_map",
        lambda refresh=False, allow_build=True: {
            "有色金属": {
                "code": "BK0478",
                "change_pct": -1.49,
                "lead_stock": "北方稀土",
                "kinds": ["industry"],
            },
            "昨日连板_含一字": {
                "code": "BK1051",
                "change_pct": 4.17,
                "lead_stock": "真视通",
                "kinds": ["concept"],
            },
        },
    )
    payload = sectors.sector_digest("industry", limit_boards=2, members_per_board=3)

    assert payload["source"] == "codebook"
    assert [b["board_name"] for b in payload["boards"]] == ["有色金属"]

    ysjs = payload["boards"][0]
    assert ysjs["board_code_resolved"] == "BK0478"
    assert ysjs["matched_by"] == "codebook"
    # 涨幅降序：-0.63 > -1.40 > -1.54
    assert [m["code"] for m in ysjs["members"]] == ["000060", "000612", "000426"]
    assert ysjs["members_covered"] == ["000612"]
    assert ysjs["members"][1]["fundamentals_as_of"] == "2026-09-30"


def test_is_noise_board():
    assert sectors.is_noise_board("昨日连板_含一字")
    assert sectors.is_noise_board("ST股")
    assert sectors.is_noise_board("融资融券")
    assert not sectors.is_noise_board("半导体设备")
    assert not sectors.is_noise_board("稀土")


def test_sector_digest_falls_back_when_codebook_empty(fake_call, monkeypatch):
    monkeypatch.setattr(sectors, "covered_symbols", lambda symbols=None: {})
    monkeypatch.setattr(sectors, "board_code_map", lambda refresh=False, allow_build=True: {})
    payload = sectors.sector_digest("industry", limit_boards=2, members_per_board=3)
    # 退到新浪口径榜：有色金属靠同名能解析，电力行业如实留空
    assert payload["source"] == "board_rank"
    by_name = {b["board_name"]: b for b in payload["boards"]}
    assert by_name["有色金属"]["board_code_resolved"] == "BK0478"
    assert by_name["电力行业"]["members"] == []


def test_sector_digest_survives_upstream_error(fake_call, monkeypatch):
    def boom(tool, params, *, timeout=30.0):
        raise sectors.SectorError("QFSD-7001 rate limited")

    monkeypatch.setattr(sectors, "_call", boom)
    monkeypatch.setattr(sectors, "covered_symbols", lambda symbols=None: {})
    monkeypatch.setattr(sectors, "board_code_map", lambda refresh=False, allow_build=True: {})
    with pytest.raises(sectors.SectorError):
        sectors.sector_digest("industry", limit_boards=1)
