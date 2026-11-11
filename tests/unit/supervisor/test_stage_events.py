"""采集 → 入库七阶段 stage 事件（QUOT-562）：schema 契约 + 跃迁观测 + 自愈联动。

钉住：

| 场景 | 期望 |
|---|---|
| schema | 七阶段注册表唯一真源；collect 1..4 / ingest 1..3；module_class 恒 data |
| 样本 | `StageSample.of` 按注册表补齐口径；to_dict 七字段；as_of 本机时区 |
| 总线 | publish_stage → event=stage、state=stage、四负载字段非空；peer 级事件不带 |
| 观测 | 首次发全量七条；之后只发跃迁（health/reason 变才发）；节流窗口内不重探 |
| 兜底 | 探针缺失 → down+「未接入」；探针异常 → down+「没跑通」；互不拖垮 |
| 快照 | 未观测占位 down；观测后回最近结论；snapshot 不触发重探 |
| 自愈 | tick 驱动阶段观测；观测异常吞掉；stage_monitor=None 快照无 stages |

全部假件（注入探针 / 时钟），不起真进程、不读真库。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ladderstock.core.stage_events import (
    HEALTH_DOWN,
    HEALTH_OK,
    HEALTH_VALUES,
    HEALTH_WARN,
    MODULE_CLASS_DATA,
    REASON_PROBE_FAILED,
    REASON_PROBE_MISSING,
    STAGE_COLLECT_PROCESS,
    STAGE_COLLECT_SCRIPT,
    STAGE_COLLECT_STORE,
    STAGE_COLLECT_SUCCESS,
    STAGE_INGEST_IDEMPOTENT,
    STAGE_INGEST_PERSIST,
    STAGE_INGEST_WATERMARK,
    STAGE_ORDER,
    STAGE_REGISTRY,
    StageSample,
)
from ladderstock.supervisor.health_events import EVENT_CRASH, EVENT_STAGE, HealthEventBus
from ladderstock.supervisor.ledger import Ledger
from ladderstock.supervisor.selfheal import RestartOutcome, SelfHealEngine
from ladderstock.supervisor.stage_monitor import (
    DEFAULT_STAGE_PROBE_INTERVAL,
    REASON_NOT_OBSERVED,
    StageHealthMonitor,
)

SEVEN_STAGES: tuple[str, ...] = (
    STAGE_COLLECT_SCRIPT,
    STAGE_COLLECT_PROCESS,
    STAGE_COLLECT_STORE,
    STAGE_COLLECT_SUCCESS,
    STAGE_INGEST_WATERMARK,
    STAGE_INGEST_PERSIST,
    STAGE_INGEST_IDEMPOTENT,
)

SCHEMA_KEYS = {
    "module",
    "module_class",
    "stage",
    "health",
    "reason",
    "as_of",
    "phase",
}


# -------------------------------------------------------------------- schema


def test_registry_covers_seven_stages_with_module_phases() -> None:
    assert STAGE_ORDER == SEVEN_STAGES
    assert set(STAGE_REGISTRY) == set(SEVEN_STAGES)
    phases = {stage: STAGE_REGISTRY[stage].phase for stage in SEVEN_STAGES}
    assert phases == {
        STAGE_COLLECT_SCRIPT: "1",
        STAGE_COLLECT_PROCESS: "2",
        STAGE_COLLECT_STORE: "3",
        STAGE_COLLECT_SUCCESS: "4",
        STAGE_INGEST_WATERMARK: "1",
        STAGE_INGEST_PERSIST: "2",
        STAGE_INGEST_IDEMPOTENT: "3",
    }
    for stage, spec in STAGE_REGISTRY.items():
        assert spec.module == ("collect" if stage.startswith("collect_") else "ingest")
        assert spec.module_class == MODULE_CLASS_DATA, "数据管线七阶段分类恒为 data"
    assert HEALTH_VALUES == (HEALTH_OK, HEALTH_WARN, HEALTH_DOWN)


def test_stage_sample_of_fills_registry_fields_and_dict_shape() -> None:
    sample = StageSample.of(STAGE_COLLECT_STORE, health=HEALTH_WARN, reason="数据有点旧")

    assert sample.module == "collect"
    assert sample.module_class == MODULE_CLASS_DATA
    assert sample.phase == "3"
    assert sample.as_of
    assert set(sample.to_dict()) == SCHEMA_KEYS
    # as_of 是本机时区 ISO8601（带 UTC 偏移，普通人可读）
    assert sample.as_of.startswith("20") and (
        "+" in sample.as_of[10:] or sample.as_of.endswith("Z")
    )


def test_stage_sample_unknown_stage_takes_caller_fallback() -> None:
    sample = StageSample.of(
        "cog_outline",
        health=HEALTH_OK,
        reason="就绪",
        module="cog",
        module_class="cog",
        phase="2",
    )

    assert sample.module == "cog"
    assert sample.module_class == "cog"
    assert sample.phase == "2"
    assert sample.stage == "cog_outline"


# -------------------------------------------------------------------- 总线


def test_publish_stage_maps_schema_onto_bus_fields() -> None:
    bus = HealthEventBus()
    sample = StageSample.of(STAGE_INGEST_WATERMARK, health=HEALTH_WARN, reason="水位落后了")

    event = bus.publish_stage(sample)
    payload = event.to_dict()

    assert event.seq == 1
    assert payload["event"] == EVENT_STAGE
    assert payload["module"] == "ingest"
    assert payload["health"] == HEALTH_WARN
    assert payload["reason"] == "水位落后了"
    assert payload["state"] == STAGE_INGEST_WATERMARK
    assert payload["stage"] == STAGE_INGEST_WATERMARK
    assert payload["module_class"] == MODULE_CLASS_DATA
    assert payload["phase"] == "1"
    assert payload["as_of"] == sample.as_of
    assert bus.latest_seq == 1


def test_peer_events_do_not_carry_stage_payload() -> None:
    bus = HealthEventBus()
    bus.publish(module="wire", event=EVENT_CRASH, health="ok", state="down", reason="进程退出")
    StageSample.of(STAGE_COLLECT_SCRIPT, health=HEALTH_OK, reason="就绪")
    bus.publish_stage(
        StageSample.of(STAGE_COLLECT_SCRIPT, health=HEALTH_OK, reason="采集链路就绪")
    )

    crash, stage = bus.since(0)
    assert crash.event == EVENT_CRASH
    assert crash.to_dict()["stage"] is None
    assert crash.to_dict()["module_class"] is None
    assert crash.to_dict()["phase"] is None
    assert crash.to_dict()["as_of"] is None
    assert stage.event == EVENT_STAGE and stage.to_dict()["stage"] is not None
    assert [item.seq for item in bus.since(0, module="collect")] == [2]


# -------------------------------------------------------------------- 观测


class _World:
    """七探针全可控的观测世界：结论表 + 调用计数 + 时钟。"""

    def __init__(
        self,
        *,
        probes: dict[str, Any] | None = None,
        probe_interval: float = DEFAULT_STAGE_PROBE_INTERVAL,
    ) -> None:
        self.bus = HealthEventBus()
        self.calls: list[str] = []
        self.conclusions: dict[str, StageSample] = {}
        self.now = 100.0
        if probes is None:
            probes = {stage: self._make(stage) for stage in SEVEN_STAGES}
        self.monitor = StageHealthMonitor(
            self.bus,
            probes=probes,
            clock=lambda: self.now,
            probe_interval=probe_interval,
        )

    def _make(self, stage: str):
        def probe() -> StageSample:
            self.calls.append(stage)
            return self.conclusions.get(
                stage, StageSample.of(stage, health=HEALTH_OK, reason="一切正常")
            )

        return probe

    def set(self, stage: str, health: str, reason: str = "结论变化") -> None:
        self.conclusions[stage] = StageSample.of(stage, health=health, reason=reason)

    def observe(self) -> None:
        self.monitor.observe()

    def stage_events(self) -> list[dict[str, Any]]:
        return [
            item.to_dict() for item in self.bus.since(0) if item.event == EVENT_STAGE
        ]

    def snapshot(self) -> list[dict[str, Any]]:
        return self.monitor.snapshot()


def test_first_observation_publishes_all_seven_in_order() -> None:
    world = _World()

    world.observe()

    events = world.stage_events()
    assert [item["state"] for item in events] == list(SEVEN_STAGES)
    for item in events:
        assert set(item) >= SCHEMA_KEYS, "stage 事件必须带全 schema 字段"
        assert item["health"] == HEALTH_OK
        assert item["as_of"]


def test_only_transitions_are_published_after_first_round() -> None:
    world = _World()
    world.observe()

    world.now += DEFAULT_STAGE_PROBE_INTERVAL + 1
    world.observe()
    assert len(world.stage_events()) == 7, "结论不变不得刷屏"

    world.now += DEFAULT_STAGE_PROBE_INTERVAL + 1
    world.set(STAGE_COLLECT_STORE, HEALTH_WARN, "本机行情库还没有今天的记录")
    world.observe()
    events = world.stage_events()
    assert len(events) == 8
    assert events[-1]["state"] == STAGE_COLLECT_STORE
    assert events[-1]["health"] == HEALTH_WARN

    # reason 变了也算跃迁（白话要跟着事实走）
    world.now += DEFAULT_STAGE_PROBE_INTERVAL + 1
    world.set(STAGE_COLLECT_STORE, HEALTH_WARN, "本机行情库落后两天了")
    world.observe()
    events = world.stage_events()
    assert len(events) == 9
    assert events[-1]["reason"] == "本机行情库落后两天了"


def test_probe_calls_throttled_within_window() -> None:
    world = _World(probe_interval=30.0)

    world.observe()
    assert len(world.calls) == 7

    world.now += 10
    world.observe()
    assert len(world.calls) == 7, "节流窗口内不得重探"

    world.now += DEFAULT_STAGE_PROBE_INTERVAL
    world.observe()
    assert len(world.calls) == 14, "超过窗口应重新探测"


def test_missing_probes_report_down_not_fake_ok() -> None:
    world = _World(
        probes={
            STAGE_COLLECT_SCRIPT: lambda: StageSample.of(
                STAGE_COLLECT_SCRIPT, health=HEALTH_OK, reason="就绪"
            )
        }
    )  # type: ignore[dict-item]

    world.observe()

    by_stage = {item["state"]: item for item in world.stage_events()}
    assert by_stage[STAGE_COLLECT_SCRIPT]["health"] == HEALTH_OK
    for stage in SEVEN_STAGES[1:]:
        assert by_stage[stage]["health"] == HEALTH_DOWN
        assert by_stage[stage]["reason"] == REASON_PROBE_MISSING


def test_failing_probe_reports_down_without_dragging_others() -> None:
    def boom() -> StageSample:
        raise OSError("水位文件被锁")

    world = _World()
    world.monitor._probes[STAGE_INGEST_WATERMARK] = boom

    world.observe()

    by_stage = {item["state"]: item for item in world.stage_events()}
    assert by_stage[STAGE_INGEST_WATERMARK]["health"] == HEALTH_DOWN
    assert by_stage[STAGE_INGEST_WATERMARK]["reason"] == REASON_PROBE_FAILED
    assert by_stage[STAGE_COLLECT_SCRIPT]["health"] == HEALTH_OK
    assert len(world.stage_events()) == 7, "单探针异常不得吞掉其余阶段的观测"


def test_snapshot_placeholders_then_latest_without_reprobe() -> None:
    world = _World()

    rows = world.snapshot()
    assert [item["stage"] for item in rows] == list(SEVEN_STAGES)
    assert all(item["health"] == HEALTH_DOWN for item in rows)
    assert all(item["reason"] == REASON_NOT_OBSERVED for item in rows)
    for item in rows:
        spec = STAGE_REGISTRY[item["stage"]]
        assert item["module"] == spec.module
        assert item["phase"] == spec.phase
        assert item["as_of"], "占位样本也要有 as_of"

    world.set(STAGE_INGEST_PERSIST, HEALTH_DOWN, "本机历史库还没有数据")
    world.observe()
    world.now += 5
    rows = world.snapshot()
    persist = rows[SEVEN_STAGES.index(STAGE_INGEST_PERSIST)]
    assert persist["health"] == HEALTH_DOWN
    assert persist["reason"] == "本机历史库还没有数据"
    assert len(world.calls) == 7, "snapshot 只读最近结论，不得触发重探"


# ---------------------------------------------------------------- 自愈联动


class _AliveRestarter:
    def is_alive(self, entry: Any) -> bool:
        return True

    def restart(self, module: str) -> RestartOutcome:
        return RestartOutcome(ok=True, pid=999)


class _SilentNotifier:
    def failure(self, **kwargs: Any) -> None:
        return None

    def gave_up(self, **kwargs: Any) -> None:
        return None

    def recovered(self, **kwargs: Any) -> None:
        return None


def _heal_with_stages(
    tmp_path: Path, monitor: StageHealthMonitor, bus: HealthEventBus
) -> SelfHealEngine:
    ledger = Ledger(tmp_path / "supervisor" / "ledger.json")
    return SelfHealEngine(
        ledger,
        _AliveRestarter(),
        _SilentNotifier(),
        clock=lambda: 0.0,
        sleeper=lambda seconds: None,
        event_bus=bus,
        stage_monitor=monitor,
    )


def test_selfheal_tick_drives_stage_observation_and_snapshot(tmp_path: Path) -> None:
    world = _World()
    engine = _heal_with_stages(tmp_path, world.monitor, world.bus)

    engine.tick()

    events = [item for item in world.bus.since(0) if item.event == EVENT_STAGE]
    assert [item.state for item in events] == list(SEVEN_STAGES)
    payload = engine.snapshot()
    assert [item["stage"] for item in payload["stages"]] == list(SEVEN_STAGES)
    assert payload["events_latest"] == 7


def test_selfheal_swallows_stage_observation_failure(tmp_path: Path) -> None:
    world = _World()
    engine = _heal_with_stages(tmp_path, world.monitor, world.bus)

    def boom() -> None:
        raise RuntimeError("阶段观测炸了")

    world.monitor.observe = boom  # type: ignore[method-assign]
    engine.tick()  # 不抛即通过：阶段观测异常绝不拖垮自愈巡检


def test_selfheal_without_stage_monitor_keeps_legacy_snapshot(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "supervisor" / "ledger.json")
    engine = SelfHealEngine(
        ledger,
        _AliveRestarter(),
        _SilentNotifier(),
        clock=lambda: 0.0,
        sleeper=lambda seconds: None,
    )

    assert "stages" not in engine.snapshot()
