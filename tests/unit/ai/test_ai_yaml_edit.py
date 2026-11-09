"""`config/ai.yaml` 原地编辑：保留注释 / 缩进 / 其余段，校验不过不落盘（QUOT-498）。"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from ladderstock.ai import ai_yaml_edit, fallback
from ladderstock.core.boundary import BoundaryViolation

REPO_AI_YAML = Path(__file__).resolve().parents[3] / "config" / "ai.yaml"

BASE = """\
# 顶部注释必须活下来
ai:
  backends:
    - id: openai
      type: openai
      models: [model-a, model-b]
    - id: anthropic
      type: anthropic
      models: [claude-sonnet-4-5]
  default_model: openai:model-a
  fallback_chain: [openai:model-b]
  structured_output: prefer

  sections:
    sentiment:
      prompt_file: prompts/sentiment.md
"""


def _modules(text: str) -> dict:
    return (yaml.safe_load(text) or {}).get("ai", {}).get("modules") or {}


def test_insert_into_file_without_modules_keeps_everything_else() -> None:
    new = ai_yaml_edit.edit_module(
        BASE,
        "tickhub",
        {
            "model": "openai:model-b",
            "memory": {"agent_id": "ladderstock-tickhub", "kb_scope": "tickhub"},
        },
    )
    assert "# 顶部注释必须活下来" in new
    assert "structured_output: prefer" in new
    assert _modules(new)["tickhub"] == {
        "model": "openai:model-b",
        "memory": {"agent_id": "ladderstock-tickhub", "kb_scope": "tickhub"},
    }
    # 没有 modules: 时插在 ai 块末尾（缩进 = ai 缩进 + 2），不另起顶层键
    assert new.rstrip().endswith(
        '  modules:\n    "tickhub":\n      model: "openai:model-b"\n'
        "      memory:\n"
        '        agent_id: "ladderstock-tickhub"\n'
        '        kb_scope: "tickhub"'
    )
    # 除了新块，其余行原样保留
    original_lines = [line for line in BASE.splitlines() if line.strip()]
    assert all(line in new for line in original_lines)


def test_update_existing_module_touches_only_its_lines() -> None:
    once = ai_yaml_edit.edit_module(BASE, "wire", {"model": "openai:model-a"})
    twice = ai_yaml_edit.edit_module(once, "wire", {"model": "anthropic:claude-sonnet-4-5"})
    assert _modules(twice)["wire"] == {"model": "anthropic:claude-sonnet-4-5"}
    assert _modules(once)["wire"] == {"model": "openai:model-a"}
    # 未改动的字段与相邻注释不动
    assert twice.count("  modules:") == 1
    assert "# 顶部注释必须活下来" in twice


def test_empty_override_deletes_entry_and_its_comment() -> None:
    seeded = BASE.replace(
        "  structured_output: prefer\n",
        "  structured_output: prefer\n"
        "  modules:\n"
        "    # Wire 资讯通道\n"
        "    wire:\n"
        "      model: openai:model-a\n\n",
    )
    cleared = ai_yaml_edit.edit_module(seeded, "wire", {})
    assert _modules(cleared) == {}
    assert "# Wire 资讯通道" not in cleared
    assert "  modules:" in cleared
    assert "structured_output: prefer" in cleared

    # 再删一次：没东西可删，文本不变
    assert ai_yaml_edit.edit_module(cleared, "wire", {}) == cleared


def test_roles_add_update_and_remove() -> None:
    text = ai_yaml_edit.edit_module(BASE, "senate", {"model": "openai:model-a"})
    text = ai_yaml_edit.edit_module(
        text,
        "senate",
        {
            "roles": {
                "bull": {"model": "anthropic:claude-sonnet-4-5"},
                "bear": {"model": "openai:model-b"},
            }
        },
    )
    text = ai_yaml_edit.edit_module(
        text, "senate", {"roles": {"bull": {"model": "openai:model-b"}}}
    )
    text = ai_yaml_edit.edit_module(text, "senate", {"roles": {"bear": {"model": ""}}})
    assert _modules(text)["senate"] == {
        "model": "openai:model-a",
        "roles": {"bull": {"model": "openai:model-b"}},
    }


def test_crlf_and_missing_trailing_newline_preserved() -> None:
    crlf = BASE.replace("\n", "\r\n")
    new = ai_yaml_edit.edit_module(crlf, "wire", {"model": "openai:model-a"})
    assert "\r\n" in new and "\n" not in new.replace("\r\n", "")

    no_eol = BASE.rstrip("\n")
    new = ai_yaml_edit.edit_module(no_eol, "wire", {"model": "openai:model-a"})
    assert not new.endswith("\n")


def test_4_space_indent_file_keeps_its_indent() -> None:
    text = """\
ai:
    backends:
        - id: openai
          type: openai
          models: [model-a]
    default_model: openai:model-a
    fallback_chain: []
"""
    new = ai_yaml_edit.edit_module(text, "wire", {"model": "openai:model-a"})
    # 沿用同级字段的 4 空格缩进（4 → 6 → 8），不硬编码 2 空格
    assert '\n    modules:\n      "wire":\n        model: "openai:model-a"\n' in new
    assert _modules(new)["wire"] == {"model": "openai:model-a"}


def test_no_ai_block_refused() -> None:
    with pytest.raises(BoundaryViolation, match="ai:"):
        ai_yaml_edit.edit_module("other:\n  x: 1\n", "wire", {"model": "openai:model-a"})


def test_repo_config_round_trips(tmp_path: Path) -> None:
    """真实 `config/ai.yaml`（modules 全注释）写一个模块后，注释与其余段逐行保留。"""
    original = REPO_AI_YAML.read_text(encoding="utf-8")
    seeded = ai_yaml_edit.edit_module(original, "senate", {"model": "openai:model-b"})
    assert _modules(seeded) == {"senate": {"model": "openai:model-b"}}
    for line in original.splitlines():
        if line.strip():
            assert line in seeded, line

    target = tmp_path / "ai.yaml"
    shutil.copy2(REPO_AI_YAML, target)
    ai_yaml_edit.write_ai_yaml(
        target, seeded, lambda candidate: fallback.load_config(candidate.parent)
    )
    config = fallback.load_config(tmp_path)
    assert config.resolve_for("senate").label == "openai:model-b"
    assert config.source_for("senate") == "module"
    # 备份与临时文件：备份在、临时目录不在
    backups = list(tmp_path.glob("ai.yaml.bak.*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == original
    assert not (tmp_path / ".ai.yaml.tmp").exists()


def test_invalid_candidate_never_reaches_disk(tmp_path: Path) -> None:
    target = tmp_path / "ai.yaml"
    shutil.copy2(REPO_AI_YAML, target)
    before = target.read_text(encoding="utf-8")
    bad = before.replace("default_model: openai:user-model", "default_model: ghost:model-a")

    def validate(candidate: Path) -> None:
        fallback.load_config(candidate.parent)

    with pytest.raises(BoundaryViolation, match="写入被拒绝"):
        ai_yaml_edit.write_ai_yaml(target, bad, validate)
    assert target.read_text(encoding="utf-8") == before
    assert not list(tmp_path.glob("ai.yaml.bak.*"))
    assert not (tmp_path / ".ai.yaml.tmp").exists()
    assert not (tmp_path / ".ai.yaml.replace").exists()
