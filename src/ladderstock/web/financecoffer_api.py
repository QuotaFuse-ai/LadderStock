"""`/api/financecoffer/*`：增强包的向导、安装、启停、卸载（DEC-086 决策 1/3/4/5/6）。

三件在服务端完成、页面不重复实现的事：

1. **体积明细**：`GET /api/financecoffer` 直接给两档清单与磁盘余量，页面只负责展示与勾选；
   「确认」是把 `confirm=true` 送到安装接口，少了它一律 `needs_confirmation`，不会动盘。
2. **单一路径**：只有完整包一条状态机（决策 6），没有任何「轻量模式」分支。
3. **数据在安装目录内**：目标固定 `<安装根>/file/modules/financecoffer`；装到系统盘时
   `plan()` 直接拒绝（决策 4），页面把这条错误原样展示，不提供「强行继续」以外的暗示。

写方法共用控制面闸门（回环 + Host + 来源证据），安装/卸载/启停都是真命令、真落盘。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends

from ladderstock.modules.financecoffer import installer, layout, manifest, redis_service, services, state
from ladderstock.web.control import _guard

FINANCECOFFER_PREFIX = "/api/financecoffer"

_jobs: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def _component_rows(with_embedding: bool) -> list[dict[str, Any]]:
    return [
        {
            "key": c.key,
            "label": c.label,
            "size_mb": c.size_mb,
            "required": c.required,
            "note": c.note,
        }
        for c in manifest.plan(with_embedding).components
    ]


def _summary(root: Path | None) -> dict[str, Any]:
    core = manifest.plan(False)
    full = manifest.plan(True)
    installed = state.read_state(root)
    matrix = redis_service.platform_matrix(root)
    doctor = services.doctor(root)
    return {
        "ok": True,
        "module_root": str(layout.module_root(root)),
        "installed": installed.get("state"),
        "state": installed,
        "sizes": {
            "core_mb": core.total_mb,
            "full_mb": full.total_mb,
            "embedding_mb": full.total_mb - core.total_mb,
        },
        "components": {
            "core": _component_rows(False),
            "full": _component_rows(True),
        },
        "consequences": list(core.consequences),
        "redis": matrix,
        "doctor": doctor,
        "job": _job_snapshot(),
    }


def _job_snapshot() -> dict[str, Any] | None:
    with _lock:
        job = _jobs.get("current")
        return dict(job) if job else None


def _start_job(kind: str, worker) -> dict[str, Any]:  # noqa: ANN001 - 内部用
    with _lock:
        running = _jobs.get("current")
        if running and running.get("status") == "running":
            return {"ok": False, "status": "busy", "detail": f"已有任务在跑：{running.get('kind')}"}
        job: dict[str, Any] = {
            "kind": kind,
            "status": "running",
            "started_at": time.time(),
            "finished_at": None,
            "progress": [],
            "result": None,
        }
        _jobs["current"] = job

    def run() -> None:
        try:
            result = worker(job)
            with _lock:
                job["result"] = result
                job["status"] = "done" if result.get("ok") else "failed"
                job["finished_at"] = time.time()
        except Exception as exc:  # noqa: BLE001 - 任务失败要能被页面看见
            with _lock:
                job["result"] = {"ok": False, "status": "failed", "detail": str(exc)}
                job["status"] = "failed"
                job["finished_at"] = time.time()

    threading.Thread(target=run, daemon=True, name=f"fc-{kind}").start()
    with _lock:
        return dict(job)


def build_financecoffer_router(root: Path | None = None) -> APIRouter:
    router = APIRouter(prefix=FINANCECOFFER_PREFIX, dependencies=[Depends(_guard)])

    @router.get("")
    def summary() -> dict[str, Any]:
        return _summary(root)

    @router.get("/plan")
    def get_plan(with_embedding: bool = True, allow_system_drive: bool = False) -> dict[str, Any]:
        preflight = installer.plan(root, with_embedding=with_embedding, allow_system_drive=allow_system_drive)
        return {
            "ok": preflight.ok,
            "target": str(preflight.target),
            "free_mb": preflight.free_mb,
            "errors": list(preflight.errors),
            "warnings": list(preflight.warnings),
            "components": _component_rows(with_embedding),
            "total_mb": preflight.plan.total_mb,
            "total_gb": preflight.plan.total_gb,
            "consequences": list(preflight.plan.consequences),
        }

    @router.post("/install")
    def install(
        with_embedding: bool = Body(True, embed=True),
        confirm: bool = Body(False, embed=True),
        allow_system_drive: bool = Body(False, embed=True),
    ) -> dict[str, Any]:
        def worker(job: dict[str, Any]) -> dict[str, Any]:
            def report(text: str) -> None:
                with _lock:
                    job["progress"].append({"at": time.time(), "text": text})

            report("开始取件")
            result = installer.install(
                root, with_embedding=with_embedding, confirm=confirm, allow_system_drive=allow_system_drive
            )
            if result.get("status") == "needs_confirmation":
                return result  # 没确认就不动盘，直接回给页面
            report("初始化数据库实例")
            init = services.initdb(root)
            if not init.get("ok"):
                report(f"initdb 失败：{init.get('detail')}")
                return {
                    "ok": False,
                    "status": "failed",
                    "detail": f"取件完成但数据库初始化失败：{init.get('detail')}",
                    "install": result,
                }
            report("安装完成")
            result["initdb"] = init
            return result

        return _start_job("install", worker)

    @router.get("/job")
    def job() -> dict[str, Any]:
        return {"ok": True, "job": _job_snapshot()}

    @router.post("/services")
    def services_action(action: str = Body(..., embed=True)) -> dict[str, Any]:
        if action not in {"start", "stop"}:
            return {"ok": False, "detail": f"未知动作：{action}（只支持 start / stop）"}
        results: dict[str, Any] = {}
        if action == "start":
            results["initdb"] = services.initdb(root)
            results["pg"] = services.start_pg(root)
            results["redis"] = redis_service.start(root)
            results["minio"] = services.start_minio(root)
            results["api"] = services.start_api(root)
            # embedding 是可选件：没装权重 / 没装依赖时只影响向量检索，不算整体失败
            results["embedding"] = services.start_embedding(root)
        else:
            # 停的顺序反过来：先停应用侧，最后停数据库
            results["embedding"] = services.stop_embedding(root)
            results["api"] = services.stop_api(root)
            results["minio"] = services.stop_minio(root)
            results["redis"] = redis_service.stop(root)
            results["pg"] = services.stop_pg(root)
        required = {key: item for key, item in results.items() if key != "embedding"}
        return {
            "ok": all(item.get("ok") for item in required.values()),
            "action": action,
            "services": results,
            "doctor": services.doctor(root),
        }

    @router.post("/uninstall")
    def uninstall(confirm: bool = Body(False, embed=True)) -> dict[str, Any]:
        return installer.uninstall(root, confirm=confirm)

    return router


__all__ = ["FINANCECOFFER_PREFIX", "build_financecoffer_router"]
