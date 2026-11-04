"""看板自身 AI 的 `ai.env` 读取与模型覆盖（QUOT-200 设置页接入 AI）。"""

from __future__ import annotations

import json

from ladderstock.ai import fallback

AI_YAML = """
ai:
  backends:
    - id: openai
      type: openai
      models: [user-model, model-a, model-b]
  default_model: openai:user-model
  fallback_chain: [openai:model-a, openai:model-b]
  sections: {}
"""


def _write_config(root) -> None:
    (root / "ai.yaml").write_text(AI_YAML, encoding="utf-8")


def test_env_file_fills_missing_credentials(tmp_path, monkeypatch) -> None:
    _write_config(tmp_path)
    for name in (
        "LDST_LLM_BASE_URL",
        "LDST_LLM_API_KEY",
        "LDST_LLM_MODEL",
        "LDST_LLM_ANTHROPIC_BASE_URL",
        "LDST_LLM_ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "ai.env").write_text(
        "LDST_LLM_BASE_URL=https://api.deepseek.com/v1\n"
        "LDST_LLM_API_KEY=sk-file\n",
        encoding="utf-8",
    )

    config = fallback.load_config(tmp_path)
    backend = config.declared_backends[0]
    assert backend.base_url == "https://api.deepseek.com/v1"
    assert backend.api_key == "sk-file"
    assert backend.base_url_source == "file"
    assert backend.api_key_source == "file"
    assert fallback.is_configured(config) is True


def test_process_env_wins_over_file(tmp_path, monkeypatch) -> None:
    _write_config(tmp_path)
    (tmp_path / "ai.env").write_text(
        "LDST_LLM_BASE_URL=https://file.test/v1\nLDST_LLM_API_KEY=sk-file\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LDST_LLM_BASE_URL", "https://process.test/v1")
    monkeypatch.delenv("LDST_LLM_API_KEY", raising=False)

    config = fallback.load_config(tmp_path)
    backend = config.declared_backends[0]
    assert backend.base_url == "https://process.test/v1"
    assert backend.base_url_source == "process"
    # 文件只补空缺：进程没配密钥时仍然用文件里的
    assert backend.api_key == "sk-file"
    assert backend.api_key_source == "file"


def test_model_override_collapses_placeholder_chain(tmp_path, monkeypatch) -> None:
    _write_config(tmp_path)
    monkeypatch.delenv("LDST_LLM_MODEL", raising=False)
    (tmp_path / "ai.env").write_text(
        "LDST_LLM_BASE_URL=https://api.deepseek.com/v1\n"
        "LDST_LLM_API_KEY=sk-file\n"
        "LDST_LLM_MODEL=deepseek-chat\n",
        encoding="utf-8",
    )

    config = fallback.load_config(tmp_path)
    assert config.default_model == "openai:deepseek-chat"
    # model-a / model-b 是占位模型，覆盖后不能留在链里制造假回退
    assert config.fallback_chain == ()
    assert fallback.model_chain(config) == ["openai:deepseek-chat"]
    assert config.resolve(config.default_model).model == "deepseek-chat"


def test_status_document_reports_sources_without_secret(tmp_path, monkeypatch) -> None:
    _write_config(tmp_path)
    monkeypatch.delenv("LDST_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LDST_LLM_API_KEY", raising=False)
    (tmp_path / "ai.env").write_text(
        "LDST_LLM_BASE_URL=https://api.moonshot.cn/v1\nLDST_LLM_API_KEY=sk-sentinel\n",
        encoding="utf-8",
    )

    document = fallback.status_document(fallback.load_config(tmp_path))
    assert document["configured"] is True
    assert document["env_file_exists"] is True
    assert document["backends"][0]["api_key_source"] == "file"
    assert "sk-sentinel" not in json.dumps(document, ensure_ascii=False)
