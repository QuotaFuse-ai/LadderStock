"""`ldst watch start|status|stop`：盘中盯盘的离线单轮扫描。

信号计算全在 `watch/signal.py`（纯函数）；本模块只负责「找本地分钟样本 → 跑一轮
→ 落盘状态与事件 → 打印结果」。**不联网、不取数**（DEC-006）：样本来自

1. `--source` 指定的 `.jsonl` / `.json`（离线 fixture、人工核对用）；
2. 否则读本机缓存 `<data_dir>/minute_bars/<date>.jsonl|json`。

没有可用样本时以退出码 4 明说缺什么，绝不自己抓数据。事件按
`(code, kind, 分钟)` 去重并追加到 `<data_dir>/watch/events-<date>.jsonl`，
所以重复执行 `watch start` 不会把同一条信号报两次。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any, NoReturn

import typer

from ladderstock.cli.output import OutputOption, emit, error
from ladderstock.cli.paths import data_dir
from ladderstock.errors import EXIT_OK, EXIT_SOURCE, EXIT_USAGE

#: 状态文件的读取判据只有一份（`watch/status.py`），MCP 的 `ladder_get_watch` 读同一份
from ladderstock.watch import status as status_file
from ladderstock.watch.signal import WatchEvent, detect_signals, load_bars
from ladderstock.watch.status import STATUS_RECENT_EVENTS

MODE_SINGLE_PASS = "offline-single-pass"


# ---------------------------------------------------------------- 落盘位置


def watch_dir() -> Path:
    return status_file.watch_dir(data_dir())


def status_path() -> Path:
    return status_file.status_path(data_dir())


def events_path(day: str) -> Path:
    return watch_dir() / f"events-{day}.jsonl"


def _default_bars_candidates(day: str) -> list[Path]:
    base = data_dir() / "minute_bars"
    return [base / f"{day}.jsonl", base / f"{day}.json"]


def _resolve_source(day: str, source: str | None) -> tuple[Path | None, list[Path]]:
    """定位分钟样本：显式 `--source` 优先，否则找本机缓存；返回 (命中, 找过的路径)。"""
    if source:
        given = Path(source)
        candidates = (
            [given / f"{day}.jsonl", given / f"{day}.json"] if given.is_dir() else [given]
        )
    else:
        candidates = _default_bars_candidates(day)
    for candidate in candidates:
        if candidate.is_file():
            return candidate, candidates
    return None, candidates


def _fail(message: str, code: int) -> NoReturn:
    error(message)
    raise typer.Exit(code)


# ---------------------------------------------------------------- 状态读写


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def read_status() -> dict[str, Any] | None:
    """读最近一次扫描的状态；文件不存在或读不出来时返回 None（判型见 `watch/status.py`）。"""
    return status_file.read_status_document(status_path()).document


def _write_status(document: dict[str, Any]) -> Path:
    path = status_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return path


def read_events(day: str) -> tuple[list[WatchEvent], int]:
    """读当日已落盘的事件；坏行只跳过并计数（状态文件坏了不该让盯盘不可用）。"""
    path = events_path(day)
    if not path.is_file():
        return [], 0
    events: list[WatchEvent] = []
    skipped = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            events.append(WatchEvent.from_dict(json.loads(line)))
        except (ValueError, TypeError):
            skipped += 1
    return events, skipped


def _append_events(day: str, fresh: list[WatchEvent]) -> Path:
    path = events_path(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for event in fresh:
            handle.write(json.dumps(event.as_dict(), ensure_ascii=False) + "\n")
    return path


# ---------------------------------------------------------------- 命令实现


def watch_start(
    threshold: Annotated[
        float, typer.Option(help="3 分钟涨跌幅阈值（%，必须大于 0）")
    ] = 3.0,
    day: Annotated[
        str | None, typer.Option("--date", help="交易日 YYYY-MM-DD（缺省今天）")
    ] = None,
    source: Annotated[
        str | None,
        typer.Option(
            "--source",
            help="分钟样本文件（.jsonl / .json）或目录；缺省读本机缓存 minute_bars/<date>.jsonl",
        ),
    ] = None,
    output: OutputOption = None,
) -> None:
    """跑一轮盯盘扫描（离线单轮）：越阈、封板 / 开板事件落盘并输出。"""
    if threshold <= 0:
        _fail(f"--threshold 必须大于 0（单位百分比）；收到 {threshold}", EXIT_USAGE)

    day_text = day or date.today().isoformat()
    try:
        date.fromisoformat(day_text)
    except ValueError:
        _fail(f"--date 不是合法日期：{day_text!r}（格式 YYYY-MM-DD）", EXIT_USAGE)

    path, searched = _resolve_source(day_text, source)
    if path is None:
        looked = "、".join(str(item) for item in searched) or "（无候选路径）"
        _fail(
            f"{day_text} 没有可用的分钟样本：找过 {looked}。"
            "盯盘只消费本机已有样本（DEC-006），请先用 --source 指定离线 fixture，"
            "或等 TickHub 落盘后再跑",
            EXIT_SOURCE,
        )

    try:
        bars = load_bars(path)
    except FileNotFoundError:
        _fail(f"分钟样本不存在：{path}", EXIT_SOURCE)
    except ValueError as exc:
        _fail(f"分钟样本格式错误：{exc}", EXIT_USAGE)

    try:
        detected = detect_signals(bars, threshold=threshold)
    except ValueError as exc:
        _fail(str(exc), EXIT_USAGE)

    known, dirty_lines = read_events(day_text)
    known_keys = {event.key for event in known}
    fresh = [event for event in detected if event.key not in known_keys]
    if fresh:
        _append_events(day_text, fresh)

    total = known + fresh
    now = _now_iso()
    document: dict[str, Any] = {
        "running": True,
        "mode": MODE_SINGLE_PASS,
        "date": day_text,
        "threshold": threshold,
        "started_at": now,
        "last_run_at": now,
        "bars": len(bars),
        "source": str(path),
        "source_detail": bars[0].source_detail if bars else "-",
        "event_count": len(total),
        "recent_events": [event.as_dict() for event in total[-STATUS_RECENT_EVENTS:]],
        "events_file": str(events_path(day_text)),
    }
    written = _write_status(document)

    payload: dict[str, Any] = {
        "ok": True,
        "date": day_text,
        "threshold": threshold,
        "mode": MODE_SINGLE_PASS,
        "source": str(path),
        "source_detail": document["source_detail"],
        "bars": len(bars),
        "events": [event.as_dict() for event in detected],
        "new_events": len(fresh),
        "known_events": len(known),
        "event_count": len(total),
        "events_file": str(events_path(day_text)),
        "status_path": str(written),
        "text_only": True,
    }
    if dirty_lines:
        payload["warnings"] = [f"events 文件有 {dirty_lines} 行无法解析，已跳过"]
        error(f"events 文件有 {dirty_lines} 行无法解析，已跳过")
    emit(payload, output)
    raise typer.Exit(EXIT_OK)


def watch_status(output: OutputOption = None) -> None:
    """最近一次盯盘扫描的状态与信号（没有跑过就如实说明，不假装在跑）。"""
    document = read_status()
    if document is None:
        emit(
            {
                "ok": True,
                "running": False,
                "mode": MODE_SINGLE_PASS,
                "detail": "尚未执行盯盘扫描（ldst watch start）",
                "event_count": 0,
                "recent_events": [],
                "status_path": str(status_path()),
            },
            output,
        )
        raise typer.Exit(EXIT_OK)

    payload: dict[str, Any] = {"ok": True, "status_path": str(status_path())}
    payload.update(document)
    emit(payload, output)
    raise typer.Exit(EXIT_OK)


def watch_stop(output: OutputOption = None) -> None:
    """停止盯盘（幂等）：没有在跑也返回 0，并说明是幂等而非失败。"""
    document = read_status()
    if document is None:
        emit(
            {
                "ok": True,
                "stopped": False,
                "running": False,
                "detail": "盯盘未在运行（幂等）",
            },
            output,
        )
        raise typer.Exit(EXIT_OK)

    was_running = bool(document.get("running"))
    document["running"] = False
    document["stopped_at"] = _now_iso()
    _write_status(document)
    emit(
        {
            "ok": True,
            "stopped": was_running,
            "running": False,
            "detail": "已停止盯盘" if was_running else "盯盘未在运行（幂等）",
            "last_run_at": document.get("last_run_at"),
        },
        output,
    )
    raise typer.Exit(EXIT_OK)


def register(watch_app: typer.Typer) -> None:
    """把 `watch` 子命令挂到 main.py 已建好的 Typer 组上。"""
    watch_app.command(
        "start", help="盘中盯盘：3 分钟涨跌幅阈值、封板/开板检测（离线单轮扫描）"
    )(watch_start)
    watch_app.command("status", help="盯盘运行状态与最近信号")(watch_status)
    watch_app.command("stop", help="停止盯盘（幂等）")(watch_stop)


__all__ = [
    "MODE_SINGLE_PASS",
    "events_path",
    "read_events",
    "register",
    "status_path",
    "watch_dir",
    "watch_start",
    "watch_status",
    "watch_stop",
]
