"""QUOT-454：`POST /api/research/{code}/assistant` 的契约测试。

只测适配层：成功回文本、空回答不算成功、未配 Key / 断网 / 超时三种降级各自
给出可读且可区分的状态码与 `code`。模型调用用假客户端，不联网、不读密钥。
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

CODE = "600000"
BASE_URL = "http://127.0.0.1:5950"
LOOPBACK = ("127.0.0.1", 54321)


class _NullControlPlane:
    peers: dict[str, Any] = {}


class _FakeClient:
    def __init__(
        self, reply: str = "页面里的现价是 9.87 元。", exc: BaseException | None = None
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
    payload = {"question": "这只最近的走势怎么样？", "context": "标的：浦发银行（600000）", **body}
    # 与研究面其它 POST 同一条闸门：写请求需要来源证据（本机页面 / 显式客户端头）
    return client.post(
        f"/api/research/{CODE}/assistant",
        json=payload,
        headers={"X-LDST-Client": "1"},
    )


def test_assistant_returns_text_and_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient()
    monkeypatch.setattr(
        assistant_module, "_load_client", lambda *_a, **_k: (fake, "openai:deepseek-chat")
    )

    response = _post(_client(tmp_path), date="2026-11-06", tab="overview")

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == fake.reply
    assert body["model"] == "openai:deepseek-chat"
    assert body["fetched_at"]
    # 上下文与问题确实进了同一条用户消息；system 里含「不要编造」
    _, system, user, json_mode = fake.calls[0]
    assert json_mode is False
    assert "不要编造" in system
    assert "【页级上下文】" in user
    assert "浦发银行（600000）" in user
    assert "这只最近的走势怎么样？" in user


def test_assistant_rejects_blank_question(tmp_path: Path) -> None:
    response = _post(_client(tmp_path), question="   ")
    assert response.status_code == 422
    assert response.json()["code"] == "empty_question"


def test_assistant_missing_config_is_readable_503(
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


def test_assistant_missing_key_is_readable_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(assistant_module.fallback, "load_config", lambda: object())
    monkeypatch.setattr(assistant_module.fallback, "is_configured", lambda _config: False)
    monkeypatch.setattr(
        assistant_module.fallback,
        "missing_env",
        lambda _config: ["LDST_LLM_BASE_URL", "LDST_LLM_API_KEY"],
    )

    response = _post(_client(tmp_path))

    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "ai_not_configured"
    assert "LDST_LLM_API_KEY" in body["detail"]


def test_assistant_timeout_is_readable_504(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(exc=httpx.ReadTimeout("timed out"))
    monkeypatch.setattr(
        assistant_module, "_load_client", lambda *_a, **_k: (fake, "openai:model-a")
    )

    response = _post(_client(tmp_path))

    assert response.status_code == 504
    body = response.json()
    assert body["code"] == "ai_timeout"
    assert "超时" in body["detail"]


def test_assistant_network_failure_is_readable_502(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeClient(exc=httpx.ConnectError("connection refused"))
    monkeypatch.setattr(
        assistant_module, "_load_client", lambda *_a, **_k: (fake, "openai:model-a")
    )

    response = _post(_client(tmp_path))

    assert response.status_code == 502
    body = response.json()
    assert body["code"] == "ai_network"
    assert "网络" in body["detail"]


def test_assistant_empty_reply_is_not_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeClient(reply="   ")
    monkeypatch.setattr(
        assistant_module, "_load_client", lambda *_a, **_k: (fake, "openai:model-a")
    )

    response = _post(_client(tmp_path))

    assert response.status_code == 502
    assert response.json()["code"] == "ai_empty"
