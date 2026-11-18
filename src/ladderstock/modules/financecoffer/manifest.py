"""增强包组件清单与体积口径（DEC-086 决策 1 / 3）。

向导必须**先给明细再让用户点确认**：外部依赖是 GB 级的，含糊一句「约几百兆」等于骗人。
两档：

- 核心（PG + Redis + MinIO + API，含 Python 依赖）≈ 1.0GB；
- 加 embedding 运行时（权重 + torch/transformers）≈ 3.8GB。

embedding 不进包时向量检索不可用，**结构化查询照常**——这句话必须原样出现在向导上，
否则用户会以为不勾就等于不装。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Component:
    key: str
    label: str
    size_mb: int
    required: bool
    note: str


#: 组件与体积（MB，**解压后实测值**，2026-11-17 在本机装完实测：
#: pg 848.2 / redis 37.9 / minio 107.7 / api 2.9+依赖 37.4 / embedding 权重 2187.1+依赖 635.4）。
#: 向导必须按磁盘实际占用报，不能按下载包大小报——PG 的 zip 只有 315MB，解开是 848MB；
#: 而「只装源码」的 API 3MB 是假象，它跑起来要 sqlalchemy/psycopg/pgvector 等 37MB 依赖。
COMPONENTS: tuple[Component, ...] = (
    Component(
        "pg", "PostgreSQL + pgvector", 848, True, "源代码只接受 postgresql:// DSN，没有 sqlite 退路"
    ),
    Component(
        "redis",
        "Redis（社区移植版，三端各一份）",
        38,
        True,
        "官方无 Windows 版；三端用同一份 redis.conf",
    ),
    Component("minio", "MinIO 对象存储", 108, True, "报告与导出物料的落点"),
    Component(
        "api",
        "FinanceCoffer API + Python 依赖",
        40,
        True,
        "源码 3MB，另带 sqlalchemy/psycopg/pgvector/redis/httpx/alembic 等约 37MB 运行依赖；"
        "没有依赖 API 起不来，结构化查询也就不可用",
    ),
    Component(
        "embedding",
        "embedding 运行时（bge-m3 权重 + torch/transformers）",
        2822,
        False,
        "不勾则向量检索长期不可用，结构化查询照常可用；勾选后首次加载 30–60s，常驻内存约 +1.5GB；"
        "其中权重 2.14GB、CPU 版 torch 与 transformers 约 635MB",
    ),
)

#: 核心形态（不含 embedding）
CORE_MB = sum(item.size_mb for item in COMPONENTS if item.key != "embedding")
#: 完整形态
FULL_MB = sum(item.size_mb for item in COMPONENTS)


@dataclass(frozen=True)
class BundlePlan:
    """向导拿到的东西：勾了哪些、要多少地方、勾选后果。"""

    with_embedding: bool
    components: tuple[Component, ...]
    total_mb: int
    consequences: tuple[str, ...] = field(default_factory=tuple)

    @property
    def total_gb(self) -> float:
        return round(self.total_mb / 1024, 2)


def plan(with_embedding: bool) -> BundlePlan:
    """`with_embedding` 由用户在向导里勾选（决策 1），不做默认静默选择。"""
    selected = tuple(
        item for item in COMPONENTS if item.required or (with_embedding and item.key == "embedding")
    )
    total = sum(item.size_mb for item in selected)
    consequences: list[str] = []
    if with_embedding:
        consequences.append("含 embedding：向量检索可用；首次加载 30–60 秒，常驻内存约 +1.5GB")
    else:
        consequences.append("不含 embedding：向量检索长期不可用，结构化查询（宽表 / 公告）照常可用")
    consequences.append(f"预计占用磁盘约 {round(total / 1024, 2)} GB（不含数据增长）")
    return BundlePlan(
        with_embedding=with_embedding,
        components=selected,
        total_mb=total,
        consequences=tuple(consequences),
    )


__all__ = ["CORE_MB", "COMPONENTS", "FULL_MB", "BundlePlan", "Component", "plan"]
