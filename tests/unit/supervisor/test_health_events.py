"""peer 级健康事件通道（QUOT-556）：总线契约 + 跃迁观测 + 自愈联动。

钉住：

| 场景 | 期望 |
|---|---|
| 总线 | 单调 seq、since/module/limit 过滤、环形缓冲挤旧不膨胀 |
| crash | 进程死 → 发一次；自愈中（health=restarting）不掩盖 crash |
| recovered | 只在「先坏过」之后发一次；健康模块手动重启全程静默 |
| 主动停 / 从未拉起 | 全程静默（含 restart 的 stop→start 间隙） |
| offline / port_conflict | 端口观测给出才判；foreign 占用优先于 crash |
| 端口观测 | 按间隔节流复用；观测失败吞掉，存活类事件不受影响 |
| 自愈联动 | tick 先观测再处置；attach 模式放弃 → needs_human 事件 |

全部假件（注入存活探针 / 端口观测 / 时钟），不起真进程、不占真端口。
"""

from __future__ import annotations

from pathlib import Path

from ladderstock.supervisor.health_events import (
    EVENT_CRASH,
    EVENT_NEEDS_HUMAN,
    EVENT_OFFLINE,
    EVENT_PORT_CONFLICT,
    EVENT_RECOVERED,
    OWNER_ADOPTED_PEER,
    OWNER_FOREIGN,
    OWNER_FREE,
    OWNER_OWN_CHILD,
    HealthEventBus,
    HealthEventMonitor,
    PortObservation,
    port_conflict_reason,
)
from ladderstock.supervisor.ledger import (
    HEALTH_NEEDS_HUMAN,
    HEALTH_OK,
    HEALTH_RESTARTING,
    STOP_BY_REQUEST,
    Ledger,
    ModuleRecord,
)
from ladderstock.supervisor.selfheal import RestartOutcome, SelfHealEngine


def _entry(module: str = "wire", **overrides: object) -> ModuleRecord:
    fields: dict[str, object] = {
        "ports": [8790, 8791],
        "pid": 101,
        "started_at": "2026-01-01T00:00:00+00:00",
        "origin": "adopted",
        "adopted_peer": True,
        "state": "running",
        "health": HEALTH_OK,
    }
    fields.update(overrides)
    return ModuleRecord(module=module, **fields)  # type: ignore[arg-type]


class _World:
    """存活探针 + 端口观测 + 时钟全可控的观测世界。"""

    def __init__(self, *, with_inspector: bool = True) -> None:
        self.bus = HealthEventBus()
        self.alive: set[int] = {101}
        self.occupants_alive: set[int] = {101}
        self.ports: dict[str, list[PortObservation]] = {}
        self.inspector_calls = 0
        self.inspector_raises = False
        self.now = [0.0]
        self.monitor = HealthEventMonitor(
            self.bus,
            port_inspector=self._inspect if with_inspector else None,
            clock=lambda: self.now[0],
            is_pid_alive=lambda pid: pid in self.occupants_alive,
        )

    def _inspect(self, entries: object) -> dict[str, list[PortObservation]]:
        self.inspector_calls += 1
        if self.inspector_raises:
            raise OSError("连接表扫描失败")
        return dict(self.ports)

    def observe(self, *entries: ModuleRecord) -> None:
        self.monitor.observe(list(entries), is_alive=lambda entry: entry.pid in self.alive)

    def events(self, module: str | None = None) -> list[dict[str, object]]:
        return [item.to_dict() for item in self.bus.since(0, module=module)]

    def kinds(self, module: str | None = None) -> list[object]:
        return [item["event"] for item in self.events(module)]


# -------------------------------------------------------------------- 总线


def test_bus_seq_monotonic_and_since_filters() -> None:
    bus = HealthEventBus()
    first = bus.publish(module="wire", event=EVENT_CRASH, health="ok", state="down", reason="r")
    second = bus.publish(module="senate", event=EVENT_CRASH, health="ok", state="down", reason="r")
    third = bus.publish(
        module="wire",
        event=EVENT_RECOVERED,
        health="ok",
        state="running",
        reason="r",
        pid=102,
        ports=(8790, 8791),
        origin="spawned",
    )

    assert [first.seq, second.seq, third.seq] == [1, 2, 3]
    assert bus.latest_seq == 3
    assert [item.seq for item in bus.since(1)] == [2, 3]
    assert [item.seq for item in bus.since(0, module="wire")] == [1, 3]
    assert [item.seq for item in bus.since(0, limit=2)] == [1, 2]
    assert bus.since(3) == []

    payload = third.to_dict()
    assert payload["ports"] == [8790, 8791]
    assert payload["pid"] == 102
    assert payload["origin"] == "spawned"
    assert payload["adopted_peer"] is False
    assert payload["ts"]


def test_bus_ring_evicts_old_events_without_unbounded_growth() -> None:
    bus = HealthEventBus(capacity=3)
    for _ in range(5):
        bus.publish(module="wire", event=EVENT_CRASH, health="ok", state="down", reason="r")

    assert bus.latest_seq == 5
    assert [item.seq for item in bus.since(0)] == [3, 4, 5]


# -------------------------------------------------------------------- 跃迁


def test_crash_emitted_once_then_recovered_once() -> None:
    world = _World()
    entry = _entry()

    world.observe(entry)
    assert world.events() == []

    world.alive.clear()
    world.observe(entry)
    world.observe(entry)  # 持续死着不刷屏
    assert world.kinds() == [EVENT_CRASH]
    crash = world.events()[0]
    assert crash["module"] == "wire"
    assert crash["state"] == "down"
    assert crash["origin"] == "adopted"
    assert crash["adopted_peer"] is True
    assert crash["ports"] == [8790, 8791]
    assert isinstance(crash["reason"], str) and crash["reason"]

    world.alive.add(101)
    world.observe(entry)
    world.observe(entry)
    assert world.kinds() == [EVENT_CRASH, EVENT_RECOVERED]
    assert world.events()[-1]["state"] == "running"


def test_crash_not_masked_by_healing_health() -> None:
    """正在自愈的崩溃也是崩溃：health=restarting 不得吞掉 crash 事件。"""
    world = _World()
    entry = _entry(health=HEALTH_RESTARTING)

    world.alive.clear()
    world.observe(entry)

    assert world.kinds() == [EVENT_CRASH]
    assert world.events()[0]["health"] == HEALTH_RESTARTING
    assert world.events()[0]["state"] == "restarting"


def test_intentional_stop_and_never_started_stay_silent() -> None:
    world = _World()
    stopped = _entry(stopping_by_user=True, state="stopped", stop_reason=STOP_BY_REQUEST)
    never = ModuleRecord(module="watch")

    world.alive.clear()
    world.observe(stopped, never)
    world.observe(stopped, never)

    assert world.events() == []


def test_manual_restart_gap_stays_silent() -> None:
    """restart 的 stop→start 间隙（stop_reason 残留 / state=starting）不误报 crash。"""
    world = _World()
    entry = _entry()
    world.observe(entry)

    world.alive.clear()
    entry.state = "stopped"
    entry.stop_reason = STOP_BY_REQUEST
    world.observe(entry)
    entry.state = "starting"
    entry.stop_reason = None
    world.observe(entry)
    entry.state = "running"
    entry.pid = 102
    world.alive.add(102)
    world.observe(entry)

    assert world.events() == []


def test_needs_human_then_reset_and_revive_emits_recovered() -> None:
    world = _World()
    entry = _entry(health=HEALTH_NEEDS_HUMAN)

    world.alive.clear()
    world.observe(entry)
    assert world.kinds() == [EVENT_NEEDS_HUMAN]
    assert world.events()[0]["state"] == "needs_human"

    entry.health = HEALTH_OK  # reset_heal 的对外效果
    world.alive.add(102)
    entry.pid = 102
    world.observe(entry)
    assert world.kinds() == [EVENT_NEEDS_HUMAN, EVENT_RECOVERED]


def test_offline_when_alive_but_ports_not_listening() -> None:
    world = _World()
    entry = _entry()
    world.ports["wire"] = [
        PortObservation(port=8790, listening=False, owner=OWNER_FREE),
        PortObservation(port=8791, listening=False, owner=OWNER_FREE),
    ]

    world.observe(entry)
    assert world.kinds() == [EVENT_OFFLINE]

    world.ports["wire"] = [PortObservation(port=8790, listening=True, owner="adopted_peer")]
    world.now[0] += 10.0  # 越过节流间隔，端口观测刷新
    world.observe(entry)
    assert world.kinds() == [EVENT_OFFLINE, EVENT_RECOVERED]


def test_foreign_port_owner_wins_over_crash_and_offline() -> None:
    world = _World()
    entry = _entry()
    world.ports["wire"] = [PortObservation(port=8790, listening=True, owner=OWNER_FOREIGN)]

    world.observe(entry)  # 活着但端口被外来进程占着
    assert world.kinds() == [EVENT_PORT_CONFLICT]

    world.alive.clear()
    world.now[0] += 10.0
    world.observe(entry)  # 死了 + 端口仍被占：报冲突而非 crash（处置归 S4）
    assert world.kinds() == [EVENT_PORT_CONFLICT]

    world.ports["wire"] = [PortObservation(port=8790, listening=False, owner=OWNER_FREE)]
    world.now[0] += 10.0
    world.observe(entry)
    assert world.kinds() == [EVENT_PORT_CONFLICT, EVENT_CRASH]


def test_no_inspector_means_no_port_events_but_crash_still_works() -> None:
    world = _World(with_inspector=False)
    entry = _entry()

    world.observe(entry)  # 没有端口信息：不判 offline
    assert world.events() == []

    world.alive.clear()
    world.observe(entry)
    assert world.kinds() == [EVENT_CRASH]


def test_port_probe_throttled_but_crash_latency_unaffected() -> None:
    world = _World()
    entry = _entry()
    world.ports["wire"] = [PortObservation(port=8790, listening=True, owner="adopted_peer")]

    world.observe(entry)
    world.now[0] += 1.0
    world.observe(entry)
    assert world.inspector_calls == 1, "节流间隔内重复扫了连接表"

    world.alive.clear()  # 两次端口扫描之间进程死了：crash 不等节流
    world.observe(entry)
    assert world.kinds() == [EVENT_CRASH]

    world.now[0] += 5.0
    world.observe(entry)
    assert world.inspector_calls == 2


def test_inspector_failure_swallowed_and_liveness_events_survive() -> None:
    world = _World()
    entry = _entry()
    world.inspector_raises = True

    world.observe(entry)
    world.alive.clear()
    world.observe(entry)

    assert world.kinds() == [EVENT_CRASH]


def test_single_module_probe_failure_does_not_break_round() -> None:
    world = _World()
    bad = _entry(module="senate")
    good = _entry(module="wire")
    world.alive.clear()

    def _is_alive(entry: ModuleRecord) -> bool:
        if entry.module == "senate":
            raise OSError("探测通道挂了")
        return False

    world.monitor.observe([bad, good], is_alive=_is_alive)

    assert world.kinds() == [EVENT_CRASH]
    assert world.events()[0]["module"] == "wire"


# ---------------------------------------------------------------- 自愈联动


class _ScriptedRestarter:
    """重拉通道假件：spawn_ok=False 模拟附加模式「无通道失败」。"""

    def __init__(self, *, spawn_ok: bool) -> None:
        self.alive: set[int] = set()
        self.spawn_ok = spawn_ok
        self.restart_calls: list[str] = []
        self._next_pid = 200

    def is_alive(self, entry: ModuleRecord) -> bool:
        return entry.pid in self.alive

    def restart(self, module: str) -> RestartOutcome:
        self.restart_calls.append(module)
        if not self.spawn_ok:
            return RestartOutcome(ok=False, detail="附加模式不自动拉起")
        self._next_pid += 1
        self.alive.add(self._next_pid)
        return RestartOutcome(ok=True, pid=self._next_pid)


def _heal(
    tmp_path: Path, restarter: _ScriptedRestarter, bus: HealthEventBus
) -> tuple[Ledger, SelfHealEngine]:
    ledger = Ledger(tmp_path / "supervisor" / "ledger.json")
    ledger.ensure("wire", ports=(8790, 8791))
    ledger.update("wire", pid=101, state="running", origin="adopted", adopted_peer=True)
    engine = SelfHealEngine(
        ledger,
        restarter,
        sleeper=lambda _seconds: None,
        backoff=(0.0, 0.0, 0.0),
        event_bus=bus,
    )
    return ledger, engine


def test_tick_emits_crash_then_recovered_after_successful_pull(tmp_path: Path) -> None:
    restarter = _ScriptedRestarter(spawn_ok=True)
    restarter.alive.add(101)
    bus = HealthEventBus()
    _ledger, engine = _heal(tmp_path, restarter, bus)

    engine.tick()
    assert bus.since(0) == []

    restarter.alive.clear()  # 外部 kill
    engine.tick()  # 先观测（crash）再处置（重拉成功换绑 pid=201）
    engine.tick()  # 确认新进程活着 → recovered

    kinds = [item.event for item in bus.since(0)]
    assert kinds == [EVENT_CRASH, EVENT_RECOVERED]
    crash, recovered = bus.since(0)
    assert crash.module == "wire" and crash.adopted_peer is True
    assert recovered.pid == 201 and recovered.state == "running"
    assert restarter.restart_calls == ["wire"]


def test_attach_mode_gives_up_emits_needs_human_then_confirmed_restart_recovers(
    tmp_path: Path,
) -> None:
    """attach 模式（spawn=False）：crash → 3 次无通道失败 → needs_human 事件；
    用户确认重启（引擎 spawn 换绑）后 → recovered 事件。"""
    restarter = _ScriptedRestarter(spawn_ok=False)
    bus = HealthEventBus()
    ledger, engine = _heal(tmp_path, restarter, bus)

    engine.tick()  # crash + 第 1 次失败
    engine.tick()  # 第 2 次失败
    engine.tick()  # 第 3 次失败
    engine.tick()  # 超限 → needs_human（观测在处置前，本轮仍 crash 条件）
    engine.tick()  # 观测到 needs_human
    assert [item.event for item in bus.since(0)] == [EVENT_CRASH, EVENT_NEEDS_HUMAN]
    assert restarter.restart_calls == ["wire"] * 3
    assert ledger.get("wire").health == HEALTH_NEEDS_HUMAN

    engine.tick()
    assert restarter.restart_calls == ["wire"] * 3, "needs_human 后仍在自动重拉"

    # 用户确认一键重启：reset + 引擎 spawn（此处直接落账模拟引擎换绑结果）
    engine.reset("wire")
    ledger.update(
        "wire", pid=301, state="running", origin="spawned", adopted_peer=False, stop_reason=None
    )
    restarter.alive.add(301)
    engine.tick()

    kinds = [item.event for item in bus.since(0)]
    assert kinds == [EVENT_CRASH, EVENT_NEEDS_HUMAN, EVENT_RECOVERED]
    assert bus.since(0)[-1].adopted_peer is False


def test_snapshot_exposes_events_latest_and_adopted_peer(tmp_path: Path) -> None:
    restarter = _ScriptedRestarter(spawn_ok=True)
    restarter.alive.add(101)
    bus = HealthEventBus()
    _ledger, engine = _heal(tmp_path, restarter, bus)

    snapshot = engine.snapshot()
    assert snapshot["events_latest"] == 0
    by_module = {item["module"]: item for item in snapshot["modules"]}
    assert by_module["wire"]["adopted_peer"] is True
    assert by_module["senate"]["adopted_peer"] is False

    bus.publish(module="wire", event=EVENT_CRASH, health="ok", state="down", reason="r")
    assert engine.snapshot()["events_latest"] == 1

    plain = SelfHealEngine(_ledger, restarter)
    assert "events_latest" not in plain.snapshot()


# ------------------------------------------------------- S4 端口冲突分类（QUOT-557）


def test_port_conflict_reason_picks_most_conservative_owner() -> None:
    """白话原因按占用者分类；混合占用取最保守（foreign > adopted > own），失败关闭。"""
    own = port_conflict_reason([OWNER_OWN_CHILD])
    adopted = port_conflict_reason([OWNER_ADOPTED_PEER])
    foreign = port_conflict_reason([OWNER_FOREIGN])
    assert "安全重启" in own
    assert "不会终止" in adopted
    assert "不会终止" in foreign and "不会自动换端口" in foreign
    assert port_conflict_reason([OWNER_OWN_CHILD, OWNER_FOREIGN]) == foreign
    assert port_conflict_reason([OWNER_OWN_CHILD, OWNER_ADOPTED_PEER]) == adopted
    assert port_conflict_reason([OWNER_ADOPTED_PEER, OWNER_FOREIGN]) == foreign
    assert port_conflict_reason([])  # 空列表回退通用原因（非空字符串）


def test_foreign_conflict_event_carries_owner_and_pid() -> None:
    world = _World()
    entry = _entry()  # 活着，但端口被外来进程占着
    world.ports["wire"] = [
        PortObservation(port=8790, listening=True, owner=OWNER_FOREIGN, pid=555)
    ]
    world.observe(entry)
    events = world.events()
    assert [e["event"] for e in events] == [EVENT_PORT_CONFLICT]
    assert events[0]["conflicts"] == [{"port": 8790, "owner": OWNER_FOREIGN, "pid": 555}]
    assert "不会终止" in events[0]["reason"]


def test_own_child_residual_pid_is_conflict_but_healthy_self_is_not() -> None:
    world = _World()
    entry = _entry(pid=101, origin="spawned", adopted_peer=False)
    # 健康本尊在听（pid 相符 + 存活）→ 非冲突
    world.ports["wire"] = [
        PortObservation(port=8790, listening=True, owner=OWNER_OWN_CHILD, pid=101)
    ]
    world.observe(entry)
    assert world.events() == []
    # 残留自有进程占口（pid 不符 + 占用者仍存活）→ own_child 冲突，可安全重启
    world.occupants_alive.add(999)
    world.ports["wire"] = [
        PortObservation(port=8790, listening=True, owner=OWNER_OWN_CHILD, pid=999)
    ]
    world.now[0] += 10.0
    world.observe(entry)
    events = world.events()
    assert [e["event"] for e in events] == [EVENT_PORT_CONFLICT]
    assert events[0]["conflicts"] == [{"port": 8790, "owner": OWNER_OWN_CHILD, "pid": 999}]
    assert "安全重启" in events[0]["reason"]


def test_dead_occupant_in_stale_observation_is_not_conflict() -> None:
    """节流复用的旧观测记着已死本尊 pid（pid 不符但占用者已死）→ 不误报冲突，
    交给存活判定发 crash（S3 崩溃 / 恢复回归护栏）。"""
    world = _World()
    entry = _entry(pid=101, origin="spawned", adopted_peer=False)
    world.occupants_alive.clear()  # 占用者 999 已死
    world.ports["wire"] = [
        PortObservation(port=8790, listening=True, owner=OWNER_OWN_CHILD, pid=999)
    ]
    world.alive.clear()  # 本模块也死了
    world.observe(entry)
    assert world.kinds() == [EVENT_CRASH]


def test_adopted_peer_residual_pid_is_conflict_report_only() -> None:
    world = _World()
    entry = _entry()
    world.occupants_alive.add(777)
    world.ports["wire"] = [
        PortObservation(port=8790, listening=True, owner=OWNER_ADOPTED_PEER, pid=777)
    ]
    world.observe(entry)
    events = world.events()
    assert [e["event"] for e in events] == [EVENT_PORT_CONFLICT]
    assert events[0]["conflicts"][0]["owner"] == OWNER_ADOPTED_PEER
    assert "不会终止" in events[0]["reason"]


def test_pid_none_preserves_legacy_healthy_self_no_conflict() -> None:
    """旧观测口径（无 pid）：own / adopted 在听即健康本尊，不误报冲突（兼容 QUOT-556）。"""
    world = _World()
    entry = _entry()
    world.ports["wire"] = [
        PortObservation(port=8790, listening=True, owner=OWNER_OWN_CHILD),
        PortObservation(port=8791, listening=True, owner=OWNER_ADOPTED_PEER),
    ]
    world.observe(entry)
    assert world.events() == []


def test_port_conflicts_accessor_matches_event_detail() -> None:
    world = _World()
    entry = _entry()
    world.ports["wire"] = [
        PortObservation(port=8790, listening=True, owner=OWNER_FOREIGN, pid=555)
    ]
    world.observe(entry)
    conflicts = world.monitor.port_conflicts(entry)
    assert [(c.port, c.owner, c.pid) for c in conflicts] == [(8790, OWNER_FOREIGN, 555)]


# ------------------------------------------------------- S4 安全处置联动（QUOT-557）


class _ConflictRestarter:
    """S4 端口冲突处置通道假件：记录调用，绝不真起进程。"""

    def __init__(self, outcome: RestartOutcome | None = None) -> None:
        self.calls: list[tuple[str, int]] = []
        self.outcome = outcome or RestartOutcome(ok=True, pid=201)

    def __call__(self, module: str, pid: int) -> RestartOutcome:
        self.calls.append((module, pid))
        return self.outcome


def _heal_conflicts(
    tmp_path: Path,
    observations: list[PortObservation],
    conflict_restarter: _ConflictRestarter,
) -> tuple[Ledger, SelfHealEngine, HealthEventBus]:
    ledger = Ledger(tmp_path / "supervisor" / "ledger.json")
    ledger.ensure("wire", ports=(8790, 8791))
    ledger.update("wire", pid=101, state="running", origin="spawned", adopted_peer=False)
    bus = HealthEventBus()
    restarter = _ScriptedRestarter(spawn_ok=True)
    restarter.alive.add(101)
    engine = SelfHealEngine(
        ledger,
        restarter,
        sleeper=lambda _seconds: None,
        backoff=(0.0, 0.0, 0.0),
        event_bus=bus,
        port_inspector=lambda entries: {"wire": observations},
        port_conflict_restarter=conflict_restarter,
        is_pid_alive=lambda _pid: True,  # 占用者均存活：本组聚焦处置路由（own vs 外来）
    )
    return ledger, engine, bus


def test_own_child_conflict_triggers_safe_restart(tmp_path: Path) -> None:
    """own_child 残留占口 → 发冲突事件 + 调处置通道安全重启（只对自有占用者）。"""
    restarter = _ConflictRestarter()
    obs = [PortObservation(port=8790, listening=True, owner=OWNER_OWN_CHILD, pid=999)]
    _ledger, engine, bus = _heal_conflicts(tmp_path, obs, restarter)

    engine.tick()

    assert restarter.calls == [("wire", 999)]
    events = bus.since(0)
    assert [e.event for e in events] == [EVENT_PORT_CONFLICT]
    assert events[0].conflicts == ({"port": 8790, "owner": OWNER_OWN_CHILD, "pid": 999},)


def test_foreign_conflict_never_calls_restarter(tmp_path: Path) -> None:
    """外来占口 → 只发冲突事件供弹窗，绝不自动处置（DEC-014 / QUOT-545 红线）。"""
    restarter = _ConflictRestarter()
    obs = [PortObservation(port=8790, listening=True, owner=OWNER_FOREIGN, pid=555)]
    _ledger, engine, bus = _heal_conflicts(tmp_path, obs, restarter)

    engine.tick()
    engine.tick()

    assert restarter.calls == [], "外来冲突被自动处置了（红线）"
    events = bus.since(0)
    assert [e.event for e in events] == [EVENT_PORT_CONFLICT]
    assert events[0].conflicts[0]["owner"] == OWNER_FOREIGN


def test_adopted_peer_conflict_never_calls_restarter(tmp_path: Path) -> None:
    """adopted 占口 → 只发冲突事件供弹窗，绝不自动处置（QUOT-545 adopt-only 红线）。"""
    restarter = _ConflictRestarter()
    obs = [PortObservation(port=8790, listening=True, owner=OWNER_ADOPTED_PEER, pid=777)]
    _ledger, engine, bus = _heal_conflicts(tmp_path, obs, restarter)

    engine.tick()

    assert restarter.calls == [], "adopted 冲突被自动处置了（红线）"
    assert [e.event for e in bus.since(0)] == [EVENT_PORT_CONFLICT]