"""`ldst research-batch` 的口径测试（E 包 / DEC-091）。

矩阵不收它（与 `research` 同口径：真跑一票会进 AI 管线），本文件用假管线
钉住队列语义：参数校验、上限、去重、逐票不中断、退出码取最差一票。
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from ladderstock.cli.main import app

runner = CliRunner()


class _FakeRun:
    def __init__(self, exit_code: int) -> None:
        self.exit_code = exit_code

    def as_dict(self) -> dict[str, Any]:
        return {"code": "600000", "sections": {}}


@pytest.fixture()
def _offline(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """数据 / 配置目录钉进临时盘；AI 配置读取换成空配置（不碰真实凭据）。"""
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LDST_CONFIG_DIR", str(tmp_path / "config"))
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)

    import ladderstock.ai.fallback as fallback

    class _FakeConfig:
        def __getattr__(self, name: str) -> None:  # 管线假桩不读字段
            return None

    monkeypatch.setattr(fallback, "load_config", lambda: _FakeConfig())


def _install_pipeline(monkeypatch: pytest.MonkeyPatch, exit_code: int) -> list[str]:
    """把管线换成假桩；返回被跑过的代码序列（断言串行与去重用）。"""
    import ladderstock.cli.research_batch as batch

    ran: list[str] = []

    class _FakePipeline:
        def __init__(self, code: str, config: Any = None) -> None:
            self.code = code

        def run(self, day: str, retry_failed: bool = False) -> _FakeRun:
            ran.append(self.code)
            return _FakeRun(exit_code)

    monkeypatch.setattr(batch, "ResearchPipeline", _FakePipeline)
    return ran


# ------------------------------------------------------------------ 参数校验


def test_no_codes_is_usage_error(_offline: None) -> None:
    result = runner.invoke(app, ["research-batch", "--output", "json"])
    assert result.exit_code == 2
    assert "没有要研究的代码" in result.stdout + result.stderr


def test_codes_and_pool_are_mutually_exclusive(_offline: None) -> None:
    result = runner.invoke(app, ["research-batch", "600000", "--from-pool"])
    assert result.exit_code == 2
    assert "二选一" in result.stdout + result.stderr


def test_pool_source_without_daily_k_is_usage_error(
    _offline: None, tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yaml

    config_dir = tmp_path / "config"
    (config_dir / "sync.yaml").write_text(
        yaml.safe_dump({"sync": {"daily_k": {"codes": None}}}, allow_unicode=True),
        encoding="utf-8",
    )
    result = runner.invoke(app, ["research-batch", "--from-pool"])
    assert result.exit_code == 2
    assert "daily_k.codes" in result.stdout + result.stderr


def test_batch_cap_is_thirty(_offline: None) -> None:
    codes = [f"60{i:04d}" for i in range(31)]
    result = runner.invoke(app, ["research-batch", *codes])
    assert result.exit_code == 2
    assert "上限 30" in result.stdout + result.stderr


# ------------------------------------------------------------------ 队列语义


def test_duplicate_codes_are_deduped_in_order(
    _offline: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran = _install_pipeline(monkeypatch, exit_code=0)
    result = runner.invoke(
        app, ["research-batch", "600000", "600519", "600000", "--output", "json"]
    )
    assert result.exit_code == 0
    assert ran == ["600000", "600519"]
    payload = __import__("json").loads(result.stdout)
    assert payload["total"] == 2
    assert payload["failed"] == 0
    assert [row["position"] for row in payload["results"]] == [1, 2]


def test_one_failure_does_not_stop_the_queue(
    _offline: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ladderstock.cli.research_batch as batch

    ran: list[str] = []
    calls: list[str] = []

    class _Pickypipeline:
        def __init__(self, code: str, config: Any = None) -> None:
            self.code = code

        def run(self, day: str, retry_failed: bool = False) -> _FakeRun:
            calls.append(self.code)
            if self.code == "600519":
                raise RuntimeError("模拟单票失败")
            ran.append(self.code)
            return _FakeRun(0)

    monkeypatch.setattr(batch, "ResearchPipeline", _Pickypipeline)
    result = runner.invoke(
        app, ["research-batch", "600000", "600519", "600001", "--output", "json"]
    )
    assert result.exit_code == 1  # 单票失败 → 最差退出码 1，不是整批崩
    assert calls == ["600000", "600519", "600001"]  # 三票都进了队列，没有中断
    payload = __import__("json").loads(result.stdout)
    assert payload["failed"] == 1
    assert payload["results"][1]["status"] == "failed"
    assert "模拟单票失败" in str(payload["results"][1]["detail"])


def test_invalid_codes_never_reach_the_pipeline(
    _offline: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran = _install_pipeline(monkeypatch, exit_code=0)
    result = runner.invoke(app, ["research-batch", "abc", "6000000", "--output", "json"])
    assert result.exit_code == 2  # 参数错误是最差一票
    assert ran == []  # 管线一次都没被碰
    payload = __import__("json").loads(result.stdout)
    assert payload["failed"] == 2
    assert all(row["status"] == "invalid" for row in payload["results"])


def test_degraded_run_maps_to_partial_exit(_offline: None, monkeypatch: pytest.MonkeyPatch) -> None:
    import ladderstock.cli.research_batch as batch

    class _DegradedRun(_FakeRun):
        def __init__(self) -> None:
            super().__init__(8)

        def as_dict(self) -> dict[str, Any]:
            return {"code": "600000", "sections": {}, "degraded": ["valuation"]}

    class _DegradedPipeline:
        def __init__(self, code: str, config: Any = None) -> None:
            ...

        def run(self, day: str, retry_failed: bool = False) -> _DegradedRun:
            return _DegradedRun()

    monkeypatch.setattr(batch, "ResearchPipeline", _DegradedPipeline)
    result = runner.invoke(app, ["research-batch", "600000", "--output", "json"])
    assert result.exit_code == 8
    payload = __import__("json").loads(result.stdout)
    assert payload["results"][0]["status"] == "degraded"
    assert payload["ok"] is False
