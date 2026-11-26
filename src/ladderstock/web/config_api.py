"""运行配置的只读视图：把 `config/*.yaml` 里"只有文件里看得见"的东西摆到页面上。

三条边界（与设置页的写操作刻意分开）：

- **只读**。这里不写任何 yaml；开关任务走 `/api/schedule/toggle`，改触发时间/代码池
  仍然只能改文件。原因：yaml 是运行时唯一真相源，页面上直接改会让"页面上看到的值"
  和"进程里生效的值"分叉——重启之前页面已经显示新值，谁都不知道哪个有效。
- **水位从磁盘算，不从内存算**。每个数据集"最新分区"是扫 `data/archive/YYYY/MM/DD/`
  得到的，不是读调度器的内存状态：服务重启后内存是空的，磁盘上的分区才是事实。
- **缺就是缺**。没分区的数据集如实返回 `latest_day: null`，不用"今天"顶替，
  也不把「按需拉过几次」算成「每日同步在跑」。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter

CONFIG_PREFIX = "/api/config"


def _project_root(root: Path | None) -> Path:
    """项目根（含 `config/`）。调用方传进来的往往是 data 目录，这里向上找一层。

    `server.build_app` 的 `root` 是 `data_root`——不纠正的话会去读
    `data/config/sync.yaml`，结果是一张空表，页面显示"配置里数据集 0"。
    """
    if root is None:
        from ladderstock.core import runtime

        return runtime.project_root()
    path = Path(root)
    if (path / "config").is_dir():
        return path
    if (path.parent / "config").is_dir():
        return path.parent
    return path


def _data_root(root: Path | None, project: Path) -> Path:
    """数据根：传进来的是 data 目录就用它，否则用 `<项目根>/data`。"""
    if root is None:
        return project / "data"
    path = Path(root)
    if path.name == "data" or (path / "archive").is_dir() or (path / "chat.sqlite").is_file():
        return path
    return project / "data"


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}


def _archive_latest(data_root: Path, dataset: str) -> str | None:
    """扫日分区，取这个数据集最近的分区日期。只认 `archive/YYYY/MM/DD/<name>.parquet`。"""
    base = data_root / "archive"
    if not base.is_dir():
        return None
    for year in sorted(base.iterdir(), reverse=True):
        if not (year.is_dir() and year.name.isdigit()):
            continue
        for month in sorted(year.iterdir(), reverse=True):
            if not (month.is_dir() and month.name.isdigit()):
                continue
            for day in sorted(month.iterdir(), reverse=True):
                if not (day.is_dir() and day.name.isdigit()):
                    continue
                if (day / f"{dataset}.parquet").is_file():
                    try:
                        return dt.date(int(year.name), int(month.name), int(day.name)).isoformat()
                    except ValueError:
                        continue
    return None


def _stale_days(latest: str | None, today: dt.date) -> int | None:
    if not latest:
        return None
    try:
        return (today - dt.date.fromisoformat(latest)).days
    except ValueError:
        return None


def _sync_view(root: Path, data_root: Path, today: dt.date) -> dict[str, Any]:
    doc = _load_yaml(root / "config" / "sync.yaml")
    datasets = (doc.get("sync") or {}).get("datasets") or {}
    rows: list[dict[str, Any]] = []
    for name, spec in datasets.items():
        if not isinstance(spec, dict):
            continue
        codes = spec.get("codes")
        pool = len(codes) if isinstance(codes, list) else 0
        fallback = spec.get("fallback") or []
        latest = _archive_latest(data_root, str(name))
        rows.append(
            {
                "name": str(name),
                "primary": str(spec.get("primary") or ""),
                "fallback": [str(item) for item in fallback],
                "mode": str(spec.get("mode") or ""),
                "codes": pool,
                "codes_from": spec.get("codes_from"),
                "latest_day": latest,
                "stale_days": _stale_days(latest, today),
                "today_ready": latest == today.isoformat(),
            }
        )
    rows.sort(key=lambda row: ((row["latest_day"] or ""), row["name"]), reverse=True)
    covered = sum(1 for row in rows if row["latest_day"])
    return {
        "ok": True,
        "today": today.isoformat(),
        "datasets": rows,
        "count": len(rows),
        "covered": covered,
        "stale": sum(1 for row in rows if (row["stale_days"] or 0) > 1),
        "base_pool": max((row["codes"] for row in rows), default=0),
    }


def _backfill_view(project: Path, data_root: Path) -> dict[str, Any]:
    doc = _load_yaml(project / "config" / "backfill.yaml").get("backfill") or {}
    universe = doc.get("universe") or []
    reports = data_root / str(doc.get("report_dir") or "backfill-reports")
    latest_report = None
    if reports.is_dir():
        candidates = sorted((item for item in reports.iterdir() if item.is_dir()), reverse=True)
        if candidates:
            latest_report = {
                "name": candidates[0].name,
                "files": sorted(item.name for item in candidates[0].glob("*") if item.is_file())[:8],
            }
    return {
        "ok": True,
        "source": doc.get("source"),
        "stage": doc.get("stage"),
        "start": doc.get("start"),
        "end": doc.get("end") or "",
        "target_years": doc.get("target_years"),
        "throttle_ms": doc.get("throttle_ms"),
        "gap_rate_threshold": doc.get("gap_rate_threshold"),
        "universe": len(universe),
        "latest_report": latest_report,
    }


def build_router(root: Path | None = None) -> APIRouter:
    router = APIRouter(prefix=CONFIG_PREFIX)
    project = _project_root(root)
    data_root = _data_root(root, project)

    @router.get("/sync")
    def sync_view() -> dict[str, Any]:
        return _sync_view(project, data_root, dt.date.today())

    @router.get("/backfill")
    def backfill_view() -> dict[str, Any]:
        return _backfill_view(project, data_root)

    return router


__all__ = ["CONFIG_PREFIX", "build_router"]
