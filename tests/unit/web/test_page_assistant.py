"""QUOT-493/QUOT-477：`POST /api/page/assistant` 的契约测试。

任意页面（watch / industry / calendar …）的抽屉问答：上下文是前端序列化好的文本
（与 research / skills 同构，QUOT-477 修复 422），system prompt 换成不含个股深挖
预设的 `PAGE_SYSTEM_PROMPT`。

路由改成走 agent 之后，桩打在 **`ask_agent`** 上；prompt 的红线（system 里不出现
个股深挖预设、保留「没取到」与禁买卖建议口径）改由 `_prepare_call` 直接验证——
它仍是 prompt 的唯一组装点，`ask_agent` 只是把它的产物交给 agent。全程不联网。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.core.boundary import BoundaryViolation
from ladderstock.web import assistant as assistant_module
from ladderstock.web.server import create_app

BASE_URL = "http://127.0.0.1:5950"
LOOPBACK = ("127.0.0.1", 54321)

DEFAULT_REPLY = "这只票今天跌了 3.2%，和上下文里展示的涨幅一致。"
CONTEXT_ROWS = json.dumps(
    {"rows": [{"code": "600519", "change": -0.032}], "as_of": "2026-11-08"},
    ensure_ascii=False,
)


class _NullControlPlane:
    peers: dict[str, Any] = {}


class _FakeClient:
    """只服务 `_prepare_call`：它拿到的 client 不会被真的调用。"""

    timeout = 0.0

    def complete(self, model: str, system: str, user: str, *, json_mode: bool) -> str:
        raise AssertionError("路由不应再直接调模型——本轮由 ask_agent 负责")


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
        # 与 research / skills 两条通道同一约定：前端序列化好的文本，不是 dict（QUOT-477）
        "context": CONTEXT_ROWS,
        **body,
    }
    # 与研究面其它 POST 同一条闸门：写请求需要来源证据（本机页面 / 显式客户端头）
    return client.post("/api/page/assistant", json=payload, headers={"X-LDST-Client": "1"})


def _patch_agent(
    monkeypatch: pytest.MonkeyPatch,
    *,
    reply: str = DEFAULT_REPLY,
    exc: BaseException | None = None,
) -> dict[str, Any]:
    calls: dict[str, Any] = {}

    async def _ask_agent(**kwargs: Any) -> dict[str, Any]:
        calls.update(kwargs)
        if exc is not None:
            raise exc
        return {
            "reply": reply,
            "model": "openai:deepseek-chat",
            "ai": {"model": "openai:deepseek-chat", "source": "config"},
            "fetched_at": "2026-11-08T10:00:00+00:00",
            "metrics": {},
            "sources": [],
            "used_tools": False,
        }

    monkeypatch.setattr(assistant_module, "ask_agent", _ask_agent)
    return calls


def test_page_assistant_returns_text_and_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _patch_agent(monkeypatch)

    response = _post(_client(tmp_path), page_key="watch", bound_skill="a-share-tape")

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == DEFAULT_REPLY
    assert body["model"] == "openai:deepseek-chat"
    assert body["fetched_at"]
    # 页面键与绑定技能进 meta 标签，问题与上下文原样交给 agent
    assert calls["page_label"] == "页面助手 · watch · a-share-tape"
    assert calls["page"] == "page"
    assert calls["question"] == "今天自选股整体表现怎么样？"
    assert "600519" in calls["context"]


def test_page_assistant_without_optional_labels_keeps_plain_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _patch_agent(monkeypatch)

    response = _post(_client(tmp_path), page_key="industries", bound_skill=None)

    assert response.status_code == 200
    assert calls["page_label"] == "页面助手 · industries"


def test_page_prompt_keeps_page_red_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """prompt 的唯一组装点仍是 `_prepare_call`：页面预设不越界到个股深挖。"""
    monkeypatch.setattr(
        assistant_module, "_load_client", lambda *_a, **_k: (_FakeClient(), "openai:model-a")
    )

    prepared = assistant_module._prepare_call(
        question="今天自选股整体表现怎么样？",
        context=CONTEXT_ROWS,
        page_label="页面助手 · watch · a-share-tape",
        system_prompt=assistant_module.PAGE_SYSTEM_PROMPT,
        module=assistant_module.DEFAULT_MODULE,
        tab="watch",
        data_root=tmp_path,
    )

    assert "个股深挖" not in prepared.system
    assert "本页这项没取到" in prepared.system
    assert "不要给出买卖建议" in prepared.system
    assert "页面：页面助手 · watch · a-share-tape" in prepared.user
    assert '"code": "600519"' in prepared.user
    assert "今天自选股整体表现怎么样？" in prepared.user


def test_page_assistant_empty_context_says_not_fetched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _patch_agent(monkeypatch)

    response = _post(_client(tmp_path), context="")

    assert response.status_code == 200
    # 空上下文字面就交给 agent（由 prompt 的缺项口径说"没取到"），路由不自己填占位
    assert calls["context"] == ""


def test_page_assistant_rejects_blank_question(tmp_path: Path) -> None:
    response = _post(_client(tmp_path), question="   ")
    assert response.status_code == 422
    assert response.json()["code"] == "empty_question"


def test_page_assistant_rejects_overlong_question(tmp_path: Path) -> None:
    response = _post(_client(tmp_path), question="长" * (assistant_module.MAX_QUESTION_CHARS + 1))
    assert response.status_code == 422
    assert response.json()["code"] == "question_too_long"


def test_page_assistant_rejects_overlong_context(tmp_path: Path) -> None:
    # 上下文上限按文本长度判定：长文本（与前端 serializedContext 同形态）也能顶到上限
    overlong = "长" * (assistant_module.MAX_CONTEXT_CHARS + 1)
    response = _post(_client(tmp_path), context=overlong)
    assert response.status_code == 422
    assert response.json()["code"] == "context_too_long"


def test_page_assistant_accepts_text_context_not_only_dict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """QUOT-477 回归：前端 serializedContext 是字符串，收 str 的端点必须 200。"""
    calls = _patch_agent(monkeypatch)

    response = _post(_client(tmp_path), context="涨跌家数 1183/5237，跌停 12")

    assert response.status_code == 200
    assert "涨跌家数 1183/5237，跌停 12" in calls["context"]


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
    _patch_agent(
        monkeypatch,
        exc=assistant_module.AssistantFailure(504, "ai_timeout", "模型调用超时，请稍后重试。"),
    )

    response = _post(_client(tmp_path))

    assert response.status_code == 504
    body = response.json()
    assert body["code"] == "ai_timeout"
    assert "超时" in body["detail"]


def test_page_assistant_network_failure_is_readable_502(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_agent(
        monkeypatch,
        exc=assistant_module.AssistantFailure(502, "ai_network", "连不上模型服务（网络）。"),
    )

    response = _post(_client(tmp_path))

    assert response.status_code == 502
    body = response.json()
    assert body["code"] == "ai_network"
    assert "网络" in body["detail"]


def test_page_assistant_rate_limit_is_readable_429(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_agent(
        monkeypatch,
        exc=assistant_module.AssistantFailure(429, "ai_quota", "模型额度不足，请稍后重试。"),
    )

    response = _post(_client(tmp_path))

    assert response.status_code == 429
    assert response.json()["code"] == "ai_quota"


def test_page_assistant_empty_reply_is_not_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_agent(
        monkeypatch,
        exc=assistant_module.AssistantFailure(
            502, "ai_empty", "AI 返回了空回答（模型未产出内容），请重试。"
        ),
    )

    response = _post(_client(tmp_path))

    assert response.status_code == 502
    assert response.json()["code"] == "ai_empty"


def test_page_assistant_requires_loopback_evidence(tmp_path: Path) -> None:
    # 与研究面同一条闸门：不带 X-LDST-Client 头的写请求被拒
    response = _client(tmp_path).post(
        "/api/page/assistant",
        json={"question": "x", "context": ""},
    )
    assert response.status_code == 403