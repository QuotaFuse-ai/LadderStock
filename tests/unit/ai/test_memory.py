"""QUOT-487：`ai/memory.py` 的单元测试——写入门槛、红线、召回、注入分段。

不联网：QUOT-611 起引擎是进程内 mem0 SDK，测试用假引擎（engine= 注入），
绝不触发真实模型加载；策略函数纯本地。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ladderstock.ai import memory
from ladderstock.ai.fallback import MemoryScope
from ladderstock.core.boundary import BoundaryViolation


def _settings(**overrides: object) -> memory.MemorySettings:
    values: dict[str, object] = {
        "user_id": "u-local",
        "top_k": 5,
        "score_threshold": 0.55,
        "timeout_sec": 5.0,
        "enabled": True,
        "models_dir": Path("models"),
        "state_dir": Path("data/mem0"),
    }
    values.update(overrides)
    return memory.MemorySettings(**values)  # type: ignore[arg-type]


class FakeEngine:
    """假 mem0 引擎：记录 add / search / clear 调用，按脚本返回结果。"""

    def __init__(
        self,
        *,
        search_results: list[dict] | None = None,
        all_results: list[dict] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.search_results = search_results or []
        self.all_results = all_results or []
        self.error = error
        self.add_calls: list[dict] = []
        self.search_calls: list[dict] = []
        self.delete_calls: list[dict] = []

    def add(self, messages: Any, **kwargs: Any) -> dict:
        self.add_calls.append({"messages": messages, **kwargs})
        if self.error is not None:
            raise self.error
        return {"results": []}

    def search(self, query: str, **kwargs: Any) -> dict:
        self.search_calls.append({"query": query, **kwargs})
        if self.error is not None:
            raise self.error
        return {"results": self.search_results}

    def get_all(self, **kwargs: Any) -> dict:
        self.delete_calls.append({"op": "get_all", **kwargs})
        if self.error is not None:
            raise self.error
        return {"results": self.all_results}

    def delete_all(self, **kwargs: Any) -> dict:
        self.delete_calls.append({"op": "delete_all", **kwargs})
        if self.error is not None:
            raise self.error
        return {"message": "All relevant memories deleted"}


def _client(engine: FakeEngine, **overrides: object) -> memory.MemoryClient:
    return memory.MemoryClient(_settings(**overrides), engine=engine)  # type: ignore[arg-type]


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
    engine = FakeEngine()
    memory.store(
        "记住我的 key 是 sk-abcdef1234567890",
        symbol="600000",
        page="research",
        run_id="r1",
        settings=_settings(),
        client=_client(engine),
    )
    assert engine.add_calls == []


def test_store_drops_chitchat_without_any_call() -> None:
    engine = FakeEngine()
    memory.store(
        "今天天气不错",
        symbol=None,
        page="skills",
        run_id="r2",
        settings=_settings(),
        client=_client(engine),
    )
    assert engine.add_calls == []


def test_store_writes_preference_with_namespace_and_metadata() -> None:
    engine = FakeEngine()
    memory.store(
        "我只看周线级别",
        symbol="600000",
        page="research",
        run_id="run-1",
        settings=_settings(),
        client=_client(engine),
    )
    assert len(engine.add_calls) == 1
    call = engine.add_calls[0]
    assert call["user_id"] == "u-local"
    assert call["agent_id"] == memory.AGENT_ID
    assert call["run_id"] == "run-1"
    assert call["metadata"] == {"page": "research", "symbol": "600000", "source": "assistant"}
    # infer=True 保持既有行为：只把用户消息交给 mem0 抽取事实，不落逐轮对话原文
    assert call["messages"] == [{"role": "user", "content": "我只看周线级别"}]
    assert call["infer"] is True


def test_store_with_module_scope_writes_both_dimensions() -> None:
    """QUOT-498 收口：给模块命名空间时，写入载荷 agent_id + metadata.kb_scope 两维齐。"""
    engine = FakeEngine()
    memory.store(
        "我只看周线级别",
        symbol="600000",
        page="research",
        run_id="run-1",
        settings=_settings(),
        client=_client(engine),
        scope=MemoryScope("ladderstock-senate", "all"),
    )
    assert len(engine.add_calls) == 1
    call = engine.add_calls[0]
    assert call["agent_id"] == "ladderstock-senate"
    assert call["metadata"]["kb_scope"] == "all"
    assert call["metadata"]["page"] == "research"


def test_store_rejects_one_dimensional_scope() -> None:
    """缺一维就是跨模块写入：checked_scope 直接拒绝，不发请求。"""
    engine = FakeEngine()
    with pytest.raises(BoundaryViolation, match="必须同时给出"):
        memory.store(
            "我只看周线级别",
            symbol=None,
            page="research",
            run_id="run-1",
            settings=_settings(),
            client=_client(engine),
            scope=MemoryScope("ladderstock-senate", ""),
        )
    assert engine.add_calls == []


def test_store_failure_enters_cooldown() -> None:
    memory.reset_runtime_state()
    engine = FakeEngine(error=RuntimeError("llm gateway down"))
    memory.store(
        "我只看周线级别",
        symbol=None,
        page="research",
        run_id="r3",
        settings=_settings(),
        client=_client(engine),
    )
    assert memory._cooldown_active(_settings())


# --------------------------------------------------------------------------- #
# 召回：阈值过滤 + symbol 加分 + top-k
# --------------------------------------------------------------------------- #


def _search_payload(scores_texts: list[tuple[float, str]], symbol: str = "") -> list[dict]:
    return [
        {"memory": text, "score": score, "metadata": {"symbol": symbol, "user_id": "u-local"}}
        for score, text in scores_texts
    ]


def test_recall_filters_low_scores_and_boosts_symbol() -> None:
    engine = FakeEngine(
        search_results=_search_payload(
            [(0.60, "用户偏好周线级别"), (0.30, "低分记忆不应出现")],
            symbol="600000",
        )
    )
    hits = memory.recall(
        "我的偏好是什么",
        symbol="600000",
        settings=_settings(),
        client=_client(engine),
    )
    texts = [hit.text for hit in hits]
    assert texts == ["用户偏好周线级别"]
    # symbol 命中加成后 ≥ 原始分数
    assert hits[0].score >= 0.60


def test_recall_with_module_scope_sends_and_filters_both_dimensions() -> None:
    """检索载荷两维同时发出；引擎侧没过滤时本地兜底挡跨 kb_scope 记忆。"""
    engine = FakeEngine(
        search_results=[
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
    )
    hits = memory.recall(
        "偏好",
        symbol=None,
        settings=_settings(),
        client=_client(engine),
        scope=MemoryScope("ladderstock-senate", "all"),
    )
    assert len(engine.search_calls) == 1
    call = engine.search_calls[0]
    assert call["filters"]["agent_id"] == "ladderstock-senate"
    assert call["filters"]["kb_scope"] == "all"
    assert [hit.text for hit in hits] == ["本空间记忆"]


def test_recall_sorts_and_caps_top_k() -> None:
    rows = _search_payload([(0.70, "甲"), (0.80, "乙"), (0.90, "丙")])
    rows[2]["metadata"]["symbol"] = "600000"
    engine = FakeEngine(search_results=rows)

    hits = memory.recall(
        "偏好",
        symbol="600000",
        settings=_settings(top_k=2),
        client=_client(engine),
    )
    # symbol 命中的 0.90 加成后仍第一，top_k=2 截断
    assert [hit.text for hit in hits] == ["丙", "乙"]


def test_recall_rejects_cross_namespace_memories() -> None:
    engine = FakeEngine(
        search_results=[{"memory": "别人的记忆", "score": 0.9, "metadata": {"user_id": "u-other"}}]
    )
    hits = memory.recall("问题", symbol=None, settings=_settings(), client=_client(engine))
    assert hits == []


def test_recall_sends_engine_contract_payload() -> None:
    """锁死引擎契约：query + top_k（2 倍预取）+ 命名空间参数，infer 永远 True。"""
    engine = FakeEngine()
    memory.reset_runtime_state()
    memory.recall("问题", symbol=None, settings=_settings(top_k=3), client=_client(engine))

    assert len(engine.search_calls) == 1
    call = engine.search_calls[0]
    assert call["query"] == "问题"
    assert call["filters"]["user_id"] == "u-local"
    assert call["filters"]["agent_id"] == memory.AGENT_ID
    assert call["top_k"] == 6


def test_recall_failure_raises_memory_unavailable() -> None:
    engine = FakeEngine(error=RuntimeError("engine boom"))
    with pytest.raises(memory.MemoryUnavailable):
        memory.recall("问题", symbol=None, settings=_settings(), client=_client(engine))


def test_recall_skips_during_cooldown() -> None:
    memory.reset_runtime_state()
    settings = _settings()
    memory._note_failure(settings)
    engine = FakeEngine()
    hits = memory.recall("问题", symbol=None, settings=settings, client=_client(engine))
    assert hits == []
    assert engine.search_calls == []


# --------------------------------------------------------------------------- #
# 客户端：SDK 异常 → MemoryUnavailable；clear 计数
# --------------------------------------------------------------------------- #


def test_client_maps_engine_error_to_unavailable() -> None:
    engine = FakeEngine(error=RuntimeError("boom"))
    with pytest.raises(memory.MemoryUnavailable):
        engine_search = memory.MemoryClient(_settings(), engine=engine)
        engine_search.search({"query": "q", "user_id": "u-local", "agent_id": memory.AGENT_ID})


def test_clear_counts_then_deletes() -> None:
    engine = FakeEngine(all_results=[{"memory": "a"}, {"memory": "b"}, {"memory": "c"}])
    removed = _client(engine).clear(user_id="u-local", agent_id=memory.AGENT_ID)
    assert removed == 3
    ops = [call["op"] for call in engine.delete_calls]
    assert ops == ["get_all", "delete_all"]
    assert engine.delete_calls[0]["filters"] == {
        "user_id": "u-local",
        "agent_id": memory.AGENT_ID,
    }
    assert engine.delete_calls[1]["user_id"] == "u-local"
    assert engine.delete_calls[1]["agent_id"] == memory.AGENT_ID


def test_clear_returns_minus_one_when_count_unknown() -> None:
    engine = FakeEngine(all_results=[])
    assert _client(engine).clear(user_id="u-local", agent_id=memory.AGENT_ID) == -1


# --------------------------------------------------------------------------- #
# 引擎单例与健康
# --------------------------------------------------------------------------- #


def test_engine_health_not_loaded_by_default() -> None:
    memory.reset_runtime_state()
    health = memory.engine_health()
    assert health["engine"] == "in-process"
    assert health["branch"] == "not_loaded"


def test_client_constructor_does_not_load_engine(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """构造客户端绝不触发引擎加载（懒加载，冷启动不卡 UI）。"""
    memory.reset_runtime_state()

    def _boom(**kwargs: Any) -> None:
        raise AssertionError("engine must not be built on client construction")

    monkeypatch.setattr(memory, "_build_memory", _boom)
    client = memory.MemoryClient(_settings(models_dir=tmp_path, state_dir=tmp_path))
    assert memory._engine is None
    assert client._engine is None


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


def test_render_block_places_wrapped_knowledge_verbatim() -> None:
    """QUOT-566：`knowledge` 是 `recall_wrapped()` 的成品（自带头/声明/边界），原样放置。"""
    hit = memory.MemoryHit(text="旧说法", score=0.6, metadata={})
    wrapped = (
        "[标的档案 SH:600000 · as_of=2026-11-07 · status=fresh]\n"
        f"{memory.KNOWLEDGE_BEGIN}\n档案正文\n{memory.KNOWLEDGE_END}"
    )
    block = memory.render_memory_block([hit], knowledge=wrapped)
    assert wrapped in block
    assert block.count(memory.KNOWLEDGE_BEGIN) == 1
    assert block.index("【对话记忆") < block.index("【标的档案")


def test_render_block_long_knowledge_not_clipped_by_memory_budget() -> None:
    body = "甲" * 3000
    wrapped = f"{memory.KNOWLEDGE_BEGIN}\n{body}\n{memory.KNOWLEDGE_END}"
    block = memory.render_memory_block([], knowledge=wrapped)
    assert block.count("甲") == 3000


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #


def test_load_settings_reads_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(memory.ENV_USER_ID, "zhang-local")
    monkeypatch.setenv(memory.ENV_TOP_K, "3")
    monkeypatch.setenv(memory.ENV_SCORE_THRESHOLD, "0.6")
    monkeypatch.setenv(memory.ENV_TIMEOUT_SEC, "4")
    monkeypatch.setenv(memory.ENV_MODELS_DIR, str(tmp_path / "models"))
    monkeypatch.setenv(memory.ENV_STATE_DIR, str(tmp_path / "mem0"))
    monkeypatch.delenv(memory.LEGACY_ENV_BASE_URL, raising=False)
    settings = memory.load_settings()
    assert settings.user_id == "zhang-local"
    assert settings.top_k == 3
    assert settings.score_threshold == 0.6
    assert settings.timeout_sec == 4.0
    assert settings.enabled is True
    assert settings.models_dir == tmp_path / "models"
    assert settings.state_dir == tmp_path / "mem0"


def test_load_settings_empty_base_url_disables(monkeypatch: pytest.MonkeyPatch) -> None:
    """兼容哨兵：LDST_MEMORY_BASE_URL='' 仍然关闭整个通道（测试隔离沿用）。"""
    monkeypatch.setenv(memory.LEGACY_ENV_BASE_URL, "")
    settings = memory.load_settings()
    assert settings.enabled is False


def test_load_settings_legacy_nonempty_base_url_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    """旧的非空 9100 地址值被忽略：进程内引擎不再有「远端实例」概念。"""
    monkeypatch.setenv(memory.LEGACY_ENV_BASE_URL, "http://127.0.0.1:9100")
    settings = memory.load_settings()
    assert settings.enabled is True


def test_load_settings_invalid_values_fall_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(memory.ENV_TOP_K, "abc")
    monkeypatch.setenv(memory.ENV_SCORE_THRESHOLD, "not-a-number")
    monkeypatch.delenv(memory.ENV_USER_ID, raising=False)
    settings = memory.load_settings()
    assert settings.top_k == memory.DEFAULT_TOP_K
    assert settings.score_threshold == memory.DEFAULT_SCORE_THRESHOLD
    assert settings.user_id  # 未配置时落持久本机标识，绝不是 "default"
    assert settings.user_id != "default"


def test_env_state_dir_isolated_per_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    """双实例隔离沿用 LDST_ENV：prod / dev 落盘目录不同，不新写规则。"""
    monkeypatch.delenv(memory.ENV_STATE_DIR, raising=False)
    monkeypatch.setattr("ladderstock.supervisor.modules.current_env", lambda: "prod")
    settings = memory.load_settings()
    prod_qdrant = settings.qdrant_path()
    monkeypatch.setattr("ladderstock.supervisor.modules.current_env", lambda: "dev")
    dev_qdrant = settings.qdrant_path()
    assert prod_qdrant != dev_qdrant
    assert prod_qdrant.parent.name == "prod"
    assert dev_qdrant.parent.name == "dev"
    assert settings.history_db_path().parent == settings.env_state_dir()


def test_shared_client_does_not_load_engine() -> None:
    """shared_client 恒返回客户端（懒加载引擎），关闭状态下调用方 recall 返回 []。"""
    memory.reset_runtime_state()
    settings = _settings(enabled=False)
    client = memory.shared_client(settings)
    assert isinstance(client, memory.MemoryClient)
    assert client._engine is None


# --------------------------------------------------------------------------- #
# 模型本地固化：SHA256 清单校验
# --------------------------------------------------------------------------- #


def test_verify_model_integrity_passes_with_matching_manifest(tmp_path: Path) -> None:
    import hashlib

    model_dir = tmp_path / "bge-small-zh-v1.5-baai"
    model_dir.mkdir()
    (model_dir / "model.safetensors").write_bytes(b"weights")
    digest = hashlib.sha256(b"weights").hexdigest()
    (tmp_path / "SHA256SUMS").write_text(
        f"{digest} *bge-small-zh-v1.5-baai/model.safetensors\n", encoding="utf-8"
    )
    memory._verify_model_integrity(tmp_path)


def test_verify_model_integrity_missing_file_fails(tmp_path: Path) -> None:
    (tmp_path / "SHA256SUMS").write_text(
        "0" * 64 + " *bge-small-zh-v1.5-baai/model.safetensors\n", encoding="utf-8"
    )
    with pytest.raises(memory.MemoryUnavailable, match="文件缺失"):
        memory._verify_model_integrity(tmp_path)


def test_verify_model_integrity_hash_mismatch_fails(tmp_path: Path) -> None:
    model_dir = tmp_path / "bge-small-zh-v1.5-baai"
    model_dir.mkdir()
    (model_dir / "model.safetensors").write_bytes(b"tampered")
    (tmp_path / "SHA256SUMS").write_text(
        "0" * 64 + " *bge-small-zh-v1.5-baai/model.safetensors\n", encoding="utf-8"
    )
    with pytest.raises(memory.MemoryUnavailable, match="哈希不一致"):
        memory._verify_model_integrity(tmp_path)


def test_verify_model_integrity_missing_manifest_is_noop(tmp_path: Path) -> None:
    memory._verify_model_integrity(tmp_path)
