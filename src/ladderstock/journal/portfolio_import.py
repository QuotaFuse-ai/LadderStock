"""交易流水批量导入：先预览（不写库），再一次性写入（写前备份、整批回滚）。

三条硬约束（与 `journal/portfolio.py` 同一条）：

1. **预览不写库**：`preview()` 只读 `portfolio_trades` 并用同一套校验跑一遍重放，
   返回逐行的 `problem` / `duplicate`；连备份都不会落。
2. **写入即整批**：`import_rows()` 把每一行重新校验一遍；任何一行不合法、重复、
   或会让某笔卖出超过持有，整批 422 拒绝，**一行都不写**。真正写入时先备份，
   再在 `BEGIN IMMEDIATE` 事务里批量插入，中途失败回滚。
3. **重复不静默**：和已记流水完全相同的行标 `duplicate=existing`；和本批前面某行
   相同的标 `duplicate=table` 并给出前一行下标。导入阶段一律拒绝，不靠前端自觉。

行数 / 请求体上限照参考实现（1000 行 / 240KB）：超限是用法错误（422），
提示「分成几份」，不让请求半路失败。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from ladderstock.core import portfolio as core_portfolio
from ladderstock.core.portfolio import Trade, replay
from ladderstock.data import sqlite_store
from ladderstock.errors import EXIT_USAGE, LadderStockError
from ladderstock.journal import portfolio as journal_portfolio

#: 单次导入行数上限（与参考实现 `CSV_MAX_ROWS` 一致）
MAX_IMPORT_ROWS = 1000

#: 单次请求体上限：参考实现的 240KB，留出 JSON 包装余量
MAX_IMPORT_BYTES = 240 * 1024

#: 预览 / 导入共用的请求体键（多一个就 400，不做模糊匹配）
BODY_FIELDS: tuple[str, ...] = ("method", "records", "selected")

_NUMERIC_KEYS: tuple[str, ...] = ("shares", "price", "fee", "amount", "ratio")

#: 模拟流水用的 id 起点：与真实自增 id 隔开，重放问题清单据此区分「本批」
SIMULATION_BASE = 1_000_000_000

#: 模拟流水的录入时间：排序要落在全部已记流水之后（真实写入的自增 id 也排在后面），
#: 否则同一天的一笔新卖出会排到已记买入前面，预览凭空报「卖超」
SIMULATED_CREATED_AT = "9999-12-31T23:59:59+00:00"

_INSERT_TRADE = (
    "INSERT INTO portfolio_trades"
    " (symbol, name, type, trade_date, shares, price, fee, amount, ratio, note,"
    "  migrated_from, undated, created_at)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 0, ?)"
)


def _usage(message: str) -> LadderStockError:
    return LadderStockError(message, EXIT_USAGE)


def _method(raw: Any) -> core_portfolio.CostMethod:
    value = "average" if raw is None or raw == "" else str(raw).strip().lower()
    if value not in core_portfolio.COST_METHODS:
        raise _usage(
            f"成本法只能是 {' / '.join(core_portfolio.COST_METHODS)}，收到 {raw!r}"
        )
    return cast(core_portfolio.CostMethod, value)


def _body(
    raw: Any, *, allowed: Sequence[str], what: str
) -> tuple[core_portfolio.CostMethod, list[Any], list[bool] | None]:
    """校验外层请求体，返回（成本法, 原始 records, selected 或 None）。"""
    if not isinstance(raw, Mapping):
        raise _usage(f"{what}的请求体必须是 JSON 对象")
    unknown = sorted(str(key) for key in raw if str(key) not in allowed)
    if unknown:
        raise _usage(f"不认识的字段：{', '.join(unknown)}（只收 {', '.join(allowed)}）")
    method = _method(raw.get("method"))
    records = raw.get("records")
    if not isinstance(records, list) or not records:
        raise _usage("records 必须是非空数组：一行一笔流水")

    selected_raw = raw.get("selected")
    selected: list[bool] | None = None
    if selected_raw is not None:
        if not isinstance(selected_raw, list) or len(selected_raw) != len(records):
            raise _usage("selected 必须是和 records 等长的布尔数组")
        if any(not isinstance(flag, bool) for flag in selected_raw):
            raise _usage("selected 里只能是 true / false")
        selected = [bool(flag) for flag in selected_raw]

    size = len(json.dumps(raw, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if size > MAX_IMPORT_BYTES:
        raise _usage(
            f"这份表格太大（约 {size // 1024}KB，上限 {MAX_IMPORT_BYTES // 1024}KB）："
            "请分成几份导入"
        )
    if len(records) > MAX_IMPORT_ROWS:
        raise _usage(
            f"一次最多导入 {MAX_IMPORT_ROWS} 行，这份有 {len(records)} 行：请分成几份"
        )
    return method, records, selected


def _number_key(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value), 6)


def _signature(fields: Mapping[str, Any]) -> tuple[Any, ...]:
    """判断「同一笔流水」的键：代码 / 类型 / 日期 / 五个数字（备注不参与）。"""
    return (
        fields["symbol"],
        fields["type"],
        fields["trade_date"],
        *(_number_key(fields.get(key)) for key in _NUMERIC_KEYS),
    )


def _clean_rows(records: Sequence[Any]) -> list[tuple[dict[str, Any] | None, str | None]]:
    """逐行做字段校验；返回（清洗后的字段, 问题）。"""
    out: list[tuple[dict[str, Any] | None, str | None]] = []
    for record in records:
        try:
            out.append((journal_portfolio.trade_write_fields(record), None))
        except LadderStockError as exc:
            out.append((None, exc.message))
    return out


def _duplicates(
    cleaned: Sequence[dict[str, Any] | None],
    existing: Sequence[Trade],
) -> tuple[list[tuple[str | None, int | None]], dict[tuple[Any, ...], int]]:
    """标出「和已记的一样」与「和本批前一行一样」。

    返回（逐行 duplicate / duplicate_of, 本批签名表）。本批签名表给导入阶段做断言用。
    """
    existing_by_signature: dict[tuple[Any, ...], int] = {}
    for trade in existing:
        signature = (
            trade.symbol,
            trade.type,
            trade.date,
            *(_number_key(getattr(trade, key)) for key in _NUMERIC_KEYS),
        )
        existing_by_signature.setdefault(signature, trade.id)

    seen: dict[tuple[Any, ...], int] = {}
    out: list[tuple[str | None, int | None]] = []
    for index, fields in enumerate(cleaned):
        if fields is None:
            out.append((None, None))
            continue
        signature = _signature(fields)
        if signature in existing_by_signature:
            out.append(("existing", existing_by_signature[signature]))
            continue
        if signature in seen:
            out.append(("table", seen[signature]))
            continue
        seen[signature] = index
        out.append((None, None))
    return out, seen


def _simulate(
    method: core_portfolio.CostMethod,
    existing: Sequence[Trade],
    cleaned: Sequence[dict[str, Any] | None],
    selected: Sequence[bool],
) -> tuple[dict[int, str], list[dict[str, Any]]]:
    """按当前勾选跑一遍重放：返回（本批被判问题的行 → 原因, 会让已记流水失效的清单）。

    字段问题用真实 id 之外的负下标没有意义，所以模拟流水用 `SIMULATION_BASE + index`
    作 id——重放问题清单里 id 落在这一段的，就是本批造成的。
    """
    proposed: list[Trade] = []
    for index, fields in enumerate(cleaned):
        if fields is None or not selected[index]:
            continue
        proposed.append(
            Trade(
                id=SIMULATION_BASE + index,
                symbol=fields["symbol"],
                name=fields["name"],
                type=fields["type"],
                date=fields["trade_date"],
                shares=fields["shares"],
                price=fields["price"],
                fee=fields["fee"],
                amount=fields["amount"],
                ratio=fields["ratio"],
                note=fields["note"],
                created_at=SIMULATED_CREATED_AT,
            )
        )

    baseline = {issue.id for issue in replay(list(existing), method).issues}
    after = replay([*existing, *proposed], method)
    batch_problems: dict[int, str] = {}
    existing_issues: list[dict[str, Any]] = []
    for issue in after.issues:
        if issue.id >= SIMULATION_BASE:
            batch_problems[issue.id - SIMULATION_BASE] = issue.why
        elif issue.id not in baseline:
            existing_issues.append(
                {
                    "id": issue.id,
                    "symbol": issue.symbol,
                    "date": issue.date,
                    "type": issue.type,
                    "why": issue.why,
                }
            )
    return batch_problems, existing_issues


def _validate(
    raw: Any,
    *,
    allow_selected: bool,
    root: Path | None,
) -> tuple[
    core_portfolio.CostMethod,
    list[dict[str, Any]],
    list[dict[str, Any] | None],
    list[str | None],
    list[tuple[str | None, int | None]],
    list[dict[str, Any]],
]:
    """预览 / 导入共用的整条校验链（只读，不写库）。"""
    allowed = BODY_FIELDS if allow_selected else ("method", "records")
    method, records, selected_raw = _body(raw, allowed=allowed, what="导入")
    selected = selected_raw if selected_raw is not None else [True] * len(records)

    cleaned_pairs = _clean_rows(records)
    cleaned = [fields for fields, _ in cleaned_pairs]
    problems = [problem for _, problem in cleaned_pairs]

    existing = journal_portfolio.list_trades(root=root)
    duplicates, _ = _duplicates(cleaned, existing)
    batch_problems, existing_issues = _simulate(method, existing, cleaned, selected)
    for index, why in batch_problems.items():
        problems[index] = problems[index] or why

    return method, records, cleaned, problems, duplicates, existing_issues


def preview(raw: Any, *, root: Path | None = None) -> dict[str, Any]:
    """逐行校验 + 重复标记；**不写库，也不备份**。"""
    _, records, cleaned, problems, duplicates, existing_issues = _validate(
        raw, allow_selected=True, root=root
    )
    rows: list[dict[str, Any]] = []
    for index in range(len(cleaned)):
        duplicate, duplicate_of = duplicates[index]
        rows.append(
            {
                "index": index,
                "problem": problems[index],
                "duplicate": duplicate,
                "duplicate_of": duplicate_of,
            }
        )
    return {
        "ok": True,
        "count": len(records),
        "limit": MAX_IMPORT_ROWS,
        "max_bytes": MAX_IMPORT_BYTES,
        "rows": rows,
        "existing_issues": existing_issues,
    }


def _report(problems: Sequence[tuple[int, str]], total: int) -> LadderStockError:
    shown = "；".join(f"第 {index + 1} 行：{why}" for index, why in problems[:5])
    more = f"（另有 {len(problems) - 5} 行）" if len(problems) > 5 else ""
    return _usage(f"这次导入没有写入：{len(problems)}/{total} 行有问题。{shown}{more}")


def import_rows(raw: Any, *, root: Path | None = None) -> dict[str, Any]:
    """再次校验 → 备份 → 事务内批量写入；任何一行失败整批不写。"""
    _, records, cleaned, problems, duplicates, _ = _validate(
        raw, allow_selected=False, root=root
    )
    failures: list[tuple[int, str]] = []
    for index, fields in enumerate(cleaned):
        if fields is None:
            failures.append((index, problems[index] or "这一行不合法"))
            continue
        if problems[index]:
            failures.append((index, problems[index] or "这一行不合法"))
            continue
        duplicate, _ = duplicates[index]
        if duplicate == "existing":
            failures.append((index, "和已记的一笔流水完全一样"))
        elif duplicate == "table":
            failures.append((index, "和本批前面的一行完全一样"))
    if failures:
        raise _report(failures, len(records))

    assert all(fields is not None for fields in cleaned)
    fields_list = [fields for fields in cleaned if fields is not None]
    conn = sqlite_store.connect(root=root, create=True)
    assert conn is not None
    try:
        backup = journal_portfolio.backup_now(conn, root=root)
        ids: list[int] = []
        try:
            conn.execute("BEGIN IMMEDIATE")
            stamp = sqlite_store.now_iso()
            for fields in fields_list:
                cursor = conn.execute(
                    _INSERT_TRADE,
                    (
                        fields["symbol"],
                        fields["name"],
                        fields["type"],
                        fields["trade_date"],
                        fields["shares"],
                        fields["price"],
                        fields["fee"],
                        fields["amount"],
                        fields["ratio"],
                        fields["note"],
                        stamp,
                    ),
                )
                ids.append(int(cursor.lastrowid or 0))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        sqlite_store.close(conn)

    return {
        "ok": True,
        "imported": len(ids),
        "ids": ids,
        "backup": backup,
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


__all__ = [
    "BODY_FIELDS",
    "MAX_IMPORT_BYTES",
    "MAX_IMPORT_ROWS",
    "import_rows",
    "preview",
]
