"""补数任务状态机（F3）。

把"判定 → 采集 → 等落库 → 建宽表 → 复判"包成一个可查询的后台任务，让工具在
超出同步预算后能先把句柄交出去，而不是把对话卡住。

设计取舍：

* **单进程内存态**。任务只活在一轮补数（秒级到几十秒），没有跨重启恢复的必要；
  为此引入持久化只会带来"库里躺着一条永远 running 的僵尸任务"这类新问题。
* **并发封顶 3**。与 TickHub 采集侧 ``--concurrency`` 对齐；再高会同时打同一批
  上游，触发限流后整批一起失败。
* **单种类失败不整单失败**。财报取到了、公告没取到，回答仍然应该给出财报——
  这叫 partial，不是 failed。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

STATUS_QUEUED = "queued"
STATUS_COLLECTING = "collecting"
STATUS_INGESTING = "ingesting"
STATUS_BUILDING = "building"
STATUS_READY = "ready"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"

FINAL_STATUSES = (STATUS_READY, STATUS_PARTIAL, STATUS_FAILED)

MAX_CONCURRENT = 3
MAX_KEPT = 64
#: 任务记录在内存里保留的秒数（超过就清掉，避免跑一天攒出一堆僵尸）
KEEP_SECONDS = 30 * 60


class TaskManager:
    def __init__(self, max_concurrent: int = MAX_CONCURRENT) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[str, dict[str, Any]] = {}
        self._semaphore = threading.Semaphore(max(1, int(max_concurrent)))
        self._created: dict[str, float] = {}

    # ---------------------------------------------------------------- 查询
    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            record = self._tasks.get(task_id)
            return dict(record) if record else None

    def list_active(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                dict(r) for r in self._tasks.values() if r.get("status") not in FINAL_STATUSES
            ]

    # ---------------------------------------------------------------- 提交
    def submit(
        self,
        symbol: str,
        kinds: list[str],
        *,
        budget_ms: int = 15000,
        priority: int = 0,
    ) -> dict[str, Any]:
        task_id = f"ens_{symbol}_{uuid.uuid4().hex[:8]}"
        # 记录里的阶段要和真正会请求的种类对齐：财报会自动带上行情依赖
        from ladderstock.data import coverage

        wanted = coverage.expand_kinds(kinds)
        record: dict[str, Any] = {
            "task_id": task_id,
            "symbol": symbol,
            "kinds": list(wanted),
            "status": STATUS_QUEUED,
            "stages": {kind: "pending" for kind in wanted},
            "queued_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "started_at": None,
            "finished_at": None,
            "elapsed_ms": 0,
            "error": None,
            "result": None,
        }
        with self._lock:
            self._prune()
            self._tasks[task_id] = record
            self._created[task_id] = time.monotonic()
        thread = threading.Thread(
            target=self._worker,
            args=(task_id, symbol, list(kinds), int(budget_ms), int(priority)),
            name=f"ensure-data-{task_id}",
            daemon=True,
        )
        thread.start()
        return dict(record)

    # ---------------------------------------------------------------- 取消
    def cancel(self, task_id: str) -> bool:
        with self._lock:
            record = self._tasks.get(task_id)
            if not record:
                return False
            if record.get("status") in FINAL_STATUSES:
                return False
            record["status"] = STATUS_FAILED
            record["error"] = "已取消"
            record["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
            return True

    # ---------------------------------------------------------------- 内部
    def _prune(self) -> None:
        now = time.monotonic()
        expired = [
            tid for tid, created in self._created.items() if now - created > KEEP_SECONDS
        ]
        for tid in expired:
            self._created.pop(tid, None)
            self._tasks.pop(tid, None)
        while len(self._tasks) > MAX_KEPT:
            oldest = min(self._created, key=self._created.get, default=None)
            if oldest is None:
                break
            self._created.pop(oldest, None)
            self._tasks.pop(oldest, None)

    def _update(self, task_id: str, **fields: Any) -> None:
        with self._lock:
            record = self._tasks.get(task_id)
            if record:
                record.update(fields)

    def _set_stage(self, task_id: str, kind: str, value: str) -> None:
        with self._lock:
            record = self._tasks.get(task_id)
            if record:
                record["stages"][kind] = value

    def _worker(
        self, task_id: str, symbol: str, kinds: list[str], budget_ms: int, priority: int
    ) -> None:
        started = time.monotonic()
        with self._semaphore:
            self._update(
                task_id,
                status=STATUS_COLLECTING,
                started_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            )
            try:
                payload = self._run(task_id, symbol, kinds)
            except Exception as exc:  # noqa: BLE001 - 任何失败都要落到任务记录上
                logger.warning("ensure_data_failed task=%s detail=%s", task_id, exc)
                self._update(
                    task_id,
                    status=STATUS_FAILED,
                    error=f"{type(exc).__name__}: {exc}",
                    finished_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                )
                return

            metadata = payload.get("metadata") or {}
            ready = metadata.get("ready") or []
            missing = metadata.get("missing") or []
            status = STATUS_READY if not missing else (STATUS_PARTIAL if ready else STATUS_FAILED)
            self._update(
                task_id,
                status=status,
                result=payload,
                error=None if status == STATUS_READY else f"仍缺：{', '.join(missing)}",
                finished_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                elapsed_ms=int((time.monotonic() - started) * 1000),
            )

    def _run(
        self, task_id: str, symbol: str, kinds: list[str]
    ) -> dict[str, Any]:
        from ladderstock.data import collector, coverage

        wanted = coverage.expand_kinds(kinds)
        before = coverage.check_coverage(symbol, kinds=wanted)

        to_fetch = [
            kind
            for kind in wanted
            if before["kinds"].get(kind, {}).get("state") != coverage.STATE_READY
        ]

        # 只有明细没有宽表的情况，不必重新采集，直接进 building
        need_wide = any(
            info.get("reason") == coverage.REASON_NO_WIDE_ROW
            for info in before["kinds"].values()
        )

        errors: list[str] = []
        collected: list[str] = []
        for kind in to_fetch:
            if kind == coverage.KIND_FINANCIAL and need_wide:
                self._set_stage(task_id, kind, "skip_collect")
                continue
            try:
                self._set_stage(task_id, kind, "collecting")
                result = collector.collect(kind, [symbol])
                collected.append(kind)
                logger.info(
                    "ensure_data_collected task=%s kind=%s fetched=%s inserted=%s",
                    task_id,
                    kind,
                    result.get("fetched"),
                    result.get("inserted"),
                )
                if not result.get("inserted") and kind == coverage.KIND_FINANCIAL and not need_wide:
                    # 一条新行都没有，说明上游这次确实没给新数据
                    errors.append(f"{kind}: 上游未返回新记录")
            except collector.CollectError as exc:
                errors.append(f"{kind}: {exc}")
                self._set_stage(task_id, kind, "failed")
                continue
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{kind}: {type(exc).__name__}: {exc}")
                self._set_stage(task_id, kind, "failed")
                continue

            self._set_stage(task_id, kind, "ingesting")

        # 等落库：只等真正采集过的那几类
        self._update(task_id, status=STATUS_INGESTING)
        for kind in collected:
            try:
                waited = collector.wait_rows(symbol, kind, seconds=min(25.0, 8.0 + len(collected) * 4.0))
                if waited.get("state") != coverage.STATE_READY and waited.get("timed_out"):
                    logger.info("ensure_data_wait_timeout task=%s kind=%s", task_id, kind)
            except Exception as exc:  # noqa: BLE001
                logger.warning("ensure_data_wait_error task=%s kind=%s detail=%s", task_id, kind, exc)

        # 建宽表
        financial_state = None
        try:
            after_collect = coverage.check_coverage(symbol, kinds=[coverage.KIND_FINANCIAL])
            financial_state = after_collect["kinds"].get(coverage.KIND_FINANCIAL)
        except coverage.CoverageUnavailable as exc:
            errors.append(f"覆盖复查失败：{exc}")

        should_build = need_wide or (
            financial_state
            and financial_state.get("state") == coverage.STATE_STALE
            and financial_state.get("reason") == coverage.REASON_NO_WIDE_ROW
        )
        if should_build:
            self._update(task_id, status=STATUS_BUILDING)
            self._set_stage(task_id, coverage.KIND_FINANCIAL, "building")
            try:
                collector.build_wide(symbol)
            except collector.CollectError as exc:
                errors.append(f"build: {exc}")
                self._set_stage(task_id, coverage.KIND_FINANCIAL, "failed")

        final = coverage.check_coverage(symbol, kinds=wanted)
        for kind in wanted:
            state = final["kinds"].get(kind, {}).get("state")
            if state == coverage.STATE_READY:
                self._set_stage(task_id, kind, "done")
            elif self._tasks.get(task_id, {}).get("stages", {}).get(kind) != "failed":
                self._set_stage(task_id, kind, "missing")

        return {
            "symbol": symbol,
            "metadata": final,
            "errors": errors,
            "collected": collected,
        }


_manager: TaskManager | None = None
_manager_lock = threading.Lock()


def get_manager() -> TaskManager:
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = TaskManager()
    return _manager


def submit(symbol: str, kinds: list[str], *, budget_ms: int = 15000) -> dict[str, Any]:
    return get_manager().submit(symbol, kinds, budget_ms=budget_ms)


def get(task_id: str) -> dict[str, Any] | None:
    return get_manager().get(task_id)


def cancel(task_id: str) -> bool:
    return get_manager().cancel(task_id)
