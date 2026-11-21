"""F12 数据覆盖面：数据集级「已注册 / 已落盘 / 覆盖池比例 / 缺口原因」+ 受控补采。

与 `data/coverage.py` 的分工（两个东西，别混）：

- `coverage.check_coverage(symbol)` 是 **F1 单票判定**：回答「这只票的财报/行情/公告够不够新」，
  读的是 FinanceCoffer 的 PostgreSQL（DWD/DWS）；
- 这里是 **F12 数据集看板**：回答「62 个数据集各自落了多少、池里 85 只盖了多少、为什么缺」，
  读的是本机 DuckDB/Parquet。前者面向问答，后者面向运维。

三条硬口径：

1. **只报事实**：行数、最新日期、覆盖比例都从库现算；表不存在就写「从未入库」，
   不拿别的表凑、不补 0 冒充有数据；
2. **补采受控**：默认 `dry_run=True`，只出计划不发请求；真跑要显式 `execute=True`，
   且单数据集单批 `max_codes` 硬顶（默认 100，与 `sync.yaml` 的单批上限一致），
   超了直接拒绝并提示分批——不做全量、不并发压源；
3. **补采复用既有通道**：走 `cli/sync.py:run_sync + sync_one`（provider_registry 的限流/冷却/
   降级都在那一层），本模块不新写采集器。

`pool` 口径与定时采集一致：`config/sync.yaml` 的 `sync.datasets.daily_k.codes`（YAML 锚点
`&base_pool`，当前 85 只）。
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

#: 逐票数据集：这些表的 `code` 是股票代码，算「池内盖了多少」有参考价值
PER_CODE_DATASETS: tuple[str, ...] = (
    "daily_k",
    "finance_indicators",
    "valuation_history",
    "fund_flow",
    "consensus_forecast",
    "institution_survey",
    "holder_trades",
    "research_report",
    "share_buyback",
    "lockup_calendar",
    "announcement_szse",
    # 2026-11-21：巨潮公告 / 东财个股新闻同样是 code 必填的逐票工具，
    # 补采计划必须带代码池（与 sync.yaml 的 base_pool 同口径）。
    "announcement_cninfo",
    "news_eastmoney_stock",
)

#: 应当覆盖全池的数据集：这些覆盖不足才算缺口。
#: 其余（龙虎榜、调研、研报、回购、解禁等）是**事件型**——只有发生了才有行，
#: 拿池比例当缺口会把「正常」误报成红。
POOL_REQUIRED: tuple[str, ...] = (
    "daily_k",
    "finance_indicators",
    "valuation_history",
    "fund_flow",
    "consensus_forecast",
)

#: 字典 / 映射类：内容本来就随版本更新，不按交易日新鲜度判
STATIC_DATASETS: tuple[str, ...] = (
    "sw_industry_map",
    "symbol_basic",
    "concept_board",
    "concept_membership",
    "broken_board",
)

#: 新鲜度：不同域容忍不同（行情 3 天、财报 130 天、新闻类 7 天，其余 14 天）
FRESH_DAYS: dict[str, int] = {
    "daily_k": 3,
    "index_daily": 3,
    "spot_quote": 3,
    "fund_flow": 3,
    "fund_flow_realtime": 1,
    "finance_indicators": 130,
    "valuation_history": 30,
    "research_report": 30,
    "announcement_cninfo": 30,
    "announcement_szse": 30,
    "flash_cls": 7,
    "flash_eastmoney": 7,
    "flash_wallstreetcn": 7,
    "theme_news": 7,
    "news_cctv": 7,
    "sentiment": 7,
}
DEFAULT_FRESH_DAYS = 14

#: 候选日期列，按优先级探测（不同数据集字段名不同）
DATE_COLUMNS: tuple[str, ...] = (
    "trade_date",
    "date",
    "ann_date",
    "publish_time",
    "report_date",
    "as_of",
    "updated_at",
)

MAX_BATCH_CODES = 100

STATE_OK = "ok"
STATE_PARTIAL = "partial"
STATE_STALE = "stale"
STATE_EMPTY = "empty"
STATE_NEVER = "never"
STATE_UNREGISTERED = "unregistered"

STATUS_ORDER = (STATE_OK, STATE_PARTIAL, STATE_STALE, STATE_EMPTY, STATE_NEVER, STATE_UNREGISTERED)

GAP_LABELS: dict[str, str] = {
    STATE_PARTIAL: "池内覆盖不全",
    STATE_STALE: "最近更新过旧",
    STATE_EMPTY: "表在但零行",
    STATE_NEVER: "从未入库（表不存在或无行）",
    STATE_UNREGISTERED: "未在 sync.yaml 注册主源",
}

#: 状态 → 中文标签（含 ok，给 CLI / 前端直接用，别各写一份）
STATE_LABELS: dict[str, str] = {STATE_OK: "正常", **GAP_LABELS}


def _config_dir() -> Path:
    from ladderstock.cli.paths import config_dir

    return config_dir()


def _data_root(root: Path | None) -> Path:
    if root is not None:
        return Path(root)
    from ladderstock.cli.paths import data_dir

    return data_dir()


def load_pool(config_root: Path | None = None) -> list[str]:
    """代码池：与定时采集同一份口径（`sync.datasets.daily_k.codes`）。"""
    path = (config_root if config_root is not None else _config_dir()) / "sync.yaml"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return []
    block: Any = None
    if isinstance(raw, dict):
        sync_block = raw.get("sync")
        if isinstance(sync_block, dict):
            datasets = sync_block.get("datasets")
            block = datasets.get("daily_k") if isinstance(datasets, dict) else None
            if block is None:
                block = sync_block.get("daily_k")
        if block is None:
            block = raw.get("daily_k")
    codes = block.get("codes") if isinstance(block, dict) else None
    if not isinstance(codes, list):
        return []
    out: list[str] = []
    for code in codes:
        text = str(code).strip()
        if text and text not in out:
            out.append(text)
    return out


def declared_datasets(config_root: Path | None = None) -> dict[str, dict[str, Any]]:
    """`sync.yaml` 里声明了的数据集 → `{primary, fallback, mode, per_code}`。"""
    path = (config_root if config_root is not None else _config_dir()) / "sync.yaml"
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    sync_block = raw.get("sync") if isinstance(raw, dict) else None
    datasets = sync_block.get("datasets") if isinstance(sync_block, dict) else None
    if not isinstance(datasets, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for name, block in datasets.items():
        if not isinstance(block, dict):
            continue
        out[str(name)] = {
            "primary": str(block.get("primary") or ""),
            "fallback": [str(item) for item in (block.get("fallback") or [])],
            "mode": str(block.get("mode") or ""),
            "per_code": bool(block.get("codes")),
        }
    return out


def catalog_datasets() -> list[str]:
    """`core/boundary.py:DATASET_CATALOG` 的全部数据集名。"""
    from ladderstock.core import boundary

    return sorted(boundary.DATASET_CATALOG.keys())


def _table_columns(conn: Any, table: str) -> list[str]:
    try:
        rows = conn.execute(f"DESCRIBE {table}").fetchall()
    except Exception:  # noqa: BLE001 - 表不存在 / 描述失败都当没有
        return []
    return [str(row[0]) for row in rows]


def _pick_date_column(columns: Iterable[str]) -> str | None:
    names = list(columns)
    for candidate in DATE_COLUMNS:
        if candidate in names:
            return candidate
    return None


@dataclass
class DatasetStat:
    dataset: str
    table: str
    registered: bool
    primary: str
    rows: int
    codes: int
    latest: str | None
    age_days: int | None
    pool_codes: int
    pool_covered: int
    pool_relevant: bool
    state: str
    reason: str
    date_column: str | None

    @property
    def coverage(self) -> float | None:
        if not self.pool_relevant or not self.pool_codes:
            return None
        return round(self.pool_covered / self.pool_codes, 4)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "table": self.table,
            "registered": self.registered,
            "primary": self.primary,
            "rows": self.rows,
            "codes": self.codes,
            "latest": self.latest,
            "age_days": self.age_days,
            "date_column": self.date_column,
            "pool_codes": self.pool_codes,
            "pool_covered": self.pool_covered,
            "pool_relevant": self.pool_relevant,
            "coverage": self.coverage,
            "state": self.state,
            "reason": self.reason,
        }


def _dataset_stat(
    conn: Any,
    dataset: str,
    *,
    declared: dict[str, Any] | None,
    pool: list[str],
    today: date,
) -> DatasetStat:
    from ladderstock.data import duckdb_store

    table = duckdb_store._table(dataset)  # noqa: SLF001 - 带引号的表名，只有一处规则
    display = table.strip('"')
    registered = declared is not None
    primary = str((declared or {}).get("primary") or "")
    pool_codes = len(pool)
    pool_relevant = dataset in PER_CODE_DATASETS

    try:
        exists = False if conn is None else duckdb_store.table_exists(conn, dataset)
    except Exception:  # noqa: BLE001
        exists = False
    if not exists:
        state = STATE_UNREGISTERED if not registered else STATE_NEVER
        return DatasetStat(
            dataset,
            display,
            registered,
            primary,
            0,
            0,
            None,
            None,
            pool_codes,
            0,
            pool_relevant,
            state,
            GAP_LABELS[state],
            None,
        )

    columns = _table_columns(conn, table)
    date_column = _pick_date_column(columns)
    try:
        if date_column:
            total_rows = int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] or 0)
            latest_raw = conn.execute(f'SELECT max("{date_column}") FROM {table}').fetchone()[0]
            latest = str(latest_raw)[:10] if latest_raw else None
        else:
            total_rows = int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] or 0)
            latest = None
    except Exception:  # noqa: BLE001
        total_rows, latest = 0, None

    has_code = "code" in columns
    try:
        codes = (
            int(conn.execute(f"SELECT count(DISTINCT code) FROM {table}").fetchone()[0] or 0)
            if has_code
            else 0
        )
    except Exception:  # noqa: BLE001
        codes = 0

    covered = 0
    if has_code and pool and pool_relevant:
        try:
            placeholders = ", ".join("?" for _ in pool)
            covered = int(
                conn.execute(
                    f"SELECT count(DISTINCT code) FROM {table} WHERE code IN ({placeholders})",
                    pool,
                ).fetchone()[0]
                or 0
            )
        except Exception:  # noqa: BLE001
            covered = 0

    age_days: int | None = None
    if latest:
        try:
            age_days = max(0, (today - date.fromisoformat(latest)).days)
        except ValueError:
            age_days = None

    fresh_limit = FRESH_DAYS.get(dataset, DEFAULT_FRESH_DAYS)
    state = STATE_OK
    reason = ""
    if total_rows <= 0:
        state, reason = STATE_EMPTY, GAP_LABELS[STATE_EMPTY]
    elif not registered:
        # 有数据但没在 sync.yaml 注册：数据是别的通道写进来的（如 symbol_basic 由字典导入）。
        # 只做提示，不算缺口——否则「补采」按钮无处可点。
        state = STATE_OK
        reason = "有数据但未在 sync.yaml 注册主源（补采前先注册）"
    elif pool_relevant and pool and covered < pool_codes and dataset in POOL_REQUIRED:
        state = STATE_PARTIAL
        reason = f"{GAP_LABELS[STATE_PARTIAL]}：{covered}/{pool_codes}"
    elif age_days is not None and age_days > fresh_limit and dataset not in STATIC_DATASETS:
        state = STATE_STALE
        reason = (
            f"{GAP_LABELS[STATE_STALE]}：最近 {latest}"
            f"（{age_days} 天前，阈值 {fresh_limit} 天）"
        )
    elif pool_relevant and pool and covered < pool_codes:
        reason = f"事件型数据集，池内覆盖 {covered}/{pool_codes}（发生了才有行，覆盖低属正常）"
    return DatasetStat(
        dataset,
        display,
        registered,
        primary,
        total_rows,
        codes,
        latest,
        age_days,
        pool_codes,
        covered,
        pool_relevant,
        state,
        reason,
        date_column,
    )


def board(
    root: Path | None = None,
    *,
    config_root: Path | None = None,
    today: date | None = None,
    datasets: list[str] | None = None,
) -> dict[str, Any]:
    """数据集覆盖面看板：62 个目录项逐个给 `state` + 缺口原因。"""
    from ladderstock.data import duckdb_store

    pool = load_pool(config_root)
    declared = declared_datasets(config_root)
    names = datasets or catalog_datasets()
    day = today or date.today()
    started = time.perf_counter()

    stats: list[DatasetStat] = []
    # 库文件不存在时不能以 read_only 打开（DuckDB 会报错，看板直接 500）。
    # 这时候正确结论就是「一个表都没有」，而不是让页面挂掉。
    db_file = duckdb_store.db_path(_data_root(root))
    conn = duckdb_store.connect(db_file, read_only=True) if db_file.is_file() else None
    try:
        for name in names:
            stats.append(
                _dataset_stat(
                    conn,
                    name,
                    declared=declared.get(name),
                    pool=pool,
                    today=day,
                )
            )
    finally:
        if conn is not None:
            duckdb_store.close(conn)

    counts = {state: 0 for state in STATUS_ORDER}
    for stat in stats:
        counts[stat.state] = counts.get(stat.state, 0) + 1

    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        "db": {"path": str(db_file), "exists": db_file.is_file()},
        "pool": {"size": len(pool), "codes": pool[:20], "truncated": len(pool) > 20},
        "declared": len(declared),
        "datasets": [stat.as_dict() for stat in stats],
        "unregistered": [
            stat.as_dict() for stat in stats if stat.state == STATE_UNREGISTERED
        ],
        "summary": {
            "total": len(stats),
            "counts": counts,
            "ok": counts.get(STATE_OK, 0),
            # 「缺口」只算有行/该有行却没到位的：未接入的 27 个数据集是「还没开始」，
            # 不是「坏了」，混在一起会让需要处理的数字虚高。
            "needs_attention": len(stats)
            - counts.get(STATE_OK, 0)
            - counts.get(STATE_UNREGISTERED, 0),
            "unregistered": counts.get(STATE_UNREGISTERED, 0),
        },
    }


def gaps(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """只挑需要处理的：按严重度排序（never → empty → partial → stale）。

    「未在 sync.yaml 注册」不在此列：那是还没接入，不是缺口；单独走 `unregistered`。
    """
    rank = {state: index for index, state in enumerate(STATUS_ORDER)}
    rows = [
        row
        for row in payload.get("datasets") or []
        if row["state"] not in (STATE_OK, STATE_UNREGISTERED)
    ]
    return sorted(rows, key=lambda row: (rank.get(row["state"], 99), row["dataset"]))


def plan(
    dataset: str,
    *,
    codes: list[str] | None = None,
    config_root: Path | None = None,
    max_codes: int = MAX_BATCH_CODES,
) -> dict[str, Any]:
    """补采计划：先校验注册与批量上限，再说明这批会请求什么（不发请求）。"""
    declared = declared_datasets(config_root)
    block = declared.get(dataset)
    if block is None:
        raise ValueError(f"{dataset} 没在 config/sync.yaml 注册，补采无处可去（先加主源）")
    if not block["primary"]:
        raise ValueError(f"{dataset} 的 primary 为空，补采会直接失败")
    pool = list(codes or load_pool(config_root))
    if block["per_code"] and not pool:
        raise ValueError(f"{dataset} 是逐票数据集，但代码池为空（检查 sync.yaml 的 daily_k.codes）")
    if block["per_code"] and len(pool) > max_codes:
        raise ValueError(
            f"{dataset} 本次 {len(pool)} 只超过单批上限 {max_codes}；"
            f"请分批（每次 ≤{max_codes}）后重试"
        )
    return {
        "dataset": dataset,
        "primary": block["primary"],
        "fallback": block["fallback"],
        "per_code": block["per_code"],
        "codes": pool if block["per_code"] else [],
        "code_count": len(pool) if block["per_code"] else 0,
        "max_codes": max_codes,
        "requests_hint": (
            f"约 {len(pool)} 只 × 1 批（provider_registry 自带限流与冷却）"
            if block["per_code"]
            else "单个请求（无 code 维度）"
        ),
    }


def backfill(
    dataset: str,
    *,
    codes: list[str] | None = None,
    execute: bool = False,
    root: Path | None = None,
    config_root: Path | None = None,
    max_codes: int = MAX_BATCH_CODES,
) -> dict[str, Any]:
    """受控补采：默认只出计划；`execute=True` 才真跑一次（单数据集、单批）。"""
    started = time.perf_counter()
    prepared = plan(
        dataset,
        codes=codes,
        config_root=config_root,
        max_codes=max_codes,
    )
    prepared["execute"] = bool(execute)
    prepared["root"] = str(_data_root(root))
    if not execute:
        prepared["status"] = "planned"
        prepared["hint"] = "这是计划，没有发任何请求；确认后用 --execute 真跑（单数据集单批）"
        return prepared

    from ladderstock.cli import sync as sync_module
    from ladderstock.data import watermark
    from ladderstock.data.db_lock import writer_lock
    from ladderstock.data.provider_registry import ProviderRegistry

    registry = ProviderRegistry(root=Path(config_root) if config_root else None)
    store = watermark.WatermarkStore(root=registry.root)
    with writer_lock(holder=f"ldst coverage backfill {dataset}"):
        payload = sync_module.run_sync(
            registry,
            store,
            [dataset],
            codes=prepared["codes"] if prepared["per_code"] else None,
        )
    result = (payload.get("results") or [{}])[0]
    return {
        **prepared,
        "status": result.get("status") or "unknown",
        "detail": result.get("detail") or "",
        "rows_stored": result.get("rows"),
        "total_rows": result.get("total_rows"),
        "source": result.get("source"),
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        "sync": payload.get("summary"),
    }


__all__ = [
    "DEFAULT_FRESH_DAYS",
    "FRESH_DAYS",
    "GAP_LABELS",
    "MAX_BATCH_CODES",
    "PER_CODE_DATASETS",
    "POOL_REQUIRED",
    "STATIC_DATASETS",
    "STATUS_ORDER",
    "STATE_LABELS",
    "STATE_EMPTY",
    "STATE_NEVER",
    "STATE_OK",
    "STATE_PARTIAL",
    "STATE_STALE",
    "STATE_UNREGISTERED",
    "DatasetStat",
    "backfill",
    "board",
    "catalog_datasets",
    "declared_datasets",
    "gaps",
    "load_pool",
    "plan",
]
