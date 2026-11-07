"""QUOT-476：`POST /api/skills/assistant` 的契约测试。

/skills 页没有 code，走独立端点：复用 `assistant.ask()` 的客户端与降级口径，
但 system prompt 换成不含个股深挖预设的 `SKILL_SYSTEM_PROMPT`，meta 行写
页面键与绑定技能。模型调用用假客户端，不联网、不读密钥。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from ladderstock.core.boundary import BoundaryViolation
from ladderstock.web import assistant as assistant_module
from ladderstock.web.server import create_app

BASE_URL = "http://127.0.0.1:5950"
LOOPBACK = ("127.0.0.1", 54321)


class _NullControlPlane:
    peers: dict[str, Any] = {}


class _FakeClient:
    def __init__(
        self, reply: str = "这一步先看步骤里要核对的口径。", exc: BaseException | None = None
    ) -> None:
        self.reply = reply
        self.exc = exc
        self.calls: list[tuple[str, str, str, bool]] = []
        self.timeout = 0.0

    def complete(self, model: str, system: str, user: str, *, json_mode: bool) -> str:
        self.calls.append((model, system, user, json_mode))
        if self.exc is not None:
            raise self.exc
        return self.reply


def _client(tmp_path: Path) -> TestClient:
    app = create_app(
        data_root=tmp_path,
        dist_dir=tmp_path / "no-dist",
        control_plane=_NullControlPlane(),
    )
    return TestClient(app, base_url=BASE_URL, client=LOOPBACK)


def _post(client: TestClient, **body: Any) -> Any:
    payload = {
        "question": "这个技能包的第一步要研究什么？",
        "context": "页面键：/skills/a-share-tape",
        **body,
    }
    # 与研究面其它 POST 同一条闸门：写请求需要来源证据（本机页面 / 显式客户端头）
    return client.post("/api/skills/assistant", json=payload, headers={"X-LDST-Client": "1"})


def test_skill_assistant_returns_text_and_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeClient()
    monkeypatch.setattr(assistant_module, "_load_client", lambda: (fake, "openai:deepseek-chat"))

    response = _post(_client(tmp_path), page_key="/skills/a-share-tape", bound_skill="a-share-tape")

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == fake.reply
    assert body["model"] == "openai:deepseek-chat"
    assert body["fetched_at"]
    # system prompt 不含个股深挖预设，但保留「不要编造」与纯文本约束
    _, system, user, json_mode = fake.calls[0]
    assert json_mode is False
    assert "个股深挖" not in system
    assert "不要编造" in system
    # meta 行写页面键与绑定技能；上下文与问题进同一条用户消息
    assert "页面：研究助手 · 技能页 · a-share-tape" in user
    assert "/skills/a-share-tape" in user
    assert "这个技能包的第一步要研究什么？" in user


def test_skill_assistant_without_bound_skill_keeps_plain_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeClient()
    monkeypatch.setattr(assistant_module, "_load_client", lambda: (fake, "openai:model-a"))

    response = _post(_client(tmp_path), page_key="/")

    assert response.status_code == 200
    _, _, user, _ = fake.calls[0]
    assert "页面：研究助手 · 技能页 · 视图 /" in user


def test_skill_assistant_rejects_blank_question(tmp_path: Path) -> None:
    response = _post(_client(tmp_path), question="   ")
    assert response.status_code == 422
    assert response.json()["code"] == "empty_question"


def test_skill_assistant_rejects_overlong_question(tmp_path: Path) -> None:
    response = _post(_client(tmp_path), question="长" * (assistant_module.MAX_QUESTION_CHARS + 1))
    assert response.status_code == 422
    assert response.json()["code"] == "question_too_long"


def test_skill_assistant_rejects_overlong_context(tmp_path: Path) -> None:
    response = _post(_client(tmp_path), context="长" * (assistant_module.MAX_CONTEXT_CHARS + 1))
    assert response.status_code == 422
    assert response.json()["code"] == "context_too_long"


def test_skill_assistant_missing_config_is_readable_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _explode() -> None:
        raise BoundaryViolation("缺少 AI 配置：D:/x/config/ai.yaml")

    monkeypatch.setattr(assistant_module.fallback, "load_config", _explode)

    response = _post(_client(tmp_path))

    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "ai_not_configured"
    assert "未配置 AI 通道" in body["detail"]
    assert body["retryable"] is True


def test_skill_assistant_timeout_is_readable_504(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeClient(exc=httpx.ReadTimeout("timed out"))
    monkeypatch.setattr(assistant_module, "_load_client", lambda: (fake, "openai:model-a"))

    response = _post(_client(tmp_path))

    assert response.status_code == 504
    body = response.json()
    assert body["code"] == "ai_timeout"
    assert "超时" in body["detail"]


def test_skill_assistant_network_failure_is_readable_502(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeClient(exc=httpx.ConnectError("connection refused"))
    monkeypatch.setattr(assistant_module, "_load_client", lambda: (fake, "openai:model-a"))

    response = _post(_client(tmp_path))

    assert response.status_code == 502
    body = response.json()
    assert body["code"] == "ai_network"
    assert "网络" in body["detail"]


def test_skill_assistant_empty_reply_is_not_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeClient(reply="   ")
    monkeypatch.setattr(assistant_module, "_load_client", lambda: (fake, "openai:model-a"))

    response = _post(_client(tmp_path))

    assert response.status_code == 502
    assert response.json()["code"] == "ai_empty"


def test_skill_assistant_requires_loopback_evidence(tmp_path: Path) -> None:
    # 与研究面同一条闸门：不带 X-LDST-Client 头的写请求被拒
    response = _client(tmp_path).post(
        "/api/skills/assistant", json={"question": "x", "context": ""}
    )
    assert response.status_code == 403
