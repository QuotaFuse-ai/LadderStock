"""采集 / 入库 / 辩论的阶段级健康事件 schema（QUOT-562 / QUOT-563）：B2 与 S3 通道的单一来源。

统一 schema（派单契约，认知层 B2 直接 import 复用，绝不各写一套）::

    {
      "module": "collect" | "ingest" | "debate",  # 阶段归属的管线模块
      "module_class": "data",              # data=数据管线(蓝)；cog=认知层(紫，B2 用)
      "stage": "collect_script",           # 阶段标识
      "health": "ok" | "warn" | "down",    # ok=正常/成功；warn=降级/陈旧；down=失败/未跑
      "reason": "白话原因（普通人能懂）",   # 不打印 argv / 连接串 / 技术栈
      "as_of": "ISO8601（本机时区）",
      "phase": "1"                         # 串行/并行分组或序号，供前端编排粒子流向
    }

阶段清单（`STAGE_REGISTRY` 是唯一真源）：

- `collect`（采集，4 阶段）：`collect_script`（链路就绪）→ `collect_process`
  （peer 采集进程存活）→ `collect_store`（本机采集库有当日记录）→
  `collect_success`（本次采集成功产出）；
- `ingest`（入库，3 阶段）：`ingest_watermark`（sync 水位）→ `ingest_persist`
  （落库持久化）→ `ingest_idempotent`（幂等校验）；
- `debate`（认知层辩论，3 阶段，QUOT-563，module_class=cog）：`debate_speak`
  （辩论角色发言）→ `debate_clash`（交锋反驳）→ `debate_converge`（收敛结论）。
  观测对象是 StockSenate 最近一次辩论的落盘快照（只读——绝不触发辩论、
  绝不编造进度，缺失/陈旧如实降级）。

口径红线（DEC-006 / 派单 §4）：

- 本模块**只定义 schema 与阶段清单**，不做任何采集、不联网、不读库——
  观测实现见 `supervisor/stage_monitor.py`（探针全注入），事件通道复用
  `supervisor/health_events.py` 的 `HealthEventBus`（`event=stage`）；
- **不编数**：降级 / 缺失如实标 `warn` / `down`，只有探针真的证明成功才 `ok`，
  绝不拿旧数据顶替当日、绝不回落演示数据；
- `health` 三值是**本 schema 的语义**，与账本 health 三值（ok/restarting/
  needs_human）是两套口径、同一字符串空间，消费方按 `event` 分流，不混用。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime

# --------------------------------------------------------------------------- #
# 模块与分类
# --------------------------------------------------------------------------- #

#: 阶段归属的管线模块（派单契约：module ∈ collect / ingest / debate）
MODULE_COLLECT = "collect"
MODULE_INGEST = "ingest"
MODULE_DEBATE = "debate"

MODULES: tuple[str, ...] = (MODULE_COLLECT, MODULE_INGEST, MODULE_DEBATE)

#: 模块分类：data = 数据管线（蓝）；cog = 认知层（紫，B2 的范畴）。
#: 本单（数据管线）恒为 data；B2 复用同一 schema 时填 cog，不改 schema。
MODULE_CLASS_DATA = "data"
MODULE_CLASS_COG = "cog"

MODULE_CLASSES: tuple[str, ...] = (MODULE_CLASS_DATA, MODULE_CLASS_COG)

# --------------------------------------------------------------------------- #
# 健康三值
# --------------------------------------------------------------------------- #

#: schema 自己的 health 三值（与账本 health 同名不同义，见模块 docstring）
HEALTH_OK = "ok"
HEALTH_WARN = "warn"
HEALTH_DOWN = "down"

HEALTH_VALUES: tuple[str, ...] = (HEALTH_OK, HEALTH_WARN, HEALTH_DOWN)

# --------------------------------------------------------------------------- #
# 阶段标识
# --------------------------------------------------------------------------- #

STAGE_COLLECT_SCRIPT = "collect_script"
STAGE_COLLECT_PROCESS = "collect_process"
STAGE_COLLECT_STORE = "collect_store"
STAGE_COLLECT_SUCCESS = "collect_success"
STAGE_INGEST_WATERMARK = "ingest_watermark"
STAGE_INGEST_PERSIST = "ingest_persist"
STAGE_INGEST_IDEMPOTENT = "ingest_idempotent"
STAGE_DEBATE_SPEAK = "debate_speak"
STAGE_DEBATE_CLASH = "debate_clash"
STAGE_DEBATE_CONVERGE = "debate_converge"


@dataclass(frozen=True)
class StageSpec:
    """一个阶段的静态口径：归属模块 / 分类 / 粒子序号 / 人话说明。

    `phase` 是模块内序号字符串（collect 1..4、ingest 1..3、debate 1..3）——
    前端按它编排粒子流向；同一 `phase` 值的阶段互不依赖（采集 / 入库阶段天然
    串行，phase 仍按序号给，认知层并行分组直接用同一字段）。
    """

    stage: str
    module: str
    module_class: str
    phase: str
    summary: str


def _spec(
    stage: str, module: str, module_class: str, phase: str, summary: str
) -> StageSpec:
    return StageSpec(
        stage=stage, module=module, module_class=module_class, phase=phase, summary=summary
    )


#: 阶段注册表唯一真源：顺序即 phase 顺序，也是看板链路展示顺序。
STAGE_REGISTRY: dict[str, StageSpec] = {
    item.stage: item
    for item in (
        _spec(
            STAGE_COLLECT_SCRIPT,
            MODULE_COLLECT,
            MODULE_CLASS_DATA,
            "1",
            "采集链路就绪：同步配置与数据目录齐备",
        ),
        _spec(
            STAGE_COLLECT_PROCESS,
            MODULE_COLLECT,
            MODULE_CLASS_DATA,
            "2",
            "采集 peer 进程存活：行情与资讯 peer 在运行",
        ),
        _spec(
            STAGE_COLLECT_STORE,
            MODULE_COLLECT,
            MODULE_CLASS_DATA,
            "3",
            "本机采集库有当日记录：数据已落到本机库",
        ),
        _spec(
            STAGE_COLLECT_SUCCESS,
            MODULE_COLLECT,
            MODULE_CLASS_DATA,
            "4",
            "本次采集成功产出：最近一次同步成功完成",
        ),
        _spec(
            STAGE_INGEST_WATERMARK,
            MODULE_INGEST,
            MODULE_CLASS_DATA,
            "1",
            "入库水位新鲜：水位已推进到最近记录日",
        ),
        _spec(
            STAGE_INGEST_PERSIST,
            MODULE_INGEST,
            MODULE_CLASS_DATA,
            "2",
            "持久化落库：数据写进本机历史库且可读回",
        ),
        _spec(
            STAGE_INGEST_IDEMPOTENT,
            MODULE_INGEST,
            MODULE_CLASS_DATA,
            "3",
            "幂等校验：重跑不会产生重复行",
        ),
        _spec(
            STAGE_DEBATE_SPEAK,
            MODULE_DEBATE,
            MODULE_CLASS_COG,
            "1",
            "辩论角色发言：最近一次辩论里各辩论角色完成发言",
        ),
        _spec(
            STAGE_DEBATE_CLASH,
            MODULE_DEBATE,
            MODULE_CLASS_COG,
            "2",
            "交锋反驳：辩论角色之间出现真实的反驳往来",
        ),
        _spec(
            STAGE_DEBATE_CONVERGE,
            MODULE_DEBATE,
            MODULE_CLASS_COG,
            "3",
            "收敛结论：辩论在轮次内收敛出结论",
        ),
    )
}

#: 全部阶段（顺序 = phase 顺序）；B2 追加自己的阶段时在本表登记即可。
STAGE_ORDER: tuple[str, ...] = tuple(STAGE_REGISTRY)

#: 模块 → 阶段列表（顺序即 phase 顺序）
MODULE_STAGES: dict[str, tuple[str, ...]] = {
    MODULE_COLLECT: (
        STAGE_COLLECT_SCRIPT,
        STAGE_COLLECT_PROCESS,
        STAGE_COLLECT_STORE,
        STAGE_COLLECT_SUCCESS,
    ),
    MODULE_INGEST: (
        STAGE_INGEST_WATERMARK,
        STAGE_INGEST_PERSIST,
        STAGE_INGEST_IDEMPOTENT,
    ),
    MODULE_DEBATE: (
        STAGE_DEBATE_SPEAK,
        STAGE_DEBATE_CLASH,
        STAGE_DEBATE_CONVERGE,
    ),
}


def stages_of(module: str) -> tuple[str, ...]:
    """某模块的阶段清单（未知模块回空元组，不抛）。"""
    return MODULE_STAGES.get(module, ())


def spec_of(stage: str) -> StageSpec | None:
    """某阶段的静态口径（未登记的阶段回 None；B2 自有阶段查不到即未知）。"""
    return STAGE_REGISTRY.get(stage)


# --------------------------------------------------------------------------- #
# 事件样本（schema 的运行时载体）
# --------------------------------------------------------------------------- #


def stage_as_of() -> str:
    """阶段观测时刻：ISO8601 本���时区（派单契约的 `as_of` 口径）。

    与 S3 既有事件（bus 内部用 UTC `now_iso()`）并存不冲突：总线按事件类型
    分流，stage 事件额外带本机时区的 `as_of`，前端展示时区可预期。
    """
    return datetime.now().astimezone().isoformat(timespec="seconds")


#: 兜底白话原因（探针缺失 / 探测失败的如实标注，绝不伪造成 ok）
REASON_PROBE_MISSING = "该阶段的观测还没有接入，暂时看不到状态"
REASON_PROBE_FAILED = "该阶段的检查没有跑通，暂时看不到状态"


@dataclass(frozen=True)
class StageSample:
    """一次阶段观测的结论：schema 七字段全量承载（B2 直接消费的对象）。

    `reason` 必须是普通人能懂的白话（不打印 argv / 连接串 / 技术栈）；
    未登记阶段（B2 自有）`module_class` / `phase` 允许为空串，注册表阶段
    一律经 `of()` 工厂补齐。
    """

    module: str
    module_class: str
    stage: str
    health: str
    reason: str
    as_of: str
    phase: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def of(
        cls,
        stage: str,
        *,
        health: str,
        reason: str,
        as_of: str | None = None,
        module_class: str | None = None,
        phase: str | None = None,
        module: str | None = None,
    ) -> StageSample:
        """按注册表口径构造样本；未登记阶段用调用方给的值兜底。"""
        spec = spec_of(stage)
        return cls(
            module=module or (spec.module if spec else ""),
            module_class=module_class or (spec.module_class if spec else ""),
            stage=stage,
            health=health,
            reason=reason,
            as_of=as_of or stage_as_of(),
            phase=phase or (spec.phase if spec else ""),
        )


__all__ = [
    "HEALTH_DOWN",
    "HEALTH_OK",
    "HEALTH_VALUES",
    "HEALTH_WARN",
    "MODULE_CLASS_COG",
    "MODULE_CLASS_DATA",
    "MODULE_CLASSES",
    "MODULE_COLLECT",
    "MODULE_DEBATE",
    "MODULE_INGEST",
    "MODULE_STAGES",
    "MODULES",
    "REASON_PROBE_FAILED",
    "REASON_PROBE_MISSING",
    "STAGE_COLLECT_PROCESS",
    "STAGE_COLLECT_SCRIPT",
    "STAGE_COLLECT_STORE",
    "STAGE_COLLECT_SUCCESS",
    "STAGE_DEBATE_CLASH",
    "STAGE_DEBATE_CONVERGE",
    "STAGE_DEBATE_SPEAK",
    "STAGE_INGEST_IDEMPOTENT",
    "STAGE_INGEST_PERSIST",
    "STAGE_INGEST_WATERMARK",
    "STAGE_ORDER",
    "STAGE_REGISTRY",
    "StageSample",
    "StageSpec",
    "spec_of",
    "stage_as_of",
    "stages_of",
]
