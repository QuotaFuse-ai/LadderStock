#!/usr/bin/env python3
"""发布前离线冒烟：`watch` → `notify` → `schedule` 一条命令跑完。

设计原则（与 DEC-006 一致）：

- **全离线**：分钟样本用脚本自己生成的临时 fixture，通知只走本机渠道，对账只读
  本机缓存；不联网、不取数、不写仓库里的 `data/`（用 `LDST_DATA_DIR` 指向临时目录）；
- **可重复**：每次都从干净的数据目录开始，跑两遍结果一致；
- **结论明确**：每步打印 `PASS` / `WARN` / `FAIL` 与耗时，有 FAIL 就以非零码退出。

用法：`python scripts/release_smoke.py [-v]`
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from typer.testing import CliRunner  # noqa: E402  - 需要先插入 src 再导入

from ladderstock.cli.main import app  # noqa: E402

DAY = "2026-10-22"
VERBOSE = "-v" in sys.argv[1:] or "--verbose" in sys.argv[1:]

runner = CliRunner()


class Report:
    """收集各步结论；FAIL 决定退出码，WARN 只提醒。"""

    def __init__(self) -> None:
        self.failed = 0
        self.warned = 0
        self._index = 0

    def record(self, name: str, status: str, detail: str, elapsed_ms: float) -> None:
        self._index += 1
        if status == "FAIL":
            self.failed += 1
        elif status == "WARN":
            self.warned += 1
        print(f"[{status}] {self._index:02d} {name}（{elapsed_ms:.0f} ms）— {detail}")

    def check(
        self, name: str, condition: bool, detail: str, elapsed_ms: float
    ) -> bool:
        self.record(name, "PASS" if condition else "FAIL", detail, elapsed_ms)
        return condition

    def warn(self, name: str, detail: str, elapsed_ms: float) -> None:
        self.record(name, "WARN", detail, elapsed_ms)


def _invoke(args: list[str], env: dict[str, str]) -> tuple[Any, float]:
    started = time.perf_counter()
    result = runner.invoke(app, args, env=env)
    return result, (time.perf_counter() - started) * 1000.0


def _payload(result: Any) -> dict[str, Any]:
    try:
        data = json.loads(result.stdout or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _stderr_tail(result: Any, limit: int = 160) -> str:
    text = " ".join((result.stderr or "").split())
    return text[-limit:] if text else "（无 stderr）"


def _write_bars(path: Path) -> Path:
    """09:30 起 10.00 → 09:33 涨 4.5% → 09:34 封板（涨停价 11.00）。"""
    rows = [
        {"ts": f"{DAY}T09:30:00", "code": "600000", "price": 10.0, "limit_price": 11.0},
        {"ts": f"{DAY}T09:31:00", "code": "600000", "price": 10.1, "limit_price": 11.0},
        {"ts": f"{DAY}T09:32:00", "code": "600000", "price": 10.2, "limit_price": 11.0},
        {"ts": f"{DAY}T09:33:00", "code": "600000", "price": 10.45, "limit_price": 11.0},
        {"ts": f"{DAY}T09:34:00", "code": "600000", "price": 11.0, "limit_price": 11.0},
    ]
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    return path


def main() -> int:
    if not (ROOT / "config" / "schedule.yaml").is_file():
        print(f"FAIL 找不到 {ROOT / 'config' / 'schedule.yaml'}；请在仓库根目录运行")
        return 2

    report = Report()
    print(f"LadderStock 发布冒烟（离线）· {DAY} · 仓库 {ROOT}")

    with tempfile.TemporaryDirectory(prefix="ldst-smoke-") as tmp:
        sandbox = Path(tmp)
        data_home = sandbox / "data"
        bars = _write_bars(sandbox / f"{DAY}.jsonl")
        base_env = {
            "LDST_DATA_DIR": str(data_home),
            # 桌面渠道用一条本机 no-op 命令验证「壳命令链路」，不依赖 winotify/plyer
            "LDST_DESKTOP_NOTIFY_CMD": f'"{sys.executable}" -c "print(\'ldst desktop ok\')"',
        }

        # ---------------------------------------------------------- watch
        result, ms = _invoke(
            ["watch", "start", "--source", str(bars), "--date", DAY, "--output", "json"],
            base_env,
        )
        payload = _payload(result)
        kinds = [event.get("kind") for event in payload.get("events") or []]
        report.check(
            "watch start 识别越阈与封板",
            result.exit_code == 0 and kinds == ["threshold_up", "seal"],
            f"exit={result.exit_code} events={kinds or _stderr_tail(result)}",
            ms,
        )
        events_file = Path(payload.get("events_file") or sandbox / "missing.jsonl")
        report.check(
            "watch start 事件落盘",
            events_file.is_file()
            and len(events_file.read_text(encoding="utf-8").strip().splitlines()) == 2,
            str(events_file),
            ms,
        )

        result, ms = _invoke(
            ["watch", "start", "--source", str(bars), "--date", DAY, "--output", "json"],
            base_env,
        )
        payload = _payload(result)
        report.check(
            "watch start 重跑不重复上报",
            result.exit_code == 0 and payload.get("new_events") == 0,
            f"new_events={payload.get('new_events')}",
            ms,
        )

        result, ms = _invoke(["watch", "status", "--output", "json"], base_env)
        payload = _payload(result)
        report.check(
            "watch status 与落盘一致",
            result.exit_code == 0
            and payload.get("running") is True
            and payload.get("event_count") == 2,
            f"event_count={payload.get('event_count')}",
            ms,
        )

        result, ms = _invoke(["watch", "stop", "--output", "json"], base_env)
        payload = _payload(result)
        report.check(
            "watch stop 幂等",
            result.exit_code == 0 and payload.get("running") is False,
            f"stopped={payload.get('stopped')}",
            ms,
        )

        # --------------------------------------------------------- notify
        result, ms = _invoke(["notify", "test", "--output", "json"], base_env)
        report.check(
            "notify test console",
            result.exit_code == 0 and _payload(result).get("ok") is True,
            f"exit={result.exit_code} {_stderr_tail(result)}",
            ms,
        )

        result, ms = _invoke(
            ["notify", "test", "--channel", "desktop", "--output", "json"], base_env
        )
        payload = _payload(result)
        if result.exit_code == 0:
            report.check(
                "notify test desktop（壳命令链路）",
                payload.get("ok") is True,
                "已通过 LDST_DESKTOP_NOTIFY_CMD 投递",
                ms,
            )
        else:
            # 本机没有桌面通知后端属于「降级可用」，不该挡住发布
            report.warn(
                "notify test desktop 不可用（降级，不阻塞）",
                payload.get("detail") or _stderr_tail(result),
                ms,
            )

        # ------------------------------------------------------- schedule
        result, ms = _invoke(["schedule", "list", "--output", "json"], base_env)
        payload = _payload(result)
        triggers = [job.get("trigger") for job in payload.get("jobs") or []]
        report.check(
            "schedule list 默认任务表",
            result.exit_code == 0 and triggers == ["09:05", "15:05"],
            f"jobs={triggers or _stderr_tail(result)}",
            ms,
        )

        result, ms = _invoke(["schedule", "run", "--once", "--output", "json"], base_env)
        payload = _payload(result)
        statuses = {
            str(row.get("id")): row.get("status")
            for row in payload.get("results") or []
        }
        acceptable = {"ok", "degraded", "skipped", "partial"}
        report.check(
            "schedule run --once 跑完一轮",
            result.exit_code == 0
            and bool(statuses)
            and all(value in acceptable for value in statuses.values()),
            f"exit={result.exit_code} statuses={statuses or _stderr_tail(result)}",
            ms,
        )

        # --------------------------------------------------------- config
        result, ms = _invoke(["config", "validate", "--output", "json"], base_env)
        payload = _payload(result)
        bad = [
            item.get("file")
            for item in payload.get("files") or []
            if not item.get("ok")
        ]
        report.check(
            "config validate 全通过",
            result.exit_code == 0 and not bad,
            f"exit={result.exit_code} 失败文件={bad or '无'}",
            ms,
        )

    print()
    if report.failed:
        print(f"冒烟失败：{report.failed} 项 FAIL、{report.warned} 项 WARN")
        return 1
    suffix = f"，{report.warned} 项 WARN（不阻塞）" if report.warned else ""
    print(f"冒烟全部通过{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
