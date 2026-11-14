"""零配置 AI 自动发现链（QUOT-601）：优先级选择、降级、mask 边界。"""

from __future__ import annotations

import json

import httpx
import pytest

from ladderstock.ai import discovery

AI_YAML = """
ai:
  backends:
    - id: openai
      type: openai
      models: [user-model]
  default_model: openai:user-model
  sections: {}
"""


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch) -> None:
    for name in (
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
        "OPENAI_MODEL",
        "LDST_LLM_BASE_URL",
        "LDST_LLM_API_KEY",
        "LDST_LLM_MODEL",
        "LDST_REUSE_CLI_LOGINS",
        "LDST_CLI_HOME",
    ):
        monkeypatch.delenv(name, raising=False)


def _mock_http(monkeypatch, *, models_ok=True, chat_ok=True, model_ids=("deepseek-v4.1-flash",)) -> None:
    """打桩 `_probe_models` / `_probe_openai_chat`：不真的发 HTTP。"""

    def probe_models(base_url: str):
        if models_ok:
            return True, "", list(model_ids)
        return False, "本地 AI 服务未启动，点击修复", []

    def probe_chat(base_url: str, key: str, model: str):
        if chat_ok:
            return True, ""
        return False, "通道返回 HTTP 402，请检查端点或密钥"

    monkeypatch.setattr(discovery, "_probe_models", probe_models)
    monkeypatch.setattr(discovery, "_probe_openai_chat", probe_chat)


def _write_config(root) -> None:
    (root / "ai.yaml").write_text(AI_YAML, encoding="utf-8")


# ---------------------------------------------------------------- P1 优先级


def test_gateway_hit_wins(monkeypatch) -> None:
    """P1 网关通：即使环境变量也在，链只报网关一条，命中来源是 workbuddy_gateway。"""
    _mock_http(monkeypatch, models_ok=True, chat_ok=True)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-1234567890")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.example.com/v1")
    document = discovery.discover()
    assert document["ok"] is True
    assert document["current_source"] == "workbuddy_gateway"
    assert [item["source"] for item in document["candidates"]] == ["workbuddy_gateway"]
    hit = document["candidates"][0]
    assert hit["model"] == "deepseek-v4.1-flash"
    assert hit["latency_ms"] is not None


def test_gateway_down_falls_to_env(monkeypatch) -> None:
    """P1 不通 → P2 环境变量接住；只读取，不写入。"""
    _mock_http(monkeypatch, models_ok=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-1234567890")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.example.com/v1")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-x")
    document = discovery.discover()
    assert document["ok"] is True
    assert document["current_source"] == "env"
    assert [item["source"] for item in document["candidates"]] == ["workbuddy_gateway", "env"]
    env_item = document["candidates"][1]
    assert env_item["model"] == "gpt-x"
    assert env_item["key_set"] is True


def test_all_fail_degrades_with_friendly_errors(monkeypatch) -> None:
    """全失败：ok=False，来源落 none，错误是白话而不是裸 4xx/连接拒绝。"""
    _mock_http(monkeypatch, models_ok=False)
    document = discovery.discover()
    assert document["ok"] is False
    assert document["current_source"] == "none"
    gateway = document["candidates"][0]
    assert gateway["ok"] is False
    assert "HTTP" not in gateway["detail"] or "未启动" in gateway["detail"]
    assert "Connection refused" not in gateway["detail"]
    assert document["detail"]


# ---------------------------------------------------------------- P3 开关


def test_cli_logins_default_off(monkeypatch, tmp_path) -> None:
    """P3 默认关：~/.codex 有登录态也不读。"""
    _mock_http(monkeypatch, models_ok=False)
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "auth.json").write_text(
        json.dumps({"OPENAI_API_KEY": "sk-codex-abcdef123456"}), encoding="utf-8"
    )
    monkeypatch.setenv("LDST_CLI_HOME", str(home))
    document = discovery.discover()
    assert document["include_cli_logins"] is False
    assert all(item["source"] != "codex" for item in document["candidates"])


def test_cli_logins_opt_in_reads_codex(monkeypatch, tmp_path) -> None:
    """P3 开启（LDST_REUSE_CLI_LOGINS=1）后能读到 Codex 登录态。"""
    _mock_http(monkeypatch, models_ok=False)
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "auth.json").write_text(
        json.dumps({"OPENAI_API_KEY": "sk-codex-abcdef123456"}), encoding="utf-8"
    )
    monkeypatch.setenv("LDST_CLI_HOME", str(home))
    monkeypatch.setenv("LDST_REUSE_CLI_LOGINS", "1")
    document = discovery.discover()
    assert document["include_cli_logins"] is True
    codex = next(item for item in document["candidates"] if item["source"] == "codex")
    assert codex["ok"] is True
    assert codex["key_set"] is True


def test_claude_login_detected_but_not_applied(monkeypatch, tmp_path) -> None:
    """Claude 登录态可探测（Anthropic 协议），但 apply 拒绝自动落地。"""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text(
        json.dumps(
            {
                "env": {
                    "ANTHROPIC_AUTH_TOKEN": "sk-claude-abcdef123456",
                    "ANTHROPIC_BASE_URL": "https://relay.example.com",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("LDST_CLI_HOME", str(home))
    monkeypatch.setenv("LDST_REUSE_CLI_LOGINS", "1")
    _mock_http(monkeypatch, models_ok=False)
    document = discovery.discover()
    claude = next(item for item in document["candidates"] if item["source"] == "claude")
    assert claude["ok"] is True
    candidate = discovery.candidate_claude()
    assert candidate is not None
    result = discovery.apply_candidate(candidate, tmp_path)
    assert result["applied"] is False


# ---------------------------------------------------------------- apply 与 mask


def test_apply_writes_env_file(monkeypatch, tmp_path) -> None:
    """落地：写 base_url / key / model 三个键到 ai.env。"""
    _mock_http(monkeypatch)
    _write_config(tmp_path)
    document = discovery.discover()
    hit = document["candidates"][0]
    candidate = discovery.candidate_workbuddy_gateway()
    result = discovery.apply_candidate(candidate, tmp_path)
    assert result["applied"] is True
    values = fallback_read(tmp_path)
    assert values["LDST_LLM_BASE_URL"] == discovery.WORKBUDDY_GATEWAY_BASE
    assert values["LDST_LLM_API_KEY"] == discovery.WORKBUDDY_PLACEHOLDER_KEY
    assert values["LDST_LLM_MODEL"] == "deepseek-v4.1-flash"
    assert hit["key_set"] is True


def fallback_read(root):
    from ladderstock.ai import fallback

    return fallback.read_env_file(fallback.env_file_path(root))


def test_discover_document_has_no_plaintext_key(monkeypatch, tmp_path) -> None:
    """结构级保险：discover 的对外载荷序列化后不含任何明文 key 值。"""
    _mock_http(monkeypatch, models_ok=False)
    home = tmp_path / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "auth.json").write_text(
        json.dumps({"OPENAI_API_KEY": "sk-codex-secret-9876543210"}), encoding="utf-8"
    )
    monkeypatch.setenv("LDST_CLI_HOME", str(home))
    monkeypatch.setenv("LDST_REUSE_CLI_LOGINS", "1")
    document = discovery.discover()
    text = json.dumps(document, ensure_ascii=False)
    assert "sk-codex-secret-9876543210" not in text


# ---------------------------------------------------------------- 白话错误翻译


def test_gateway_connection_error_is_translated(monkeypatch) -> None:
    """连接拒绝/超时翻译为白话「本地 AI 服务未启动，点击修复」。"""

    def probe_models(base_url: str):
        raise_discovery = httpx.ConnectError("connection refused")
        del raise_discovery
        return False, "本地 AI 服务未启动，点击修复", []

    monkeypatch.setattr(discovery, "_probe_models", probe_models)
    candidate = discovery.candidate_workbuddy_gateway()
    assert candidate.ok is False
    assert "未启动" in candidate.detail
    assert "refused" not in candidate.detail


def test_pick_openai_model_prefers_flash() -> None:
    ids = ["glm-5.3", "deepseek-v4-pro", "deepseek-v4.1-flash", "kimi-k3-1"]
    assert discovery._pick_openai_model(ids) == "deepseek-v4.1-flash"
    assert discovery._pick_openai_model(["glm-5.3", "kimi-k3-1"]) == "glm-5.3"
    assert discovery._pick_openai_model([]) == ""
