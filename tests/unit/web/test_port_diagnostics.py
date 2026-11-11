"""端口诊断 + 安全自愈（QUOT-547）：全部走 fake host，不起真进程、不占真端口。

覆盖派单四条验收：
1. 占用四分类（own_child / adopted_peer / foreign / free）的纯判定 + 诊断 API；
2. 统一口径：adopted_peer 不再算 foreign（`_port_conflicts` / `_decide_status`
    consult supervisor 账本），与 page_boot 一致；
3. 健康探针修复：MCP 端口无 /healthz 时用 MCP 协议判活，绝不把 404 / 无会话当失败；
4. 安全自愈只动 own_child：adopted_peer 只认领 + 复检（绝不终止 / 重启），
   foreign 只报告（绝不自动杀），free 无动作。

末尾附反向验证：去掉认领态 + MCP 判活即复现「adopted peer 被误判 foreign / 异常」。
"""

from __future__ import annotations

import io
import urllib.error
from pathlib import Path
from typing import Any

import pytest

from ladderstock.core.boundary import PeerControl
from ladderstock.supervisor.ledger import Ledger, ModuleRecord
from ladderstock.supervisor.proctree import cmdline_hash
from ladderstock.web import port_diagnostics as pd
from ladderstock.web.control import ControlPlane, PortOwner, StepResult
from ladderstock.web.port_diagnostics import (
    ADOPTED_PEER,
    FOREIGN,
    FREE,
    OWN_CHILD,
    classify_port_owner,
    mcp_path_for,
    probe_mcp_alive,
)

#: 真实 bug 现场：8000 的持有者就是 TickHub-CN peer 本身（外部启动、被认领）
PEER_PID = 27572
PEER_CMDLINE = (
    "th-cn.exe",
    "start-all",
    "--host",
    "127.0.0.1",
    "--port",
    "8000",
    "--crawler",
    "on",
)
FOREIGN_CMDLINE = ("other.exe", "--serve", "--port", "8000")


def _record(*, adopted_peer: bool, pid: int = PEER_PID, state: str = "running") -> ModuleRecord:
    return ModuleRecord(
        module="tickhub",
        ports=[8000, 8787],
        pid=pid,
        origin="adopted" if adopted_peer else "spawned",
        adopted_peer=adopted_peer,
        cmdline_hash=cmdline_hash(PEER_CMDLINE),
        state=state,  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------- #
# 1. 纯判定：占用四分类
# --------------------------------------------------------------------------- #


def test_classify_own_child_from_control_plane_state() -> None:
    classification, reason = classify_port_owner(pid=PEER_PID, mine_pids={PEER_PID}, record=None)
    assert classification == OWN_CHILD
    assert "本控制面" in reason


def test_classify_own_child_from_supervisor_ledger() -> None:
    classification, _ = classify_port_owner(
        pid=PEER_PID, mine_pids=set(), record=_record(adopted_peer=False)
    )
    assert classification == OWN_CHILD


def test_classify_adopted_peer_not_foreign() -> None:
    classification, reason = classify_port_owner(
        pid=PEER_PID, mine_pids=set(), record=_record(adopted_peer=True)
    )
    assert classification == ADOPTED_PEER
    assert "绝不终止" in reason


def test_classify_foreign_when_no_evidence() -> None:
    classification, _ = classify_port_owner(pid=999, mine_pids=set(), record=None)
    assert classification == FOREIGN


def test_classify_foreign_when_pid_unknown() -> None:
    classification, reason = classify_port_owner(pid=None, mine_pids=set(), record=None)
    assert classification == FOREIGN
    assert "拿不到" in reason


def test_classify_pid_reuse_is_foreign_even_if_adopted() -> None:
    """账本认得这个 PID，但实时命令行对不上指纹 → PID 复用，按 foreign（不终止）。"""
    classification, reason = classify_port_owner(
        pid=PEER_PID,
        mine_pids=set(),
        record=_record(adopted_peer=True),
        live_cmdline=FOREIGN_CMDLINE,
    )
    assert classification == FOREIGN
    assert "复用" in reason


def test_classify_adopted_peer_with_matching_cmdline() -> None:
    classification, _ = classify_port_owner(
        pid=PEER_PID,
        mine_pids=set(),
        record=_record(adopted_peer=True),
        live_cmdline=PEER_CMDLINE,
    )
    assert classification == ADOPTED_PEER


def test_classify_stopped_ledger_record_does_not_claim_port() -> None:
    """账本记录已 stopped：端口上的活进程不是它，按 foreign。"""
    classification, _ = classify_port_owner(
        pid=PEER_PID, mine_pids=set(), record=_record(adopted_peer=True, state="stopped")
    )
    assert classification == FOREIGN


def test_classify_stale_adopted_flag_is_observe_only() -> None:
    """现网账本由旧代码写成：origin=adopted 但漏打 adopted_peer 标记（False）。

    这正是 8000 的真实账本态。绝不能因标记滞后判成 own_child 进而被自愈重拉
    （红线：绝不终止 / 重启 peer）；origin=adopted 一律按认领态只观测。
    """
    stale = ModuleRecord(
        module="tickhub",
        ports=[8000],
        pid=PEER_PID,
        origin="adopted",
        adopted_peer=False,
        cmdline_hash=cmdline_hash(PEER_CMDLINE),
        state="running",  # type: ignore[arg-type]
    )
    classification, reason = classify_port_owner(
        pid=PEER_PID, mine_pids=set(), record=stale, live_cmdline=PEER_CMDLINE
    )
    assert classification == ADOPTED_PEER
    assert "origin=adopted" in reason
    assert "绝不自动终止" in reason


# --------------------------------------------------------------------------- #
# 2. MCP 路径推导 + 存活判定
# --------------------------------------------------------------------------- #


def test_mcp_path_from_default_url() -> None:
    assert mcp_path_for({"default_url": "http://127.0.0.1:8000/mcp"}) == "/mcp"
    assert mcp_path_for({"default_url": "http://127.0.0.1:8791/custom"}) == "/custom"
    assert mcp_path_for({}) == "/mcp"


@pytest.mark.parametrize(
    ("code", "headers", "body", "alive"),
    [
        (200, {}, "", True),
        (400, {"mcp-session-id": "abc"}, "", True),
        (400, {}, '{"jsonrpc":"2.0","error":{"code":-32000,"message":"Missing session ID"}}', True),
        (405, {}, "", True),
        (406, {}, "", True),
        (404, {}, "", False),
        (400, {}, "plain bad request", False),
        (500, {}, "", False),
    ],
)
def test_mcp_verdict(code: int, headers: dict, body: str, alive: bool) -> None:
    ok, _ = pd._mcp_verdict(code, headers, body)
    assert ok is alive


class _FakeResponse:
    def __init__(self, status: int, headers: dict, body: str) -> None:
        self.status = status
        self.headers = headers
        self._body = body.encode("utf-8")

    def read(self, limit: int) -> bytes:
        return self._body[:limit]

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class _FakeOpener:
    def __init__(self, *, response: _FakeResponse | None = None, error: Exception | None = None):
        self._response = response
        self._error = error

    def open(self, request: Any, timeout: float | None = None) -> _FakeResponse:
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response


def _patch_opener(monkeypatch: pytest.MonkeyPatch, opener: _FakeOpener) -> None:
    monkeypatch.setattr(pd.urllib.request, "build_opener", lambda *a, **k: opener)


def test_probe_mcp_alive_on_200(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_opener(monkeypatch, _FakeOpener(response=_FakeResponse(200, {}, "{}")))
    ok, detail = probe_mcp_alive("http://127.0.0.1:8000/mcp")
    assert ok is True
    assert "200" in detail


def test_probe_mcp_alive_on_400_jsonrpc(monkeypatch: pytest.MonkeyPatch) -> None:
    body = '{"jsonrpc":"2.0","error":{"code":-32000,"message":"Missing session ID"}}'
    error = urllib.error.HTTPError(
        "http://127.0.0.1:8000/mcp", 400, "Bad Request", {}, io.BytesIO(body.encode())
    )
    _patch_opener(monkeypatch, _FakeOpener(error=error))
    ok, detail = probe_mcp_alive("http://127.0.0.1:8000/mcp")
    assert ok is True
    assert "MCP 服务存活" in detail


def test_probe_mcp_alive_on_404_is_not_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    error = urllib.error.HTTPError(
        "http://127.0.0.1:8000/mcp", 404, "Not Found", {}, io.BytesIO(b"")
    )
    _patch_opener(monkeypatch, _FakeOpener(error=error))
    ok, _ = probe_mcp_alive("http://127.0.0.1:8000/mcp")
    assert ok is False


def test_probe_mcp_alive_on_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_opener(monkeypatch, _FakeOpener(error=urllib.error.URLError("refused")))
    ok, detail = probe_mcp_alive("http://127.0.0.1:8000/mcp")
    assert ok is False
    assert "无法连接" in detail


# --------------------------------------------------------------------------- #
# 3. 控制面集成（fake host + 真账本）：统一口径 + 健康修复 + 诊断 + 自愈
# --------------------------------------------------------------------------- #


class RecordingEngine:
    """顺序启停引擎假件：真账本 + 记录 adopt / restart 调用（绝不真起停）。"""

    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger
        self.adopt_calls: list[str] = []
        self.restart_calls: list[str] = []

    def adopt_module(self, name: str) -> ModuleRecord | None:
        self.adopt_calls.append(name)
        return self.ledger.record(name)

    def restart_module(self, name: str) -> dict[str, Any]:
        self.restart_calls.append(name)
        return {"module": name, "ok": True}


REGISTRY = {
    "tickhub": {
        "product": "TickHub-CN",
        "mcp_name": "tickhub-cn",
        "default_url": "http://127.0.0.1:8000/mcp",
        "health_path": "/healthz",
        "default_port": 8000,
    }
}


def _plane(
    tmp_path: Path,
    *,
    engine: Any = None,
    ports: Any = None,
    health: Any = None,
    mcp: Any = None,
    cmdline: Any = None,
    registry: dict | None = None,
) -> ControlPlane:
    directory = tmp_path / "tickhub"
    directory.mkdir(parents=True, exist_ok=True)
    peer = PeerControl(
        key="tickhub",
        directory=str(directory),
        import_package="tickhub_cn",
        mcp_port=8000,
        web_port=8787,
        web_port_label="管理端",
        health_path="/healthz",
        start_commands=(("th-cn", "start-all"),),
        config_keys=(),
    )
    return ControlPlane(
        data_root=tmp_path,
        peers={"tickhub": peer},
        components={},
        sources_loader=lambda: REGISTRY if registry is None else registry,
        run=lambda args, cwd, timeout: StepResult(0, "ok"),
        port_probe=ports or (lambda port: []),
        # HTTP 健康探针默认全失败（8787 拒绝、8000/healthz 404）——真实 bug 现场
        health_probe=health or (lambda url, timeout: (False, "HTTP 404")),
        mcp_probe=mcp or (lambda url, timeout: (True, "HTTP 400 · JSON-RPC 响应（MCP 服务存活）")),
        cmdline_probe=cmdline or (lambda pid: PEER_CMDLINE),
        supervisor=engine,
    )


def _ledger(tmp_path: Path, record: ModuleRecord | None) -> Ledger:
    ledger = Ledger(tmp_path / "supervisor" / "ledger.json")
    if record is not None:
        ledger.update(record)
    return ledger


def _adopted_plane(tmp_path: Path) -> tuple[ControlPlane, RecordingEngine]:
    ledger = _ledger(tmp_path, _record(adopted_peer=True))
    engine = RecordingEngine(ledger)
    plane = _plane(
        tmp_path,
        engine=engine,
        ports=lambda port: [PortOwner(pid=PEER_PID, process="th-cn.exe")] if port == 8000 else [],
    )
    return plane, engine


def test_port_conflicts_adopted_peer_is_not_foreign(tmp_path: Path) -> None:
    plane, _ = _adopted_plane(tmp_path)
    conflicts = plane.snapshot("tickhub")["port_conflicts"]
    assert len(conflicts) == 1
    assert conflicts[0]["class"] == ADOPTED_PEER
    assert conflicts[0]["foreign"] is False
    assert conflicts[0]["severity"] == "info"
    assert conflicts[0]["pid"] == PEER_PID


def test_snapshot_adopted_mcp_peer_is_running_not_failed(tmp_path: Path) -> None:
    """核心 bug：8000 是 adopted peer + MCP 存活 → RUNNING，不再「健康检查失败 / 被占用」。"""
    plane, _ = _adopted_plane(tmp_path)
    snapshot = plane.snapshot("tickhub")
    assert snapshot["status"] == "running"
    assert snapshot["health"]["ok"] is True
    assert "mcp-protocol" in snapshot["health"]["detail"]
    assert all(c["foreign"] is False for c in snapshot["port_conflicts"])


def test_health_fallback_skipped_for_non_mcp_peer(tmp_path: Path) -> None:
    """注册表无 mcp_name（非 MCP peer）：不做 MCP 判活，HTTP 失败仍是失败。"""
    mcp_calls: list[str] = []
    plane = _plane(
        tmp_path,
        ports=lambda port: [PortOwner(pid=PEER_PID, process="x.exe")] if port == 8000 else [],
        registry={"tickhub": {"product": "TickHub-CN"}},
        mcp=lambda url, timeout: (mcp_calls.append(url), (True, "alive"))[1],
    )
    snapshot = plane.snapshot("tickhub")
    assert snapshot["health"]["ok"] is False
    assert mcp_calls == []


def test_diagnose_ports_four_classes_and_cmdline(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, _record(adopted_peer=True))
    engine = RecordingEngine(ledger)
    plane = _plane(
        tmp_path,
        engine=engine,
        ports=lambda port: [PortOwner(pid=PEER_PID, process="th-cn.exe")] if port == 8000 else [],
    )
    payload = plane.diagnose_ports("tickhub")
    assert payload["ok"] is True
    ports = {entry["port"]: entry for entry in payload["peers"][0]["ports"]}
    assert ports[8000]["class"] == ADOPTED_PEER
    assert ports[8000]["pid"] == PEER_PID
    assert ports[8000]["cmdline"] == list(PEER_CMDLINE)
    assert ports[8000]["listening"] is True
    # 8787 无监听 → free
    assert ports[8787]["class"] == FREE
    assert ports[8787]["listening"] is False


def test_diagnose_ports_masks_secrets_in_cmdline(tmp_path: Path) -> None:
    secret = "sk-live-QUOT547-SECRET-donotprint"
    ledger = _ledger(tmp_path, _record(adopted_peer=False))
    plane = _plane(
        tmp_path,
        engine=RecordingEngine(ledger),
        ports=lambda port: [PortOwner(pid=PEER_PID, process="th-cn.exe")] if port == 8000 else [],
        cmdline=lambda pid: ("th-cn.exe", "start-all", f"--api-key={secret}"),
    )
    entry = plane.diagnose_ports("tickhub")["peers"][0]["ports"][0]
    joined = " ".join(entry["cmdline"])
    assert secret not in joined
    assert "***" in joined


def test_diagnose_ports_foreign_and_own_child(tmp_path: Path) -> None:
    # foreign：无账本、非本控制面 PID
    plane = _plane(
        tmp_path,
        ports=lambda port: [PortOwner(pid=999, process="other.exe")] if port == 8000 else [],
        cmdline=lambda pid: FOREIGN_CMDLINE,
    )
    entry = plane.diagnose_ports("tickhub")["peers"][0]["ports"][0]
    assert entry["class"] == FOREIGN
    # own_child：本控制面状态账记录的 PID
    plane._peer_state("tickhub")["pids"] = [999]
    entry = plane.diagnose_ports("tickhub")["peers"][0]["ports"][0]
    assert entry["class"] == OWN_CHILD


# ---- 安全自愈：只动 own_child ------------------------------------------------


def test_heal_adopted_peer_only_adopts_never_restarts(tmp_path: Path) -> None:
    plane, engine = _adopted_plane(tmp_path)
    result = plane.heal_ports("tickhub")
    assert result["action"] == "adopt"
    assert result["ok"] is True
    assert engine.adopt_calls == ["tickhub"]
    assert engine.restart_calls == []  # 绝不终止 / 重启 adopted peer


def test_heal_stale_adopted_flag_never_restarts(tmp_path: Path) -> None:
    """现网 8000 账本态（origin=adopted、adopted_peer=False）：自愈只认领，绝不重拉。"""
    stale = ModuleRecord(
        module="tickhub",
        ports=[8000],
        pid=PEER_PID,
        origin="adopted",
        adopted_peer=False,
        cmdline_hash=cmdline_hash(PEER_CMDLINE),
        state="running",  # type: ignore[arg-type]
    )
    engine = RecordingEngine(_ledger(tmp_path, stale))
    plane = _plane(
        tmp_path,
        engine=engine,
        ports=lambda port: [PortOwner(pid=PEER_PID, process="th-cn.exe")] if port == 8000 else [],
    )
    result = plane.heal_ports("tickhub")
    assert result["action"] == "adopt"
    assert engine.adopt_calls == ["tickhub"]
    assert engine.restart_calls == []  # 标记滞后也绝不终止 / 重启 peer


def test_heal_own_child_restarts(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, _record(adopted_peer=False))
    engine = RecordingEngine(ledger)
    plane = _plane(
        tmp_path,
        engine=engine,
        ports=lambda port: [PortOwner(pid=PEER_PID, process="th-cn.exe")] if port == 8000 else [],
    )
    result = plane.heal_ports("tickhub")
    assert result["action"] == "restart"
    assert engine.restart_calls == ["tickhub"]
    assert engine.adopt_calls == []


def test_heal_foreign_only_reports_never_touches_engine(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, None)
    engine = RecordingEngine(ledger)
    plane = _plane(
        tmp_path,
        engine=engine,
        ports=lambda port: [PortOwner(pid=999, process="other.exe")] if port == 8000 else [],
        cmdline=lambda pid: FOREIGN_CMDLINE,
    )
    result = plane.heal_ports("tickhub")
    assert result["action"] == "report"
    assert engine.adopt_calls == []
    assert engine.restart_calls == []
    assert "不终止" in result["detail"]


def test_heal_free_port_no_action(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, None)
    engine = RecordingEngine(ledger)
    plane = _plane(tmp_path, engine=engine)
    result = plane.heal_ports("tickhub")
    assert result["action"] == "none"
    assert engine.adopt_calls == []
    assert engine.restart_calls == []


def test_heal_adopted_peer_without_engine_does_not_terminate(tmp_path: Path) -> None:
    plane = _plane(
        tmp_path,
        engine=None,
        ports=lambda port: [PortOwner(pid=PEER_PID, process="th-cn.exe")] if port == 8000 else [],
    )
    # 无 supervisor 引擎时仍能从自愈账本读到认领态（这里无账本 → foreign），
    # 但即便有认领态也绝不终止：用真账本注入 selfheal 验证只读路径。
    ledger = _ledger(tmp_path, _record(adopted_peer=True))
    plane.selfheal = type("Heal", (), {"ledger": ledger})()
    result = plane.heal_ports("tickhub")
    assert result["action"] == "adopt"
    assert "未做任何终止" in result["detail"]


# --------------------------------------------------------------------------- #
# 4. 反向验证：去掉修复即复现「adopted peer 被误判 foreign / 异常」
# --------------------------------------------------------------------------- #


def test_reverse_verification_reproduces_misjudgment_without_fix(tmp_path: Path) -> None:
    """复现旧 bug：无认领态（账本不认）+ 无 MCP 判活 → foreign + 健康失败 → FAILED。

    这正是修复前的现场：8000 的持有者其实是 peer 本身，却被判「被其他进程占用」。
    与 test_snapshot_adopted_mcp_peer_is_running_not_failed 对照，证明修复翻转了结论。
    """
    plane = _plane(
        tmp_path,
        engine=None,  # 无 supervisor 账本 → 分类退回 foreign
        ports=lambda port: [PortOwner(pid=PEER_PID, process="th-cn.exe")] if port == 8000 else [],
        registry={"tickhub": {"product": "TickHub-CN"}},  # 无 mcp_name → 不做 MCP 判活
    )
    snapshot = plane.snapshot("tickhub")
    assert snapshot["status"] == "failed"
    assert snapshot["port_conflicts"][0]["foreign"] is True
    assert "其他进程占用" in snapshot["detail"]
