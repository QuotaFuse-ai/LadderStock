"""QUOT-454：`POST /api/research/{code}/assistant` 的契约测试。

路由在接入 agent 之后不再「自己调一次模型、再按意图补取数」，而是把整轮交给
`ask_agent`（模型自主选工具、定轮次）。因此适配层的桩打在 **`ask_agent`** 上：
成功回文本 + 模型名、空回答不算成功、未配 Key / 断网 / 超时三种降级各自给出
可读且可区分的状态码与 `code`。全程不联网、不读密钥。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.core.boundary import BoundaryViolation
from ladderstock.web import assistant as assistant_module
from ladderstock.web.server import create_app

CODE = "600000"
BASE_URL = "http://127.0.0.1:5950"
LOOPBACK = ("127.0.0.1", 54321)

DEFAULT_REPLY = "页面里的现价是 9.87 元。"


class _NullControlPlane:
    peers: dict[str, Any] = {}


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


def _patch_agent(
    monkeypatch: pytest.MonkeyPatch,
    *,
    reply: str = DEFAULT_REPLY,
    exc: BaseException | None = None,
    used_tools: bool = True,
    metrics: dict[str, Any] | None = None,
    sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """把 `ask_agent` 换成本地桩，返回它收到的 kwargs（供断言入参被原样带过去）。"""
    calls: dict[str, Any] = {}

    async def _ask_agent(**kwargs: Any) -> dict[str, Any]:
        calls.update(kwargs)
        if exc is not None:
            raise exc
        return {
            "reply": reply,
            "model": "openai:deepseek-chat",
            "ai": {"model": "openai:deepseek-chat", "source": "config"},
            "fetched_at": "2026-11-06T10:00:00+00:00",
            "metrics": metrics or {},
            "sources": sources or [],
            "used_tools": used_tools,
        }

    monkeypatch.setattr(assistant_module, "ask_agent", _ask_agent)
    return calls


def test_assistant_returns_text_and_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _patch_agent(monkeypatch)

    response = _post(_client(tmp_path), date="2026-11-06", tab="overview")

    assert response.status_code == 200
    body = response.json()
    assert body["reply"] == DEFAULT_REPLY
    assert body["model"] == "openai:deepseek-chat"
    assert body["fetched_at"]
    # 问题与页级上下文原样交给 agent：路由不再自己拼提示词、也不再按意图补取数
    assert calls["question"] == "这只最近的走势怎么样？"
    assert "浦发银行（600000）" in calls["context"]
    assert calls["day"] == "2026-11-06"
    assert calls["tab"] == "overview"


def test_assistant_tool_sources_reach_citation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """工具取到的来源要进 citation 块：正文不写来源，来源只能走这条通道。

    `metric` 块需要带出处的数值（`assert_provenance` 的红线），这里只给来源，
    因此断言落在 citation——数值那一层由 `test_assistant_blocks` 覆盖。
    """
    _patch_agent(
        monkeypatch,
        reply="浦发银行现价取到了。",
        metrics={},
        sources=[{"name": "tickhub · tencent_quote", "fetched_at": "2026-11-16T10:00:00+08:00"}],
    )

    body = _post(_client(tmp_path)).json()

    assert "citation" in [block["type"] for block in body["blocks"]]


def test_assistant_no_tool_turn_does_not_claim_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """模型没调任何工具（如「介绍下自己」），不该摆「本轮没有取到可核实的指标数据」。

    `fetch_attempted` 的语义是「模型有没有查」，不是「页面上有没有数」——
    没查就没有"没取到"可言，这是改走 agent 之后与旧路径的关键差别。
    """
    _patch_agent(monkeypatch, reply="我是 LadderStock 的研究助手。", used_tools=False)

    body = _post(_client(tmp_path)).json()

    assert not [
        block
        for block in body["blocks"]
        if block["type"] == "notice"
        and block["payload"].get("kind") == "missing_data"
        and block["payload"].get("section") == "指标"
    ]
    assert not [
        block
        for block in body["blocks"]
        if block["type"] == "citation"
    ]


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


def test_assistant_timeout_is_readable_504(
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


def test_assistant_network_failure_is_readable_502(
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


def test_assistant_empty_reply_is_not_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 空回答由 `ask_agent` 自己判失败（502 ai_empty），路由只需把它翻成可读响应
    _patch_agent(
        monkeypatch,
        exc=assistant_module.AssistantFailure(
            502, "ai_empty", "AI 返回了空回答（模型未产出内容），请重试。"
        ),
    )

    response = _post(_client(tmp_path))

    assert response.status_code == 502
    assert response.json()["code"] == "ai_empty"