"""QUOT-473：`POST /api/control/ai/chat` 的契约测试。

只测适配层，不联网、不读密钥：通道用假实现（或指向临时 config 目录），
把「成功 / 无通道 / 断网 / 超时 / 空回答 / 中止」与「来源清单、会话、研究建议」
逐条钉住；同时确认 `generate` / `retry` 的 job 型契约一字未变。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from ladderstock.ai import cli_provider
from ladderstock.web import chat as chat_channel
from ladderstock.web.server import create_app

BASE_URL = "http://127.0.0.1:5950"
LOOPBACK = ("127.0.0.1", 54321)
WRITE_HEADERS = {"X-LDST-Client": "1"}
CHAT = "/api/control/ai/chat"


class _Plane(SimpleNamespace):
    """只要 `peers` 与 `config_root`：chat 路由不碰控制面的其它能力。"""


def _plane(tmp_path: Path) -> _Plane:
    config_root = tmp_path / "config"
    config_root.mkdir(parents=True, exist_ok=True)
    return _Plane(peers={}, config_root=config_root)


def _client(tmp_path: Path, plane: Any | None = None) -> TestClient:
    app = create_app(
        data_root=tmp_path,
        dist_dir=tmp_path / "no-dist",
        control_plane=plane or _plane(tmp_path),
    )
    return TestClient(app, base_url=BASE_URL, client=LOOPBACK)


def _post(client: TestClient, **body: Any) -> Any:
    return client.post(CHAT, json=body, headers=WRITE_HEADERS)


def _ok_channel() -> chat_channel.Channel:
    return chat_channel.Channel(
        kind="cli", label="cli_codex:default", config=None, target=None
    )


def _write_ai_config(config_root: Path, *, cli: str | None = None) -> None:
    (config_root / "ai.yaml").write_text(
        "ai:\n"
        "  backends:\n"
        "    - id: openai\n"
        "      type: openai\n"
        "      models: [model-a]\n"
        "  default_model: openai:model-a\n",
        encoding="utf-8",
    )
    if cli:
        (config_root / "ai.env").write_text(f"LDST_LLM_CLI={cli}\n", encoding="utf-8")


@pytest.fixture(autouse=True)
def _clean_sessions() -> None:
    chat_channel.reset_sessions()
    yield
    chat_channel.reset_sessions()


# --------------------------------------------------------------------------- #
# 成功路径
# --------------------------------------------------------------------------- #

def test_chat_returns_reply_and_real_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_complete(channel, system, user, *, timeout):
        assert "【页内数据（只读）】" in user
        assert "腾讯行情快照" in user
        return "今天量能比昨日放大。"

    monkeypatch.setattr(chat_channel, "load_channel", lambda _root=None: _ok_channel())
    monkeypatch.setattr(chat_channel, "complete", fake_complete)

    response = _post(
        _client(tmp_path),
        message="今天大盘怎么样？",
        context={
            "page": "home",
            "data": {"turnover": 1.2e12},
            "sources": [
                {
                    "name": "腾讯行情快照",
                    "fetched_at": "2026-11-07T09:20:00+08:00",
                    "url": "https://qt.gtimg.cn/q=sh000001?token=secret",
                }
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["reply"] == "今天量能比昨日放大。"
    assert body["model"] == "cli_codex:default"
    assert body["channel"] == "cli"
    assert body["session_id"]
    source = body["sources"][0]
    assert source["name"] == "腾讯行情快照"
    assert source["fetched_at"] == "2026-11-07T09:20:00+08:00"
    assert source["used_at"]
    # 链接里的查询串（可能带 token）不进响应
    assert source["url"] == "https://qt.gtimg.cn/q=sh000001"
    assert body["pending_research"] == []


def test_chat_without_sources_says_so_in_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_complete(channel, system, user, *, timeout):
        return "建议关注量能变化。"

    monkeypatch.setattr(chat_channel, "load_channel", lambda _root=None: _ok_channel())
    monkeypatch.setattr(chat_channel, "complete", fake_complete)

    body = _post(_client(tmp_path), message="随便聊聊").json()

    assert body["sources"] == []
    assert body["reply"].startswith("建议关注量能变化。")
    assert "没有任何可核实的数据来源" in body["reply"]


def test_chat_suggests_but_never_starts_research(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_complete(channel, system, user, *, timeout):
        return "这只票的看点如下。"

    monkeypatch.setattr(chat_channel, "load_channel", lambda _root=None: _ok_channel())
    monkeypatch.setattr(chat_channel, "complete", fake_complete)

    body = _post(
        _client(tmp_path), message="帮我分析一下 600000 的基本面"
    ).json()

    pending = body["pending_research"]
    assert [item["code"] for item in pending] == ["600000"]
    assert pending[0]["kind"] == "stock_research"
    assert pending[0]["confirm_required"] is True


# --------------------------------------------------------------------------- #
# 失败三态（+ 其它类）
# --------------------------------------------------------------------------- #

def test_chat_without_config_is_503_no_channel(tmp_path: Path) -> None:
    response = _post(_client(tmp_path), message="你好")

    assert response.status_code == 503
    body = response.json()
    assert body["ok"] is False
    assert body["code"] == "ai_no_channel"
    assert body["retryable"] is True
    assert body["failure"]["reason"] == "no_channel"
    assert body["sources"] == []
    assert body["reply"] == ""


def test_chat_cli_not_logged_in_exposes_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plane = _plane(tmp_path)
    _write_ai_config(plane.config_root, cli="codex")
    monkeypatch.setattr(
        cli_provider,
        "probe",
        lambda spec: {"state": "no_login", "detail": "需要先登录", "exe": "codex"},
    )

    response = _post(_client(tmp_path, plane), message="你好")

    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "ai_no_channel"
    assert body["failure"]["channel_state"] == "no_login"
    assert "登录" in body["failure"]["message"]


def test_chat_network_failure_is_502(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_complete(channel, system, user, *, timeout):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(chat_channel, "load_channel", lambda _root=None: _ok_channel())
    monkeypatch.setattr(chat_channel, "complete", fake_complete)

    response = _post(_client(tmp_path), message="你好")

    assert response.status_code == 502
    body = response.json()
    assert body["code"] == "ai_network"
    assert body["failure"]["kind"] == "network"
    assert body["failure"]["reason"] == "network"
    assert body["retryable"] is True
    assert "网络" in body["detail"]


def test_chat_upstream_5xx_is_502_upstream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """上游真的回了 5xx：与「请求没送达」区分开报 `ai_upstream`，仍可重试。"""

    class _UpstreamError(Exception):
        status_code = 500

    async def fake_complete(channel, system, user, *, timeout):
        raise _UpstreamError('HTTP 500: {"error":{"message":"upstream failed"}}')

    monkeypatch.setattr(chat_channel, "load_channel", lambda _root=None: _ok_channel())
    monkeypatch.setattr(chat_channel, "complete", fake_complete)

    response = _post(_client(tmp_path), message="你好")

    assert response.status_code == 502
    body = response.json()
    assert body["code"] == "ai_upstream"
    assert body["failure"]["kind"] == "network"
    assert body["failure"]["reason"] == "upstream"
    assert body["retryable"] is True
    assert "上游模型返回 500" in body["detail"]
    assert "未送达" not in body["detail"]


def test_chat_timeout_is_504(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cancelled = asyncio.Event()

    async def slow_complete(channel, system, user, *, timeout):
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return "迟到的回答"

    monkeypatch.setattr(chat_channel, "load_channel", lambda _root=None: _ok_channel())
    monkeypatch.setattr(chat_channel, "complete", slow_complete)
    monkeypatch.setattr(chat_channel, "effective_timeout", lambda _requested: 0.15)

    response = _post(_client(tmp_path), message="你好")

    assert response.status_code == 504
    body = response.json()
    assert body["code"] == "ai_timeout"
    assert body["failure"]["reason"] == "timeout"
    assert body["failure"]["kind"] == "network"
    assert body["retryable"] is True
    assert "超时" in body["detail"]
    assert cancelled.is_set()


def test_chat_empty_reply_is_not_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_complete(channel, system, user, *, timeout):
        return "   "

    monkeypatch.setattr(chat_channel, "load_channel", lambda _root=None: _ok_channel())
    monkeypatch.setattr(chat_channel, "complete", fake_complete)

    response = _post(_client(tmp_path), message="你好")

    assert response.status_code == 502
    assert response.json()["code"] == "ai_empty"


def test_chat_rejects_future_source_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(chat_channel, "load_channel", lambda _root=None: _ok_channel())

    response = _post(
        _client(tmp_path),
        message="你好",
        context={"sources": [{"name": "未来数据", "fetched_at": "2999-01-01T00:00:00+08:00"}]},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_source"
    assert response.json()["retryable"] is False


def test_chat_rejects_oversized_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(chat_channel, "load_channel", lambda _root=None: _ok_channel())

    response = _post(
        _client(tmp_path),
        message="你好",
        context={"data": {"blob": "x" * (chat_channel.MAX_CONTEXT_CHARS + 1)}},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "context_too_long"


def test_chat_rejects_blank_message(tmp_path: Path) -> None:
    response = _post(_client(tmp_path), message="   ")

    assert response.status_code == 400
    assert response.json()["code"] == "empty_message"


# --------------------------------------------------------------------------- #
# 会话记忆 / 中止
# --------------------------------------------------------------------------- #

def test_chat_multi_turn_keeps_successful_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prompts: list[str] = []
    replies = iter(["第一轮回答", "第二轮回答", "第三轮回答"])

    async def fake_complete(channel, system, user, *, timeout):
        prompts.append(user)
        return next(replies)

    monkeypatch.setattr(chat_channel, "load_channel", lambda _root=None: _ok_channel())
    monkeypatch.setattr(chat_channel, "complete", fake_complete)

    client = _client(tmp_path)
    first = _post(client, message="第一个问题").json()
    session_id = first["session_id"]
    _post(client, message="第二个问题", session_id=session_id)

    assert "【历史对话】" not in prompts[0]
    assert "【历史对话】" in prompts[1]
    assert "第一个问题" in prompts[1]
    assert "第一轮回答" in prompts[1]

    # 无效 session_id 直接 400，不会悄悄换一个
    bad = _post(client, message="第三个问题", session_id="bad id!")
    assert bad.status_code == 400
    assert bad.json()["code"] == "invalid_session"


def test_success_history_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    state = {"fail": True}

    async def flaky(channel, system, user, *, timeout):
        calls.append(user)
        if state["fail"]:
            raise httpx.ConnectError("connection refused")
        return "成功回答"

    monkeypatch.setattr(chat_channel, "load_channel", lambda _root=None: _ok_channel())
    monkeypatch.setattr(chat_channel, "complete", flaky)

    client = _client(tmp_path)
    failed = _post(client, message="会失败的问题").json()
    state["fail"] = False
    _post(client, message="第二轮", session_id=failed["session_id"])

    assert "会失败的问题" not in calls[1]


def test_turn_aborts_when_frontend_disconnects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cancelled = asyncio.Event()

    async def slow_complete(channel, system, user, *, timeout):
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return "不该拿到的回答"

    monkeypatch.setattr(chat_channel, "load_channel", lambda _root=None: _ok_channel())
    monkeypatch.setattr(chat_channel, "complete", slow_complete)
    monkeypatch.setattr(chat_channel, "effective_timeout", lambda _requested: 30.0)
    chat_channel.reset_sessions()

    async def disconnecting() -> bool:
        return True

    async def scenario() -> None:
        with pytest.raises(chat_channel.ChatAborted):
            await chat_channel.run_turn(
                message="你好",
                session_id="session-abcdefgh",
                is_disconnected=disconnecting,
            )

    asyncio.run(scenario())
    assert cancelled.is_set()
    assert chat_channel._history("session-abcdefgh") == []


# --------------------------------------------------------------------------- #
# 回归：generate / retry 的 job 型契约不变
# --------------------------------------------------------------------------- #

def test_generate_and_retry_contract_unchanged(tmp_path: Path) -> None:
    plane = _plane(tmp_path)
    plane.create_ai_job = lambda kind, day, **kwargs: SimpleNamespace(
        id=f"job-{kind}", status="running"
    )
    client = _client(tmp_path, plane)

    generated = client.post(
        "/api/control/ai/generate", json={"date": "2026-11-06"}, headers=WRITE_HEADERS
    )
    retried = client.post(
        "/api/control/ai/retry",
        json={"date": "2026-11-06", "section": "sentiment"},
        headers=WRITE_HEADERS,
    )

    assert generated.status_code == 200
    assert generated.json() == {"job_id": "job-generate", "status": "running"}
    assert retried.status_code == 200
    assert retried.json() == {"job_id": "job-retry", "status": "running"}

    # 契约字段没变：`force` 照旧透传，未知 `section` 照旧 400
    forced = client.post(
        "/api/control/ai/generate",
        json={"date": "2026-11-06", "force": True},
        headers=WRITE_HEADERS,
    )
    assert forced.status_code == 200
    bad_section = client.post(
        "/api/control/ai/retry",
        json={"date": "2026-11-06", "section": "not-a-section"},
        headers=WRITE_HEADERS,
    )
    assert bad_section.status_code == 400
    # QUOT-473 没给这两个模型加 `extra="forbid"`：多传字段仍按既有行为忽略
    extra = client.post(
        "/api/control/ai/generate",
        json={"date": "2026-11-06", "unexpected": 1},
        headers=WRITE_HEADERS,
    )
    assert extra.status_code == 200
    assert extra.json() == {"job_id": "job-generate", "status": "running"}
