"""F9 回访：到期记录拉日线、判对错、回写结果。

为什么单独成模块而不是并进 `judge`：`judge` 是纯函数（给定 bars 出结论），
回访要碰 IO（扫库、取行情、回写）。把 IO 全留在这一层，纯逻辑才守得住测试；
取行情也做成可注入的 `BarFetcher`，单测不需要联网。

三条运行纪律：

- **缺行情不算失败**。取不到日线的记录保持 `open`，下一轮再试——把"没数据"
  当成"判错"，等于自己给自己扣分。
- **能判就当场定终态**。翻转条件触发即 `flipped`，到期且方向应验即 `hit/miss`，
  不再让记录挂在 `open` 上漂着。
- **回访失败不影响服务**。异常只记日志，由调用方（API / 定时任务）决定要不要报。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Sequence

from ladderstock.ai import ledger
from ladderstock.data import chat_store

logger = logging.getLogger(__name__)

#: `(symbol, base_date) -> [(交易日, 收盘)]`，升序，只含基准日之后
BarFetcher = Callable[[str, str], Sequence[tuple[str, float]]]

#: 本机日线缓存通常只覆盖自选那几十只票，现取一条要 60 根才够 20 个交易日
LIVE_BAR_LIMIT = 60


def run_recall(
    conn: Any,
    *,
    data_root: Any = None,
    as_of: str | None = None,
    limit: int = 200,
    fetch_bars: BarFetcher | None = None,
) -> dict[str, Any]:
    """扫一遍到期记录并回写判定，返回本轮统计。

    `as_of` 缺省取 Asia/Shanghai 的今天：回访说的是"到今天为止对不对"，
    用 UTC 会在跨零点那几小时里把昨天当成今天。
    """
    day = (as_of or _today())[:10]
    fetcher = fetch_bars or (lambda symbol, base: _bars_for(symbol, base, data_root))
    due = chat_store.due_records(conn, day, limit=limit)
    stats = {
        "as_of": day,
        "due": len(due),
        "hit": 0,
        "miss": 0,
        "flipped": 0,
        "undecided": 0,
        "waiting": 0,
        "skipped": 0,
    }
    for record in due:
        symbol = str(record.get("symbol") or "").strip()
        base_date = str(record.get("base_date") or "").strip()[:10]
        if not symbol or not base_date:
            _close(conn, record, chat_store.RECORD_UNDECIDED, {"reason": "缺标的代码或基准日"})
            stats["undecided"] += 1
            continue
        try:
            bars = list(fetcher(symbol, base_date))
        except Exception as exc:  # noqa: BLE001 - 取数失败留在 open，下一轮再试
            logger.warning("recall_fetch_failed symbol=%s detail=%s", symbol, exc)
            stats["skipped"] += 1
            continue
        if not bars:
            stats["skipped"] += 1
            continue
        verdict = ledger.judge(record, bars)
        status = str(verdict.get("status") or chat_store.RECORD_UNDECIDED)
        reason = str(verdict.get("reason") or "")
        # 「还没到到期日」「没有可用行情」都只是暂时判不了，记录留 open 等下一轮；
        # 其余 undecided（无基准价、翻转条件是毛利率这类非价格指标）是终态。
        if status == "undecided" and ("没有可用行情" in reason or "还没到到期日" in reason):
            stats["waiting"] += 1
            continue
        if status not in (chat_store.RECORD_HIT, chat_store.RECORD_MISS, chat_store.RECORD_FLIPPED):
            status = chat_store.RECORD_UNDECIDED
        _close(conn, record, status, verdict)
        stats[status] = stats.get(status, 0) + 1
    return stats


def _close(
    conn: Any,
    record: dict[str, Any],
    status: str,
    outcome: dict[str, Any],
) -> None:
    chat_store.close_record(
        conn,
        str(record.get("record_id")),
        status=status,
        outcome=outcome,
    )


def _bars_for(symbol: str, base_date: str, data_root: Any = None) -> list[tuple[str, float]]:
    """基准日之后的日线收盘：先本机 `daily_k`，不够再现取。

    本机缓存与现取是同口径（tickhub `tencent_kline`，日线前复权），
    所以两种来源可以拼在一条序列里，不需要标注来源差异。
    """
    code = _code(symbol)
    if not code:
        return []
    rows = _local_bars(code, data_root)
    if not rows:
        rows = _live_bars(code)
    return _after(rows, base_date)


def _local_bars(code: str, data_root: Any) -> list[tuple[str, float]]:
    from pathlib import Path

    from ladderstock.data import duckdb_store

    try:
        root = Path(data_root) if data_root else None
        records = duckdb_store.query_dataset("daily_k", code=code, root=root)
    except Exception as exc:  # noqa: BLE001 - 本机库不可用只是缺缓存
        logger.warning("recall_local_bars_failed code=%s detail=%s", code, exc)
        return []
    return _pairs(records)


def _live_bars(code: str) -> list[tuple[str, float]]:
    from ladderstock.data.provider_registry import ProviderRegistry

    try:
        outcome = ProviderRegistry(root=None).fetch_live(
            "kline_chart",
            params={
                "code": code,
                "period": "daily",
                "adjust": "qfq",
                "limit": LIVE_BAR_LIMIT,
            },
            allow_cache=False,
            timeout=10.0,
        )
        payload = getattr(getattr(outcome, "envelope", None), "payload", {}) or {}
        records = [row for row in (payload.get("records") or []) if isinstance(row, dict)]
    except Exception as exc:  # noqa: BLE001 - 现取失败同样是缺口
        logger.warning("recall_live_bars_failed code=%s detail=%s", code, exc)
        return []
    return _pairs(records)


def _pairs(records: Sequence[Any]) -> list[tuple[str, float]]:
    pairs: list[tuple[str, float]] = []
    for row in records:
        if not isinstance(row, dict):
            continue
        day = str(row.get("trade_date") or "")[:10]
        close = row.get("close")
        if not day or isinstance(close, bool) or not isinstance(close, (int, float)):
            continue
        pairs.append((day, float(close)))
    pairs.sort(key=lambda item: item[0])
    return pairs


def _after(rows: Sequence[tuple[str, float]], base_date: str) -> list[tuple[str, float]]:
    base = str(base_date)[:10]
    return [(day, close) for day, close in rows if day > base]


def _code(symbol: str) -> str | None:
    import re

    match = re.search(r"(\d{6})", str(symbol or ""))
    return match.group(1) if match else None


def _today() -> str:
    return datetime.now(timezone(timedelta(hours=8))).date().isoformat()


__all__ = ["BarFetcher", "LIVE_BAR_LIMIT", "run_recall"]
