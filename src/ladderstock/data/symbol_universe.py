"""全市场标的字典（`symbol_basic`）：代码段穷举 + 批量报价 → 代码/名称整表。

**为什么是穷举**：生态里没有「名称 → 代码」的检索工具——`stock_profile` /
`baostock_stock_basic` 都要先给代码，`iwencai_search` 要 API key。唯一能一次带回
名称的工具是 `tencent_quote_batch`（`data.quotes` 里每行带 `name`），而它收的是
**代码列表**。于是用一个本地事实（A 股代码段分布）生成候选号码，按 50 只一批
问 peer「这些代码叫什么」，把有效的挑出来落成字典。

三条口径：

- **只走 peer**：候选号码是本机生成的事实，取数仍走 `ProviderRegistry.fetch_live`
  的 `symbol_basic` 链路（`sources.yaml` 的 tickhub + `tencent_quote_batch`），
  不直连任何行情站点（DEC-006 闸门 2）。
- **不编名称**：peer 没回的行就是不存在，不猜、不按代码顶替名称。
- **按需刷新，不按日增量**：3.5 万个候选号码 ≈ 700 次往返，只在新股上市 /
  改名 / 首次装机时刷新，因此不进 `sync.yaml` 的每日调度。
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from ladderstock.data import duckdb_store

DATASET = "symbol_basic"

#: 批量报价一次能收多少只（`tencent_quote_batch` 的上游上限）
CHUNK_CODES = 50

#: 并发批数。**默认串行**：`ProviderRegistry` 的 MCP 客户端对同一个 peer 不是线程安全的
#: （实测 4 线程并发在同一 peer 上跑到第 100 批就整体停住，既不返回也不抛错，只能杀进程）。
#: 串行时一批约 1 秒，702 批 ≈ 12 分钟——这正是「按需刷新」而不是每日调度的原因。
DEFAULT_WORKERS = 1

#: 候选代码段（左闭右开）。取值来自 A 股实际代码段分布：沪主板 600/601/602/603/605、
#: 科创板 688/689、深主板 000/001/002/003、创业板 300/301/302、北交所 43/83/87/920。
#: 穷举是**唯一不依赖代码表工具**的办法，代价是大量空段——所以只按需刷新。
CODE_RANGES: tuple[tuple[int, int], ...] = (
    (600000, 604000),   # 沪主板 600 / 601 / 602 / 603
    (605000, 606000),   # 沪主板 605
    (688000, 689100),   # 科创板 688 / 689（含 CDR）
    (1, 4000),          # 深主板 000 / 001 / 002 / 003
    (300000, 303000),   # 创业板 300 / 301 / 302
    (430000, 431000),   # 北交所（原三板）43
    (830000, 840000),   # 北交所 83
    (870000, 880000),   # 北交所 87
    (920000, 921000),   # 北交所新代码 920
)


def candidate_codes(ranges: Iterable[tuple[int, int]] | None = None) -> list[str]:
    """候选号码（去重保序）：6 位十进制字符串。"""
    out: list[str] = []
    seen: set[str] = set()
    for start, end in ranges or CODE_RANGES:
        for number in range(start, end):
            code = f"{number:06d}"
            if code not in seen:
                seen.add(code)
                out.append(code)
    return out


@dataclass
class RefreshResult:
    """一次刷新的结果：落库行数、批次次数、失败批数、耗时。"""

    rows: int = 0
    total_rows: int = 0
    batches: int = 0
    failed_batches: list[int] = field(default_factory=list)
    codes_asked: int = 0
    days: list[str] = field(default_factory=list)
    #: 前几条失败批的原因（诊断用：全 0 的时候能直接看到是「链路没登记」还是「上游拒参」）
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset": DATASET,
            "rows": self.rows,
            "total_rows": self.total_rows,
            "codes_asked": self.codes_asked,
            "batches": self.batches,
            "failed_batches": len(self.failed_batches),
            "days": list(self.days),
            "errors": list(self.errors),
        }


def _row_of(record: dict[str, Any]) -> dict[str, Any] | None:
    """一行记录 → 字典表的一行；没有代码或名称的直接丢（不编名称）。"""
    code = str(record.get("code") or "").strip()
    name = str(record.get("name") or "").strip()
    if not code or not name:
        return None
    volume = record.get("volume")
    try:
        active = float(volume or 0) > 0
    except (TypeError, ValueError):
        active = False
    return {
        "code": code,
        "name": name,
        "market": str(record.get("market") or "").strip().upper() or None,
        "price": record.get("price"),
        "prev_close": record.get("prev_close"),
        "volume": volume,
        "active": active,
    }


def fetch_chunk(
    registry: Any,
    codes: list[str],
    *,
    trade_date: date | None = None,
) -> list[dict[str, Any]]:
    """一批（≤50 只）问 peer「这些代码叫什么」，返回可落库的行。"""
    envelope = registry.fetch_live(
        DATASET,
        trade_date=trade_date,
        params={"codes": ",".join(codes)},
    )
    payload = envelope.envelope.payload or {}
    records = payload.get("records")
    if not isinstance(records, list):
        return []
    rows: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        row = _row_of(record)
        if row is not None:
            rows.append(row)
    return rows


def refresh(
    registry: Any,
    *,
    codes: list[str] | None = None,
    chunk: int = CHUNK_CODES,
    workers: int = DEFAULT_WORKERS,
    root: Path | None = None,
    merge_existing: bool | None = None,
    progress: Callable[[int, int, int], None] | None = None,
) -> RefreshResult:
    """跑一轮全市场字典刷新：分批取数 → 合并 → 一天一份整表落库。

    单批失败不拖垮整轮（记进 `failed_batches`）；**全部失败**才返回空结果，
    不覆盖已有字典——`write_dataset` 收到空记录时本身也不会覆盖。

    `merge_existing` 默认「传了部分 `codes` 就合并」：局部刷新只补/改那几只，
    不把整张字典表缩成这一小撮。
    """
    pool = list(codes) if codes is not None else candidate_codes()
    if merge_existing is None:
        merge_existing = codes is not None
    if not pool:
        return RefreshResult()
    chunks = [pool[i : i + chunk] for i in range(0, len(pool), chunk)]
    merged: dict[str, dict[str, Any]] = {}
    failed: list[int] = []
    errors: list[str] = []
    done_lock = threading.Lock()
    done = {"count": 0}
    today = date.today()

    def run(index: int, part: list[str]) -> tuple[int, list[dict[str, Any]], bool]:
        """一批号码 → (批号, 行, 是否真失败)。

        **空返回不算失败**：代码段里本来就有大量空号（602 段几乎无股票），
        `tencent_quote_batch` 对不存在的代码静默丢弃，回 0 行是正常结果。
        只有抛异常（链路不通 / 上游拒参）才是失败。
        """
        try:
            return index, fetch_chunk(registry, part, trade_date=today), False
        except Exception as exc:  # noqa: BLE001 - 单批失败记下来，不让整轮崩
            with done_lock:
                if len(errors) < 5:
                    errors.append(f"批 {index}: {type(exc).__name__}: {exc}")
            return index, [], True

    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = [executor.submit(run, i, part) for i, part in enumerate(chunks)]
        for future in as_completed(futures):
            index, rows, hard_fail = future.result()
            if hard_fail:
                failed.append(index)
            for row in rows:
                merged[row["code"]] = row
            with done_lock:
                done["count"] += 1
                if progress is not None:
                    progress(done["count"], len(chunks), len(merged))

    result = RefreshResult(
        rows=len(merged),
        batches=len(chunks),
        failed_batches=failed,
        codes_asked=len(pool),
        errors=errors,
    )
    if not merged:
        return result

    # 全量口径（不合并旧行）下，失败批过多说明这轮不是「几个坏号码」而是链路有问题：
    # 此时写库会把整张字典缩成残缺的一份，宁可保留旧的完整字典。
    if not merge_existing and len(failed) > max(1, int(len(chunks) * 0.1)):
        result.errors.append(
            f"失败批过多（{len(failed)}/{len(chunks)}），保留旧字典不覆盖"
        )
        return result

    # 局部刷新（`--codes` 只覆盖一部分号码）不能把整表缩成这几只：
    # 已有行先并进来，本次结果按 code 覆盖。全量穷举时不需要这步——
    # 那一次的候选号码本来就覆盖全市场，多余旧行（退市/改名）留给下一轮全量清。
    if merge_existing:
        for row in duckdb_store.query_dataset(DATASET, root=root):
            code = str(row.get("code") or "").strip()
            name = str(row.get("name") or "").strip()
            if not code or not name or code in merged:
                continue
            merged[code] = {
                "code": code,
                "name": name,
                "market": row.get("market"),
                "price": row.get("price"),
                "prev_close": row.get("prev_close"),
                "volume": row.get("volume"),
                "active": row.get("active"),
            }

    payload: list[dict[str, Any]] = []
    for code, row in merged.items():
        payload.append({**row, "trade_date": today.isoformat()})
    write = duckdb_store.write_dataset(
        DATASET,
        payload,
        source="tickhub:tencent_quote_batch",
        default_trade_date=today,
        root=root,
    )
    result.total_rows = write.total_rows
    result.days = list(write.days)
    return result


def latest_trade_date(root: Path | None = None) -> date | None:
    """字典里最新的写入日（判断是否该刷新）。"""
    path = duckdb_store.db_path(root)
    if not path.is_file():
        return None
    conn = duckdb_store.connect(path, read_only=True)
    try:
        if not duckdb_store.table_exists(conn, DATASET):
            return None
        row = conn.execute(f"select max(trade_date) from {DATASET}").fetchone()
        if not row or row[0] is None:
            return None
        return duckdb_store.record_day({"trade_date": row[0]})
    except Exception:  # noqa: BLE001 - 坏表按「没有字典」处理
        return None
    finally:
        duckdb_store.close(conn)


def count_rows(root: Path | None = None) -> int:
    """字典行数（医生诊断与 CLI 展示用）。"""
    try:
        return duckdb_store.dataset_rows(DATASET, root=root)
    except Exception:  # noqa: BLE001
        return 0


__all__ = [
    "CHUNK_CODES",
    "CODE_RANGES",
    "DATASET",
    "RefreshResult",
    "candidate_codes",
    "count_rows",
    "fetch_chunk",
    "latest_trade_date",
    "refresh",
]
