"""复盘流水线（M3 §6.4）：指标 → 五段 AI 编排 → 落盘。

流程固定为「先算数、后写字」：

1. `core/sentiment.py` / `core/ladder.py` 直出全部指标（AI 不参与数字）；
2. `ai/orchestrator.py` 按 情绪 → 资金 → 题材 → 龙虎榜 → 龙头 五段生成叙述，
   每段落 checkpoint，单段失败不影响其余段落；
3. 合并结果写成复盘文档，**同时落两处**：
   - 规范位置 `<data_dir>/state/review/<date>.json`（M3 契约，供 MCP / 验证读取）
   - 兼容位置 `<data_dir>/review/<date>.json`（`ldst export` 的既有入口）
4. 把「明日验证条件」写到 `<data_dir>/state/validation/<date>.json`，
   `ldst validate --date` 随后可直接读取。

数据只来自本地缓存（`data/archive.py`），本模块不做任何网络取数。
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from ladderstock.ai import checkpoint, fallback
from ladderstock.ai.orchestrator import Orchestrator, RunResult
from ladderstock.core import ladder as ladder_module
from ladderstock.core import sentiment as sentiment_module
from ladderstock.core import validation as validation_module
from ladderstock.core.sentiment import narrative_metrics

CANONICAL_SUBDIR = ("state", "review")
MIRROR_SUBDIR = "review"
FRESHNESS = "cached"

FOOTER = (
    "本报告指标由 LadderStock 程序计算，AI 仅生成叙述文字；"
    "数据来自 TickHub-CN / QuotaFuse Wire / StockSenate 的本地缓存。"
)


# --------------------------------------------------------------------------- #
# 路径与读写
# --------------------------------------------------------------------------- #

def _data_dir(root: Any = None) -> Path:
    from ladderstock.cli.paths import data_dir

    return Path(root) if root is not None else data_dir()


def review_path(day: date | str, root: Any = None) -> Path:
    """复盘文档规范位置：`<data_dir>/state/review/<date>.json`。"""
    return _data_dir(root).joinpath(*CANONICAL_SUBDIR) / f"{day}.json"


def mirror_path(day: date | str, root: Any = None) -> Path:
    """`ldst export` 使用的兼容位置：`<data_dir>/review/<date>.json`。"""
    return _data_dir(root) / MIRROR_SUBDIR / f"{day}.json"


def load_report(day: date | str, root: Any = None) -> dict[str, Any] | None:
    """读取已生成的复盘（先规范位置，再兼容位置）。"""
    for path in (review_path(day, root), mirror_path(day, root)):
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict):
                return data
    return None


def latest_report(root: Any = None) -> tuple[str, dict[str, Any]] | None:
    """取最新一份复盘（规范位置优先，按文件名倒序）。"""
    base = _data_dir(root).joinpath(*CANONICAL_SUBDIR)
    files = sorted(base.glob("*.json"), reverse=True) if base.is_dir() else []
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            return path.stem, data
    return None


# --------------------------------------------------------------------------- #
# 指标快照
# --------------------------------------------------------------------------- #

def _records(dataset: str, day: date | str, root: Any) -> tuple[list[dict[str, Any]], str]:
    return ladder_module.dataset_snapshot(dataset, day, root)


def collect_metrics(day: date | str, *, root: Any = None) -> dict[str, dict[str, Any]]:
    """五段各自的只读指标快照；缺数据如实标 `available=False`，不补默认值。"""
    sentiment = sentiment_module.compute_sentiment(day, root=root)
    ladder = ladder_module.compute_ladder(day, root=root)
    return {
        "sentiment": sentiment_metrics(sentiment),
        "capital": _flow_metrics(day, root),
        "theme": _theme_metrics(day, root),
        "dragon_tiger": _dragon_tiger_metrics(day, root),
        "leader": ladder_metrics(ladder),
    }


def sentiment_metrics(sentiment: dict[str, Any]) -> dict[str, Any]:
    """情绪段快照：情绪温度/晋级率/封板率 + 天梯高度与断层。"""
    snapshot = narrative_metrics(sentiment)
    snapshot["available"] = bool(sentiment.get("available"))
    if not sentiment.get("available"):
        snapshot["reason"] = sentiment.get("reason")
    return snapshot


def ladder_metrics(ladder: dict[str, Any]) -> dict[str, Any]:
    """龙头段快照：天梯高度、断层与龙头候选（程序计算）。"""
    return {
        "available": bool(ladder.get("available")),
        "reason": ladder.get("reason"),
        "height": ladder.get("height"),
        "count": ladder.get("count"),
        "gap": ladder.get("gap"),
        "gap_levels": ladder.get("gap_levels") or [],
        "leader_candidates": ladder.get("leader_candidates") or [],
        "unknown_board": ladder.get("unknown_board"),
        "summary": ladder_module.summary(ladder),
    }


def _flow_metrics(day: date | str, root: Any) -> dict[str, Any]:
    records, backend = _records("fund_flow", day, root)
    if not records:
        return {
            "available": False,
            "reason": "本地缓存没有资金流向数据集（先运行 ldst sync）",
            "source_detail": f"fund_flow: {backend}",
        }
    return {
        "available": True,
        "records": len(records),
        "source_detail": f"fund_flow: {backend}（{len(records)} 条）",
    }


def _theme_metrics(day: date | str, root: Any) -> dict[str, Any]:
    industry, industry_backend = _records("industry_board", day, root)
    concept, concept_backend = _records("concept_board", day, root)
    news, news_backend = _records("theme_news", day, root)
    available = bool(industry or concept or news)
    snapshot: dict[str, Any] = {
        "available": available,
        "industry_rows": len(industry),
        "concept_rows": len(concept),
        "news_rows": len(news),
        "source_detail": (
            f"industry_board: {industry_backend}; concept_board: {concept_backend}; "
            f"theme_news: {news_backend}"
        ),
    }
    if not available:
        snapshot["reason"] = "本地缓存没有板块/题材数据（先运行 ldst sync）"
    return snapshot


def _dragon_tiger_metrics(day: date | str, root: Any) -> dict[str, Any]:
    board, board_backend = _records("dragon_tiger", day, root)
    seats, seats_backend = _records("dragon_tiger_seats", day, root)
    available = bool(board or seats)
    snapshot: dict[str, Any] = {
        "available": available,
        "board_rows": len(board),
        "seat_rows": len(seats),
        "source_detail": (
            f"dragon_tiger: {board_backend}; dragon_tiger_seats: {seats_backend}"
        ),
    }
    if not available:
        snapshot["reason"] = "本地缓存没有龙虎榜数据（先运行 ldst sync）"
    return snapshot


# --------------------------------------------------------------------------- #
# 生成本地复盘
# --------------------------------------------------------------------------- #

def generate(
    day: date | str,
    *,
    root: Any = None,
    config_root: Any = None,
    config: fallback.AiConfig | None = None,
    client: Any = None,
    force: bool = False,
    retry_failed: bool = False,
    only: str | None = None,
) -> tuple[dict[str, Any], RunResult]:
    """跑一遍复盘流水线并落盘；返回 `(report, run_result)`。"""
    metrics = collect_metrics(day, root=root)
    orchestrator = Orchestrator(
        config=config, client=client, root=root, config_root=config_root
    )
    run = orchestrator.run(day, metrics, force=force, retry_failed=retry_failed, only=only)

    sentiment = sentiment_module.compute_sentiment(day, root=root)
    ladder = ladder_module.compute_ladder(day, root=root)
    report = build_report(day, run, metrics, sentiment, ladder, root=root)
    write_report(report, day, root)
    _write_conditions(day, sentiment, ladder, root)
    return report, run


def build_report(
    day: date | str,
    run: RunResult,
    metrics: dict[str, dict[str, Any]],
    sentiment: dict[str, Any],
    ladder: dict[str, Any],
    *,
    root: Any = None,
) -> dict[str, Any]:
    """把编排结果组装成 `review/export.py` 认识的复盘文档。"""
    sections: list[dict[str, Any]] = []
    for section in run.sections:
        sections.append(
            {
                "name": section.get("name"),
                "title": section.get("title"),
                "status": section.get("status"),
                "mode": section.get("mode"),
                "model": section.get("model"),
                "degraded": bool(section.get("degraded")),
                "narrative": section.get("narrative") or [],
                "metrics": section.get("metrics") or {},
                "notes": section.get("notes") or [],
            }
        )

    return {
        "title": f"{day} 短线复盘",
        "date": str(day),
        "generated_at": _now(),
        "freshness": FRESHNESS,
        "source_status": _source_status(run),
        "source_detail": _source_detail(metrics),
        "ai": {"model": "、".join(run.models) or None, "degraded": run.degraded},
        "headline": _headline(sentiment, ladder),
        "sections": sections,
        "ai_failures": list(run.ai_failures),
        "validation": {
            "date": _condition_day(day, root),
            "conditions": [
                item["text"]
                for item in validation_module.build_conditions(day, sentiment, ladder)
            ],
        },
        "footer": FOOTER,
    }


def write_report(report: dict[str, Any], day: date | str, root: Any = None) -> list[Path]:
    """写规范位置 + 兼容位置；两处内容一致，任一失败不影响另一处。"""
    written: list[Path] = []
    for path in (review_path(day, root), mirror_path(day, root)):
        try:
            validation_module.write_document(path, report)
            written.append(path)
        except OSError:
            continue
    return written


def _write_conditions(
    day: date | str,
    sentiment: dict[str, Any],
    ladder: dict[str, Any],
    root: Any,
) -> None:
    conditions = validation_module.build_conditions(day, sentiment, ladder)
    if not conditions:
        return
    document = {
        "date": str(day),
        "generated_at": _now(),
        "source": "review_pipeline",
        "conditions": conditions,
    }
    try:
        validation_module.write_document(validation_module.validation_path(day, root), document)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# 杂项
# --------------------------------------------------------------------------- #

def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _source_status(run: RunResult) -> str:
    if run.failed:
        return "partial"
    if run.degraded:
        return "degraded"
    return "ok"


def _source_detail(metrics: dict[str, dict[str, Any]]) -> str:
    parts = [
        str(snapshot.get("source_detail"))
        for snapshot in metrics.values()
        if snapshot.get("source_detail")
    ]
    return "；".join(parts)


def _headline(sentiment: dict[str, Any], ladder: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    if sentiment.get("available"):
        lines.append(
            f"情绪温度 {sentiment.get('temperature')}，"
            f"晋级率 {sentiment.get('promotion_rate')}%，封板率 {sentiment.get('seal_rate')}%"
        )
    else:
        lines.append(f"情绪指标缺失：{sentiment.get('reason')}")
    lines.append(ladder_module.summary(ladder))
    if sentiment.get("gap_levels"):
        levels = "、".join(f"{level} 板" for level in sentiment["gap_levels"])
        lines.append(f"梯队断层：{levels}")
    return lines


def _condition_day(day: date | str, root: Any = None) -> str:
    """验证条件的适用日 = 下一交易日（本地无数据时退回次日）。"""
    try:
        start = date.fromisoformat(str(day)[:10])
    except ValueError:
        return str(day)
    return (validation_module.next_session(start, root=root) or _next_weekday(start)).isoformat()


def _next_weekday(day: date) -> date:
    candidate = day + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def section_order() -> tuple[str, ...]:
    return checkpoint.SECTIONS


__all__ = [
    "CANONICAL_SUBDIR",
    "MIRROR_SUBDIR",
    "build_report",
    "collect_metrics",
    "generate",
    "ladder_metrics",
    "latest_report",
    "load_report",
    "mirror_path",
    "review_path",
    "section_order",
    "sentiment_metrics",
    "write_report",
]
