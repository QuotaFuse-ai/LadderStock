"""`/api/control/self-ai` 的落盘、白名单与连通性探测（QUOT-200）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from ladderstock.web.control import ControlPlane

AI_YAML = """
ai:
  backends:
    - id: openai
      type: openai
      models: [user-model]
  default_model: openai:user-model
  fallback_chain: []
  sections: {}
"""

SENTINEL = "sk-self-ai-SENTINEL"


def _plane(tmp_path: Path, *, http_post=None) -> ControlPlane:
    config_root = tmp_path / "config"
    config_root.mkdir(parents=True)
    (config_root / "ai.yaml").write_text(AI_YAML, encoding="utf-8")
    return ControlPlane(
        data_root=tmp_path / "data",
        peers={},
        config_root=config_root,
        harden_acl=lambda path: None,
        resolve=lambda host: ["93.184.216.34"],
        http_post=http_post
        or (lambda url, payload, headers, timeout: (200, json.dumps({"model": "deepseek-chat"}))),
    )


def test_update_writes_env_file_and_never_echoes_key(tmp_path, monkeypatch) -> None:
    for name in ("LDST_LLM_BASE_URL", "LDST_LLM_API_KEY", "LDST_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    plane = _plane(tmp_path)

    payload = plane.self_ai_update(
        {
            "LDST_LLM_BASE_URL": "https://api.deepseek.com/v1",
            "LDST_LLM_MODEL": "deepseek-chat",
            "LDST_LLM_API_KEY": SENTINEL,
        },
        [],
    )
    env_file = Path(payload["env_file"])
    assert env_file.name == "ai.env"
    text = env_file.read_text(encoding="utf-8")
    assert "LDST_LLM_BASE_URL=https://api.deepseek.com/v1" in text
    assert SENTINEL not in json.dumps(payload, ensure_ascii=False)
    assert payload["configured"] is True
    assert payload["default_model"] == "openai:deepseek-chat"
    assert payload["backends"][0]["api_key_source"] == "file"


def test_unknown_key_rejected(tmp_path) -> None:
    plane = _plane(tmp_path)
    with pytest.raises(HTTPException) as raised:
        plane.self_ai_update({"LDST_SHELL_COMMAND": "rm -rf"}, [])
    assert raised.value.status_code == 400


def test_non_http_endpoint_rejected(tmp_path) -> None:
    plane = _plane(tmp_path)
    with pytest.raises(HTTPException) as raised:
        plane.self_ai_update({"LDST_LLM_BASE_URL": "file:///etc/passwd"}, [])
    assert raised.value.status_code == 400


def test_clear_removes_key(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("LDST_LLM_API_KEY", raising=False)
    plane = _plane(tmp_path)
    plane.self_ai_update({"LDST_LLM_API_KEY": SENTINEL}, [])
    payload = plane.self_ai_update({}, ["LDST_LLM_API_KEY"])
    assert payload["backends"][0]["api_key_set"] is False
    assert SENTINEL not in Path(payload["env_file"]).read_text(encoding="utf-8")


def test_test_reuses_stored_key_only_for_same_endpoint(tmp_path, monkeypatch) -> None:
    for name in ("LDST_LLM_BASE_URL", "LDST_LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    seen: list[dict[str, str]] = []

    def post(url, payload, headers, timeout):
        seen.append(dict(headers))
        return 200, json.dumps({"model": payload["model"]})

    plane = _plane(tmp_path, http_post=post)
    plane.self_ai_update(
        {
            "LDST_LLM_BASE_URL": "https://api.deepseek.com/v1",
            "LDST_LLM_API_KEY": SENTINEL,
        },
        [],
    )

    plane.self_ai_test(base_url="https://api.deepseek.com/v1", model="deepseek-chat")
    assert seen[-1].get("Authorization") == f"Bearer {SENTINEL}"

    # 换了端点就不再回落到已存密钥（避免把密钥发到别处）
    plane.self_ai_test(base_url="https://other.test/v1", model="deepseek-chat")
    assert "Authorization" not in seen[-1]
