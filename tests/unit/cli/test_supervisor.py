"""`ldst supervisor`（QUOT-523 单入口 CLI）：预演 / 确认 / 只动账本托管的进程。

引擎整体换成假件（`_engine` 注入点），不读真实账本、不起真进程。
"""

from __future__ import annotations

import json
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from ladderstock.cli import supervisor as supervisor_cli
from ladderstock.errors import EXIT_CONFIG, EXIT_OK, EXIT_PARTIAL


class FakeEngine:
    def __init__(self, *, failing: set[str] | None = None) -> None:
        self.failing = failing or set()
        self.calls: list[tuple[str, str | None]] = []
        self._order = ["tickhub", "watch"]

    def _check(self, name: str) -> None:
        if name in self.failing:
            from ladderstock.supervisor.engine import SupervisorError

            raise SupervisorError(f"{name} 失败")

    def start_all(self) -> dict[str, Any]:
        self.calls.append(("start_all", None))
        return {"ok": True, "modules": {"tickhub": {"ok": True, "error": None}}}

    def start_module(self, name: str) -> dict[str, Any]:
        self.calls.append(("start", name))
        self._check(name)
        return {"module": name, "ok": True}

    def stop_all(self) -> dict[str, Any]:
        self.calls.append(("stop_all", None))
        return {"ok": True, "modules": {"tickhub": {"ok": True, "error": None}}}

    def stop_module(self, name: str) -> dict[str, Any]:
        self.calls.append(("stop", name))
        self._check(name)
        return {"module": name, "ok": True}

    def restart_module(self, name: str) -> dict[str, Any]:
        self.calls.append(("restart", name))
        self._check(name)
        return {"module": name, "ok": True}

    def state(self) -> dict[str, Any]:
        return {
            "supervisor_pid": 999,
            "order": list(self._order),
            "modules": {
                "tickhub": {"ownership": "managed", "listening": True, "pid": 10},
                "watch": {"ownership": "external", "listening": True, "pid": 20},
            },
        }


def _app() -> typer.Typer:
    app = typer.Typer()
    supervisor_cli.register(app)
    return app


@pytest.fixture()
def engine(monkeypatch: pytest.MonkeyPatch) -> FakeEngine:
    fake = FakeEngine()
    monkeypatch.setattr(supervisor_cli, "_engine", lambda _root: fake)
    return fake


def _run(args: list[str]) -> tuple[int, Any]:
    # 只挂本组时入口仍在 `supervisor` 组名下，和真实 `ldst supervisor ...` 路径一致
    result = CliRunner().invoke(_app(), ["supervisor", *args])
    return result.exit_code, result.output


def test_stop_without_force_is_a_dry_run(engine: FakeEngine) -> None:
    code, out = _run(["stop", "--output", "json"])

    assert code == EXIT_OK
    payload = json.loads(out)
    assert payload["dry_run"] is True
    # 只提示账本证明是我们拉起的模块；外部进程（watch）绝不列入
    assert payload["would_stop"] == ["tickhub"]
    assert engine.calls == []


def test_stop_force_stops_everything(engine: FakeEngine) -> None:
    code, out = _run(["stop", "--force", "--output", "json"])

    assert code == EXIT_OK
    assert json.loads(out)["ok"] is True
    assert engine.calls == [("stop_all", None)]


def test_stop_reports_partial_failure(engine: FakeEngine) -> None:
    engine.failing = {"senate"}
    code, out = _run(["stop", "--force", "--only", "senate", "--output", "json"])

    assert code == EXIT_PARTIAL
    assert json.loads(out)["modules"]["senate"]["ok"] is False


def test_start_without_only_starts_all(engine: FakeEngine) -> None:
    code, _ = _run(["start", "--output", "json"])

    assert code == EXIT_OK
    assert engine.calls == [("start_all", None)]


def test_start_only_hits_named_modules(engine: FakeEngine) -> None:
    code, _ = _run(["start", "--only", "watch", "--only", "wire", "--output", "json"])

    assert code == EXIT_OK
    assert engine.calls == [("start", "watch"), ("start", "wire")]


def test_restart_requires_only(engine: FakeEngine) -> None:
    code, _ = _run(["restart", "--output", "json"])

    assert code == EXIT_CONFIG
    assert engine.calls == []


def test_restart_hits_the_named_module(engine: FakeEngine) -> None:
    code, out = _run(["restart", "--only", "watch", "--output", "json"])

    assert code == EXIT_OK
    assert json.loads(out)["modules"]["watch"]["ok"] is True
    assert engine.calls == [("restart", "watch")]


def test_status_prints_the_ownership_view(engine: FakeEngine) -> None:
    code, out = _run(["status", "--output", "json"])

    assert code == EXIT_OK
    payload = json.loads(out)
    assert payload["modules"]["tickhub"]["ownership"] == "managed"
    assert payload["modules"]["watch"]["ownership"] == "external"
    assert engine.calls == []
