"""QUOT-493：`POST /api/page/assistant` 的契约测试。

任意页面（watch / industry / calendar …）的抽屉问答：复用 `assistant.ask()` 的客户端与
降级口径，上下文是前端传的任意 JSON（本端序列化后进同一条用户消息），system prompt 换成
不含个股深挖预设的 `PAGE_SYSTEM_PROMPT`。模型调用用假客户端，不联网、不读密钥。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from ladderstock.ai.providers import ProviderError
from ladderstock.core.boundary import BoundaryViolation
from ladderstock.web import assistant as assistant_module
from ladderstock.web.server import create_app

BASE_URL = "http://127.0.0.1:5950"
LOOPBACK = ("127.0.0.1", 54321)


class _NullControlPlane:
    peers: dict[str, Any] = {}


class _FakeClient:
    def __init__(
        self,
        reply: str = "这只票今天跌了 3.2%，和上下文里展示的涨幅一致。",
        exc: BaseException | None = None,
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
    payload: dict[str, Any] = {
        "question": "今天自选股整体表现怎么样？",
        "context": {"rows": [{"code": "600519", "change": -0.032}], "as_of": "2026-11-08"},
        **body,
    }
    # 与研究面其它 POST 同一条闸门：写请求需要来源证据（本机页面 / 显式客户端头）
    return client.post("/api/page/assistant", json=payload, headers={"X-LDST-Client": "1"})


def test_page_assistant_returns_text_and_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeClient()
    monkeypatch.setattr(assistant_module, "_load_client", lambda: (fake, "openai:deepseek-chat"))

    response = _post(_client(tmp_path), page_key="watch", bound_skill="a-share-tape")

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == fake.reply
    assert body["model"] == "openai:deepseek-chat"
    assert body["fetched_at"]
    # system prompt 不含个股深挖预设，但保留「没取到」口径与买卖建议禁令
    _, system, user, json_mode = fake.calls[0]
    assert json_mode is False
    assert "个股深挖" not in system
    assert "本页这项没取到" in system
    assert "不要给出买卖建议" in system
    # meta 行写页面键与绑定技能；上下文 JSON 序列化后与问题进同一条用户消息
    assert "页面：页面助手 · watch · a-share-tape" in user
    assert '"code": "600519"' in user
    assert '"change": -0.032' in user
    assert "今天自选股整体表现怎么样？" in user


def test_page_assistant_without_optional_labels_keeps_plain_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeClient()
    monkeypatch.setattr(assistant_module, "_load_client", lambda: (fake, "openai:model-a"))

    response = _post(_client(tmp_path), page_key="industries", bound_skill=None)

    assert response.status_code == 200
    _, _, user, _ = fake.calls[0]
    assert "页面：页面助手 · industries" in user


def test_page_assistant_empty_context_says_not_fetched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeClient()
    monkeypatch.setattr(assistant_module, "_load_client", lambda: (fake, "openai:model-a"))

    response = _post(_client(tmp_path), context=None)

    assert response.status_code == 200
    _, _, user, _ = fake.calls[0]
    # 没有上下文时沿用 ask() 的缺项口径，不编造、也不把占位当回答
    assert "（这一页的数据还没取到。）" in user
    assert fake.reply not in user


def test_page_assistant_rejects_blank_question(tmp_path: Path) -> None:
    response = _post(_client(tmp_path), question="   ")
    assert response.status_code == 422
    assert response.json()["code"] == "empty_question"


def test_page_assistant_rejects_overlong_question(tmp_path: Path) -> None:
    response = _post(_client(tmp_path), question="长" * (assistant_module.MAX_QUESTION_CHARS + 1))
    assert response.status_code == 422
    assert response.json()["code"] == "question_too_long"


def test_page_assistant_rejects_overlong_context(tmp_path: Path) -> None:
    # 上下文上限按序列化后的文本长度判定：嵌套 JSON 也能顶到上限
    nested = {"rows": [{"blob": "长" * assistant_module.MAX_CONTEXT_CHARS}]}
    response = _post(_client(tmp_path), context=nested)
    assert response.status_code == 422
    assert response.json()["code"] == "context_too_long"


def test_page_assistant_missing_config_is_readable_503(
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


def test_page_assistant_timeout_is_readable_504(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeClient(exc=httpx.ReadTimeout("timed out"))
    monkeypatch.setattr(assistant_module, "_load_client", lambda: (fake, "openai:model-a"))

    response = _post(_client(tmp_path))

    assert response.status_code == 504
    body = response.json()
    assert body["code"] == "ai_timeout"
    assert "超时" in body["detail"]


def test_page_assistant_network_failure_is_readable_502(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeClient(exc=httpx.ConnectError("connection refused"))
    monkeypatch.setattr(assistant_module, "_load_client", lambda: (fake, "openai:model-a"))

    response = _post(_client(tmp_path))

    assert response.status_code == 502
    body = response.json()
    assert body["code"] == "ai_network"
    assert "网络" in body["detail"]


def test_page_assistant_rate_limit_is_readable_429(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeClient(exc=ProviderError("HTTP 429: rate limit exceeded"))
    monkeypatch.setattr(assistant_module, "_load_client", lambda: (fake, "openai:model-a"))

    response = _post(_client(tmp_path))

    assert response.status_code == 429
    body = response.json()
    assert body["code"] == "ai_quota"


def test_page_assistant_empty_reply_is_not_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = _FakeClient(reply="   ")
    monkeypatch.setattr(assistant_module, "_load_client", lambda: (fake, "openai:model-a"))

    response = _post(_client(tmp_path))

    assert response.status_code == 502
    assert response.json()["code"] == "ai_empty"


def test_page_assistant_requires_loopback_evidence(tmp_path: Path) -> None:
    # 与研究面同一条闸门：不带 X-LDST-Client 头的写请求被拒
    response = _client(tmp_path).post(
        "/api/page/assistant",
        json={"question": "x", "context": {"rows": []}},
    )
    assert response.status_code == 403
