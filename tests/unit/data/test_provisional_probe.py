"""`doctor` 的 council_memo 联调提示：探一次 `tools/list`，措辞说准（QUOT-112）。

契约：

- 工具在列表里 → **不产生** warning（联调已确认）；
- 工具不在列表里 → 明说工具不存在，并列出 peer 实际工具名；
- peer 不可达 → 「未联调：peer 不可达（打码地址）」，**绝不打印凭据**；
- 只探一次、短超时、失败不改 `ok` / 退出码 / 检查项数（DEC-008）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from ladderstock.cli.main import app
from ladderstock.data.provider_registry import (
    PeerEndpoint,
    SourceUnavailable,
    provisional_tool_warnings,
)

runner = CliRunner()

SENATE_URL = "http://127.0.0.1:8765/mcp"


class FakeTransport:
    """记录调用次数、按预设返回 `tools/list` 的假 transport。"""

    def __init__(self, *, tools: list[str] | None = None, error: Exception | None = None) -> None:
        self.tools = tools or []
        self.error = error
        self.calls: list[tuple[str, float]] = []

    def probe(self, peer: PeerEndpoint, *, timeout: float) -> dict[str, Any]:
        self.calls.append((peer.key, timeout))
        if self.error is not None:
            raise self.error
        return {"peer": peer.key, "tools": list(self.tools)}

    def call(self, peer: PeerEndpoint, tool: str, params: dict[str, Any], *, timeout: float):
        raise AssertionError("doctor 只该探 tools/list，不该调 tools/call")


@pytest.fixture()
def repo_env(repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LDST_SENATE_MCP_URL", raising=False)
    return repo


def _warnings(**kwargs: Any) -> list[str]:
    return provisional_tool_warnings(timeout=0.01, **kwargs)


def test_tool_present_yields_no_warning(repo_env: Path) -> None:
    transport = FakeTransport(tools=["report_query", "something_else"])
    assert _warnings(transport=transport) == []
    assert transport.calls == [("senate", 0.01)], "只探一次，且只探 senate"


def test_tool_missing_reports_actual_tools(repo_env: Path) -> None:
    transport = FakeTransport(tools=["report_list", "backtest"])
    warnings = _warnings(transport=transport)
    assert len(warnings) == 1
    text = warnings[0]
    assert text.startswith("council_memo:")
    assert "report_query" in text
    assert "不在" in text
    assert "report_list" in text and "backtest" in text
    assert "待联调" not in text, "既要联调就别说「待联调」这种谁也判断不了的话"


def test_empty_tool_list_says_none(repo_env: Path) -> None:
    warnings = _warnings(transport=FakeTransport(tools=[]))
    assert len(warnings) == 1
    assert "无" in warnings[0]


def test_unreachable_peer_warning_is_masked_and_single_attempt(
    repo_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(
        "LDST_SENATE_MCP_URL",
        "http://alice:hunter2@127.0.0.1:8765/mcp?token=SECRET123",
    )
    transport = FakeTransport(error=SourceUnavailable("boom"))
    warnings = _warnings(transport=transport)

    assert len(warnings) == 1
    text = warnings[0]
    assert "未联调：peer 不可达" in text
    assert "http://127.0.0.1:8765/mcp" in text
    # 凭据一个字都不许落进输出（audit R-3）
    assert "hunter2" not in text
    assert "alice" not in text
    assert "SECRET123" not in text
    assert len(transport.calls) == 1, "失败不重试：只探一次"


def test_probe_error_never_escapes(repo_env: Path) -> None:
    warnings = _warnings(transport=FakeTransport(error=TimeoutError("slow")))
    assert len(warnings) == 1
    assert "不可达" in warnings[0]


def test_doctor_keeps_22_checks_and_warning_does_not_change_exit(
    repo_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unmasked = "council_memo: 未联调：peer 不可达（http://127.0.0.1:8765/mcp）"
    monkeypatch.setattr(
        "ladderstock.cli.main.provisional_tool_warnings", lambda: [unmasked]
    )

    result = runner.invoke(app, ["doctor", "--output", "json"])

    payload = json.loads(result.stdout)
    assert len(payload["checks"]) == 22, "检查项数必须仍是 22"
    assert unmasked in payload["warnings"]
    assert result.exit_code == (0 if payload["ok"] else 3)
