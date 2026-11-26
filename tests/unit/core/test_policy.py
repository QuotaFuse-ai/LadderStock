"""F5 权限闸门的单测。"""

import json

import pytest

from ladderstock.core import policy


def test_default_when_file_missing(tmp_path):
    loaded = policy.load_policy(tmp_path)
    assert loaded["confirm_policy"] == policy.POLICY_ASK_ON_RISK
    assert loaded["scope"] == policy.SCOPE_SESSION


def test_save_and_load_roundtrip(tmp_path):
    saved = policy.save_policy("allow_all", scope="global", root=tmp_path)
    assert saved["confirm_policy"] == policy.POLICY_ALLOW_ALL
    assert saved["scope"] == policy.SCOPE_GLOBAL

    loaded = policy.load_policy(tmp_path)
    assert loaded["confirm_policy"] == policy.POLICY_ALLOW_ALL
    assert loaded["scope"] == policy.SCOPE_GLOBAL


def test_corrupt_file_falls_back(tmp_path):
    path = policy.policy_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert policy.load_policy(tmp_path)["confirm_policy"] == policy.DEFAULT_POLICY


def test_unknown_policy_rejected(tmp_path):
    with pytest.raises(policy.PolicyError):
        policy.save_policy("whatever", root=tmp_path)


def test_unknown_tier_rejected():
    with pytest.raises(policy.PolicyError):
        policy.needs_confirm("destroy")


def test_ask_on_risk_matrix():
    # 只读放行，采集与管理要问
    assert policy.needs_confirm(policy.TIER_READ, policy=policy.POLICY_ASK_ON_RISK) is False
    assert policy.needs_confirm(policy.TIER_COLLECT, policy=policy.POLICY_ASK_ON_RISK) is True
    assert policy.needs_confirm(policy.TIER_ADMIN, policy=policy.POLICY_ASK_ON_RISK) is True


def test_always_ask_matrix():
    assert policy.needs_confirm(policy.TIER_READ, policy=policy.POLICY_ALWAYS_ASK) is True
    assert policy.needs_confirm(policy.TIER_COLLECT, policy=policy.POLICY_ALWAYS_ASK) is True
    assert policy.needs_confirm(policy.TIER_ADMIN, policy=policy.POLICY_ALWAYS_ASK) is True


def test_allow_all_keeps_admin_gated():
    """全部允许放行取数，但不放行启停服务——那是改环境，不是查数据。"""

    assert policy.needs_confirm(policy.TIER_READ, policy=policy.POLICY_ALLOW_ALL) is False
    assert policy.needs_confirm(policy.TIER_COLLECT, policy=policy.POLICY_ALLOW_ALL) is False
    assert policy.needs_confirm(policy.TIER_ADMIN, policy=policy.POLICY_ALLOW_ALL) is True


def test_needs_confirm_reads_file(tmp_path):
    policy.save_policy("allow_all", root=tmp_path)
    assert policy.needs_confirm(policy.TIER_COLLECT, root=tmp_path) is False
    assert policy.needs_confirm(policy.TIER_ADMIN, root=tmp_path) is True


def test_describe_shape():
    info = policy.describe("ask_on_risk")
    assert info["confirm_policy"] == "ask_on_risk"
    assert info["tiers"] == {"read": True, "collect": False, "admin": False}
    assert set(info["labels"]) == {"always_ask", "ask_on_risk", "allow_all"}
