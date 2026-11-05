"""我的自选：本机隐私库 `watchlist` 表的读写（DEC-052 读面 + DEC-055 写面修订）。

表结构只有三列（`data/sqlite_store.py:221`）：`code`（UNIQUE）/ `note` / `created_at`。

三条硬约束（与 `journal/position.py` 同一条）：

1. **只依赖本机隐私库**：`data/sqlite_store` + 标准库。不 import 适配器、行情缓存、
   MCP、AI，不产生任何网络请求——「自选在看什么」与「行情可不可用」是两个问题，
   不混在同一个出口里。
2. **写面只写本机 SQLite**（DEC-052 §2.3 经 QUOT-419 / DEC-055 修订）：增删有两个入口——
   本机页面 `POST /api/watchlist` / `…/{code}/delete`（回环独占 + 来源证据）和 CLI
   `ldst watch add|rm`，两者落到同一个函数、同一张表。写面仍然只有回环一条路，
   不进信封、不上传、不进 AI 上下文。
   这张表此前**只有 schema、没有生产者**，数据恒为空——本模块就是那个生产者。
3. **`note` 这一版不出**（QUOT-380 拍板 / DEC-052 §2.2）：自由文本不进任何对外载荷，
   所以 `list_watch()` 根本不读这一列，而不是读出来再由上层抹。要出得重开一次决定。

代码口径：存 **6 位数字**（与代码池 `cli/sync.py watchlist_codes()`、`spot_quote` 回键
一致）。入参容忍 `sh600000` / `SZ000700` 这类带交易所前缀的写法，剥掉前缀后落库——
`watchlist` 与 `daily_k` / `spot_quote` 用的都是裸 6 位，存两种写法会让同一条自选
在两个地方匹配不上。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ladderstock.data import sqlite_store
from ladderstock.errors import EXIT_NOT_FOUND, EXIT_USAGE, LadderStockError

#: 表的全部列（`ldst watch list` 与只读接口用的是同一个投影，不另建字段表）
WATCHLIST_TABLE = "watchlist"

#: 对外可用的列：`note` 不在其中（见模块文档 3）
WATCHLIST_FIELDS: tuple[str, ...] = ("code", "created_at")

_SELECT = f"SELECT {', '.join(WATCHLIST_FIELDS)} FROM watchlist"

#: 6 位数字，可选 `sh` / `sz` / `bj` 前缀（北交所也是 6 位）
_CODE_PATTERN = re.compile(r"^(?:sh|sz|bj)?(\d{6})$", re.IGNORECASE)

#: 代码去重靠 SQLite 的 `UNIQUE(code)`，这里不另建索引


def clean_code(code: str) -> str:
    """校验并归一化自选代码；非法是用法错误（exit 2），由 CLI 与接口层各自翻译。"""
    text = str(code or "").strip().upper()
    if not text:
        raise LadderStockError("自选代码不能为空", EXIT_USAGE)
    match = _CODE_PATTERN.match(text)
    if match is None:
        raise LadderStockError(
            f"自选代码要 6 位数字（可带 sh/sz/bj 前缀），收到 {code!r}", EXIT_USAGE
        )
    return match.group(1)


def add_watch(code: str, *, root: Path | None = None) -> dict[str, Any]:
    """加一条自选（幂等）：已在清单里就原样返回，不刷新 `created_at`、不报错。

    清单是「集合」不是「流水」，重复添加同一只票没有第二种含义，
    所以这里不学 `add_position` 的「重复即用法错误」。
    """
    clean = clean_code(code)
    conn = sqlite_store.connect(root=root, create=True)
    assert conn is not None  # create=True 时不会返回 None
    try:
        conn.execute(
            "INSERT INTO watchlist (code, created_at) VALUES (?, ?)"
            " ON CONFLICT(code) DO NOTHING",
            (clean, sqlite_store.now_iso()),
        )
        row = conn.execute(_SELECT + " WHERE code = ?", (clean,)).fetchone()
    finally:
        sqlite_store.close(conn)
    if row is None:  # pragma: no cover - 插入后读不到属于磁盘异常
        raise LadderStockError("写入自选失败：插入后读不到记录", EXIT_USAGE)
    return dict(row)


def remove_watch(code: str, *, root: Path | None = None) -> str:
    """删一条自选，返回被删的代码；不在清单里就是「无结果」（exit 6），不静默成功。"""
    clean = clean_code(code)
    conn = sqlite_store.connect(root=root, create=True)
    assert conn is not None
    try:
        cursor = conn.execute("DELETE FROM watchlist WHERE code = ?", (clean,))
    finally:
        sqlite_store.close(conn)
    if cursor.rowcount == 0:
        raise LadderStockError(f"{clean} 不在本机自选里", EXIT_NOT_FOUND)
    return clean


def list_watch(*, root: Path | None = None) -> list[dict[str, Any]]:
    """读本机自选清单（`code` + `created_at`），按加入时间排序——首页的顺序就是这个顺序。

    只读：`create=False`，本机没有隐私库时返回空列表**且不建库**
    （`GET /api/watchlist` 的空态靠这一条才不会出现「访问一下就有个空文件」）。
    """
    conn = sqlite_store.connect(root=root, create=False)
    if conn is None:
        return []
    try:
        if not sqlite_store.table_names(conn) or WATCHLIST_TABLE not in sqlite_store.table_names(
            conn
        ):
            return []
        return sqlite_store.rows_to_dicts(conn.execute(_SELECT + " ORDER BY created_at").fetchall())
    finally:
        sqlite_store.close(conn)


__all__ = [
    "WATCHLIST_FIELDS",
    "WATCHLIST_TABLE",
    "add_watch",
    "clean_code",
    "list_watch",
    "remove_watch",
]
