"""QUOT-487：`ai/memory.py` 的单元测试——写入门槛、红线、召回、注入分段。

不联网：mem0 客户端用 `httpx.MockTransport` 假服务；策略函数纯本地。
"""

from __future__ import annotations

import httpx
import pytest

from ladderstock.ai import memory
from ladderstock.ai.fallback import MemoryScope
from ladderstock.core.boundary import BoundaryViolation


def _settings(**overrides: object) -> memory.MemorySettings:
    values: dict[str, object] = {
        "base_url": "http://mem0.test",
        "user_id": "u-local",
        "top_k": 5,
        "score_threshold": 0.55,
        "timeout_sec": 5.0,
        "enabled": True,
    }
    values.update(overrides)
    return memory.MemorySettings(**values)  # type: ignore[arg-type]


def _client(handler) -> memory.MemoryClient:  # type: ignore[no-untyped-def]
    return memory.MemoryClient("http://mem0.test", transport=httpx.MockTransport(handler))


# --------------------------------------------------------------------------- #
# 重要性打分（写入门槛）
# --------------------------------------------------------------------------- #


def test_explicit_remember_scores_full() -> None:
    score, reason = memory.importance_score("帮我记住我习惯看周线")
    assert score == 1.0
    assert reason == "explicit_remember"


def test_preference_passes_threshold() -> None:
    score, reason = memory.importance_score("我只看周线级别")
    assert score >= memory.WRITE_THRESHOLD
    assert "preference" in reason


def test_conclusion_judgement_passes_threshold() -> None:
    score, reason = memory.importance_score("这个板块我不跟了")
    assert score >= memory.WRITE_THRESHOLD
    assert reason


def test_chitchat_is_below_threshold() -> None:
    score, reason = memory.importance_score("今天大盘怎么样？")
    assert score < memory.WRITE_THRESHOLD
    assert reason == "chitchat"


# --------------------------------------------------------------------------- #
# 红线：密钥与未验证数字
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text",
    [
        "我的 key 是 sk-abcdef1234567890 别外传",
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.x.y",
        "postgres://admin:hunter2@db.internal:5432/prod",
        "api_key = 0123456789abcdef",
        "AKIAIOSFODNN7EXAMPLE 是示例",
        "ghp_" + "a" * 30,
    ],
)
def test_secret_rules_reject(text: str) -> None:
    assert memory.secret_rule(text) is not None


def test_normal_text_has_no_secret_rule() -> None:
    assert memory.secret_rule("我只看周线，这个板块我不跟了") is None


@pytest.mark.parametrize(
    "text",
    [
        "它今天涨了 3.5%，估值不算贵",
        "市值已经 3000 亿了",
        "现在 12.3 元，PE 约 25",
    ],
)
def test_unverified_numbers_reject(text: str) -> None:
    assert memory.unverified_number_rule(text) is not None


def test_preference_without_numbers_passes() -> None:
    assert memory.unverified_number_rule("我只看周线级别") is None


def test_store_never_writes_secrets_even_when_explicit() -> None:
    """显式「记住」也不能越过密钥红线：红线优先于必写。"""
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append({"path": request.url.path, "body": request.read().decode()})
        return httpx.Response(200, json={"results": []})

    settings = _settings()
    memory.store(
        "记住我的 key 是 sk-abcdef1234567890",
        symbol="600000",
        page="research",
        run_id="r1",
        settings=settings,
        client=_client(handler),
    )
    assert calls == []


def test_store_drops_chitchat_without_any_call() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"results": []})

    memory.store(
        "今天天气不错",
        symbol=None,
        page="skills",
        run_id="r2",
        settings=_settings(),
        client=_client(handler),
    )
    assert calls == []


def test_store_writes_preference_with_namespace_and_metadata() -> None:
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as json_module

        bodies.append(json_module.loads(request.read().decode()))
        return httpx.Response(200, json={"results": []})

    memory.store(
        "我只看周线级别",
        symbol="600000",
        page="research",
        run_id="run-1",
        settings=_settings(),
        client=_client(handler),
    )
    assert len(bodies) == 1
    body = bodies[0]
    assert body["user_id"] == "u-local"
    assert body["agent_id"] == memory.AGENT_ID
    assert body["run_id"] == "run-1"
    assert body["metadata"] == {"page": "research", "symbol": "600000", "source": "assistant"}
    # 只把用户消息交给 mem0 抽取事实，不落逐轮对话原文
    assert body["messages"] == [{"role": "user", "content": "我只看周线级别"}]


def test_store_with_module_scope_writes_both_dimensions() -> None:
    """QUOT-498 收口：给模块命名空间时，写入载荷 agent_id + metadata.kb_scope 两维齐。"""
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as json_module

        bodies.append(json_module.loads(request.read().decode()))
        return httpx.Response(200, json={"results": []})

    memory.store(
        "我只看周线级别",
        symbol="600000",
        page="research",
        run_id="run-1",
        settings=_settings(),
        client=_client(handler),
        scope=MemoryScope("ladderstock-senate", "all"),
    )
    assert len(bodies) == 1
    body = bodies[0]
    assert body["agent_id"] == "ladderstock-senate"
    assert body["metadata"]["kb_scope"] == "all"
    assert body["metadata"]["page"] == "research"


def test_store_rejects_one_dimensional_scope() -> None:
    """缺一维就是跨模块写入：checked_scope 直接拒绝，不发请求。"""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"results": []})

    with pytest.raises(BoundaryViolation, match="必须同时给出"):
        memory.store(
            "我只看周线级别",
            symbol=None,
            page="research",
            run_id="run-1",
            settings=_settings(),
            client=_client(handler),
            scope=MemoryScope("ladderstock-senate", ""),
        )
    assert calls == []


# --------------------------------------------------------------------------- #
# 召回：阈值过滤 + symbol 加分 + top-k
# --------------------------------------------------------------------------- #


def _search_payload(scores_texts: list[tuple[float, str]], symbol: str = "") -> list[dict]:
    return [
        {"memory": text, "score": score, "metadata": {"symbol": symbol, "user_id": "u-local"}}
        for score, text in scores_texts
    ]


def test_recall_filters_low_scores_and_boosts_symbol() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": _search_payload(
                    [
                        (0.60, "用户偏好周线级别"),
                        (0.30, "低分记忆不应出现"),
                    ],
                    symbol="600000",
                )
            },
        )

    hits = memory.recall(
        "我的偏好是什么",
        symbol="600000",
        settings=_settings(),
        client=_client(handler),
    )
    texts = [hit.text for hit in hits]
    assert texts == ["用户偏好周线级别"]
    # symbol 命中加成后 ≥ 原始分数
    assert hits[0].score >= 0.60


def test_recall_with_module_scope_sends_and_filters_both_dimensions() -> None:
    """检索载荷两维同时发出；服务端没过滤时本地兜底挡跨 kb_scope 记忆。"""
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json as json_module

        captured.append(json_module.loads(request.read().decode()))
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "memory": "本空间记忆",
                        "score": 0.8,
                        "metadata": {"user_id": "u-local", "kb_scope": "all"},
                    },
                    {
                        "memory": "别的模块记忆",
                        "score": 0.9,
                        "metadata": {"user_id": "u-local", "kb_scope": "wire"},
                    },
                ]
            },
        )

    hits = memory.recall(
        "偏好",
        symbol=None,
        settings=_settings(),
        client=_client(handler),
        scope=MemoryScope("ladderstock-senate", "all"),
    )
    assert captured[0]["agent_id"] == "ladderstock-senate"
    assert captured[0]["kb_scope"] == "all"
    assert [hit.text for hit in hits] == ["本空间记忆"]


def test_recall_sorts_and_caps_top_k() -> None:
    rows = _search_payload([(0.70, "甲"), (0.80, "乙"), (0.90, "丙")])
    rows[2]["metadata"]["symbol"] = "600000"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": rows})

    hits = memory.recall(
        "偏好",
        symbol="600000",
        settings=_settings(top_k=2),
        client=_client(handler),
    )
    # symbol 命中的 0.90 加成后仍第一，top_k=2 截断
    assert [hit.text for hit in hits] == ["丙", "乙"]


def test_recall_rejects_cross_namespace_memories() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "results": [
                    {"memory": "别人的记忆", "score": 0.9, "metadata": {"user_id": "u-other"}}
                ]
            },
        )

    hits = memory.recall("问题", symbol=None, settings=_settings(), client=_client(handler))
    assert hits == []


def test_recall_failure_raises_memory_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(memory.MemoryUnavailable):
        memory.recall("问题", symbol=None, settings=_settings(), client=_client(handler))
    # 失败进入冷却窗口
    assert memory._cooldown_active("http://mem0.test")


def test_recall_skips_during_cooldown() -> None:
    memory.reset_runtime_state()
    memory._note_failure("http://mem0.test")
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"results": []})

    hits = memory.recall("问题", symbol=None, settings=_settings(), client=_client(handler))
    assert hits == []
    assert calls == []


# --------------------------------------------------------------------------- #
# 客户端：非 2xx / 非 JSON → MemoryUnavailable
# --------------------------------------------------------------------------- #


def test_client_maps_http_error_to_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"detail": "bad gateway"})

    client = _client(handler)
    with pytest.raises(memory.MemoryUnavailable):
        client.search({"query": "q"})


def test_client_maps_non_json_to_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>not json</html>")

    client = _client(handler)
    with pytest.raises(memory.MemoryUnavailable):
        client.search({"query": "q"})


def test_clear_uses_delete_then_falls_back() -> None:
    seen: list[tuple[str, int]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, 1))
        return httpx.Response(200, json={"deleted_ids": ["m1", "m2", "m3"]})

    removed = _client(handler).clear(user_id="u-local", agent_id=memory.AGENT_ID)
    assert removed == 3
    assert seen[0][0] == "/v1/memories/"


def test_clear_falls_back_to_delete_all_endpoint() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.method == "DELETE":
            return httpx.Response(405)
        return httpx.Response(200, json={"deleted": ["m1"]})

    removed = _client(handler).clear(user_id="u-local", agent_id=memory.AGENT_ID)
    assert removed == 1
    assert paths == ["/v1/memories/", "/v1/memories/delete_all/"]


# --------------------------------------------------------------------------- #
# 注入分段：物理分段 + 知识库边界
# --------------------------------------------------------------------------- #


def test_render_block_segments_memory_and_knowledge() -> None:
    hit = memory.MemoryHit(text="用户偏好周线级别", score=0.72, metadata={})
    block = memory.render_memory_block([hit], knowledge=None)
    assert "【对话记忆（可能过时，仅作线索）】" in block
    assert "- (score 0.72) 用户偏好周线级别" in block
    assert "【标的档案（带 as_of，优先于对话记忆）】" in block
    assert memory.KNOWLEDGE_BEGIN in block
    assert memory.KNOWLEDGE_END in block
    # 记忆段与档案段物理分段（中间有空行）
    assert "【对话记忆】" not in block.split("【标的档案")[0]


def test_render_block_empty_when_no_hits_and_no_knowledge() -> None:
    assert memory.render_memory_block([], knowledge=None) == ""


def test_render_block_keeps_knowledge_inside_boundary() -> None:
    hit = memory.MemoryHit(text="旧说法", score=0.6, metadata={})
    block = memory.render_memory_block([hit], knowledge="档案正文 as_of 2026-11-07")
    after_begin = block.split(memory.KNOWLEDGE_BEGIN)[1]
    body = after_begin.split(memory.KNOWLEDGE_END)[0]
    assert "档案正文" in body


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #


def test_load_settings_reads_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    monkeypatch.setenv(memory.ENV_BASE_URL, "http://127.0.0.1:9100/")
    monkeypatch.setenv(memory.ENV_USER_ID, "zhang-local")
    monkeypatch.setenv(memory.ENV_TOP_K, "3")
    monkeypatch.setenv(memory.ENV_SCORE_THRESHOLD, "0.6")
    monkeypatch.setenv(memory.ENV_TIMEOUT_SEC, "4")
    settings = memory.load_settings()
    assert settings.base_url == "http://127.0.0.1:9100"
    assert settings.user_id == "zhang-local"
    assert settings.top_k == 3
    assert settings.score_threshold == 0.6
    assert settings.timeout_sec == 4.0
    assert settings.enabled is True


def test_load_settings_empty_base_url_disables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(memory.ENV_BASE_URL, "")
    settings = memory.load_settings()
    assert settings.enabled is False


def test_load_settings_invalid_values_fall_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(memory.ENV_TOP_K, "abc")
    monkeypatch.setenv(memory.ENV_SCORE_THRESHOLD, "not-a-number")
    monkeypatch.delenv(memory.ENV_USER_ID, raising=False)
    monkeypatch.setenv(memory.ENV_BASE_URL, "http://127.0.0.1:9100")
    settings = memory.load_settings()
    assert settings.top_k == memory.DEFAULT_TOP_K
    assert settings.score_threshold == memory.DEFAULT_SCORE_THRESHOLD
    assert settings.user_id  # 未配置时落持久本机标识，绝不是 "default"
    assert settings.user_id != "default"


def test_shared_client_none_when_disabled() -> None:
    assert memory.shared_client(_settings(enabled=False)) is None
