"""打开即补取（ensure-on-open，QUOT-555 / DEC-080）：只读 GET 陈旧时经 peer 懒补取。

页面 GET 原本纯只读本机库（`/api/market/theme`、`/api/news`、`/api/pool`、`/api/industry`），
本机数据陈旧/缺失就出 `available=false` + 「先同步再刷新」手动指引。本模块给这些只读 GET
加一层**受控懒补取**：探测到陈旧/缺失时，复用既有同步路径（`cli/sync.py` 的 `sync_one` /
`run_sync`、`news/stream.py` 的 `collect_stream`）经 TickHub/Wire peer 缓存补一轮，补完再读。

四条纪律（与硬红线一一对应）：

1. **DEC-006 合规**：只走已登记 peer 的既有同步链路，不新增任何采集源；数据集未在
   `config/sync.yaml` 登记 → `no_source`，一次外呼都不发（闸门 4：无源不自采）。
2. **免费 peer 读自动、付费同步仍显式**：industry 组只补 `SNAPSHOT_DATASETS` 的
   **base_pool 常规口径**（`codes=None`，与 S1 每日自动同步同范围），**绝不**触发
   `ldst sync industry` 的锚点扩池（DEC-074 的成本非 0 显式动作）。
3. **幂等 + 冷却 + 并发上限**：复用同步路径的水位增量 / `(code, trade_date)` 幂等 upsert /
   QFSD-7001 退避（DEC-048）；本层再加每组冷却（默认 300s）+ 单飞（同组同时只补一轮）+
   历史库写锁短超时（拿不到锁就跳过，不阻塞 GET），避免每次刷新 hammer 上游。
4. **失败如实降级、不回落演示**：补取失败/上游全挂 → `degraded`/`failed`/`empty`，GET 仍按
   本机真实读取出空态；演示态是前端 `?state=` 的事，后端从不返回演示数据（SPEC-HOME-V2 §5.6）。

落库口径与 CLI 完全一致：`sync_one`/`run_sync` 经 `write_dataset()` 写的是 `data_dir()`
（`LDST_DATA_DIR`），不读 `registry.root`（见 `cli/sync.py` 写锁注释）。所以本模块的 `root`
只用于**探测读取**，生产里 `create_app` 的 `data_root` 默认即 `data_dir()`，读写同库；
测试里把 `LDST_DATA_DIR` 与 `data_root` 一起钉到 tmp 即可（与所有既有 sync 测试同一套隔离）。

本模块默认**不接线**：`create_app(open_ensure=None)` 时四个 GET 行为与改造前逐字节一致，
既有契约测试零改动；只有生产入口（`cli/main.py` / `cli/run.py`）显式传入 `OpenEnsure()` 才启用。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from ladderstock.core.ladder import dataset_snapshot
from ladderstock.core.pool import POOL_DATASETS as CORE_POOL_DATASETS
from ladderstock.data import archive
from ladderstock.data.db_lock import writer_lock
from ladderstock.data.provider_registry import ProviderRegistry
from ladderstock.errors import EXIT_DB_BUSY, EXIT_OK, EXIT_PARTIAL, LadderStockError
from ladderstock.industry import snapshot as industry_snapshot
from ladderstock.news import store as news_store

#: 每组补取后的冷却（秒）：这段时间内再打开页面只读本机库，不再触发外呼。
DEFAULT_COOLDOWN_SEC = 300.0

#: 历史库写锁的等待上限（秒）：GET 不该为一个正在跑的同步长时间阻塞，拿不到就跳过本次补取。
LOCK_TIMEOUT_SEC = 2.0

#: 候选池页面输入（`core/pool.py: POOL_DATASETS`）+ 当日实时资金批量分区。
POOL_GROUP_DATASETS: tuple[str, ...] = (*CORE_POOL_DATASETS, "fund_flow_realtime")

#: 产业页面输入（`industry/snapshot.py: SNAPSHOT_DATASETS`），与 DEC-074 同一份清单。
INDUSTRY_GROUP_DATASETS: tuple[str, ...] = tuple(industry_snapshot.SNAPSHOT_DATASETS)

#: 产业 freshness 探针数据集（归属表）：有任意历史记录即视为已覆盖（DEC-074：data_as_of
#: 是分类计入日而非行情日，不按「当日」判陈旧，只按冷启动「有没有」判）。
INDUSTRY_PROBE_DATASET = industry_snapshot.META_PRIMARY_DATASET

GROUPS: tuple[str, ...] = ("theme", "news", "pool", "industry")


@dataclass(frozen=True)
class EnsureResult:
    """一次 ensure 的结论。`triggered` 仅在真正发起补取时为 True。

    `status` 词表：
    - 未触发：`fresh`（本机已新鲜）/ `cooldown`（冷却中）/ `inflight`（同组正在补）/
      `no_source`（未登记，DEC-006 闸门 4）/ `disabled`（未知组）/ `probe_error`（探测读失败）；
    - 已触发：`ok` / `degraded` / `empty` / `skipped` / `failed` / `busy`（写锁被占）。
    """

    group: str
    triggered: bool
    status: str
    detail: str = ""

    def note(self) -> str:
        """面向 `source_detail` 的一句话；未触发时返回空串（不污染既有空态文案）。"""
        if not self.triggered:
            return ""
        return f"打开即补取（{self.group}）：{self.status} — {self.detail}"


def _summary_status(summary: dict[str, Any]) -> str:
    """`run_sync` 汇总 → 单词状态：有失败也有成功是 degraded，全失败是 failed，否则 ok。"""
    failed = int(summary.get("failed") or 0)
    succeeded = sum(int(summary.get(name) or 0) for name in ("ok", "degraded", "empty"))
    if failed and succeeded:
        return "degraded"
    if failed:
        return "failed"
    return "ok"


def _exit_status(exit_code: int) -> str:
    """同步退出码 → 单词状态（0 ok / 8 partial→degraded / 其余 failed）。"""
    if exit_code == EXIT_OK:
        return "ok"
    if exit_code == EXIT_PARTIAL:
        return "degraded"
    return "failed"


class OpenEnsure:
    """四个只读 GET 共用的懒补取闸门：探测 → 冷却/单飞判定 → 复用同步路径补取。

    `registry` 可注入（测试用 FakeTransport，绝不真联网）；缺省按需现建一个
    `ProviderRegistry`（与 `radar.indices_view` 同一范式）。`clock` 可注入以便离线测冷却。
    """

    def __init__(
        self,
        *,
        registry: ProviderRegistry | None = None,
        root: Path | None = None,
        cooldown_sec: float = DEFAULT_COOLDOWN_SEC,
        clock: Any = None,
    ) -> None:
        self._registry = registry
        self.root = Path(root) if root is not None else None
        self.cooldown_sec = float(cooldown_sec)
        self._clock = clock if clock is not None else time.monotonic
        self._lock = threading.Lock()
        self._inflight: set[str] = set()
        self._cooldown_until: dict[str, float] = {}

    # ------------------------------------------------------------------ 入口

    def ensure(self, group: str, *, day: date, end: date | None = None) -> EnsureResult:
        """探测 `group` 是否陈旧/缺失，必要时受控补取一轮；永不向 GET 抛异常。"""
        if group not in GROUPS:
            return EnsureResult(group, False, "disabled", f"未知补取组：{group}")

        now = self._clock()
        with self._lock:
            if group in self._inflight:
                return EnsureResult(group, False, "inflight", "同组补取正在进行，本次只读本机库")
            if now < self._cooldown_until.get(group, 0.0):
                return EnsureResult(group, False, "cooldown", "补取冷却中，本次只读本机库")

        try:
            stale = self._stale(group, day, end)
        except Exception:  # noqa: BLE001 - 探测读失败不该触发外呼，让 GET 自己暴露真实错误
            return EnsureResult(group, False, "probe_error", "本机探测读取失败，未触发补取")
        if not stale:
            return EnsureResult(group, False, "fresh", "本机数据已新鲜，无需补取")

        with self._lock:
            if group in self._inflight:  # 双检：探测期间别的请求可能已抢先单飞
                return EnsureResult(group, False, "inflight", "同组补取正在进行，本次只读本机库")
            self._inflight.add(group)
        try:
            return self._refetch(group, day, end)
        finally:
            with self._lock:
                self._inflight.discard(group)
                # 无论成败都进冷却：上游挂了也不该每次刷新都重打一轮（冷却后才允许重试）。
                self._cooldown_until[group] = self._clock() + self.cooldown_sec

    # ------------------------------------------------------------------ 探测

    def _stale(self, group: str, day: date, end: date | None) -> bool:
        """只读本机库判陈旧/缺失，零外呼。True = 需要补取。"""
        if group == "theme":
            records, _ = dataset_snapshot("theme_news", day, self.root)
            return not records
        if group == "pool":
            records, _ = dataset_snapshot("limit_pool", day, self.root)
            return not records
        if group == "industry":
            return archive.latest_day(INDUSTRY_PROBE_DATASET, root=self.root) is None
        if group == "news":
            conn = news_store.connect(self.root, create=False)
            if conn is None:
                return True
            try:
                return news_store.covering_run(conn, start=day, end=end or day) is None
            finally:
                news_store.close(conn)
        return False

    # ------------------------------------------------------------------ 补取

    def _active_registry(self) -> ProviderRegistry:
        if self._registry is None:
            self._registry = ProviderRegistry(root=self.root)
        return self._registry

    def _refetch(self, group: str, day: date, end: date | None) -> EnsureResult:
        """复用既有同步路径补取；任何异常都收成 failed/busy，绝不冒泡到 GET。"""
        try:
            registry = self._active_registry()
            if group == "news":
                # 资讯落 news.sqlite（独立库），不占历史库写锁。
                return self._refetch_news(registry, day, end)
            with writer_lock(holder=f"ensure-on-open:{group}", timeout=LOCK_TIMEOUT_SEC):
                if group == "theme":
                    return self._refetch_theme(registry)
                if group == "pool":
                    return self._refetch_datasets(registry, "pool", POOL_GROUP_DATASETS)
                if group == "industry":
                    return self._refetch_datasets(registry, "industry", INDUSTRY_GROUP_DATASETS)
        except LadderStockError as exc:
            if exc.code == EXIT_DB_BUSY:
                return EnsureResult(group, True, "busy", "历史库正被另一个同步占用，本次未补取")
            return EnsureResult(group, True, "failed", exc.message)
        except Exception as exc:  # noqa: BLE001 - 补取失败如实降级，不回落演示、不崩 GET
            return EnsureResult(group, True, "failed", str(exc))
        return EnsureResult(group, False, "disabled", f"未知补取组：{group}")

    def _refetch_theme(self, registry: ProviderRegistry) -> EnsureResult:
        from ladderstock.cli import sync as sync_module
        from ladderstock.data import watermark

        if "theme_news" not in registry.datasets:
            return EnsureResult(
                "theme", False, "no_source", "theme_news 未在 config/sync.yaml 登记（无源不自采）"
            )
        store = watermark.WatermarkStore(root=registry.root)
        row = sync_module.sync_one(registry, store, "theme_news")
        return EnsureResult(
            "theme",
            True,
            str(row.get("status") or "failed"),
            str(row.get("detail") or row.get("source") or ""),
        )

    def _refetch_datasets(
        self, registry: ProviderRegistry, group: str, datasets: tuple[str, ...]
    ) -> EnsureResult:
        from ladderstock.cli import sync as sync_module
        from ladderstock.data import watermark

        # 只补已登记的数据集：未登记的链路 registry.chain() 会判配置错误（DEC-006 闸门 4）。
        declared = [dataset for dataset in datasets if dataset in registry.datasets]
        if not declared:
            detail = f"{group} 输入数据集均未在 config/sync.yaml 登记（无源不自采）"
            return EnsureResult(group, False, "no_source", detail)
        store = watermark.WatermarkStore(root=registry.root)
        # codes=None：industry 走 base_pool 常规口径（与 S1 每日自动同范围），不做锚点扩池。
        payload = sync_module.run_sync(registry, store, declared)
        summary = payload["summary"]
        detail = (
            f"{summary.get('ok', 0)} ok / {summary.get('degraded', 0)} degraded / "
            f"{summary.get('empty', 0)} empty / {summary.get('failed', 0)} failed"
            f"（{len(declared)} 个数据集）"
        )
        return EnsureResult(group, True, _summary_status(summary), detail)

    def _refetch_news(
        self, registry: ProviderRegistry, day: date, end: date | None
    ) -> EnsureResult:
        from ladderstock.news import stream as news_stream

        window_end = end or day
        result = news_stream.collect_stream(
            start=day,
            end=window_end,
            registry=registry,
            root=self.root,
            persist=True,
        )
        detail = f"fetched={result.fetched} stored={result.stored}（窗口 {day}→{window_end}）"
        return EnsureResult("news", True, _exit_status(result.exit_code), detail)


__all__ = [
    "DEFAULT_COOLDOWN_SEC",
    "GROUPS",
    "INDUSTRY_GROUP_DATASETS",
    "INDUSTRY_PROBE_DATASET",
    "LOCK_TIMEOUT_SEC",
    "POOL_GROUP_DATASETS",
    "EnsureResult",
    "OpenEnsure",
]