"""`ldst sync` 命令体：按水位增量同步生态 peer，落 DuckDB 与 Parquet 日分区。

实现与注册分离（`cli/main.py` 只 import + 注册），便于测试注入假 transport。

一次 `sync` 对每个数据集做四件事（DEC-007 §2.3）：

1. 取水位（`--full` 显式忽略），增量请求带上 `since`；
2. 走「主工具 → 同 peer 备工具 → 本地缓存回放」链路（provider_registry）；
3. 幂等落库：`daily_k` 按 `(code, trade_date)` upsert，其余按 `trade_date` 整日覆盖；
4. 写成功才推进水位并记一条运行记录——中途失败可从水位断点续跑。

退出码：全部成功 0；部分必需数据集失败 8；**一个都没成**（peer 全挂且无缓存）
4；配置/边界错误 3。`optional: true` 的数据集（Wire / Senate）取不到只记
`skipped`，不拖垮整体。
"""

from __future__ import annotations

import sys
from collections import Counter
from datetime import date, datetime
from typing import Annotated, Any

import typer

from ladderstock.cli.output import OutputOption, emit, error
from ladderstock.core.boundary import BoundaryViolation, owner_of
from ladderstock.core.models import LadderEnvelope
from ladderstock.data import duckdb_store, watermark
from ladderstock.data.provider_registry import (
    ProviderRegistry,
    SourceUnavailable,
)
from ladderstock.errors import (
    EXIT_CONFIG,
    EXIT_OK,
    EXIT_PARTIAL,
    EXIT_SOURCE,
    EXIT_USAGE,
    LadderStockError,
)

MODE_INCREMENTAL = "incremental"
MODE_FULL = "full"

# 视为「这一天跑成功了」的状态：只有 failed 才影响退出码
GOOD_STATUSES = ("ok", "degraded", "empty")


def _log(message: str) -> None:
    """进度日志一律走 stderr，stdout 只留结构化结果（docs/CLI.md §F）。"""
    sys.stderr.write(f"ldst: {message}\n")
    sys.stderr.flush()


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


# --------------------------------------------------------------------------- #
# 记录与水位
# --------------------------------------------------------------------------- #

def envelope_records(envelope: LadderEnvelope) -> list[dict[str, Any]]:
    """从信封取待落库记录。

    - `payload.records` 是记录列表时直接用（TickHub 形状）；
    - 否则把整个 payload 当成**一行**快照（Wire 叙事 / Senate 报告没有行概念，
      不硬凑字段，DEC-006 闸门 2）；
    - 列表里的非 dict 项包成 `{"text": ...}`，保留原文而不是丢弃。
    """
    payload = envelope.payload or {}
    raw = payload.get("records")
    if isinstance(raw, list):
        rows: list[dict[str, Any]] = []
        for item in raw:
            if isinstance(item, dict):
                rows.append(dict(item))
            elif item is not None:
                rows.append({"text": item if isinstance(item, str) else str(item)})
        return rows
    if payload:
        return [dict(payload)]
    return []


def watermark_value(
    dataset: str, records: list[dict[str, Any]], envelope: LadderEnvelope
) -> str | None:
    """这条链路的最新日期：记录里水位字段的最大值，退化到 `data_as_of`。"""
    field = owner_of(dataset).watermark_field
    days: list[date] = []
    for record in records:
        day = _as_date(record.get(field))
        if day is None:
            day = duckdb_store.record_day(record)
        if day is not None:
            days.append(day)
    if days:
        return max(days).isoformat()
    day = _as_date(envelope.data_as_of)
    return day.isoformat() if day else None


# --------------------------------------------------------------------------- #
# 单个数据集
# --------------------------------------------------------------------------- #

def _skip_row(
    dataset: str,
    *,
    mode: str,
    status: str,
    detail: str,
    before: str | None,
    optional: bool,
    source: str = "-",
    trail: list[str] | None = None,
    from_cache: bool = False,
) -> dict[str, Any]:
    return {
        "dataset": dataset,
        "status": status,
        "mode": mode,
        "rows": 0,
        "total_rows": 0,
        "days": [],
        "source": source,
        "optional": optional,
        "from_cache": from_cache,
        "watermark_before": before,
        "watermark_after": before,
        "detail": detail,
        "trail": list(trail or []),
    }


def sync_one(
    registry: ProviderRegistry,
    store: watermark.WatermarkStore,
    dataset: str,
    *,
    full: bool = False,
) -> dict[str, Any]:
    """同步一个数据集；返回可序列化的结果行。配置/边界错误向外抛。"""
    binding = owner_of(dataset)
    declared = registry.datasets.get(dataset) or {}
    optional = bool(declared.get("optional"))
    mode = MODE_FULL if full else MODE_INCREMENTAL
    before = None if full else store.value(dataset)
    params: dict[str, Any] = {} if full or not before else {"since": before}
    started = _now_iso()
    scope = "全量" if full else (f"水位 {before} 之后" if before else "首次全量")
    _log(f"sync {dataset}（{scope}）")

    try:
        outcome = registry.fetch(dataset, params=params)
    except SourceUnavailable as exc:
        status = "skipped" if optional else "failed"
        detail = (
            f"可选数据集，peer 不可用已跳过：{exc}"
            if optional
            else f"生态链路全部不可用且无本地缓存：{exc}"
        )
        _log(f"  {dataset} {status}：{detail}")
        store.record_run(
            dataset,
            mode=mode,
            status=status,
            rows=0,
            source="-",
            trail=[detail],
            started_at=started,
            watermark_before=before,
            watermark_after=before,
            detail=detail,
        )
        return _skip_row(
            dataset, mode=mode, status=status, detail=detail, before=before, optional=optional
        )

    envelope = outcome.envelope
    if outcome.from_cache:
        detail = "peer 全部不可用，回放本地缓存（freshness=cached，未重写、未推进水位）"
        _log(f"  {dataset} degraded：{detail}")
        store.record_run(
            dataset,
            mode=mode,
            status="degraded",
            rows=0,
            source=outcome.source,
            trail=outcome.trail,
            started_at=started,
            watermark_before=before,
            watermark_after=before,
            detail=detail,
        )
        return _skip_row(
            dataset,
            mode=mode,
            status="degraded",
            detail=detail,
            before=before,
            optional=optional,
            source=outcome.source,
            trail=outcome.trail,
            from_cache=outcome.from_cache,
        )

    records = envelope_records(envelope)
    result = duckdb_store.write_dataset(
        dataset,
        records,
        mode=binding.mode,
        source=outcome.source,
        default_trade_date=_as_date(envelope.data_as_of),
    )

    value = watermark_value(dataset, records, envelope)
    if result.empty or not value:
        status = "empty"
        after = before
        detail = "peer 返回空记录，未覆盖已有数据，水位保持"
    else:
        status = "degraded" if (outcome.used_fallback or envelope.source_status != "ok") else "ok"
        entry = store.advance(
            dataset, value, rows=result.rows, source=outcome.source, status=status
        )
        after = entry.value
        detail = f"{result.rows} 行 / {len(result.days)} 天，分区 {len(result.partitions)} 个"
        if outcome.used_fallback:
            detail += "（主源不可用，已切同 peer 备源）"

    _log(f"  {dataset} {status}：{detail}，水位 {before or '-'} → {after or '-'}")
    store.record_run(
        dataset,
        mode=mode,
        status=status,
        rows=result.rows,
        source=outcome.source,
        trail=outcome.trail,
        started_at=started,
        watermark_before=before,
        watermark_after=after,
        detail=detail,
    )
    return {
        "dataset": dataset,
        "status": status,
        "mode": mode,
        "rows": result.rows,
        "total_rows": result.total_rows,
        "days": list(result.days),
        "source": outcome.source,
        "optional": optional,
        "from_cache": False,
        "watermark_before": before,
        "watermark_after": after,
        "detail": detail,
        "trail": list(outcome.trail),
    }


def run_sync(
    registry: ProviderRegistry,
    store: watermark.WatermarkStore,
    datasets: list[str],
    *,
    full: bool = False,
) -> dict[str, Any]:
    """按顺序同步多个数据集，汇总成 `ldst sync` 的输出载荷。"""
    started = _now_iso()
    rows: list[dict[str, Any]] = []
    for dataset in datasets:
        try:
            rows.append(sync_one(registry, store, dataset, full=full))
        except BoundaryViolation as exc:
            # 主备跨 peer / 未登记归属：配置问题，整轮中止（DEC-006 闸门 3）
            raise LadderStockError(str(exc), EXIT_CONFIG) from exc

    counts = Counter(row["status"] for row in rows)
    failed = counts.get("failed", 0)
    succeeded = sum(counts.get(name, 0) for name in GOOD_STATUSES)
    if failed and succeeded:
        code = EXIT_PARTIAL
    elif failed:
        code = EXIT_SOURCE
    else:
        code = EXIT_OK
    return {
        "ok": code == EXIT_OK,
        "mode": MODE_FULL if full else MODE_INCREMENTAL,
        "started_at": started,
        "finished_at": _now_iso(),
        "state_file": str(store.path),
        "summary": {
            "datasets": len(rows),
            "ok": counts.get("ok", 0),
            "degraded": counts.get("degraded", 0),
            "empty": counts.get("empty", 0),
            "skipped": counts.get("skipped", 0),
            "failed": failed,
            "exit_code": code,
        },
        "results": rows,
    }


# --------------------------------------------------------------------------- #
# 命令
# --------------------------------------------------------------------------- #

def _targets(registry: ProviderRegistry, dataset: str | None) -> list[str]:
    declared = list(registry.datasets)
    if not declared:
        error("config/sync.yaml 没有声明任何数据集")
        raise typer.Exit(EXIT_CONFIG)
    if dataset is None:
        return declared
    if dataset not in registry.datasets:
        error(f"未知数据集 {dataset}；可选：{', '.join(declared)}")
        raise typer.Exit(EXIT_USAGE)
    return [dataset]


def sync_command(
    ctx: typer.Context,
    dataset: Annotated[str | None, typer.Option(help="只同步指定数据集")] = None,
    full: Annotated[bool, typer.Option("--full", help="强制全量（忽略水位，维护用）")] = False,
    output: OutputOption = None,
) -> None:
    if ctx.invoked_subcommand:
        return
    registry = ProviderRegistry()
    targets = _targets(registry, dataset)
    store = watermark.WatermarkStore(root=registry.root)
    payload = run_sync(registry, store, targets, full=full)
    emit(payload, output)
    raise typer.Exit(payload["summary"]["exit_code"])


def sync_status(output: OutputOption = None) -> None:
    from ladderstock.cli.paths import data_dir

    registry = ProviderRegistry()
    store = watermark.WatermarkStore(root=registry.root)
    payload = store.status(limit=20)
    payload["active_cooldowns"] = registry.health.active_cooldowns()
    payload["data_dir"] = str(registry.root if registry.root is not None else data_dir())
    emit(payload, output)


def register(sync_app: typer.Typer) -> None:
    """把 `sync` 回调与子命令挂到 main.py 已建好的 Typer 组上。"""
    sync_app.callback()(sync_command)
    sync_app.command("status", help="水位线与最近同步状态")(sync_status)
