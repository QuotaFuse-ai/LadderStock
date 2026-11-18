"""QUOT-487：助手问答链上的 mem0 记忆接入（注入分段 / 写入门槛 / 降级）。

只测适配层，不连真实引擎：召回与写入都注入假 engine，
断言「提示词里出现的分段」与「传给引擎的载荷」两件事。
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.ai import memory
from ladderstock.ai.fallback import MemoryScope
from ladderstock.web import assistant as assistant_module
from ladderstock.web.server import create_app

CODE = "600000"
BASE_URL = "http://127.0.0.1:5950"
LOOPBACK = ("127.0.0.1", 54321)
REPO_ROOT = Path(__file__).resolve().parents[3]

MEMORY_HEADER = memory.MEMORY_SECTION_HEADER
KNOWLEDGE_HEADER = memory.KNOWLEDGE_SECTION_HEADER
KNOWLEDGE_BEGIN = memory.KNOWLEDGE_BEGIN


class _NullControlPlane:
    peers: dict[str, Any] = {}


class _FakeClient:
    def __init__(self, reply: str = "好，已按周线口径梳理。") -> None:
        self.reply = reply
        self.calls: list[tuple[str, str, str, bool]] = []
        self.timeout = 0.0

    def complete(self, model: str, system: str, user: str, *, json_mode: bool) -> str:
        self.calls.append((model, system, user, json_mode))
        return self.reply


def _settings(**overrides: Any) -> memory.MemorySettings:
    payload: dict[str, Any] = {
        "user_id": "u-quot487",
        "top_k": 5,
        "score_threshold": 0.55,
        "timeout_sec": 5.0,
        "enabled": True,
        "models_dir": Path("models"),
        "state_dir": Path("data/mem0"),
    }
    payload.update(overrides)
    return memory.MemorySettings(**payload)


class _FakeEngine:
    """记录调用的假引擎：search 返回给定 hits，add 记载荷，可注入故障。"""

    def __init__(
        self,
        hits: list[dict[str, Any]] | None = None,
        exc: BaseException | None = None,
        add_error: BaseException | None = None,
    ) -> None:
        self.hits = hits or []
        self.exc = exc
        self.add_error = add_error
        self.added: list[dict[str, Any]] = []
        self.searched: list[dict[str, Any]] = []
        self.calls: list[str] = []

    def add(
        self,
        messages: list[dict[str, Any]],
        *,
        user_id: str,
        agent_id: str | None = None,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        infer: bool = True,
    ) -> dict[str, Any]:
        self.calls.append("add")
        if self.exc is not None:
            raise self.exc
        if self.add_error is not None:
            raise self.add_error
        self.added.append(
            {
                "messages": messages,
                "user_id": user_id,
                "agent_id": agent_id,
                "run_id": run_id,
                "metadata": dict(metadata or {}),
            }
        )
        return {"results": []}

    def search(
        self, query: str, *, top_k: int = 5, filters: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.calls.append("search")
        if self.exc is not None:
            raise self.exc
        self.searched.append({"query": query, "top_k": top_k, "filters": dict(filters or {})})
        return {"results": self.hits}

    def get_all(self, *, filters: dict[str, Any] | None = None, top_k: int = 100) -> dict[str, Any]:
        self.calls.append("get_all")
        return {"results": []}

    def delete_all(self, *, user_id: str, agent_id: str | None = None) -> None:
        self.calls.append("delete_all")


def _fake_client(fake_engine: _FakeEngine, settings: memory.MemorySettings):
    return memory.MemoryClient(settings, engine=fake_engine)


def _patch_files(monkeypatch: pytest.MonkeyPatch, fake: _FakeClient) -> None:
    """两个桩：直调 `ask` 的用例走 `_load_client`；走 HTTP 的用例走 `ask_agent`。

    本组要验的是**记忆召回与写入**，因此 ask_agent 的替身不返回假文本，而是把这一轮
    转给真实的 `ask()`——召回、写回、降级日志都按现役代码跑，只有模型客户端是假的。
    """

    monkeypatch.setattr(
        assistant_module, "_load_client", lambda *_a, **_k: (fake, "openai:model-a")
    )

    async def _ask_agent(**kwargs: Any) -> dict[str, Any]:
        result = assistant_module.ask(
            question=kwargs.get("question", ""),
            context=kwargs.get("context", ""),
            day=kwargs.get("day"),
            tab=kwargs.get("tab"),
            page_label=kwargs.get("page_label", "个股深挖"),
            system_prompt=kwargs.get("system_prompt", assistant_module.SYSTEM_PROMPT),
            module=kwargs.get("module", assistant_module.DEFAULT_MODULE),
            symbol=kwargs.get("symbol"),
            page=kwargs.get("page", "research"),
            data_root=kwargs.get("data_root"),
            sync_memory=bool(kwargs.get("sync_memory", False)),
        )
        return {**result, "metrics": {}, "sources": [], "used_tools": False}

    monkeypatch.setattr(assistant_module, "ask_agent", _ask_agent)
    # 命名空间解析走仓库真实配置（modules 未配置 → 默认命名空间），不依赖 CWD
    monkeypatch.setenv("LDST_CONFIG_DIR", str(REPO_ROOT / "config"))


def _ask_direct(
    monkeypatch: pytest.MonkeyPatch,
    fake: _FakeClient,
    fake_engine: _FakeEngine,
    settings: memory.MemorySettings,
    *,
    question: str = "这只最近的走势怎么样？",
    context: str = "标的：浦发银行（600000）",
    sync_memory: bool = False,
    data_root: Any = None,
) -> dict[str, Any]:
    """绕过 HTTP 层直调 `ask`：记忆 settings/client 显式注入，可控同步写入。

    `data_root` 必须由调用方给空目录：档案段会读 `<root>/knowledge`，不传就落到
    仓库真实数据盘，盘里有该标的档案时「无档案」的断言会随盘数据而失效。
    """
    _patch_files(monkeypatch, fake)
    return assistant_module.ask(
        question=question,
        context=context,
        day="2026-11-06",
        tab="overview",
        symbol=CODE,
        page="research",
        memory_settings=settings,
        memory_client=_fake_client(fake_engine, settings),
        sync_memory=sync_memory,
        data_root=data_root,
    )


def _endpoint_setup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake: _FakeClient,
    fake_engine: _FakeEngine,
    settings: memory.MemorySettings,
    *,
    sync_store: bool = False,
) -> TestClient:
    """走真实 HTTP 端点：假 AI 客户端 + 假引擎注入 `assistant` 模块。"""
    _patch_files(monkeypatch, fake)
    monkeypatch.setattr(
        assistant_module.memory,
        "shared_client",
        lambda _s: _fake_client(fake_engine, settings),
    )
    monkeypatch.setenv("LDST_MEMORY_USER_ID", settings.user_id)
    # conftest 隔离 fixture 关了通道（哨兵 env）；端点级测试显式开回
    monkeypatch.delenv(memory.LEGACY_ENV_BASE_URL, raising=False)
    monkeypatch.setenv("LDST_MEMORY_ENABLED", "1")
    if sync_store:

        def _sync_dispatch(
            question: str,
            *,
            symbol: str | None,
            page: str,
            run_id: str,
            settings: memory.MemorySettings,
            client: memory.MemoryClient,
            sync: bool = False,
            scope: MemoryScope | None = None,
        ) -> None:
            memory.store(
                question,
                symbol=symbol,
                page=page,
                run_id=run_id,
                settings=settings,
                client=client,
                scope=scope,
            )

        monkeypatch.setattr(assistant_module.memory, "dispatch_store", _sync_dispatch)
    app = create_app(
        data_root=tmp_path,
        dist_dir=tmp_path / "no-dist",
        control_plane=_NullControlPlane(),
    )
    return TestClient(app, base_url=BASE_URL, client=LOOPBACK)


# --------------------------------------------------------------------------- #
# 注入：高分记忆进提示词，物理分段 + KNOWLEDGE 边界
# --------------------------------------------------------------------------- #


def test_memory_hit_injected_as_separated_block(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fake = _FakeClient()
    fake_engine = _FakeEngine(
        hits=[
            {
                "memory": "用户偏好看周线做波段",
                "score": 0.72,
                "metadata": {"page": "research", "symbol": CODE},
            }
        ]
    )
    _ask_direct(monkeypatch, fake, fake_engine, _settings(), data_root=tmp_path)

    _, system, user, json_mode = fake.calls[0]
    assert json_mode is False
    # symbol 命中加权 +0.05：0.72 → 0.77（过滤按原始分，加权只影响排序与展示）
    assert MEMORY_HEADER in system
    assert "- (score 0.77) 用户偏好看周线做波段" in system
    # 段首标注记忆归属的模块命名空间（QUOT-498 收口）
    scope = assistant_module.memory_scope_for(assistant_module.DEFAULT_MODULE)
    assert scope is not None and scope["label"] in system
    # 有记忆时档案段照旧成段；测试数据盘无档案 → 空态说明（QUOT-566 接了检索）
    assert KNOWLEDGE_HEADER in system
    assert KNOWLEDGE_BEGIN in system
    assert memory.KNOWLEDGE_END in system
    assert memory.EMPTY_KNOWLEDGE_NOTE in system
    # 冲突规则：不许静默采用记忆
    assert "记忆里是旧说法" in system
    # 记忆段出现在档案段之前；用户消息仍带问题
    assert system.index(MEMORY_HEADER) < system.index(KNOWLEDGE_HEADER)
    assert "浦发银行（600000）" in user
    assert "这只最近的走势怎么样？" in user


def test_symbol_hit_boosted_and_shown_with_boosted_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeClient()
    fake_engine = _FakeEngine(
        hits=[
            # 当前标的的记忆加分：0.56 + 0.05 = 0.61（原始分低于阈值但加分后过线）
            {"memory": "用户关注该行基本面", "score": 0.56, "metadata": {"symbol": CODE}},
            # 别的标的的记忆不加分：0.60 原样
            {
                "memory": "用户关注 000001 的公告节奏",
                "score": 0.60,
                "metadata": {"symbol": "000001"},
            },
        ]
    )
    _ask_direct(monkeypatch, fake, fake_engine, _settings())

    system = fake.calls[0][1]
    assert "- (score 0.61) 用户关注该行基本面" in system
    assert "- (score 0.60) 用户关注 000001 的公告节奏" in system


def test_low_score_memory_not_injected(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient()
    fake_engine = _FakeEngine(
        hits=[{"memory": "用户问过这只票", "score": 0.30, "metadata": {"symbol": CODE}}]
    )
    _ask_direct(monkeypatch, fake, fake_engine, _settings())

    assert fake_engine.searched  # 召回确实发生了
    # 低分不注入：判断的是「记忆段不出现」，而不是「system 逐字等于常量」——
    # 数据盘里合法的长期偏好段（preferences.json）不属于记忆，允许存在。
    system = fake.calls[0][1]
    assert MEMORY_HEADER not in system
    assert "用户问过这只票" not in system
    assert MEMORY_HEADER not in fake.calls[0][1]


def test_cross_user_memory_not_injected(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient()
    fake_engine = _FakeEngine(
        hits=[
            {
                "memory": "别人的偏好",
                "score": 0.99,
                "metadata": {"user_id": "someone-else"},
            }
        ]
    )
    _ask_direct(monkeypatch, fake, fake_engine, _settings())

    assert MEMORY_HEADER not in fake.calls[0][1]


def test_recall_capped_by_top_k(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient()
    fake_engine = _FakeEngine(
        hits=[
            {"memory": f"偏好 {i}", "score": 0.9 - 0.01 * i, "metadata": {"symbol": CODE}}
            for i in range(8)
        ]
    )
    _ask_direct(monkeypatch, fake, fake_engine, _settings(top_k=2))

    system = fake.calls[0][1]
    assert "- (score 0.95) 偏好 0" in system  # 最高分保留（0.90 + symbol 加权）
    assert "偏好 7" not in system  # 截断后不进提示词
    assert system.count("- (score") == 2


# --------------------------------------------------------------------------- #
# 写入：值得记的才落库，命名空间与 metadata 定死
# --------------------------------------------------------------------------- #


def test_preference_stored_with_namespace_and_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient()
    fake_engine = _FakeEngine()
    _ask_direct(
        monkeypatch,
        fake,
        fake_engine,
        _settings(),
        question="我只看周线，日线太碎",
        sync_memory=True,
    )

    assert len(fake_engine.added) == 1
    payload = fake_engine.added[0]
    scope = assistant_module.memory_scope_for(assistant_module.DEFAULT_MODULE)
    assert scope is not None
    assert payload["user_id"] == "u-quot487"
    # 写入与召回同一对命名空间字段（QUOT-498）：agent_id + metadata.kb_scope
    assert payload["agent_id"] == scope["agent_id"]
    assert payload["metadata"] == {
        "page": "research",
        "symbol": CODE,
        "source": memory.SOURCE,
        "kb_scope": scope["kb_scope"],
    }
    assert payload["messages"] == [{"role": "user", "content": "我只看周线，日线太碎"}]
    assert payload["run_id"]


def test_explicit_remember_always_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient()
    fake_engine = _FakeEngine()
    _ask_direct(
        monkeypatch,
        fake,
        fake_engine,
        _settings(),
        question="记住：别给我推 ST 股",
        sync_memory=True,
    )

    assert len(fake_engine.added) == 1
    assert fake_engine.added[0]["metadata"]["symbol"] == CODE


def test_chitchat_not_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient()
    fake_engine = _FakeEngine()
    result = _ask_direct(
        monkeypatch,
        fake,
        fake_engine,
        _settings(),
        question="你好呀",
        sync_memory=True,
    )

    assert result["reply"] == fake.reply  # 回复照常
    assert fake_engine.added == []  # 无意义闲聊不落库
    assert "add" not in fake_engine.calls


def test_secret_never_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient()
    fake_engine = _FakeEngine()
    _ask_direct(
        monkeypatch,
        fake,
        fake_engine,
        _settings(),
        question="我的 key 是 sk-abcdefgh12345678 帮我记住",
        sync_memory=True,
    )

    assert fake_engine.added == []  # 红线优先于「帮我记住」


def test_unverified_number_not_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient()
    fake_engine = _FakeEngine()
    _ask_direct(
        monkeypatch,
        fake,
        fake_engine,
        _settings(),
        question="这只今年涨了 30% 帮我记住",
        sync_memory=True,
    )

    assert fake_engine.added == []


# --------------------------------------------------------------------------- #
# 降级：记忆引擎不可用不影响回答
# --------------------------------------------------------------------------- #


def test_memory_unavailable_still_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    fake = _FakeClient()
    fake_engine = _FakeEngine(exc=RuntimeError("engine boom"))
    client = _endpoint_setup(monkeypatch, tmp_path, fake, fake_engine, _settings(), sync_store=True)

    with caplog.at_level(logging.WARNING, logger="ladderstock.web.assistant"):
        response = client.post(
            f"/api/research/{CODE}/assistant",
            json={"question": "我的偏好是什么？", "context": "标的：浦发银行（600000）"},
            headers={"X-LDST-Client": "1"},
        )

    assert response.status_code == 200  # 助手照常回答，不 500
    assert response.json()["reply"] == fake.reply
    assert "memory_unavailable" in caplog.text  # 静默降级但有日志
    assert MEMORY_HEADER not in fake.calls[0][1]


def test_research_endpoint_passes_symbol_and_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    fake = _FakeClient()
    fake_engine = _FakeEngine()
    client = _endpoint_setup(monkeypatch, tmp_path, fake, fake_engine, _settings())

    def _capture(question: str, **kwargs: Any) -> None:
        captured["question"] = question
        captured.update(kwargs)

    monkeypatch.setattr(assistant_module.memory, "dispatch_store", _capture)

    response = client.post(
        f"/api/research/{CODE}/assistant",
        json={"question": "我的偏好是什么？", "context": "标的：浦发银行（600000）"},
        headers={"X-LDST-Client": "1"},
    )

    assert response.status_code == 200
    assert captured["symbol"] == CODE
    assert captured["page"] == "research"
    assert len(captured["run_id"]) == 32
    assert uuid.UUID(captured["run_id"])  # 是合法 hex run_id
    # research 通道落 senate 模块命名空间；检索载荷两维同时上线（红线）
    senate = assistant_module.memory_scope_for("senate")
    assert senate is not None
    assert captured["scope"].agent_id == senate["agent_id"]
    assert captured["scope"].kb_scope == senate["kb_scope"]
    assert fake_engine.searched[0]["filters"]["agent_id"] == senate["agent_id"]
    assert fake_engine.searched[0]["filters"]["kb_scope"] == senate["kb_scope"]


def test_skills_endpoint_passes_page_skills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, Any] = {}
    fake = _FakeClient()
    fake_engine = _FakeEngine()
    client = _endpoint_setup(monkeypatch, tmp_path, fake, fake_engine, _settings())

    def _capture(question: str, **kwargs: Any) -> None:
        captured["question"] = question
        captured.update(kwargs)

    monkeypatch.setattr(assistant_module.memory, "dispatch_store", _capture)

    response = client.post(
        "/api/skills/assistant",
        json={
            "question": "这个技能包下一步研究什么？",
            "context": "页面键：skills/quarterly-report",
            "page_key": "skills/quarterly-report",
            "bound_skill": "quarterly-report",
        },
        headers={"X-LDST-Client": "1"},
    )

    assert response.status_code == 200
    assert captured["page"] == "skills"
    assert captured["symbol"] is None
    # 未知页面键落默认模块（tickhub），不与 research 的 senate 空间混写
    tickhub = assistant_module.memory_scope_for(assistant_module.DEFAULT_MODULE)
    assert tickhub is not None
    assert captured["scope"].agent_id == tickhub["agent_id"]


def test_write_failure_does_not_break_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """召回成功、写入被引擎拒绝：回答仍然是 200，只记日志。"""
    fake = _FakeClient()
    fake_engine = _FakeEngine(add_error=RuntimeError("write rejected"))
    client = _endpoint_setup(
        monkeypatch,
        tmp_path,
        fake,
        fake_engine,
        _settings(),
        sync_store=True,
    )

    with caplog.at_level(logging.WARNING, logger="ladderstock.ai.memory"):
        response = client.post(
            f"/api/research/{CODE}/assistant",
            json={
                "question": "我只看周线，日线太碎",
                "context": "标的：浦发银行（600000）",
            },
            headers={"X-LDST-Client": "1"},
        )

    assert response.status_code == 200
    assert response.json()["reply"] == fake.reply
    assert "memory_write_failed" in caplog.text
