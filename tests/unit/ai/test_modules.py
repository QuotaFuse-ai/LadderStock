"""三层模型配置（QUOT-498）：角色 > 子模块 > 全局。

覆盖组合矩阵（≥8 组）、来源回显、链替换语义、`modules` 整段缺失时的兼容行为，
以及读取期的各类配置错误文案。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ladderstock.ai import fallback
from ladderstock.core.boundary import BoundaryViolation

BACKENDS = """
  backends:
    - id: openai
      type: openai
      models: [model-a, model-b]
    - id: anthropic
      type: anthropic
      models: [claude-sonnet-4-5]
  default_model: openai:model-a
  fallback_chain: [openai:model-b]
"""


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "ai.yaml"
    path.write_text("ai:\n" + body, encoding="utf-8")
    return path


def _load(tmp_path: Path, body: str) -> fallback.AiConfig:
    _write(tmp_path, body)
    return fallback.load_config(tmp_path)


# --------------------------------------------------------------------------- #
# 组合矩阵：每一行独立断言 (value, source)
# --------------------------------------------------------------------------- #

MATRIX = [
    # 无 modules 段 → 全落全局
    (BACKENDS, "tickhub", None, "openai:model-a", "global"),
    (BACKENDS, "senate", "bull", "openai:model-a", "global"),
    # 模块覆盖
    (
        BACKENDS + "  modules:\n    tickhub:\n      model: anthropic:claude-sonnet-4-5\n",
        "tickhub",
        None,
        "anthropic:claude-sonnet-4-5",
        "module",
    ),
    # 模块覆盖不影响别的模块
    (
        BACKENDS + "  modules:\n    tickhub:\n      model: openai:model-b\n",
        "wire",
        None,
        "openai:model-a",
        "global",
    ),
    # 角色 > 模块
    (
        BACKENDS
        + "  modules:\n    senate:\n      model: openai:model-b\n"
        + "      roles:\n        bull: {model: anthropic:claude-sonnet-4-5}\n",
        "senate",
        "bull",
        "anthropic:claude-sonnet-4-5",
        "role",
    ),
    # 有角色但仍落到模块档（角色没配）
    (
        BACKENDS
        + "  modules:\n    senate:\n      model: openai:model-b\n"
        + "      roles:\n        bull: {model: anthropic:claude-sonnet-4-5}\n",
        "senate",
        "bear",
        "openai:model-b",
        "module",
    ),
    # 只有角色、模块档为空 → 角色命中，其它角色回全局
    (
        BACKENDS
        + "  modules:\n    senate:\n      roles:\n"
        + "        bear: {model: anthropic:claude-sonnet-4-5}\n",
        "senate",
        "bear",
        "anthropic:claude-sonnet-4-5",
        "role",
    ),
    (
        BACKENDS
        + "  modules:\n    senate:\n      roles:\n"
        + "        bear: {model: anthropic:claude-sonnet-4-5}\n",
        "senate",
        "bull",
        "openai:model-a",
        "global",
    ),
    # 空串 = 继承，不是「关掉」
    (
        BACKENDS + '  modules:\n    wire:\n      model: ""\n',
        "wire",
        None,
        "openai:model-a",
        "global",
    ),
    # 空表 = 继承
    (BACKENDS + "  modules:\n    tickhub_us:\n", "tickhub_us", None, "openai:model-a", "global"),
    # 裸模型名按「第一个声明它的后端」解析，标签带前缀
    (
        BACKENDS + "  modules:\n    wire:\n      model: model-b\n",
        "wire",
        None,
        "openai:model-b",
        "module",
    ),
]


@pytest.mark.parametrize(("body", "module", "role", "value", "source"), MATRIX)
def test_resolve_matrix(
    tmp_path: Path, body: str, module: str, role: str | None, value: str, source: str
) -> None:
    config = _load(tmp_path, body)
    assert config.resolve_for(module, role).label == value
    assert config.source_for(module, role) == source


# --------------------------------------------------------------------------- #
# 链语义
# --------------------------------------------------------------------------- #


def test_chain_replaces_not_merges(tmp_path: Path) -> None:
    config = _load(
        tmp_path,
        BACKENDS
        + "  modules:\n    tickhub:\n      model: anthropic:claude-sonnet-4-5\n"
        + "      fallback_chain: [openai:model-b, openai:model-a]\n",
    )
    assert [t.label for t in config.chain_for("tickhub")] == [
        "anthropic:claude-sonnet-4-5",
        "openai:model-b",
        "openai:model-a",
    ]


def test_chain_without_module_override_uses_global(tmp_path: Path) -> None:
    config = _load(tmp_path, BACKENDS + "  modules:\n    wire:\n")
    assert [t.label for t in config.chain_for("wire")] == ["openai:model-a", "openai:model-b"]


def test_chain_dedupes_and_keeps_first(tmp_path: Path) -> None:
    config = _load(
        tmp_path,
        BACKENDS
        + "  modules:\n    senate:\n      model: openai:model-b\n"
        + "      fallback_chain: [openai:model-b, openai:model-a]\n",
    )
    assert [t.label for t in config.chain_for("senate")] == ["openai:model-b", "openai:model-a"]


# --------------------------------------------------------------------------- #
# 现有 targets / models 语义不变
# --------------------------------------------------------------------------- #


def test_targets_ignore_modules(tmp_path: Path) -> None:
    body = (
        BACKENDS
        + "  modules:\n    tickhub:\n      model: anthropic:claude-sonnet-4-5\n"
        + "      fallback_chain: [anthropic:claude-sonnet-4-5]\n"
    )
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    plain = _load(tmp_path / "a", BACKENDS)
    covered = _load(tmp_path / "b", body)
    assert [t.label for t in covered.targets] == [t.label for t in plain.targets]
    assert covered.models == plain.models


# --------------------------------------------------------------------------- #
# 接口载荷
# --------------------------------------------------------------------------- #


def test_module_document_shape(tmp_path: Path) -> None:
    config = _load(
        tmp_path,
        BACKENDS
        + "  modules:\n    senate:\n      model: openai:model-b\n"
        + "      roles:\n        bull: {model: anthropic:claude-sonnet-4-5}\n"
        + "      memory: {agent_id: senate-x, kb_scope: senate.*}\n",
    )
    doc = config.module_document()
    assert set(fallback.MODULE_KEYS) <= set(doc["modules"])
    assert doc["models"] == [
        "openai:model-a",
        "openai:model-b",
        "anthropic:claude-sonnet-4-5",
    ]
    senate = doc["modules"]["senate"]
    assert senate["value"] == "openai:model-b"
    assert senate["source"] == "module"
    assert senate["configured"] is True
    assert senate["inherited_value"] == "openai:model-a"
    assert senate["agent_id"] == "senate-x"
    assert senate["kb_scope"] == "senate.*"
    assert senate["roles"]["bull"] == {
        "value": "anthropic:claude-sonnet-4-5",
        "source": "role",
        "configured": True,
        "inherited_value": "openai:model-b",
    }
    tickhub = doc["modules"]["tickhub"]
    assert tickhub["source"] == "global"
    assert tickhub["inherited_value"] is None
    assert tickhub["agent_id"] == "ladderstock-tickhub"
    assert tickhub["kb_scope"] == "all"


def test_memory_defaults_and_override(tmp_path: Path) -> None:
    config = _load(
        tmp_path,
        BACKENDS + "  modules:\n    wire:\n      memory: {kb_scope: wire}\n",
    )
    scope = config.memory_scope("wire")
    assert (scope.agent_id, scope.kb_scope) == ("ladderstock-wire", "wire")
    assert config.memory_scope("senate").agent_id == "ladderstock-senate"


# --------------------------------------------------------------------------- #
# 错误路径：文案必须可直接给人看
# --------------------------------------------------------------------------- #


def _expect(tmp_path: Path, body: str, needle: str) -> None:
    with pytest.raises(BoundaryViolation) as info:
        _load(tmp_path, body)
    assert needle in str(info.value)


def test_unknown_backend_in_module_model(tmp_path: Path) -> None:
    _expect(
        tmp_path,
        BACKENDS + "  modules:\n    tickhub:\n      model: nope:model-a\n",
        "未声明的后端 'nope'（已声明：anthropic、openai）",
    )


def test_unknown_backend_in_role_model(tmp_path: Path) -> None:
    _expect(
        tmp_path,
        BACKENDS + "  modules:\n    senate:\n      roles:\n        bull: {model: ghost:model-a}\n",
        "modules.senate.roles.bull.model = 'ghost:model-a'",
    )


def test_unknown_backend_in_module_chain(tmp_path: Path) -> None:
    _expect(
        tmp_path,
        BACKENDS + "  modules:\n    wire:\n      fallback_chain: [ghost:model-a]\n",
        "modules.wire.fallback_chain = 'ghost:model-a'",
    )


def test_unknown_module(tmp_path: Path) -> None:
    _expect(
        tmp_path,
        BACKENDS + "  modules:\n    hedge:\n      model: openai:model-a\n",
        "未知子模块 'hedge'（已声明：tickhub、tickhub_us、wire、senate）",
    )


def test_unknown_role(tmp_path: Path) -> None:
    _expect(
        tmp_path,
        BACKENDS
        + "  modules:\n    senate:\n      roles:\n        wizard: {model: openai:model-a}\n",
        "没有角色 'wizard'",
    )


def test_roles_on_wrong_module(tmp_path: Path) -> None:
    _expect(
        tmp_path,
        BACKENDS
        + "  modules:\n    tickhub:\n      roles:\n        bull: {model: openai:model-a}\n",
        "modules.tickhub.roles 只在 senate 下可用",
    )


def test_plaintext_secret_field_rejected(tmp_path: Path) -> None:
    _expect(
        tmp_path,
        BACKENDS + "  modules:\n    wire:\n      api_key: sk-should-not-be-here\n",
        "看起来是密钥字段",
    )


def test_unknown_module_field(tmp_path: Path) -> None:
    _expect(
        tmp_path,
        BACKENDS + "  modules:\n    wire:\n      temperature: 0.2\n",
        "modules.wire 含未知字段 'temperature'",
    )


def test_unknown_role_field(tmp_path: Path) -> None:
    _expect(
        tmp_path,
        BACKENDS + "  modules:\n    senate:\n      roles:\n        bull: {provider: x}\n",
        "modules.senate.roles.bull 含未知字段 'provider'",
    )


def test_bad_fallback_chain_type(tmp_path: Path) -> None:
    _expect(
        tmp_path,
        BACKENDS + "  modules:\n    wire:\n      fallback_chain: openai:model-a\n",
        "modules.wire.fallback_chain 必须是数组",
    )


def test_bad_model_type(tmp_path: Path) -> None:
    _expect(
        tmp_path,
        BACKENDS + "  modules:\n    wire:\n      model: [a, b]\n",
        "modules.wire.model 必须是字符串",
    )


def test_illegal_agent_id(tmp_path: Path) -> None:
    _expect(
        tmp_path,
        BACKENDS + "  modules:\n    wire:\n      memory: {agent_id: 'bad id!'}\n",
        "modules.wire.memory.agent_id 非法",
    )


def test_illegal_kb_scope(tmp_path: Path) -> None:
    _expect(
        tmp_path,
        BACKENDS + "  modules:\n    wire:\n      memory: {kb_scope: 'a/b'}\n",
        "modules.wire.memory.kb_scope 非法",
    )


def test_unknown_memory_field(tmp_path: Path) -> None:
    _expect(
        tmp_path,
        BACKENDS + "  modules:\n    wire:\n      memory: {user_id: u1}\n",
        "modules.wire.memory 含未知字段 'user_id'",
    )


def test_modules_must_be_mapping(tmp_path: Path) -> None:
    _expect(tmp_path, BACKENDS + "  modules: [tickhub]\n", "modules 必须是对象")


def test_runtime_unknown_module_raises(tmp_path: Path) -> None:
    config = _load(tmp_path, BACKENDS)
    with pytest.raises(BoundaryViolation):
        config.resolve_for("hedge")
    with pytest.raises(BoundaryViolation):
        config.source_for("tickhub", "bull")
    with pytest.raises(BoundaryViolation):
        config.chain_for("senate", "wizard")


def test_senate_roles_match_stocksenate() -> None:
    """角色键必须与 StockSenate `ROLE_LLM_KEYS` 逐字一致（那边不是本仓依赖，故写死对照）。"""
    assert fallback.SENATE_ROLE_KEYS == (
        "market",
        "social",
        "news",
        "fundamentals",
        "policy",
        "hot_money",
        "lockup",
        "bull",
        "bear",
        "research_manager",
        "trader",
        "risk_aggressive",
        "risk_neutral",
        "risk_conservative",
        "quality_gate",
        "portfolio_manager",
    )
