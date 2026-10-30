"""MCP Server（stdio / Streamable HTTP）：11 个工具，全部只读 + 1 个发起研究。

协议与数据边界：

- 工具清单来自 `mcp/tools.py`（契约测试的唯一样本），这里只做**实现**与
  `LadderEnvelope` 包装；新增工具必须先改 `tools.py`。
- 所有返回都带 `source_detail`（主备源 / 降级记录）与 `source_status`
  （`ok` / `degraded` / `partial`）；数据缺失时如实标 `partial` + 原因，
  **不伪造数字**。
- 交易日志 / 持仓 / 自选永不进入任何工具结果（`strip_private` 兜底剥离）。
- `mcp stdio` 的 stdout **只输出协议报文**：日志一律走 stderr（见 `_log_to_stderr`）。

工具实现全部是同步函数（读本地缓存 / 算指标 / 读已落盘复盘），
在事件循环里通过 `anyio.to_thread.run_sync` 调起，避免阻塞协议读写。
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import anyio
import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from ladderstock import __version__
from ladderstock.core import ladder as ladder_module
from ladderstock.core import sentiment as sentiment_module
from ladderstock.core import validation as validation_module
from ladderstock.core.listener import TokenGuardMiddleware
from ladderstock.core.models import LadderEnvelope

#: 隐私字段集合与剥离实现只有一份（`core/privacy.py`），与 web / export 共用；
#: 这里 re-export 保持既有导入路径不变（audit F-2 / R-2）。
from ladderstock.core.privacy import PRIVATE_KEYS, strip_private
from ladderstock.mcp.tools import TOOL_SPECS, ToolSpec

SERVER_NAME = "ladderstock"
SERVER_INSTRUCTIONS = (
    "LadderStock 只读工具集：复盘、情绪、连板天梯、龙虎榜、验证条件与对账。"
    "所有指标由程序计算，AI 只写叙述；交易日志与持仓不在本服务范围内。"
)

#: 工具统一输出 `LadderEnvelope`；字段与 core/models.py 对齐
ENVELOPE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "domain": {"type": "string", "enum": ["review", "sentiment", "validation"]},
        "topic": {"type": "string"},
        "event_id": {"type": ["string", "null"]},
        "data_as_of": {"type": "string", "format": "date"},
        "payload": {"type": "object"},
        "source": {"type": "string"},
        "source_detail": {"type": "string"},
        "fetched_at": {"type": "string", "format": "date-time"},
        "freshness": {"type": "string", "enum": ["realtime", "cached", "archive"]},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "source_status": {"type": "string", "enum": ["ok", "degraded", "partial"]},
    },
    "required": ["domain", "topic", "data_as_of", "payload", "fetched_at"],
}

STRENGTH_KEYS: tuple[str, ...] = (
    "change_pct",
    "pct_chg",
    "pct_change",
    "changePercent",
    "change",
    "涨跌幅",
    "涨幅",
    "strength",
)


class ToolError(Exception):
    """工具参数/状态错误：作为 `isError` 结果返回给客户端，而不是崩溃。"""


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #

def _now() -> datetime:
    return datetime.now(UTC)


def _log_to_stderr() -> None:
    """stdio 模式下 stdout 属于协议；所有日志固定写 stderr。"""
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig(
            stream=sys.stderr,
            level=logging.INFO,
            format="ldst(mcp): %(levelname)s %(message)s",
        )
    else:
        for handler in root.handlers:
            if isinstance(handler, logging.StreamHandler):
                handler.setStream(sys.stderr)


def _parse_day(arguments: dict[str, Any]) -> date:
    raw = arguments.get("date")
    if not isinstance(raw, str) or not raw.strip():
        raise ToolError("缺少 date（YYYY-MM-DD）")
    try:
        return date.fromisoformat(raw.strip()[:10])
    except ValueError:
        raise ToolError(f"非法日期：{raw!r}（需要 YYYY-MM-DD）") from None


def _envelope(
    *,
    domain: str,
    topic: str,
    day: date,
    payload: dict[str, Any],
    available: bool = True,
    source_detail: str = "",
    source_status: str | None = None,
    confidence: str | None = None,
    freshness: str = "cached",
) -> dict[str, Any]:
    status = source_status or ("ok" if available else "partial")
    clean = strip_private(payload)
    # 与本地看板 API 同口径：payload 里始终带 data_source_status（缺失/降级要显式可读）
    clean.setdefault("data_source_status", "ok" if available else "missing")
    envelope = LadderEnvelope(
        domain=domain,  # type: ignore[arg-type]
        topic=topic,
        data_as_of=day,
        payload=clean,
        source="ladderstock",
        source_detail=source_detail,
        fetched_at=_now(),
        freshness=freshness,  # type: ignore[arg-type]
        confidence=confidence or ("high" if available else "low"),  # type: ignore[arg-type]
        source_status=status,  # type: ignore[arg-type]
    )
    return envelope.model_dump(mode="json")


def _brief(record: dict[str, Any]) -> dict[str, Any]:
    """板块/龙虎榜记录的通用摘要：字段缺失就留 None，不猜。"""
    return {
        "code": ladder_module.pick(record, ladder_module.CODE_KEYS),
        "name": ladder_module.pick(record, ladder_module.NAME_KEYS),
        "change_pct": ladder_module.as_float(ladder_module.pick(record, STRENGTH_KEYS)),
    }


def _records(dataset: str, day: date, root: Any) -> tuple[list[dict[str, Any]], str]:
    return ladder_module.dataset_snapshot(dataset, day, root)


# --------------------------------------------------------------------------- #
# 工具实现（同步）
# --------------------------------------------------------------------------- #

def _tool_review(arguments: dict[str, Any], root: Any) -> dict[str, Any]:
    from ladderstock.review import pipeline

    day = _parse_day(arguments)
    report = pipeline.load_report(day, root)
    path = pipeline.review_path(day, root)
    if report is None:
        return _envelope(
            domain="review",
            topic="review",
            day=day,
            payload={"date": day.isoformat(), "review": None, "data_source_status": "missing"},
            available=False,
            source_detail=(
                f"local review cache 不存在：{path.name}"
                f"（先运行 ldst review --date {day}）"
            ),
        )

    status = report.get("source_status")
    source_status = status if status in ("ok", "degraded", "partial") else "ok"
    ai = report.get("ai")
    degraded = bool(isinstance(ai, dict) and ai.get("degraded"))
    if degraded and source_status == "ok":
        source_status = "degraded"
    confidence = "high" if source_status == "ok" else "medium"
    if source_status == "partial":
        confidence = "low"
    detail = str(report.get("source_detail") or "") or f"local review cache: {path.name}"
    freshness = report.get("freshness")
    return _envelope(
        domain="review",
        topic="review",
        day=day,
        payload={"date": day.isoformat(), "review": report, "data_source_status": source_status},
        source_detail=detail,
        source_status=source_status,
        confidence=confidence,
        freshness=freshness if freshness in ("realtime", "cached", "archive") else "cached",
    )


def _tool_validation(arguments: dict[str, Any], root: Any) -> dict[str, Any]:
    day = _parse_day(arguments)
    document = validation_module.load_validation(day, root)
    path = validation_module.validation_path(day, root)
    if document is None:
        return _envelope(
            domain="validation",
            topic="validation",
            day=day,
            payload={
                "date": day.isoformat(),
                "conditions": [],
                "reconcile": None,
                "data_source_status": "missing",
            },
            available=False,
            source_detail=(
                f"local validation cache 不存在：{path.name}"
                f"（先运行 ldst review --date {day}）"
            ),
        )

    reconcile = document.get("reconcile")
    reconcile_status = reconcile.get("status") if isinstance(reconcile, dict) else None
    source_status = "ok" if reconcile_status == "ok" else "partial"
    detail = f"local validation cache: {path.name}"
    if isinstance(reconcile, dict):
        detail += f"；对账 {reconcile_status}"
        if reconcile.get("next_session"):
            detail += f"（下一交易日 {reconcile['next_session']}）"
    else:
        detail += "；尚未对账（ldst validate reconcile）"
    return _envelope(
        domain="validation",
        topic="validation",
        day=day,
        payload={
            "date": day.isoformat(),
            **{key: value for key, value in document.items() if key != "date"},
            "data_source_status": "ok" if reconcile_status == "ok" else "partial",
        },
        available=reconcile_status == "ok",
        source_detail=detail,
        source_status=source_status,
        confidence="high" if reconcile_status == "ok" else "low",
    )


def _tool_research_status(arguments: dict[str, Any], root: Any) -> dict[str, Any]:
    """个股研究六段进度：只读 `research_checkpoint/<code>/<date>.json`，不触发任何生成。

    `code` 必填；`date` 缺省取该代码最近一次有记录的日期，没有则按今天报 `missing`。
    缺哪一段就点名哪一段（`reason` + 可直接执行的补齐命令），**不伪造进度**。
    """
    from ladderstock.research import artifacts
    from ladderstock.research import checkpoint as research_checkpoint

    raw_code = arguments.get("code")
    if not isinstance(raw_code, str) or not raw_code.strip():
        raise ToolError("缺少 code（证券代码，6 位数字）")
    try:
        code = research_checkpoint.normalize_code(raw_code)
    except ValueError as exc:
        raise ToolError(str(exc)) from None

    raw_day = arguments.get("date")
    if raw_day in (None, ""):
        latest = research_checkpoint.latest_day(code, root)
        day_text = latest or date.today().isoformat()
    else:
        if not isinstance(raw_day, str):
            raise ToolError(f"非法日期：{raw_day!r}（需要 YYYY-MM-DD）")
        day_text = raw_day.strip()[:10]
    try:
        day = date.fromisoformat(day_text)
    except ValueError:
        raise ToolError(f"非法日期：{raw_day!r}（需要 YYYY-MM-DD）") from None

    summary = research_checkpoint.status_summary(code, day.isoformat(), root)
    summary["artifacts"] = artifacts.artifact_paths(code, day.isoformat(), root)
    pending = summary["pending"]
    available = bool(summary["complete"])
    if available:
        source_status = "ok"
        detail = f"local research checkpoint: {summary['checkpoint']}"
    elif summary["exists"]:
        source_status = "partial"
        detail = (
            f"local research checkpoint: {summary['checkpoint']}；"
            f"未完成：{'、'.join(pending) or '（含失败段）'}"
        )
    else:
        source_status = "partial"
        detail = (
            f"local research checkpoint 不存在：{summary['checkpoint']}"
            f"（先运行 ldst research {code} --date {day.isoformat()}）"
        )
    payload = {
        "code": code,
        "date": day.isoformat(),
        "research": summary,
        "reason": None if available else f"六段未完成，缺：{'、'.join(pending) or '失败段'}",
        "data_source_status": (
            "ok" if available else "partial" if summary["exists"] else "missing"
        ),
    }
    return _envelope(
        domain="review",
        topic="research_status",
        day=day,
        payload=payload,
        available=available,
        source_detail=detail,
        source_status=source_status,
        confidence="high" if available else "medium" if summary["exists"] else "low",
    )


def _tool_sentiment(arguments: dict[str, Any], root: Any) -> dict[str, Any]:
    day = _parse_day(arguments)
    result = sentiment_module.compute_sentiment(day, root=root)
    available = bool(result.get("available"))
    return _envelope(
        domain="sentiment",
        topic="shortline_sentiment",
        day=day,
        payload={"sentiment": result},
        available=available,
        source_detail=str(result.get("source_detail") or ""),
    )


def _tool_sentiment_series(arguments: dict[str, Any], root: Any) -> dict[str, Any]:
    day = _parse_day(arguments)
    sessions = [item for item in validation_module.session_days(root=root) if item <= day]
    selected = (sessions or [day])[-10:]
    series: list[dict[str, Any]] = []
    for session in selected:
        snapshot = sentiment_module.compute_sentiment(session, root=root)
        series.append(
            {
                "date": session.isoformat(),
                "available": bool(snapshot.get("available")),
                "temperature": snapshot.get("temperature"),
                "promotion_rate": snapshot.get("promotion_rate"),
                "seal_rate": snapshot.get("seal_rate"),
                "gap_levels": snapshot.get("gap_levels") or [],
                "reason": snapshot.get("reason"),
            }
        )
    available = any(item["available"] for item in series)
    return _envelope(
        domain="sentiment",
        topic="shortline_sentiment_series",
        day=day,
        payload={
            "series": series,
            "days": len(series),
            "data_source_status": "ok" if available else "missing",
        },
        available=available,
        source_detail=f"limit_pool 本地缓存；{len(series)} 个交易日",
    )


def _tool_limit_ladder(arguments: dict[str, Any], root: Any) -> dict[str, Any]:
    day = _parse_day(arguments)
    result = ladder_module.compute_ladder(day, root=root)
    available = bool(result.get("available"))
    return _envelope(
        domain="sentiment",
        topic="limit_ladder",
        day=day,
        payload={"ladder": result},
        available=available,
        source_detail=str(result.get("source_detail") or ""),
    )


def _tool_leader(arguments: dict[str, Any], root: Any) -> dict[str, Any]:
    day = _parse_day(arguments)
    result = ladder_module.compute_ladder(day, root=root)
    available = bool(result.get("available"))
    return _envelope(
        domain="sentiment",
        topic="leader",
        day=day,
        payload={
            "date": day.isoformat(),
            "height": result.get("height"),
            "leader_candidates": result.get("leader_candidates") or [],
            "gap": result.get("gap"),
            "gap_levels": result.get("gap_levels") or [],
            "unknown_board": result.get("unknown_board"),
            "summary": ladder_module.summary(result),
            "reason": result.get("reason"),
            "data_source_status": "ok" if available else "missing",
        },
        available=available,
        source_detail=str(result.get("source_detail") or ""),
    )


def _tool_theme_rotation(arguments: dict[str, Any], root: Any) -> dict[str, Any]:
    day = _parse_day(arguments)
    industry, industry_backend = _records("industry_board", day, root)
    concept, concept_backend = _records("concept_board", day, root)
    news, news_backend = _records("theme_news", day, root)
    available = bool(industry or concept or news)

    industry_rows = sorted(
        (row for row in (_brief(item) for item in industry) if row["name"] or row["code"]),
        key=lambda row: row["change_pct"] if row["change_pct"] is not None else -1e9,
        reverse=True,
    )[:10]
    concept_rows = sorted(
        (row for row in (_brief(item) for item in concept) if row["name"] or row["code"]),
        key=lambda row: row["change_pct"] if row["change_pct"] is not None else -1e9,
        reverse=True,
    )[:10]

    return _envelope(
        domain="sentiment",
        topic="theme_rotation",
        day=day,
        payload={
            "date": day.isoformat(),
            "industry": industry_rows,
            "concept": concept_rows,
            "counts": {
                "industry_rows": len(industry),
                "concept_rows": len(concept),
                "news_rows": len(news),
            },
            "data_source_status": "ok" if available else "missing",
            "reason": None if available else "本地缓存没有板块/题材数据（先运行 ldst sync）",
        },
        available=available,
        source_detail=(
            f"industry_board: {industry_backend}; concept_board: {concept_backend}; "
            f"theme_news: {news_backend}"
        ),
    )


def _tool_dragon_tiger(arguments: dict[str, Any], root: Any) -> dict[str, Any]:
    day = _parse_day(arguments)
    board, board_backend = _records("dragon_tiger", day, root)
    seats, seats_backend = _records("dragon_tiger_seats", day, root)
    available = bool(board or seats)
    rows = [row for row in (_brief(item) for item in board) if row["name"] or row["code"]][:10]
    return _envelope(
        domain="sentiment",
        topic="dragon_tiger",
        day=day,
        payload={
            "date": day.isoformat(),
            "board": rows,
            "counts": {"board_rows": len(board), "seat_rows": len(seats)},
            "data_source_status": "ok" if available else "missing",
            "reason": None if available else "本地缓存没有龙虎榜数据（先运行 ldst sync）",
        },
        available=available,
        source_detail=f"dragon_tiger: {board_backend}; dragon_tiger_seats: {seats_backend}",
    )


def _tool_sources_health(arguments: dict[str, Any], root: Any) -> dict[str, Any]:
    day = date.today()
    try:
        from ladderstock.data.provider_registry import ProviderRegistry

        registry = ProviderRegistry(root=Path(root) if root is not None else None)
        payload = registry.health.report(limit=100)
        payload["sources_in_cooldown"] = [
            row for row in registry.describe() if row.get("cooldown_until") not in (None, "-")
        ]
        payload["peers"] = registry.peer_rows()
    except Exception as exc:  # noqa: BLE001 - 健康信息不可用不应让 MCP 崩掉
        return _envelope(
            domain="review",
            topic="sources_health",
            day=day,
            payload={"data_source_status": "missing", "reason": f"数据源注册表不可用：{exc}"},
            available=False,
            source_detail="local health log 不可读",
        )
    return _envelope(
        domain="review",
        topic="sources_health",
        day=day,
        payload={"health": payload, "data_source_status": "ok"},
        source_detail=f"local health log: {payload.get('state_file')}",
    )


def _tool_watch(arguments: dict[str, Any], root: Any) -> dict[str, Any]:
    """盯盘状态：读 `data/watch/status.json`（由 `ldst watch start` 写入）。

    读取与判型复用 `watch/status.py`（与 `ldst watch status` 同一份判据，DEC-008），
    这里只负责把它包成信封。状态文件缺失 / 损坏时如实标 `missing` + 可读原因，
    **不伪造信号**。
    """
    from ladderstock.watch import status as status_file

    day = _parse_day(arguments)
    base = Path(root) if root is not None else _data_dir()
    path = status_file.status_path(base)
    result = status_file.read_status_document(path)

    if not result.ok:
        if result.state == status_file.STATE_MISSING:
            reason = f"尚未执行 ldst watch start：盯盘状态文件不存在（{path}）"
        elif result.state == status_file.STATE_CORRUPT:
            reason = (
                f"盯盘状态文件损坏、无法解析：{path}（{result.error}）；"
                "请重新执行 ldst watch start"
            )
        else:
            reason = (
                f"盯盘状态文件 schema 不认识：{path}（{result.error}）；"
                "请升级 ldst 或重新执行 ldst watch start"
            )
        return _envelope(
            domain="review",
            topic="watch",
            day=day,
            payload={
                "date": day.isoformat(),
                "watch": None,
                "data_source_status": "missing",
                "watch_state": result.state,
                "status_path": str(path),
                "reason": reason,
            },
            available=False,
            source_detail=f"watch 状态文件：{path}（{result.state}）",
        )

    document = result.document or {}
    events_file = document.get("events_file")
    payload: dict[str, Any] = {
        "date": day.isoformat(),
        "watch": {
            "running": document["running"],
            "mode": document["mode"],
            "detail": status_file.status_detail(document),
            "watch_date": document.get("date"),
            "threshold": document.get("threshold"),
            "started_at": document.get("started_at"),
            "last_run_at": document.get("last_run_at"),
            "stopped_at": document.get("stopped_at"),
            "bars": document.get("bars"),
            "source": document.get("source"),
            "event_count": document["event_count"],
            "recent_events": status_file.recent_events(document),
            "events_file": events_file,
        },
        "status_path": str(path),
        "watch_state": result.state,
        "data_source_status": "ok",
    }
    source_detail = f"watch 状态文件：{path}"
    if isinstance(events_file, str) and events_file:
        source_detail += f"；事件文件：{events_file}"
    return _envelope(
        domain="review",
        topic="watch",
        day=day,
        payload=payload,
        available=True,
        source_detail=source_detail,
    )


def _tool_start_research(arguments: dict[str, Any], root: Any) -> dict[str, Any]:
    day = _parse_day(arguments)
    requested = arguments.get("sections")
    sections: list[str] | None = None
    if requested is not None:
        if not isinstance(requested, list) or not all(isinstance(item, str) for item in requested):
            raise ToolError("sections 必须是字符串数组")
        unknown = [item for item in requested if item not in _section_names()]
        if unknown:
            raise ToolError(f"未知段落：{', '.join(unknown)}；可选：{', '.join(_section_names())}")
        sections = list(requested)

    task_id = uuid.uuid4().hex
    record_path = _research_path(task_id, root)
    record = {
        "task_id": task_id,
        "date": day.isoformat(),
        "requested_sections": sections,
        "status": "running",
        "started_at": _now().isoformat(timespec="seconds"),
        "updated_at": _now().isoformat(timespec="seconds"),
        "note": "五段状态机按固定顺序执行；已完成段落复用 checkpoint，不重复消耗额度",
    }
    _write_json(record_path, record)
    thread = threading.Thread(
        target=_run_research,
        args=(task_id, day, sections, root, record_path),
        name=f"ldst-research-{task_id[:8]}",
        daemon=True,
    )
    thread.start()
    return _envelope(
        domain="review",
        topic="research_task",
        day=day,
        payload={
            "task_id": task_id,
            "status": "running",
            "date": day.isoformat(),
            "requested_sections": sections,
            "record": str(record_path),
            "data_source_status": "ok",
        },
        source_detail="review pipeline（本地缓存 → 指标 → AI 叙述 → 落盘）",
    )


# --------------------------------------------------------------------------- #
# 研究任务（后台线程）
# --------------------------------------------------------------------------- #

def _section_names() -> tuple[str, ...]:
    from ladderstock.ai.checkpoint import SECTIONS

    return SECTIONS


def _research_dir(root: Any) -> Path:
    base = Path(root) if root is not None else _data_dir()
    return base / "state" / "research"


def _research_path(task_id: str, root: Any) -> Path:
    return _research_dir(root) / f"{task_id}.json"


def _write_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


def _run_research(
    task_id: str, day: date, sections: list[str] | None, root: Any, record_path: Path
) -> None:
    """后台跑一遍复盘；结果写进任务记录（MCP 端不阻塞、不吞异常）。"""
    from ladderstock.review import pipeline

    record = _read_json(record_path) or {"task_id": task_id, "date": day.isoformat()}
    try:
        report, run = pipeline.generate(day, root=root)
    except Exception as exc:  # noqa: BLE001 - 原因写进任务记录，便于用户排查
        record.update(
            {
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "updated_at": _now().isoformat(timespec="seconds"),
            }
        )
        _write_json(record_path, record)
        return
    record.update(
        {
            "status": "ok" if run.exit_code == 0 else "degraded",
            "exit_code": run.exit_code,
            "sections": [
                {"name": section.get("name"), "status": section.get("status")}
                for section in run.sections
            ],
            "review": {
                "date": report.get("date"),
                "source_status": report.get("source_status"),
            },
            "updated_at": _now().isoformat(timespec="seconds"),
        }
    )
    _write_json(record_path, record)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# --------------------------------------------------------------------------- #
# 路径（延迟导入，避免 mcp → review → ai 的循环）
# --------------------------------------------------------------------------- #

def _data_dir() -> Path:
    from ladderstock.cli.paths import data_dir

    return data_dir()


# --------------------------------------------------------------------------- #
# 分发与 Server
# --------------------------------------------------------------------------- #

TOOL_HANDLERS: dict[str, Callable[[dict[str, Any], Any], dict[str, Any]]] = {
    "ladder_get_watch": _tool_watch,
    "ladder_get_sentiment_series": _tool_sentiment_series,
    "ladder_get_limit_ladder": _tool_limit_ladder,
    "ladder_get_theme_rotation": _tool_theme_rotation,
    "ladder_get_dragon_tiger": _tool_dragon_tiger,
    "ladder_get_leader": _tool_leader,
    "ladder_get_sources_health": _tool_sources_health,
    "ladder_start_research": _tool_start_research,
    "ladder_get_review": _tool_review,
    "ladder_get_validation": _tool_validation,
    "ladder_get_sentiment": _tool_sentiment,
    "research_status": _tool_research_status,
}


def dispatch(
    name: str, arguments: dict[str, Any] | None = None, *, root: Any = None
) -> dict[str, Any]:
    """同步分发：契约测试与 stdio/HTTP 共用同一条实现路径。"""
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        raise ToolError(f"未知工具：{name}")
    return handler(dict(arguments or {}), root)


def tool_definitions() -> list[types.Tool]:
    """按 `mcp/tools.py` 的清单构造 SDK Tool 对象（顺序即清单顺序）。"""
    tools: list[types.Tool] = []
    for spec in TOOL_SPECS:
        tools.append(_tool_from_spec(spec))
    return tools


def _tool_from_spec(spec: ToolSpec) -> types.Tool:
    return types.Tool(
        name=spec.name,
        description=spec.description,
        input_schema=spec.input_schema or {"type": "object", "properties": {}},
        output_schema=ENVELOPE_OUTPUT_SCHEMA,
        annotations=types.ToolAnnotations(read_only_hint=spec.read_only),
    )


def build_server(*, data_root: Any = None) -> Server[Any]:
    """构造低层 Server；`data_root` 可注入，便于测试指向临时目录。"""

    async def on_list_tools(
        _ctx: Any, _params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tool_definitions())

    async def on_call_tool(
        _ctx: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        name = params.name
        arguments = dict(params.arguments or {})
        try:
            envelope = await anyio.to_thread.run_sync(
                lambda: dispatch(name, arguments, root=data_root)
            )
        except ToolError as exc:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=str(exc))], is_error=True
            )
        except Exception as exc:  # noqa: BLE001 - 工具失败以 isError 回给客户端
            logging.getLogger(__name__).exception("工具 %s 调用失败", name)
            message = f"{name} 调用失败：{type(exc).__name__}: {exc}"
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=message)], is_error=True
            )
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text",
                    text=json.dumps(envelope, ensure_ascii=False, default=str),
                )
            ],
            structured_content=envelope,
        )

    return Server(
        SERVER_NAME,
        version=__version__,
        title="LadderStock MCP",
        description="A 股短线复盘台只读工具集（复盘/情绪/天梯/验证）",
        instructions=SERVER_INSTRUCTIONS,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


# --------------------------------------------------------------------------- #
# 传输层
# --------------------------------------------------------------------------- #

def serve_stdio(*, data_root: Any = None) -> None:
    """stdio 传输：stdout 只写协议，日志全部 stderr。"""
    _log_to_stderr()
    anyio.run(_stdio_main, data_root)


async def _stdio_main(data_root: Any) -> None:
    server = build_server(data_root=data_root)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
            raise_exceptions=False,
        )


def create_http_app(
    *, data_root: Any = None, host: str = "127.0.0.1", token: str | None = None
) -> Any:
    """Streamable HTTP（`POST /mcp`），默认只监听回环。

    `token` 非空时给 `/mcp` 挂 Bearer 鉴权（只有非回环绑定才需要，Token 由
    `core/listener.require_binding_policy()` 产出，与 web 共用同一个判据——
    audit R-1：MCP 侧不能只告警）。
    """
    server = build_server(data_root=data_root)
    app = server.streamable_http_app(streamable_http_path="/mcp", host=host)
    if not token:
        return app
    return TokenGuardMiddleware(app, token=token, path_prefix="/mcp")


__all__ = [
    "ENVELOPE_OUTPUT_SCHEMA",
    "PRIVATE_KEYS",
    "SERVER_NAME",
    "TOOL_HANDLERS",
    "ToolError",
    "build_server",
    "create_http_app",
    "dispatch",
    "serve_stdio",
    "tool_definitions",
]
