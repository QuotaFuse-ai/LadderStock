"""水位线与同步运行记录（DEC-007 §2.3）。

落地选择：**JSON 文件** `<data_dir>/state/watermark.json`，而不是 DuckDB 元数据表。

原因：
- 水位线要在 DuckDB 打不开（文件占用 / 损坏）时仍然可读，`ldst sync status` 不能跟着挂；
- 断点续跑需要「每完成一个数据集即推进」，JSON 原子替换（tmp + `os.replace`）足够；
- 只有一个本地用户、一个写入进程，不需要事务级并发控制。

结构::

    {
      "version": 1,
      "watermarks": {
        "daily_k": {"dataset": "daily_k", "value": "2026-10-24",
                     "updated_at": "2026-10-24T15:31:00+08:00",
                     "rows": 5218, "source": "tickhub_kline", "status": "ok"}
      },
      "runs": [
        {"dataset": "daily_k", "mode": "incremental", "started_at": "...",
         "finished_at": "...", "status": "ok|degraded|skipped|failed",
         "rows": 5218, "source": "tickhub_kline", "trail": ["..."],
         "watermark_before": "2026-10-23", "watermark_after": "2026-10-24",
         "detail": ""}
      ]
    }

约定：
- `value` 是 ISO 日期或 ISO 时间串，字典序即时间序，便于 `max()` 推进；
- 水位**只进不退**（`--full` 不会把水位拉回过去）；
- `runs` 只保留最近 `MAX_RUNS` 条，避免状态文件无界增长。
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ladderstock.cli.paths import data_dir

STATE_VERSION = 1
MAX_RUNS = 200

_lock = threading.Lock()


class WatermarkError(RuntimeError):
    """水位文件损坏或不可写。"""


@dataclass(frozen=True)
class WatermarkEntry:
    dataset: str
    value: str
    updated_at: str
    rows: int = 0
    source: str = ""
    status: str = "ok"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunRecord:
    dataset: str
    mode: str
    started_at: str
    finished_at: str
    status: str
    rows: int = 0
    source: str = ""
    trail: list[str] = field(default_factory=list)
    watermark_before: str | None = None
    watermark_after: str | None = None
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def watermark_path(root: Path | None = None) -> Path:
    """水位文件路径：`<data_dir>/state/watermark.json`。"""
    base = Path(root) if root is not None else data_dir()
    return base / "state" / "watermark.json"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class WatermarkStore:
    """水位 + 运行记录的读写门面。"""

    def __init__(self, path: Path | None = None, *, root: Path | None = None) -> None:
        self.path = Path(path) if path is not None else watermark_path(root)
        self._data: dict[str, Any] = self._load()

    # ---------------------------------------------------------------- 读写

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"version": STATE_VERSION, "watermarks": {}, "runs": []}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError as exc:
            raise WatermarkError(f"水位文件损坏：{self.path}（{exc.msg}）") from exc
        if not isinstance(raw, dict):
            raise WatermarkError(f"水位文件结构非法：{self.path}")
        raw.setdefault("version", STATE_VERSION)
        raw.setdefault("watermarks", {})
        raw.setdefault("runs", [])
        return raw

    def _flush(self) -> None:
        """原子替换写盘：先写 tmp 再 `os.replace`，断电不会留半份文件。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        os.replace(tmp, self.path)

    def reload(self) -> None:
        self._data = self._load()

    # ---------------------------------------------------------------- 水位

    def get(self, dataset: str) -> WatermarkEntry | None:
        item = (self._data.get("watermarks") or {}).get(dataset)
        if not item:
            return None
        return WatermarkEntry(
            dataset=dataset,
            value=str(item.get("value") or ""),
            updated_at=str(item.get("updated_at") or ""),
            rows=int(item.get("rows") or 0),
            source=str(item.get("source") or ""),
            status=str(item.get("status") or "ok"),
        )

    def value(self, dataset: str) -> str | None:
        entry = self.get(dataset)
        return entry.value if entry and entry.value else None

    def advance(
        self,
        dataset: str,
        value: str,
        *,
        rows: int = 0,
        source: str = "",
        status: str = "ok",
    ) -> WatermarkEntry:
        """推进水位；只进不退，空值不推进。"""
        if not value:
            raise WatermarkError(f"{dataset} 的水位值不能为空")
        with _lock:
            current = self.get(dataset)
            if current is not None and current.value and value < current.value:
                # 历史补拉 / 备源较早的数据：保留更高水位，不倒退
                return current
            entry = WatermarkEntry(
                dataset=dataset,
                value=value,
                updated_at=_now(),
                rows=rows,
                source=source,
                status=status,
            )
            self._data.setdefault("watermarks", {})[dataset] = entry.as_dict()
            self._flush()
            return entry

    def entries(self) -> list[WatermarkEntry]:
        names = sorted((self._data.get("watermarks") or {}).keys())
        return [entry for name in names if (entry := self.get(name)) is not None]

    # ---------------------------------------------------------------- 运行记录

    def record_run(
        self,
        dataset: str,
        *,
        mode: str,
        status: str,
        rows: int = 0,
        source: str = "",
        trail: list[str] | None = None,
        started_at: str | None = None,
        watermark_before: str | None = None,
        watermark_after: str | None = None,
        detail: str = "",
    ) -> RunRecord:
        record = RunRecord(
            dataset=dataset,
            mode=mode,
            started_at=started_at or _now(),
            finished_at=_now(),
            status=status,
            rows=rows,
            source=source,
            trail=list(trail or []),
            watermark_before=watermark_before,
            watermark_after=watermark_after,
            detail=detail,
        )
        with _lock:
            runs = self._data.setdefault("runs", [])
            runs.append(record.as_dict())
            if len(runs) > MAX_RUNS:
                del runs[: len(runs) - MAX_RUNS]
            self._flush()
        return record

    def runs(self, limit: int = 20) -> list[dict[str, Any]]:
        items = list(self._data.get("runs") or [])
        if limit > 0:
            items = items[-limit:]
        return [dict(item) for item in items]

    def status(self, *, limit: int = 20) -> dict[str, Any]:
        """`ldst sync status` 的载荷：水位 + 最近运行。"""
        entries = [entry.as_dict() for entry in self.entries()]
        seen = {e["dataset"] for e in entries}
        return {
            "state_file": str(self.path),
            "watermarks": entries,
            "datasets_without_watermark": [
                name for name in sorted(_known_datasets()) if name not in seen
            ],
            "recent_runs": self.runs(limit=limit),
        }


def _known_datasets() -> list[str]:
    from ladderstock.core.boundary import DATASET_CATALOG

    return list(DATASET_CATALOG)
