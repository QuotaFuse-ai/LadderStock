"""`ldst sync` 命令体：按水位增量同步生态 peer，落 DuckDB 与 Parquet 日分区。

实现与注册分离（`cli/main.py` 只 import + 注册），便于测试注入假 transport。

一次 `sync` 对每个数据集做四件事（DEC-007 §2.3）：

1. 取水位（`--full` 显式忽略），增量请求带上 `since`；
2. 走「主工具 → 同 peer 备工具 → 本地缓存回放」链路（provider_registry）；
3. 幂等落库：`daily_k` 按 `(code, trade_date)` upsert，其余按 `trade_date` 整日覆盖；
4. 写成功才推进水位并记一条运行记录——中途失败可从水位断点续跑。

**代码池数据集**（`daily_k` / `fund_flow` / `dragon_tiger_seats`）另走一条分支：
真实工具 schema 里 `code` 是必填（`tencent_kline` 空参实测回 `QFSD-9001`，QUOT-233），
所以必须**逐只代码各取一次**再合并落库，池子来源只有三处、按优先级：

1. 本次命令行 `--codes 600000,000001`；
2. `config/sync.yaml` 里该数据集的 `codes`；
3. 本机隐私库的自选表 `watchlist`（只读代码，日志与输出里只出现数量）。

三处都为空时**不发空参请求**（那正是把整包压成 1 行、日期取成抓取时间的旧洞），
直接判该数据集失败并在 detail 里写清去哪配。

**赛道池数据集**（`theme_news`，Wire）是同一条纪律的另一个形状：`wire_get_digest`
的 `topic` 必填且只认**赛道 slug**（`semiconductor`，不是「半导体」），一次调用只覆盖
一个赛道，所以按 `config/sync.yaml` 的 `topics` 逐个取数再合并落库（QUOT-328）。
空池同样一次调用都不发——空参直接被上游判 `topic Field required`，`theme_news`
因此永远同步不到。

退出码：全部成功 0；部分必需数据集失败 8；**一个都没成**（peer 全挂且无缓存）
4；配置/边界错误 3；**历史库被别的进程占着**（拿锁超时 / 打库重试耗尽）10。
`optional: true` 的数据集（Wire / Senate）取不到只记
`skipped`，不拖垮整体。

**并发口径（QUOT-234）**：命令一进来先拿本机历史库的进程级写锁
（`data/db_lock.py`），同库的多个 `ldst sync` 排队而不是互相把对方的
`duckdb.connect()` 顶成 `IOException`；等待上限 `LDST_DB_LOCK_TIMEOUT`
（秒，默认 30），超时明确报错。没走这个入口的写方（`ldst review`、归档读）
由 `duckdb_store.connect()` 的有界退避重试兜底。
"""

from __future__ import annotations

import sqlite3
import sys
from collections import Counter
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any

import typer

from ladderstock.cli.output import OutputOption, emit, error
from ladderstock.core.boundary import BoundaryViolation, owner_of
from ladderstock.core.models import LadderEnvelope
from ladderstock.data import duckdb_store, sqlite_store, watermark
from ladderstock.data.adapters.tickhub import needs_code, tool_params
from ladderstock.data.adapters.wire import needs_topic
from ladderstock.data.adapters.wire import tool_params as wire_tool_params
from ladderstock.data.db_lock import writer_lock
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

#: 代码池单次上限：一只票一次工具调用，peer 侧有并发与最小间隔（DEC-007 §2.4），
#: 上千只会在一次同步里打爆限流。超了要拆批，而不是悄悄截断。
MAX_POOL_CODES = 100

#: 池子来源（写进 detail，便于复盘时知道这次是谁提供的代码）
POOL_CLI = "命令行 --codes"
POOL_CONFIG = "config/sync.yaml"

#: 扇出内的连续失败止损上限（QUOT-328）。扇出不吃 source 冷却——一次失败常常只是
#: 「这个入参上游不认识」，按冷却判下去就是 1 个坏元素废掉整池。代价是 peer 真下线时
#: 会把整池撞一遍，所以留这道闸：连续这么多项全失败，就判定它是不健康而不是入参问题，
#: 剩余元素不发起调用。上限取 3：单个坏元素后面仍连着 1 次成功，不会误伤。
FANOUT_FAILURE_STOP = 3
POOL_WATCHLIST = "本机自选(watchlist)"

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
# 代码池（QUOT-233：`code` 必填的工具必须有池子可填）
# --------------------------------------------------------------------------- #

def clean_codes(values: Any) -> list[str]:
    """把 `--codes 600000,000001` 与 yaml 列表统一成去重保序的代码列表。"""
    codes: list[str] = []
    items = values if isinstance(values, (list, tuple, set)) else [values]
    for value in items:
        if value is None:
            continue
        for part in str(value).replace("，", ",").split(","):
            code = part.strip()
            if code and code not in codes:
                codes.append(code)
    return codes


def watchlist_codes(root: Path | None = None) -> list[str]:
    """本机隐私库的自选表代码。库不存在就返回空——**只读，不因为查一下就建库**。

    代码本身要发给本机 peer 才能取行情，但日志与对外输出里只出现数量，
    不出现「谁在自选里」这件事（DEC-006 隐私边界：只进不出本机）。
    """
    conn = sqlite_store.connect(root, create=False)
    if conn is None:
        return []
    try:
        if "watchlist" not in sqlite_store.table_names(conn):
            return []
        rows = conn.execute("SELECT code FROM watchlist ORDER BY created_at").fetchall()
    except sqlite3.Error:  # 库损坏 / 表结构不符：宁可不取，也不要把一次同步打断
        return []
    finally:
        sqlite_store.close(conn)
    return clean_codes([row["code"] for row in rows])


def resolve_code_pool(
    dataset: str, *, declared: dict[str, Any], cli_codes: list[str], root: Path | None
) -> tuple[list[str], str]:
    """按 `--codes` → `sync.yaml` 的 `codes` → 本机自选 取代码池，返回 (池子, 来源)。"""
    if cli_codes:
        return cli_codes, POOL_CLI
    configured = clean_codes(declared.get("codes") or [])
    if configured:
        return configured, POOL_CONFIG
    watched = watchlist_codes(root)
    if watched:
        return watched, POOL_WATCHLIST
    return [], "-"


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


def sync_code_pool(
    registry: ProviderRegistry,
    store: watermark.WatermarkStore,
    dataset: str,
    *,
    binding: Any,
    declared: dict[str, Any],
    mode: str,
    before: str | None,
    full: bool,
    optional: bool,
    started: str,
    cli_codes: list[str],
) -> dict[str, Any]:
    """`code` 必填的数据集：逐只代码各取一次，合并成一个事务落库。

    单只失败不拖垮整轮（记进 detail），**全失败**才按 `failed` / 缓存回放 `degraded` 处理；
    池子为空则一次调用都不发——空参请求会被 peer 拒回 `QFSD-9001`，
    即便某些桩接受了空参，落下来的也是「整包压成 1 行、日期取成抓取时间」的脏数据。
    """
    codes, pool_source = resolve_code_pool(
        dataset, declared=declared, cli_codes=cli_codes, root=registry.root
    )
    scope = "全量" if full else (f"水位 {before} 之后" if before else "首次全量")
    _log(f"sync {dataset}（{scope}，代码池 {len(codes)} 只/{pool_source}）")

    if len(codes) > MAX_POOL_CODES:
        raise LadderStockError(
            f"{dataset} 代码池 {len(codes)} 只，超过单次上限 {MAX_POOL_CODES} 只；"
            f"请分批判 `--codes` 同步（全市场口径请用登记在册的盘后包备源，不新供商）",
            EXIT_USAGE,
        )

    return sync_per_item(
        registry,
        store,
        dataset,
        binding=binding,
        mode=mode,
        before=before,
        optional=optional,
        started=started,
        items=codes,
        pool_desc=f"代码池 {len(codes)} 只（{pool_source}）",
        unit="只",
        params_for=lambda code: tool_params(
            dataset, code=code, since=None if full else before
        ),
        empty_detail=(
            f"代码池为空：`--codes` / config/sync.yaml 的 sync.datasets.{dataset}.codes / "
            f"本机自选均为空，未发起调用（{binding.tool} 的 code 必填，"
            "空参实测回 QFSD-9001）"
        ),
    )


# --------------------------------------------------------------------------- #
# 赛道池（QUOT-328：`topic` 必填的 Wire 数据集）
# --------------------------------------------------------------------------- #

def resolve_topic_pool(declared: dict[str, Any]) -> list[str]:
    """`config/sync.yaml` 里该数据集的 `topics`：Wire 认的是**赛道 slug**。

    中文名（`半导体`）上游直接判「未知赛道」，所以这里只收 slug，代码里也不留一份
    赛道名常量表——合法取值以 Wire 的报错回包为准（`未知赛道: …（可用: ai、semiconductor…）`）。
    """
    return clean_codes(declared.get("topics") or [])


def sync_topic_pool(
    registry: ProviderRegistry,
    store: watermark.WatermarkStore,
    dataset: str,
    *,
    binding: Any,
    declared: dict[str, Any],
    mode: str,
    before: str | None,
    full: bool,
    optional: bool,
    started: str,
) -> dict[str, Any]:
    """`topic` 必填的 Wire 数据集：按赛道 slug 逐个取数，合并成一个事务落库。

    Wire 的 `wire_get_digest(topic, date=None)` 里 `topic` 是必填赛道 slug，
    **没有「空参 = 取全部赛道」的调用形状**：空参直接吃 `1 validation error …
    topic Field required`，这就是 `theme_news` 永远 `unavailable` 的根因（QUOT-328）。
    摘要按天整批替换，Wire 侧没有增量入参，所以这里不带 `since`。
    """
    chain = registry.chain(dataset)
    for spec in chain:
        if not needs_topic(spec.tool):
            raise LadderStockError(
                f"{dataset} 的链路里 {spec.id} 用的是 {spec.tool}，它不是「按赛道 slug 取数」的工具"
                f"（`wire_get_timeline` 要 `event_id`，按日同步没有事件维度），"
                f"请把它从 config/sync.yaml 的 sync.datasets.{dataset}.fallback 里摘掉"
                "（DEC-043）",
                EXIT_CONFIG,
            )
    tool = chain[0].tool
    topics = resolve_topic_pool(declared)
    if len(topics) > MAX_POOL_CODES:
        raise LadderStockError(
            f"{dataset} 赛道池 {len(topics)} 个，超过单次上限 {MAX_POOL_CODES} 个；"
            "请分批写进 config/sync.yaml 的 topics（一次调用一个赛道，上限由 peer 侧"
            "并发与最小间隔决定，不悄悄截断）",
            EXIT_USAGE,
        )
    scope = "全量" if full else (f"水位 {before} 之后" if before else "首次全量")
    _log(f"sync {dataset}（{scope}，赛道 {len(topics)} 个/{POOL_CONFIG}）")

    return sync_per_item(
        registry,
        store,
        dataset,
        binding=binding,
        mode=mode,
        before=before,
        optional=optional,
        started=started,
        items=topics,
        pool_desc=f"赛道池 {len(topics)} 个（{POOL_CONFIG}）",
        unit="个",
        params_for=lambda topic: wire_tool_params(tool, topic=topic),
        empty_detail=(
            f"赛道池为空：config/sync.yaml 的 sync.datasets.{dataset}.topics 为空，"
            f"未发起调用（{tool} 的 topic 必填，空参实测回 `topic Field required` 校验错误；"
            "取值为 Wire 的赛道 slug，不是中文赛道名）"
        ),
    )


def sync_per_item(
    registry: ProviderRegistry,
    store: watermark.WatermarkStore,
    dataset: str,
    *,
    binding: Any,
    mode: str,
    before: str | None,
    optional: bool,
    started: str,
    items: list[str],
    pool_desc: str,
    unit: str,
    params_for: Callable[[str], dict[str, Any]],
    empty_detail: str,
) -> dict[str, Any]:
    """「一个元素一次工具调用」的数据集：逐项取数、合并、一次事务落库。

    上游把某个入参做成必填时（TickHub 的 `code`、Wire 的 `topic`），一次调用只覆盖
    一个元素，池子来源与单位由调用方给；纪律只有三条——**空池不发调用**、
    单项失败记进 detail 不拖垮整轮（**不吃 source 冷却**，见 `fetch(ignore_cooldown)`）、
    连续失败到 `FANOUT_FAILURE_STOP` 才止损，全失败才判 `failed`（可选数据集 `skipped`）。
    """
    if not items:
        status = "skipped" if optional else "failed"
        detail = empty_detail
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
            dataset,
            mode=mode,
            status=status,
            detail=detail,
            before=before,
            optional=optional,
        )

    rows: list[dict[str, Any]] = []
    sources: list[str] = []
    trail: list[str] = []
    failures: list[str] = []
    halted: list[str] = []
    cache_hits = 0
    degraded = False
    last_envelope: LadderEnvelope | None = None
    consecutive = 0

    for index, item in enumerate(items):
        if consecutive >= FANOUT_FAILURE_STOP:
            # 连续失败到上限：这就不像「某个入参被拒」而像 peer 整体不健康，
            # 剩下的元素不再逐个去撞（DEC-007 的止损，扇出内唯一保留的闸门）
            halted = items[index:]
            break
        try:
            outcome = registry.fetch(dataset, params=params_for(item), ignore_cooldown=True)
        except SourceUnavailable as exc:
            consecutive += 1
            failures.append(f"{item}：{_one_line(exc)}")
            continue
        consecutive = 0
        trail.extend(outcome.trail)
        if outcome.from_cache:
            cache_hits += 1
            continue
        envelope = outcome.envelope
        last_envelope = envelope
        rows.extend(envelope_records(envelope))
        sources.append(outcome.source)
        if outcome.used_fallback or envelope.source_status != "ok":
            degraded = True

    succeeded = len(items) - len(failures) - len(halted) - cache_hits
    halt_note = (
        f"；连续 {FANOUT_FAILURE_STOP} {unit}失败后止损，剩余 {len(halted)} {unit}未发起调用"
        if halted
        else ""
    )
    if not succeeded:
        if cache_hits:
            detail = (
                f"peer 全部不可用，回放本地缓存（{pool_desc}，freshness=cached，"
                "未重写、未推进水位）"
            )
            status, source = "degraded", "local_cache"
        else:
            detail = f"{pool_desc}全部失败：{_clip('; '.join(failures))}{halt_note}"
            status, source = ("skipped" if optional else "failed"), "-"
        _log(f"  {dataset} {status}：{detail}")
        store.record_run(
            dataset,
            mode=mode,
            status=status,
            rows=0,
            source=source,
            trail=trail,
            started_at=started,
            watermark_before=before,
            watermark_after=before,
            detail=detail,
        )
        return _skip_row(
            dataset,
            mode=mode,
            status=status,
            detail=detail,
            before=before,
            optional=optional,
            source=source,
            trail=trail,
            from_cache=bool(cache_hits),
        )

    result = duckdb_store.write_dataset(
        dataset,
        rows,
        mode=binding.mode,
        source=",".join(dict.fromkeys(sources)),
        default_trade_date=_as_date(last_envelope.data_as_of) if last_envelope else None,
    )
    value = (
        watermark_value(dataset, rows, last_envelope) if last_envelope is not None else None
    )
    if result.empty or not value:
        status = "empty"
        after = before
        detail = f"{pool_desc}返回空记录，未覆盖已有数据，水位保持"
    else:
        status = "degraded" if (degraded or failures or halted or cache_hits) else "ok"
        after = store.advance(
            dataset, value, rows=result.rows, source=result_source(sources), status=status
        ).value
        detail = f"{result.rows} 行 / {len(result.days)} 天，分区 {len(result.partitions)} 个"
        detail += f"；{pool_desc}，成功 {succeeded} {unit}"
        if failures:
            detail += f"，失败 {len(failures)} {unit}：{_clip('; '.join(failures))}"
        detail += halt_note
        if cache_hits:
            detail += f"，{cache_hits} {unit}走本地缓存"

    _log(f"  {dataset} {status}：{detail}，水位 {before or '-'} → {after or '-'}")
    store.record_run(
        dataset,
        mode=mode,
        status=status,
        rows=result.rows,
        source=result_source(sources),
        trail=trail,
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
        "source": result_source(sources),
        "optional": optional,
        "from_cache": False,
        "watermark_before": before,
        "watermark_after": after,
        "detail": detail,
        "trail": trail,
    }



def result_source(sources: list[str]) -> str:
    return ",".join(dict.fromkeys(sources)) or "-"


def _one_line(exc: BaseException) -> str:
    return " ".join(str(exc).split())


def _clip(text: str, limit: int = 240) -> str:
    return text if len(text) <= limit else f"{text[:limit]}…"


def sync_one(
    registry: ProviderRegistry,
    store: watermark.WatermarkStore,
    dataset: str,
    *,
    full: bool = False,
    codes: Any = None,
) -> dict[str, Any]:
    """同步一个数据集；返回可序列化的结果行。配置/边界错误向外抛。"""
    binding = owner_of(dataset)
    declared = registry.datasets.get(dataset) or {}
    optional = bool(declared.get("optional"))
    mode = MODE_FULL if full else MODE_INCREMENTAL
    before = None if full else store.value(dataset)
    started = _now_iso()
    pool_codes = clean_codes(codes)
    if binding.peer == "tickhub" and needs_code(dataset):
        return sync_code_pool(
            registry,
            store,
            dataset,
            binding=binding,
            declared=declared,
            mode=mode,
            before=before,
            full=full,
            optional=optional,
            started=started,
            cli_codes=pool_codes,
        )
    if binding.peer == "wire" and needs_topic(registry.chain(dataset)[0].tool):
        return sync_topic_pool(
            registry,
            store,
            dataset,
            binding=binding,
            declared=declared,
            mode=mode,
            before=before,
            full=full,
            optional=optional,
            started=started,
        )
    params: dict[str, Any] = {} if full or not before else {"since": before}
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
    codes: Any = None,
) -> dict[str, Any]:
    """按顺序同步多个数据集，汇总成 `ldst sync` 的输出载荷。"""
    started = _now_iso()
    rows: list[dict[str, Any]] = []
    for dataset in datasets:
        try:
            rows.append(sync_one(registry, store, dataset, full=full, codes=codes))
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
    codes: Annotated[
        list[str] | None,
        typer.Option(
            "--codes",
            help="本次代码池，逗号分隔或重复传（覆盖 config/sync.yaml 与本机自选）；"
            "仅 `code` 必填的数据集生效",
        ),
    ] = None,
    output: OutputOption = None,
) -> None:
    if ctx.invoked_subcommand:
        return
    registry = ProviderRegistry()
    targets = _targets(registry, dataset)
    store = watermark.WatermarkStore(root=registry.root)
    # 进程级串行：DuckDB 库文件锁是独占的，两个 sync 并发就是一个必抛 IOException。
    # 锁跟着 `write_dataset()` 真正写的那个库走（data_dir），不跟 registry.root——
    # 落库路径本来就不读 registry.root，锁在它上面等于没锁。
    with writer_lock(holder="ldst sync"):
        payload = run_sync(registry, store, targets, full=full, codes=codes)
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
