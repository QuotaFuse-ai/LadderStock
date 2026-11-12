"""阶段级健康观测（QUOT-562 / 563）：采集 → 入库 → 辩论阶段的 StageSample 发射。

schema 单一来源在 `core/stage_events.py`（B2 直接 import）；本模块只负责
**观测与发射**：

- 探针全注入（`probes: stage → Callable[[], StageSample]`，生产实现由 web
  装配侧组装，见 `web/supervisor_api.py`），本模块零 I/O——不联网、不读库、
  绝不触发任何采集动作（DEC-006：只读暴露阶段状态）；
- **只发跃迁，不刷屏**：同一阶段的 (health, reason) 不变就不再发；首次观测
  发注册表全量（前端拿到完整链路才能渲染粒子流向），之后每次变化各发一条；
- 探针缺失 / 抛异常按 `down` + 兜底白话如实上报（不编数、不伪造 ok）；
- 探测按 `probe_interval` 节流（DuckDB 只读探测有成本，存活巡检每 tick 的
  节奏不适合这里），节流窗口内 `observe()` 直接复用上一轮结果。

事件经 `HealthEventBus.publish_stage()` 进 S3 既有环形缓冲，与 peer 级事件
共用 `GET /api/supervisor/events?since=<seq>` 轮询；快照经 `snapshot()`
随 `GET /api/supervisor/state` 的 `stages` 字段整链路输出。
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from typing import Any

from ladderstock.core.stage_events import (
    HEALTH_DOWN,
    REASON_PROBE_FAILED,
    REASON_PROBE_MISSING,
    STAGE_ORDER,
    StageSample,
    spec_of,
    stage_as_of,
)
from ladderstock.supervisor.health_events import HealthEventBus

_log = logging.getLogger(__name__)

#: 阶段探测节流：探针要读水位 / 探历史库（DuckDB 只读连接有成本），
#: 比 S3 端口扫描（5s）更慢的节奏，30s 足够前端粒子的「新鲜」观感。
DEFAULT_STAGE_PROBE_INTERVAL = 30.0

#: 还没跑过第一轮观测时的快照占位（down = 未跑，schema 口径如实标注）
REASON_NOT_OBSERVED = "还没有观测到这一阶段的状态（等待第一轮巡检）"


class StageHealthMonitor:
    """把注册表探针翻译成 stage 事件（只发跃迁）。全部依赖注入，测试零 I/O。

    `probes` 缺哪个阶段，那个阶段就如实报 `down`（REASON_PROBE_MISSING）——
    不允许「没接入就装作正常」。
    """

    def __init__(
        self,
        bus: HealthEventBus,
        *,
        probes: Mapping[str, Callable[[], StageSample]] | None = None,
        clock: Callable[[], float] = time.time,
        probe_interval: float = DEFAULT_STAGE_PROBE_INTERVAL,
    ) -> None:
        self._bus = bus
        self._probes: dict[str, Callable[[], StageSample]] = dict(probes or {})
        self._clock = clock
        self._probe_interval = float(probe_interval)
        #: stage → 最近一次 (health, reason)：跃迁比对的依据
        self._prev: dict[str, tuple[str, str]] = {}
        #: stage → 最近一次样本（快照复用，不重探）
        self._latest: dict[str, StageSample] = {}
        self._checked_at: float | None = None

    # ------------------------------------------------------------------ 观测

    def observe(self) -> None:
        """一轮全阶段观测（节流）：逐阶段探测、跃迁发布。单阶段异常吞掉。

        节流窗口内直接返回——`_latest` 仍保有上一轮结果，快照照常可用。
        """
        now = self._clock()
        if (
            self._checked_at is not None
            and now - self._checked_at < self._probe_interval
        ):
            return
        self._checked_at = now
        for stage in STAGE_ORDER:
            try:
                sample = self._probe(stage)
            except Exception:  # noqa: BLE001 - 探针失败如实报 down，不拖垮其余阶段
                _log.exception("阶段 %s 观测异常（按 down 兜底上报）", stage)
                sample = StageSample.of(
                    stage, health=HEALTH_DOWN, reason=REASON_PROBE_FAILED
                )
            self._publish_if_changed(sample)

    def _probe(self, stage: str) -> StageSample:
        probe = self._probes.get(stage)
        if probe is None:
            return StageSample.of(
                stage, health=HEALTH_DOWN, reason=REASON_PROBE_MISSING
            )
        return probe()

    def _publish_if_changed(self, sample: StageSample) -> None:
        key = (sample.health, sample.reason)
        prev = self._prev.get(sample.stage)
        self._prev[sample.stage] = key
        self._latest[sample.stage] = sample
        if prev == key:
            return  # 同一结论：只发跃迁，不刷屏
        self._bus.publish_stage(sample)

    # ------------------------------------------------------------------ 快照

    def snapshot(self) -> list[dict[str, Any]]:
        """当前整链路快照（`/state` 的 `stages`）：全部注册阶段输出，未观测的补占位。

        占位样本按 schema 的 down=「未跑」口径给 REASON_NOT_OBSERVED；
        `as_of` 用占位生成时刻，绝不复用旧样本冒充新鲜观测。
        """
        rows: list[dict[str, Any]] = []
        for stage in STAGE_ORDER:
            sample = self._latest.get(stage)
            if sample is None:
                spec = spec_of(stage)
                sample = StageSample(
                    module=spec.module if spec else "",
                    module_class=spec.module_class if spec else "",
                    stage=stage,
                    health=HEALTH_DOWN,
                    reason=REASON_NOT_OBSERVED,
                    as_of=stage_as_of(),
                    phase=spec.phase if spec else "",
                )
            rows.append(sample.to_dict())
        return rows


__all__ = [
    "DEFAULT_STAGE_PROBE_INTERVAL",
    "REASON_NOT_OBSERVED",
    "StageHealthMonitor",
]
