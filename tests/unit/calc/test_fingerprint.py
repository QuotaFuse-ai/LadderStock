from __future__ import annotations

import math

import pytest

from ladderstock.calc import CALC_VERSION, calc_id, canonical_params, data_fingerprint

FP = data_fingerprint(
    [{"date": "2026-11-03", "close": 10.1}, {"date": "2026-11-06", "close": 10.5}]
)


def test_calc_id_format() -> None:
    cid = calc_id("ma", {"window": 5}, data_fp=FP)
    assert cid.startswith("c_") and len(cid) == 10
    assert all(ch in "0123456789abcdef" for ch in cid[2:])


# ------------------------------------------------------------------ 稳定性三条


def test_same_input_same_id() -> None:
    a = calc_id("ma", {"window": 5, "field": "close"}, data_fp=FP)
    b = calc_id("ma", {"field": "close", "window": 5}, data_fp=FP)
    assert a == b


def test_changed_param_changes_id() -> None:
    base = calc_id("ma", {"window": 5}, data_fp=FP)
    assert calc_id("ma", {"window": 6}, data_fp=FP) != base
    assert calc_id("ma", {"window": 5, "extra": 0}, data_fp=FP) != base


def test_changed_version_changes_id() -> None:
    base = calc_id("ma", {"window": 5}, data_fp=FP)
    assert calc_id("ma", {"window": 5}, data_fp=FP, version="9.9.9") != base
    assert calc_id("ma", {"window": 5}, data_fp=FP, version=CALC_VERSION) == base


def test_changed_data_fp_changes_id() -> None:
    other = data_fingerprint([{"date": "2026-11-03", "close": 10.2}])
    assert calc_id("ma", {"window": 5}, data_fp=FP) != calc_id("ma", {"window": 5}, data_fp=other)


# ------------------------------------------------------------------ 规范化规则


def test_none_differs_from_absent() -> None:
    assert canonical_params({"a": None}) != canonical_params({})
    assert canonical_params({"a": None}) != canonical_params({"a": 0})


def test_float_uniform_repr() -> None:
    # 0.1 与 0.1 + 0.2 位级不同，必须得到不同规范串
    assert canonical_params({"x": 0.1}) != canonical_params({"x": 0.1 + 0.2})
    # int 1 与 float 1.0 也是不同参数
    assert canonical_params({"x": 1}) != canonical_params({"x": 1.0})
    # 同一浮点值无论来源如何，规范串一致
    assert canonical_params({"x": 0.5}) == canonical_params({"x": 1 / 2})
    # nan / inf 不抛错（规范串只做哈希），但彼此可区分
    assert canonical_params({"x": math.nan}) != canonical_params({"x": math.inf})


def test_nested_keys_sorted() -> None:
    a = canonical_params({"b": {"z": 1, "a": [{"y": 2, "x": 3}]}, "a": 0})
    b = canonical_params({"a": 0, "b": {"a": [{"x": 3, "y": 2}], "z": 1}})
    assert a == b


# ------------------------------------------------------------------ 数据指纹


def test_data_fingerprint_shape() -> None:
    rows = [
        {"date": "2026-11-03", "open": 1.0, "close": 1.1},
        {"date": "2026-11-04", "open": 1.1, "close": 1.2},
        {"date": "2026-11-06", "open": 1.2, "close": 1.3},
    ]
    fp = data_fingerprint(rows)
    n, first, last, digest = fp.split(":")
    assert (n, first, last) == ("3", "2026-11-03", "2026-11-06")
    assert len(digest) == 16
    assert data_fingerprint(rows) == fp


def test_data_fingerprint_sensitive_to_content() -> None:
    a = data_fingerprint([{"date": "2026-11-03", "close": 10.0}])
    b = data_fingerprint([{"date": "2026-11-03", "close": 10.0001}])
    assert a != b


def test_data_fingerprint_sensitive_to_order() -> None:
    r1 = [{"date": "2026-11-03", "close": 1.0}, {"date": "2026-11-04", "close": 2.0}]
    assert data_fingerprint(r1) != data_fingerprint(list(reversed(r1)))


def test_data_fingerprint_none_vs_empty() -> None:
    assert data_fingerprint(None) == "none"
    assert data_fingerprint([]) == "0::-:"


def test_calc_id_requires_op_and_fp() -> None:
    with pytest.raises(ValueError):
        calc_id("", {}, data_fp=FP)
    with pytest.raises(ValueError):
        calc_id("ma", {}, data_fp="")
