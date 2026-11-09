"""`/api/control/ai/modules` 三件套：GET 三件套 / PUT 原地写 / POST 探针（QUOT-498）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from ladderstock.ai import fallback
from ladderstock.core.boundary import BoundaryViolation
from ladderstock.web import control

AI_YAML = """\
# 顶部注释必须活下来
ai:
  backends:
    - id: openai
      type: openai
      models: [model-a, model-b]
    - id: anthropic
      type: anthropic
      models: [claude-sonnet-4-5]
  default_model: openai:model-a
  fallback_chain: [openai:model-b]
  structured_output: prefer
  sections:
    sentiment:
      prompt_file: prompts/sentiment.md
"""

SENTINEL = "sk-modules-SENTINEL"

ENV = {
    "LDST_LLM_BASE_URL": "https://api.example.test/v1",
    "LDST_LLM_API_KEY": SENTINEL,
    "LDST_LLM_ANTHROPIC_BASE_URL": "https://anthropic.example.test",
    "LDST_LLM_ANTHROPIC_API_KEY": SENTINEL,
}


class _FakeClient:
    """替身补全客户端：记下每次请求用的模型标签。"""

    def __init__(self, reply: str = "ok", error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error
        self.calls: list[str] = []

    def complete(self, model, system, user, *, json_mode=False):  # noqa: ANN001
        self.calls.append(model)
        if self.error is not None:
            raise self.error
        return self.reply


@pytest.fixture()
def plane(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    config_root = tmp_path / "config"
    config_root.mkdir(parents=True)
    (config_root / "ai.yaml").write_text(AI_YAML, encoding="utf-8")
    return control.ControlPlane(
        data_root=tmp_path / "data",
        peers={},
        config_root=config_root,
        harden_acl=lambda path: None,
        resolve=lambda host: ["93.184.216.34"],
    )


def _path(plane) -> Path:
    return plane.config_root / "ai.yaml"


def _put(plane, module: str, **override) -> dict:
    return plane.ai_modules_update(module, override)


# --------------------------------------------------------------------------- #
# GET
# --------------------------------------------------------------------------- #


def test_status_lists_every_module_and_role_with_source(plane) -> None:
    doc = plane.ai_modules_status()
    assert set(fallback.MODULE_KEYS) <= set(doc["modules"])
    assert doc["models"] == [
        "openai:model-a",
        "openai:model-b",
        "anthropic:claude-sonnet-4-5",
    ]
    assert doc["modules"]["wire"] == {
        "value": "openai:model-a",
        "source": "global",
        "configured": False,
        "inherited_value": None,
        "fallback_chain": [],
        "agent_id": "ladderstock-wire",
        "kb_scope": "all",
        "roles": {},
    }
    assert set(doc["modules"]["senate"]["roles"]) == set(fallback.SENATE_ROLE_KEYS)
    assert doc["sources"] == list(fallback.SOURCES)
    assert doc["config_path"] == str(_path(plane))


# --------------------------------------------------------------------------- #
# PUT
# --------------------------------------------------------------------------- #


def test_put_inserts_module_and_keeps_comments(plane) -> None:
    before = _path(plane).read_text(encoding="utf-8")
    payload = _put(
        plane,
        "wire",
        model="anthropic:claude-sonnet-4-5",
        fallback_chain=["openai:model-b"],
        memory={"agent_id": "ladderstock-wire", "kb_scope": "wire"},
    )

    assert payload["changed"] is True
    assert payload["updated"] == "wire"
    entry = payload["modules"]["wire"]
    assert (entry["value"], entry["source"], entry["configured"]) == (
        "anthropic:claude-sonnet-4-5",
        "module",
        True,
    )
    assert entry["inherited_value"] == "openai:model-a"
    assert (entry["agent_id"], entry["kb_scope"]) == ("ladderstock-wire", "wire")

    after = _path(plane).read_text(encoding="utf-8")
    assert "# 顶部注释必须活下来" in after
    assert "prompts/sentiment.md" in after
    kept = [line for line in before.splitlines() if line.strip()]
    assert all(line in after for line in kept)
    # 写前留了备份，临时目录已清干净
    backups = list(_path(plane).parent.glob("ai.yaml.bak.*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == before
    assert not list(_path(plane).parent.glob(".ai.yaml.*"))


def test_put_is_idempotent(plane) -> None:
    first = _put(plane, "wire", model="openai:model-b")
    second = _put(plane, "wire", model="openai:model-b")
    assert first["changed"] is True
    assert second["changed"] is False
    assert second["modules"]["wire"]["value"] == "openai:model-b"


def test_put_empty_model_clears_override_back_to_global(plane) -> None:
    _put(plane, "wire", model="openai:model-b")
    payload = _put(plane, "wire", model="")
    entry = payload["modules"]["wire"]
    assert entry["source"] == "global"
    assert entry["value"] == "openai:model-a"
    assert entry["configured"] is False
    # 清空不等于关闭：modules: 段仍读得出，module 只是继承全局
    assert "modules:" in _path(plane).read_text(encoding="utf-8")
    assert fallback.load_config(plane.config_root).resolve_for("wire").label == "openai:model-a"


def test_put_role_override_on_senate(plane) -> None:
    payload = _put(
        plane,
        "senate",
        model="openai:model-b",
        roles={"bull": {"model": "anthropic:claude-sonnet-4-5"}},
    )
    bull = payload["modules"]["senate"]["roles"]["bull"]
    assert (bull["value"], bull["source"]) == ("anthropic:claude-sonnet-4-5", "role")
    assert bull["inherited_value"] == "openai:model-b"
    bear = payload["modules"]["senate"]["roles"]["bear"]
    assert (bear["value"], bear["source"]) == ("openai:model-b", "module")


def test_put_empty_role_model_clears_that_role(plane) -> None:
    _put(plane, "senate", roles={"bull": {"model": "anthropic:claude-sonnet-4-5"}})
    payload = _put(plane, "senate", roles={"bull": {"model": ""}})
    bull = payload["modules"]["senate"]["roles"]["bull"]
    assert bull["source"] == "global"
    assert bull["configured"] is False


@pytest.mark.parametrize(
    ("module", "override", "needle"),
    [
        ("nope", {"model": "openai:model-a"}, "未知子模块"),
        ("wire", {"roles": {"bull": {"model": "openai:model-a"}}}, "不支持角色级覆盖"),
        ("senate", {"roles": {"dragon": {"model": "openai:model-a"}}}, "角色"),
        ("wire", {"temperature": "0.2"}, "不允许的子模块字段"),
        ("wire", {"model": "ghost:model-a"}, "未声明的后端"),
        ("wire", {"fallback_chain": ["ghost:model-a"]}, "未声明的后端"),
        ("wire", {"model": "openai:model-a\nai: {}"}, "换行"),
        ("senate", {"roles": {"bull": {"model": "openai:no-such-model"}}}, "no-such-model"),
        ("wire", {"memory": {"kb_scope": "wire\nx"}}, "换行"),
    ],
)
def test_put_rejects_bad_input_and_leaves_file_alone(
    plane, module: str, override: dict, needle: str
) -> None:
    before = _path(plane).read_text(encoding="utf-8")
    with pytest.raises(HTTPException) as raised:
        plane.ai_modules_update(module, override)
    assert raised.value.status_code == 400
    assert needle in str(raised.value.detail)
    assert _path(plane).read_text(encoding="utf-8") == before
    assert not list(_path(plane).parent.glob("ai.yaml.bak.*"))


def test_put_candidate_that_fails_schema_validation_never_reaches_disk(plane, monkeypatch) -> None:
    before = _path(plane).read_text(encoding="utf-8")

    def reject(candidate: Path) -> None:
        raise BoundaryViolation("模拟：候选文件校验未通过")

    monkeypatch.setattr(control.ControlPlane, "_validate_ai_candidate", staticmethod(reject))
    with pytest.raises(HTTPException) as raised:
        plane.ai_modules_update("wire", {"model": "openai:model-b"})
    assert raised.value.status_code == 400
    assert "校验未通过" in str(raised.value.detail)
    assert _path(plane).read_text(encoding="utf-8") == before
    assert not list(_path(plane).parent.glob("ai.yaml.bak.*"))
    assert not list(_path(plane).parent.glob(".ai.yaml.*"))


def test_put_memory_must_use_known_fields(plane) -> None:
    with pytest.raises(HTTPException) as raised:
        plane.ai_modules_update("wire", {"memory": {"agent_id": "x", "namespace": "y"}})
    assert raised.value.status_code == 400
    assert "namespace" in str(raised.value.detail)


def test_put_request_model_forbids_extra_fields() -> None:
    with pytest.raises(ValueError):
        control.AiModulesUpdateRequest.model_validate(
            {"module": "wire", "model": "openai:model-a", "api_key": SENTINEL}
        )


# --------------------------------------------------------------------------- #
# POST /test
# --------------------------------------------------------------------------- #


def test_modules_test_uses_resolved_model_and_reports_source(plane, monkeypatch) -> None:
    fake = _FakeClient(reply="ok\n")
    monkeypatch.setattr(control.ControlPlane, "_build_chat_client", lambda self, config: fake)
    _put(plane, "senate", roles={"bull": {"model": "anthropic:claude-sonnet-4-5"}})

    result = plane.ai_modules_test("senate", "bull")
    assert result["ok"] is True
    assert result["model"] == "anthropic:claude-sonnet-4-5"
    assert result["source"] == "role"
    assert result["reply_head"] == "ok"
    assert result["error"] is None
    assert isinstance(result["latency_ms"], int)
    assert fake.calls == ["anthropic:claude-sonnet-4-5"]


def test_modules_test_failure_is_200_with_masked_reason(plane, monkeypatch) -> None:
    fake = _FakeClient(error=RuntimeError(f"bad key {SENTINEL}"))
    monkeypatch.setattr(control.ControlPlane, "_build_chat_client", lambda self, config: fake)

    result = plane.ai_modules_test("wire")
    assert result["ok"] is False
    assert result["model"] == "openai:model-a"
    assert result["source"] == "global"
    assert result["reply_head"] is None
    assert SENTINEL not in json.dumps(result, ensure_ascii=False)


def test_modules_test_rejects_unknown_module(plane) -> None:
    with pytest.raises(HTTPException) as raised:
        plane.ai_modules_test("nope")
    assert raised.value.status_code == 400


def test_routes_wire_get_put_post_and_enforce_body_schema(plane, tmp_path) -> None:
    from fastapi.testclient import TestClient

    from ladderstock.web.server import create_app

    client = TestClient(
        create_app(data_root=tmp_path / "data", dist_dir=tmp_path / "missing", control_plane=plane),
        client=("127.0.0.1", 54321),
        headers={"X-LDST-Client": "1"},
    )

    status = client.get("/api/control/ai/modules")
    assert status.status_code == 200
    assert status.json()["models"][0] == "openai:model-a"

    put = client.put("/api/control/ai/modules", json={"module": "wire", "model": "openai:model-b"})
    assert put.status_code == 200
    assert put.json()["modules"]["wire"]["value"] == "openai:model-b"
    # 白名单之外的字段在请求模型层就被拒（422），不会进到写盘
    extra = client.put(
        "/api/control/ai/modules",
        json={"module": "wire", "model": "openai:model-a", "api_key": SENTINEL},
    )
    assert extra.status_code == 422

    probe = client.post("/api/control/ai/modules/test", json={"module": "nope"})
    assert probe.status_code == 400


def test_modules_test_requires_configured_credentials(plane, monkeypatch) -> None:
    for name in ENV:
        monkeypatch.delenv(name)
    with pytest.raises(HTTPException) as raised:
        plane.ai_modules_test("wire")
    assert raised.value.status_code == 400
    assert "LDST_LLM_API_KEY" in str(raised.value.detail)


def test_modules_test_only_checks_resolved_backend_credentials(plane, monkeypatch) -> None:
    # anthropic 未配置不该拦住落在 openai 的探测：探针只打解析出的那一个落点
    monkeypatch.delenv("LDST_LLM_ANTHROPIC_BASE_URL")
    monkeypatch.delenv("LDST_LLM_ANTHROPIC_API_KEY")
    fake = _FakeClient(reply="ok\n")
    monkeypatch.setattr(control.ControlPlane, "_build_chat_client", lambda self, config: fake)

    result = plane.ai_modules_test("wire")
    assert result["ok"] is True
    assert result["model"] == "openai:model-a"
    assert fake.calls == ["openai:model-a"]


def test_modules_test_400_when_resolved_backend_missing_credentials(plane, monkeypatch) -> None:
    monkeypatch.delenv("LDST_LLM_ANTHROPIC_BASE_URL")
    monkeypatch.delenv("LDST_LLM_ANTHROPIC_API_KEY")
    _put(plane, "senate", roles={"bull": {"model": "anthropic:claude-sonnet-4-5"}})
    with pytest.raises(HTTPException) as raised:
        plane.ai_modules_test("senate", "bull")
    assert raised.value.status_code == 400
    assert "LDST_LLM_ANTHROPIC_API_KEY" in str(raised.value.detail)
