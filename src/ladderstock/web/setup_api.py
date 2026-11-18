"""首次配置 / 子模块安装向导的后端接口。

对外三个面：

- `GET  /api/setup/status`  当前 6 个子模块装没装、差多少空间、解释器从哪来
- `POST /api/setup/install` 启动安装（body 可指定 peers；默认全装），后台线程执行
- `GET  /api/setup/job`     查进度：总体 pct + 每个 peer 的 pct/message/状态

进度是**按体积加权**的总体值，前端画一条总条 + 每个 peer 一条子条。
安装是顺序执行的：同一时间只装一个，避免多个 pip 抢同一个镜像和同一块磁盘。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body

from ladderstock.modules.installer import manifest, runner, state

SETUP_PREFIX = "/api/setup"

_lock = threading.Lock()
_jobs: dict[str, Any] = {}


def _snapshot() -> dict[str, Any] | None:
    with _lock:
        job = _jobs.get("current")
        return dict(job) if job else None


def _status(root: Path | None) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    for item in manifest.PEERS:
        target = runner.boundary_modules_target(item, root)
        exists = target.exists()
        if not exists:
            missing.append(item.key)
        raw_state = str(state.read_state(item.key, root).get("state") or "not_installed")
        # 目录在、但状态文件从没登记过（开发态同步过来的实体 / 历史产物）：
        # 如实标成 present，而不是撒谎说「没装」——向导据此不会重复装。
        display_state = "present" if (exists and raw_state == "not_installed") else raw_state
        rows.append(
            {
                "key": item.key,
                "label": item.label,
                "target": str(target),
                "kind": item.kind,
                "package": item.package,
                "version": item.version,
                "size_mb": item.size_mb,
                "required": item.required,
                "note": item.note,
                "exists": exists,
                "state": display_state,
            }
        )
    interpreter, origin = runner.resolve_interpreter(root)
    return {
        "ok": True,
        "modules_root": str(runner.boundary_modules_target(manifest.PEERS[0], root).parent),
        "interpreter": str(interpreter),
        "interpreter_origin": origin,
        "portable_python": str(runner.portable_python(root) or ""),
        "peers": rows,
        "missing": missing,
        "missing_count": len(missing),
        "total_mb": manifest.total_mb(),
        "total_gb": round(manifest.total_mb() / 1024, 2),
        "mirrors": list(manifest.PYPI_MIRRORS),
        "job": _snapshot(),
    }


def _install_job(peers: list[str], root: Path | None) -> dict[str, Any]:
    """顺序装 peers；每个 peer 的进度写回 job 的 peers 表。"""
    with _lock:
        running = _jobs.get("current")
        if running and running.get("status") == "running":
            return {
                "ok": False,
                "status": "busy",
                "detail": f"已有安装任务在跑：{running.get('current_peer')}",
            }

        weights = {key: max(manifest.BY_KEY[key].size_mb, 1) for key in peers}
        total_weight = max(sum(weights.values()), 1)
        job: dict[str, Any] = {
            "status": "running",
            "started_at": time.time(),
            "finished_at": None,
            "current_peer": peers[0] if peers else None,
            "overall": 0.0,
            "peers": {
                key: {"pct": 0.0, "message": "等待中", "ok": None, "detail": ""} for key in peers
            },
            "done_weight": 0.0,
            "total_weight": total_weight,
        }
        _jobs["current"] = job

    def run() -> None:
        failed: list[str] = []
        try:
            for key in peers:
                with _lock:
                    job["current_peer"] = key
                    job["peers"][key]["message"] = "开始安装"

                def on_progress(pct: float, message: str, _key: str = key) -> None:
                    with _lock:
                        job["peers"][_key]["pct"] = round(pct, 3)
                        job["peers"][_key]["message"] = message
                        base = job["done_weight"]
                        job["overall"] = round(
                            min((base + weights[_key] * pct) / job["total_weight"], 1.0), 3
                        )

                result = runner.install_peer(key, root=root, on_progress=on_progress)
                with _lock:
                    job["peers"][key]["ok"] = result.ok
                    job["peers"][key]["detail"] = result.detail
                    job["peers"][key]["pct"] = 1.0 if result.ok else job["peers"][key]["pct"]
                    job["peers"][key]["message"] = (
                        "完成" if result.ok else f"失败：{result.detail[:80]}"
                    )
                    job["done_weight"] += weights[key]
                    job["overall"] = round(min(job["done_weight"] / job["total_weight"], 1.0), 3)
                if not result.ok:
                    failed.append(key)

            with _lock:
                job["status"] = "failed" if failed else "done"
                job["finished_at"] = time.time()
                job["current_peer"] = None
        except Exception as exc:  # noqa: BLE001 - 任务线程不能静默死掉
            with _lock:
                job["status"] = "failed"
                job["finished_at"] = time.time()
                job["error"] = f"{type(exc).__name__}: {exc}"

    threading.Thread(target=run, name="setup-install", daemon=True).start()
    return {"ok": True, "status": "running", "peers": peers, "job": _snapshot()}


def build_router(root: Path | None = None) -> APIRouter:
    router = APIRouter(prefix=SETUP_PREFIX)

    @router.get("/status")
    def status() -> dict[str, Any]:
        return _status(root)

    @router.post("/install")
    def install(
        peers: list[str] | None = Body(None, embed=True),  # noqa: B008
        only_missing: bool = Body(True, embed=True),  # noqa: B008
    ) -> dict[str, Any]:
        if peers is None:
            peers = [item.key for item in manifest.PEERS]
            if only_missing:
                peers = [
                    key
                    for key in peers
                    if not runner.boundary_modules_target(manifest.BY_KEY[key], root).exists()
                ]
        unknown = [key for key in peers if key not in manifest.BY_KEY]
        if unknown:
            return {"ok": False, "detail": f"未知子模块：{unknown}"}
        if not peers:
            return {"ok": True, "status": "nothing_to_do", "detail": "所选子模块都已存在"}
        return _install_job(peers, root)

    @router.get("/job")
    def job() -> dict[str, Any]:
        snapshot = _snapshot()
        return {"ok": True, "job": snapshot}

    @router.post("/uninstall")
    def uninstall(
        peer: str = Body(..., embed=True),
        confirm: bool = Body(False, embed=True),
    ) -> dict[str, Any]:
        if peer not in manifest.BY_KEY:
            return {"ok": False, "detail": f"未知子模块：{peer}"}
        if not confirm:
            return {
                "ok": False,
                "status": "needs_confirmation",
                "detail": "卸载会删除整个目录（含数据），需要 confirm=true",
            }
        result = runner.uninstall_peer(peer, root=root, confirm=True)
        return result.as_dict()

    return router


__all__ = ["SETUP_PREFIX", "build_router"]
