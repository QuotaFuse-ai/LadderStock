"""`sources.yaml` / `sync.yaml` ↔ `DATASET_CATALOG` 的双向一致性（DEC-008）。

本次事故的形态：`theme_news` 的备源 `wire_timeline` 写在 yaml 里，却没有登记进
`DATASET_CATALOG['theme_news'].fallback_tools`。旧校验只检查 primary 工具，
于是 `doctor` 全绿而 `sources validate` 只给一句警告——两个口径各说各话。

现在只有一处口径：`core.boundary.chain_problems()`。本文件从四个角度钉住它：

1. 正向：逐条 yaml 记录核对 dataset / via / tool / fallback；
2. 反向：每个目录条目的主工具必须在 yaml 有 primary 记录；
3. 回归：把 `theme_news` 改回旧的不一致状态，校验必须报红；
4. CLI：配置非法 → 3（指出记录和字段）、peer 全下线但配置合法 → 4、
   doctor 的 `sources:chain` 只反映配置合法性、不探测 peer。
"""

from __future__ import annotations

import copy
import json
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml
from fixtures.fake_peers import FakeTransport
from typer.testing import CliRunner

from ladderstock.cli.main import app
from ladderstock.core.boundary import (
    DATASET_CATALOG,
    ChainProblem,
    chain_problems,
    owner_of,
)
from ladderstock.errors import EXIT_CONFIG, EXIT_SOURCE

CONFIG = Path(__file__).resolve().parents[2] / "config"
runner = CliRunner()


def _load(name: str) -> dict[str, Any]:
    return yaml.safe_load((CONFIG / f"{name}.yaml").read_text(encoding="utf-8"))


def _record(doc: dict[str, Any], sid: str) -> dict[str, Any]:
    return next(item for item in doc["sources"] if item["id"] == sid)


def _mutated_problems(mutator: Any) -> list[ChainProblem]:
    """深拷贝真实配置后改一处，返回校验结果（不改仓库文件）。"""
    sources = copy.deepcopy(_load("sources"))
    sync = copy.deepcopy(_load("sync"))
    mutator(sources, sync)
    return chain_problems(sources, sync)


def _has(problems: list[ChainProblem], record: str, field: str) -> bool:
    return any(p.record == record and p.field == field for p in problems)


# --------------------------------------------------------------------------- #
# 1. 真实配置：双向一致 / 逐条核对
# --------------------------------------------------------------------------- #

def test_config_matches_catalog_bidirectionally() -> None:
    problems = chain_problems(_load("sources"), _load("sync"))
    assert problems == [], [str(p) for p in problems]


def test_every_sources_record_respects_catalog() -> None:
    sources = _load("sources")["sources"]
    by_id = {item["id"]: item for item in sources}
    for sid, item in by_id.items():
        binding = owner_of(item["dataset"])
        assert item["via"] == binding.peer, (
            f"{sid} 走 {item['via']}，但 {item['dataset']} 归属 {binding.peer}"
        )
        assert item["tool"] in (binding.tool, *binding.fallback_tools), (
            f"{sid} 的工具 {item['tool']} 未在 {item['dataset']} 登记"
        )
        if item["primary"]:
            assert item["tool"] == binding.tool
        for fid in item.get("fallback") or []:
            other = by_id[fid]  # 引用不存在的 id 会在这里 KeyError：主备必须真实存在
            assert other["dataset"] == item["dataset"]
            assert other["via"] == binding.peer


def test_every_catalog_dataset_has_a_primary_record() -> None:
    primaries = {
        item["dataset"]: item["tool"]
        for item in _load("sources")["sources"]
        if item["primary"]
    }
    for name, binding in DATASET_CATALOG.items():
        assert primaries.get(name) == binding.tool, f"{name} 缺 {binding.tool} 的 primary 记录"


# --------------------------------------------------------------------------- #
# 2. 回归：把 theme_news 改回旧的不一致状态，必须报红
# --------------------------------------------------------------------------- #

def test_theme_news_fallback_regression_is_detected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DEC-008 的由来：备源工具没登记时，校验必须指出具体记录。"""
    binding = DATASET_CATALOG["theme_news"]
    monkeypatch.setitem(
        DATASET_CATALOG, "theme_news", replace(binding, fallback_tools=())
    )
    problems = chain_problems(_load("sources"), _load("sync"))
    assert _has(problems, "wire_timeline", "tool"), [str(p) for p in problems]


# --------------------------------------------------------------------------- #
# 3. 反向验证：每条规则都能被单独触发，并定位到记录 / 字段
# --------------------------------------------------------------------------- #

def test_unregistered_dataset_is_reported() -> None:
    def mutate(sources: dict[str, Any], _sync: dict[str, Any]) -> None:
        _record(sources, "wire_timeline")["dataset"] = "secret_scraper"

    problems = _mutated_problems(mutate)
    assert _has(problems, "wire_timeline", "dataset"), [str(p) for p in problems]


def test_cross_peer_via_is_reported() -> None:
    def mutate(sources: dict[str, Any], _sync: dict[str, Any]) -> None:
        _record(sources, "wire_timeline")["via"] = "tickhub"

    problems = _mutated_problems(mutate)
    assert _has(problems, "wire_timeline", "via"), [str(p) for p in problems]


def test_unregistered_tool_is_reported() -> None:
    def mutate(sources: dict[str, Any], _sync: dict[str, Any]) -> None:
        _record(sources, "wire_timeline")["tool"] = "wire_get_archive"

    problems = _mutated_problems(mutate)
    assert _has(problems, "wire_timeline", "tool"), [str(p) for p in problems]


def test_missing_fallback_id_is_reported() -> None:
    def mutate(sources: dict[str, Any], _sync: dict[str, Any]) -> None:
        _record(sources, "wire_digest")["fallback"] = ["wire_missing"]

    problems = _mutated_problems(mutate)
    assert _has(problems, "wire_digest", "fallback"), [str(p) for p in problems]


def test_cross_peer_fallback_is_reported() -> None:
    def mutate(sources: dict[str, Any], _sync: dict[str, Any]) -> None:
        _record(sources, "wire_timeline")["via"] = "senate"

    problems = _mutated_problems(mutate)
    assert _has(problems, "wire_digest", "fallback"), [str(p) for p in problems]


def test_duplicate_primary_is_reported() -> None:
    def mutate(sources: dict[str, Any], _sync: dict[str, Any]) -> None:
        _record(sources, "wire_timeline")["primary"] = True

    problems = _mutated_problems(mutate)
    assert any(
        p.record == "theme_news" and p.field == "primary" and "多个主源" in p.detail
        for p in problems
    ), [str(p) for p in problems]


def test_missing_primary_record_is_reported() -> None:
    """反向：目录里登记的主工具，yaml 里没有 primary 记录（本次事故的形态）。"""

    def mutate(sources: dict[str, Any], _sync: dict[str, Any]) -> None:
        _record(sources, "wire_digest")["primary"] = False

    problems = _mutated_problems(mutate)
    assert _has(problems, "theme_news", "tool"), [str(p) for p in problems]


# --------------------------------------------------------------------------- #
# 4. CLI：退出码优先级与 doctor 的链路检查
# --------------------------------------------------------------------------- #

@pytest.fixture()
def config_copy(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把仓库配置拷到 tmp，改坏也不碰真实文件。"""
    destination = tmp_path / "config"
    shutil.copytree(CONFIG, destination)
    monkeypatch.setenv("LDST_CONFIG_DIR", str(destination))
    return destination


@pytest.fixture()
def peers_down(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeTransport:
    """三个 peer 全下线：探测一律失败，但不拨真网、不写仓库 data/。"""
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    transport = FakeTransport(probe_error=RuntimeError("connection refused"))
    monkeypatch.setattr(
        "ladderstock.data.provider_registry.build_transport",
        lambda _peers: transport,
    )
    return transport


def _break_theme_news(config: Path) -> None:
    path = config / "sources.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "tool: wire_get_timeline", "tool: wire_get_archive"
        ),
        encoding="utf-8",
    )


def test_validate_returns_4_when_config_valid_but_peers_down(
    repo: Path, config_copy: Path, peers_down: FakeTransport
) -> None:
    result = runner.invoke(app, ["sources", "validate", "--output", "json"])
    assert result.exit_code == EXIT_SOURCE, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["reason"] == "unreachable"
    assert payload["problems"] == []
    assert {row["reachable"] for row in payload["peers"]} == {False}
    assert "不在" not in result.stderr


def test_validate_returns_3_and_names_record_when_config_broken(
    repo: Path, config_copy: Path, peers_down: FakeTransport
) -> None:
    _break_theme_news(config_copy)
    result = runner.invoke(app, ["sources", "validate", "--output", "json"])
    assert result.exit_code == EXIT_CONFIG, result.stderr
    payload = json.loads(result.stdout)
    assert payload["reason"] == "config"
    problem = payload["problems"][0]
    assert (problem["where"], problem["record"], problem["field"]) == (
        "sources.yaml",
        "wire_timeline",
        "tool",
    )
    assert "wire_timeline.tool" in result.stderr
    assert all(row["reachable"] is None for row in payload["peers"])
    assert not peers_down.probes  # 配置非法时不探测，避免「不可达」掩盖真问题


def _doctor_chain(payload: dict[str, Any]) -> dict[str, Any]:
    return next(c for c in payload["checks"] if c["check"] == "sources:chain")


def test_doctor_red_when_chain_broken(
    repo: Path, config_copy: Path, peers_down: FakeTransport
) -> None:
    _break_theme_news(config_copy)
    result = runner.invoke(app, ["doctor", "--output", "json"])
    assert result.exit_code == EXIT_CONFIG
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert _doctor_chain(payload)["ok"] is False


def test_doctor_green_when_chain_valid_even_if_peers_down(
    repo: Path, config_copy: Path, peers_down: FakeTransport
) -> None:
    result = runner.invoke(app, ["doctor", "--output", "json"])
    payload = json.loads(result.stdout)
    assert _doctor_chain(payload)["ok"] is True, _doctor_chain(payload)

    # 可达性仍只归 sources validate / health：doctor 唯一的探针是「待联调工具」
    # 那一次 `tools/list`（QUOT-112），只探 senate、只探一次，失败只改 warning 措辞，
    # 不改 ok、不改退出码、不改检查项数（DEC-008）。
    # 23 = 原 22 项 + R5 的 `config:industry.yaml`（QUOT-143）。
    assert peers_down.probes == ["senate"]
    assert len(payload["checks"]) == 23
    assert any("未联调：peer 不可达" in text for text in payload["warnings"])

    problems = chain_problems(_load("sources"), _load("sync"))
    assert problems == [], [str(p) for p in problems]
