"""子模块记忆命名空间（QUOT-498 第三节）：`agent_id` + `kb_scope` 必须同时到齐。

真正的 mem0 通道归 QUOT-487（PR #156，未合入）；这里只钉住「配置 → 检索载荷」
这一层，保证 #156 收口时接上的是模块隔离的命名空间，而不是一条全局记忆。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ladderstock.ai import fallback, module_memory
from ladderstock.ai.fallback import MemoryScope
from ladderstock.core.boundary import BoundaryViolation
from ladderstock.web.assistant import memory_scope_for

BASE = """
ai:
  default_model: openai:model-a
  backends:
    - id: openai
      type: openai
      models: [model-a, model-b]
  sections:
    sentiment:
      prompt_file: p.md
      output_schema: s.json
"""


def _write(tmp_path: Path, text: str) -> Path:
    (tmp_path / "ai.yaml").write_text(text, encoding="utf-8")
    return tmp_path


def test_each_module_gets_its_own_namespace(tmp_path: Path) -> None:
    config = fallback.load_config(_write(tmp_path, BASE))
    scopes = {name: config.memory_scope(name) for name in fallback.MODULE_KEYS}

    assert scopes["tickhub"] == MemoryScope("ladderstock-tickhub", "all")
    assert scopes["tickhub_us"] == MemoryScope("ladderstock-tickhub_us", "all")
    assert scopes["wire"] == MemoryScope("ladderstock-wire", "all")
    assert scopes["senate"] == MemoryScope("ladderstock-senate", "all")
    # agent_id 两两不同 => 任一模块的检索都不会召回别的模块
    assert len({scope.agent_id for scope in scopes.values()}) == len(fallback.MODULE_KEYS)


def test_configured_memory_overrides_defaults(tmp_path: Path) -> None:
    text = BASE.replace(
        "  sections:",
        "  modules:\n"
        "    wire:\n"
        "      memory:\n"
        '        agent_id: "news-wire"\n'
        '        kb_scope: "wire-*"\n'
        "  sections:",
    )
    config = fallback.load_config(_write(tmp_path, text))

    assert config.memory_scope("wire") == MemoryScope("news-wire", "wire-*")
    assert config.memory_scope("senate") == MemoryScope("ladderstock-senate", "all")


def test_partial_memory_falls_back_for_the_missing_dimension(tmp_path: Path) -> None:
    """只写 `kb_scope` 时 `agent_id` 仍是 `ladderstock-<module>`，不会是空串。"""
    text = BASE.replace(
        "  sections:",
        '  modules:\n    senate:\n      memory:\n        kb_scope: "research"\n  sections:',
    )
    config = fallback.load_config(_write(tmp_path, text))

    scope = config.memory_scope("senate")
    assert scope == MemoryScope("ladderstock-senate", "research")


@pytest.mark.parametrize("bad", ["a b", "-leading", "x" * 81])
def test_bad_agent_id_is_rejected_at_load(tmp_path: Path, bad: str) -> None:
    text = BASE.replace(
        "  sections:",
        f'  modules:\n    wire:\n      memory:\n        agent_id: "{bad}"\n  sections:',
    )
    with pytest.raises(BoundaryViolation, match="memory.agent_id 非法"):
        fallback.load_config(_write(tmp_path, text))


def test_bad_kb_scope_is_rejected_at_load(tmp_path: Path) -> None:
    text = BASE.replace(
        "  sections:",
        '  modules:\n    wire:\n      memory:\n        kb_scope: "bad scope!"\n  sections:',
    )
    with pytest.raises(BoundaryViolation, match="memory.kb_scope 非法"):
        fallback.load_config(_write(tmp_path, text))


def test_memory_unknown_field_is_rejected_at_load(tmp_path: Path) -> None:
    text = BASE.replace(
        "  sections:",
        '  modules:\n    wire:\n      memory:\n        user_id: "u"\n  sections:',
    )
    with pytest.raises(BoundaryViolation, match="memory 含未知字段 'user_id'"):
        fallback.load_config(_write(tmp_path, text))


def test_recall_params_carry_both_dimensions() -> None:
    scope = MemoryScope("ladderstock-wire", "news")
    payload = module_memory.recall_params(scope, "上周怎么看", user_id="u-1", top_k=5)

    assert payload == {
        "query": "上周怎么看",
        "user_id": "u-1",
        "agent_id": "ladderstock-wire",
        "top_k": 5,
        "kb_scope": "news",
    }


def test_store_params_carry_both_dimensions() -> None:
    scope = MemoryScope("ladderstock-senate", "research")
    payload = module_memory.store_params(
        scope,
        [{"role": "user", "content": "记住我偏好低估值"}],
        user_id="u-1",
        run_id="run-9",
        metadata={"page": "/review", "source": "assistant"},
    )

    assert payload["agent_id"] == "ladderstock-senate"
    assert payload["metadata"] == {
        "page": "/review",
        "source": "assistant",
        "kb_scope": "research",
    }
    assert payload["run_id"] == "run-9"


@pytest.mark.parametrize("scope", [MemoryScope("", "news"), MemoryScope("ladderstock-wire", "")])
def test_empty_dimension_is_refused_before_any_io(scope: MemoryScope) -> None:
    """缺一维就是跨模块召回：在拼载荷前就拒，绝不发出去。"""
    with pytest.raises(BoundaryViolation, match="必须同时给出 agent_id 与 kb_scope"):
        module_memory.recall_params(scope, "q", user_id="u-1", top_k=5)
    with pytest.raises(BoundaryViolation, match="必须同时给出 agent_id 与 kb_scope"):
        module_memory.store_params(scope, [], user_id="u-1", run_id="r")
    with pytest.raises(BoundaryViolation):
        module_memory.scope_label(scope)


def test_scope_label_names_both_dimensions() -> None:
    label = module_memory.scope_label(MemoryScope("ladderstock-tickhub", "market"))

    assert "ladderstock-tickhub" in label
    assert "market" in label


def test_assistant_exposes_module_scope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LDST_CONFIG_DIR", str(_write(tmp_path, BASE)))

    wire = memory_scope_for("wire")
    senate = memory_scope_for("senate")

    assert wire is not None and senate is not None
    assert wire["agent_id"] == "ladderstock-wire"
    assert senate["kb_scope"] == "all"
    assert wire["agent_id"] != senate["agent_id"]
    assert wire["label"].startswith("【记忆命名空间：")


def test_assistant_scope_is_none_when_config_is_broken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LDST_CONFIG_DIR", str(_write(tmp_path, "ai: [broken\n")))

    assert memory_scope_for("wire") is None
