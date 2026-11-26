"""提醒规则与出站渠道的落库（`data/alerts.sqlite`，独立于隐私库）。

为什么单开一个库而不是塞进 `journal.sqlite`：

1. `journal.sqlite` 是**隐私库**（`core/privacy.py` 的硬边界：只允许可信的 journal
   子模块精确读写，进 MCP / AI prompt 都被扫描拦截）。提醒规则不是交易隐私——它
   描述「这台机器在盯什么条件」，把它塞进隐私库只会扩大被扫描面的口径；
2. `chat.sqlite` / `news.sqlite` 已开了「一摊事一个库」的先例，提醒规则同样独立。

存储形态与 `journal/inbox` 一致：WAL、`sqlite3.Row`、`connect(root=…)` 注入数据根
（单测写临时目录），所有 CRUD 都返回普通 dict，不让 sqlite 对象跨模块泄漏。

表结构（`_SCHEMA` 建齐，幂等）：

- `rules`：用户配置的规则本体；
- `rule_state`：每条规则的运行态（armed / fired / cooldown），冷却与重臂由
  `evaluate.should_fire` 决定，这里只负责持久化；
- `history`：触发历史（含投递结果摘要），保留最近 `HISTORY_CAP` 条；
- `channels`：出站渠道（DEC-087）。webhook URL 属于半敏感信息——**写面全量保存**、
  **读面一律打码**（`channels.public_config`），SMTP 密码只存环境变量名，值不落库。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from ladderstock.alert import rules as rule_model
from ladderstock.alert.channels import (
    CHANNEL_KINDS,
    normalize_channel_id,
    validate_channel_config,
)
from ladderstock.cli.paths import data_dir
from ladderstock.errors import EXIT_NOT_FOUND, EXIT_USAGE, LadderStockError

DB_NAME = "alerts.sqlite"

#: 触发历史保留条数（超出裁最旧；提醒不是日志系统，只服务「最近提醒过什么」）。
HISTORY_CAP = 200

_SCHEMA = """
CREATE TABLE IF NOT EXISTS rules (
    id           TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,
    symbol       TEXT NOT NULL,
    threshold    REAL NOT NULL,
    window       INTEGER NOT NULL DEFAULT 20,
    cooldown_min INTEGER NOT NULL DEFAULT 60,
    channels     TEXT NOT NULL DEFAULT '[]',
    note         TEXT NOT NULL DEFAULT '',
    enabled      INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rules_symbol ON rules (symbol);
CREATE TABLE IF NOT EXISTS rule_state (
    rule_id        TEXT PRIMARY KEY REFERENCES rules (id) ON DELETE CASCADE,
    status         TEXT NOT NULL DEFAULT 'armed',
    last_value     REAL,
    fired_at       TEXT,
    cooldown_until TEXT,
    updated_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS history (
    id          TEXT PRIMARY KEY,
    rule_id     TEXT NOT NULL,
    fired_at    TEXT NOT NULL,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    deliveries  TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_history_rule ON history (rule_id);
CREATE TABLE IF NOT EXISTS channels (
    id         TEXT PRIMARY KEY,
    kind       TEXT NOT NULL,
    name       TEXT NOT NULL DEFAULT '',
    config     TEXT NOT NULL DEFAULT '{}',
    enabled    INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

_COLUMNS = (
    "id, kind, symbol, threshold, window, cooldown_min,"
    " channels, note, enabled, created_at, updated_at"
)


def db_path(root: Path | None = None) -> Path:
    return (root if root is not None else data_dir()) / DB_NAME


def connect(root: Path | None = None, *, create: bool = True) -> sqlite3.Connection | None:
    """打开提醒库；库不存在时按 `create` 决定建库或返回 `None`（只读方不建库）。"""
    path = db_path(root)
    if not path.exists():
        if not create:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    return conn


def close(conn: sqlite3.Connection | None) -> None:
    if conn is not None:
        conn.close()


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _fail(message: str, code: int = EXIT_USAGE) -> LadderStockError:
    return LadderStockError(message, code)


def _missing(rule_id: str) -> LadderStockError:
    return _fail(f"规则不存在：{rule_id}", EXIT_NOT_FOUND)


# --------------------------------------------------------------------------- #
# rules
# --------------------------------------------------------------------------- #


def _rule_from_row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["enabled"] = bool(item["enabled"])
    try:
        item["channels"] = json.loads(item.get("channels") or "[]")
    except json.JSONDecodeError:  # pragma: no cover - 库被手改坏时兜成空列表
        item["channels"] = []
    return item


def add_rule(
    kind: str,
    symbol: str,
    threshold: float,
    *,
    window: int | None = None,
    cooldown_min: int = 60,
    channels: list[str] | None = None,
    note: str = "",
    root: Path | None = None,
) -> dict[str, Any]:
    """新增一条规则（参数合法性由 `rules.validate_rule` 把关，非法即 exit 2）。"""
    clean = rule_model.validate_rule(
        kind, symbol, threshold, window=window, cooldown_min=cooldown_min, channels=channels or []
    )
    rule_id = uuid.uuid4().hex[:12]
    stamp = _now()
    conn = connect(root)
    assert conn is not None
    try:
        conn.execute(
            f"INSERT INTO rules ({_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                rule_id,
                clean["kind"],
                clean["symbol"],
                clean["threshold"],
                clean["window"],
                clean["cooldown_min"],
                json.dumps(clean["channels"], ensure_ascii=False),
                str(note or "").strip()[:200],
                1,
                stamp,
                stamp,
            ),
        )
        row = conn.execute(f"SELECT {_COLUMNS} FROM rules WHERE id = ?", (rule_id,)).fetchone()
    finally:
        close(conn)
    if row is None:  # pragma: no cover - 插入后读不到属于磁盘异常
        raise _fail("写入规则失败")
    return _rule_from_row(row)


def list_rules(
    *, root: Path | None = None, enabled_only: bool = False, symbol: str | None = None
) -> list[dict[str, Any]]:
    conn = connect(root, create=False)
    if conn is None:
        return []
    where = []
    params: list[Any] = []
    if enabled_only:
        where.append("enabled = 1")
    if symbol:
        where.append("symbol = ?")
        params.append(str(symbol).strip().upper())
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    try:
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM rules{clause} ORDER BY created_at, id", params
        ).fetchall()
    finally:
        close(conn)
    return [_rule_from_row(row) for row in rows]


def get_rule(rule_id: str, *, root: Path | None = None) -> dict[str, Any]:
    conn = connect(root, create=False)
    if conn is None:
        raise _missing(rule_id)
    try:
        row = conn.execute(
            f"SELECT {_COLUMNS} FROM rules WHERE id = ?", (str(rule_id).strip(),)
        ).fetchone()
    finally:
        close(conn)
    if row is None:
        raise _missing(rule_id)
    return _rule_from_row(row)


def update_rule(rule_id: str, *, root: Path | None = None, **fields: Any) -> dict[str, Any]:
    """改规则；先读旧值，合并后整条过校验（不允许「改一半」留下非法组合）。"""
    current = get_rule(rule_id, root=root)
    merged = {**current, **{key: value for key, value in fields.items() if value is not None}}
    clean = rule_model.validate_rule(
        merged["kind"],
        merged["symbol"],
        float(merged["threshold"]),
        window=int(merged["window"]),
        cooldown_min=int(merged["cooldown_min"]),
        channels=merged["channels"] or [],
    )
    conn = connect(root)
    assert conn is not None
    try:
        row_exists = conn.execute("SELECT 1 FROM rules WHERE id = ?", (rule_id,)).fetchone()
        if row_exists is None:
            raise _missing(rule_id)
        conn.execute(
            "UPDATE rules SET kind=?, symbol=?, threshold=?, window=?, cooldown_min=?,"
            " channels=?, note=?, updated_at=? WHERE id=?",
            (
                clean["kind"],
                clean["symbol"],
                clean["threshold"],
                clean["window"],
                clean["cooldown_min"],
                json.dumps(clean["channels"], ensure_ascii=False),
                str(merged.get("note") or "").strip()[:200],
                _now(),
                rule_id,
            ),
        )
    finally:
        close(conn)
    return get_rule(rule_id, root=root)


def remove_rule(rule_id: str, *, root: Path | None = None) -> dict[str, Any]:
    removed = get_rule(rule_id, root=root)
    conn = connect(root)
    assert conn is not None
    try:
        conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
        conn.execute("DELETE FROM rule_state WHERE rule_id = ?", (rule_id,))
    finally:
        close(conn)
    return {"removed": True, "rule": removed}


def toggle_rule(rule_id: str, enabled: bool, *, root: Path | None = None) -> dict[str, Any]:
    """启停规则；停用不删状态（重新启用后冷却照样接上，不重复轰炸）。"""
    get_rule(rule_id, root=root)
    conn = connect(root)
    assert conn is not None
    try:
        conn.execute(
            "UPDATE rules SET enabled=?, updated_at=? WHERE id=?",
            (1 if enabled else 0, _now(), rule_id),
        )
    finally:
        close(conn)
    return get_rule(rule_id, root=root)


# --------------------------------------------------------------------------- #
# rule_state / history
# --------------------------------------------------------------------------- #


def _empty_state() -> dict[str, Any]:
    return {
        "rule_id": "",
        "status": "armed",
        "last_value": None,
        "fired_at": None,
        "cooldown_until": None,
        "updated_at": "",
    }


def get_state(rule_id: str, *, root: Path | None = None) -> dict[str, Any]:
    conn = connect(root, create=False)
    if conn is None:
        return _empty_state()
    try:
        row = conn.execute(
            "SELECT rule_id, status, last_value, fired_at, cooldown_until, updated_at"
            " FROM rule_state WHERE rule_id = ?",
            (rule_id,),
        ).fetchone()
    finally:
        close(conn)
    return dict(row) if row is not None else _empty_state()


def set_state(
    rule_id: str,
    status: str,
    *,
    last_value: float | None,
    fired_at: str | None,
    cooldown_until: str | None,
    root: Path | None = None,
) -> None:
    conn = connect(root)
    assert conn is not None
    try:
        conn.execute(
            "INSERT INTO rule_state"
            " (rule_id, status, last_value, fired_at, cooldown_until, updated_at)"
            " VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(rule_id) DO UPDATE SET status=excluded.status,"
            " last_value=excluded.last_value, fired_at=excluded.fired_at,"
            " cooldown_until=excluded.cooldown_until, updated_at=excluded.updated_at",
            (rule_id, status, last_value, fired_at, cooldown_until, _now()),
        )
    finally:
        close(conn)


def append_history(
    rule_id: str,
    title: str,
    body: str,
    deliveries: list[dict[str, Any]],
    *,
    root: Path | None = None,
) -> None:
    conn = connect(root)
    assert conn is not None
    try:
        conn.execute(
            "INSERT INTO history (id, rule_id, fired_at, title, body, deliveries)"
            " VALUES (?,?,?,?,?,?)",
            (
                uuid.uuid4().hex[:12],
                rule_id,
                _now(),
                str(title)[:200],
                str(body)[:2000],
                json.dumps(deliveries, ensure_ascii=False),
            ),
        )
        conn.execute(
            "DELETE FROM history WHERE id IN ("
            " SELECT id FROM history ORDER BY fired_at DESC LIMIT -1 OFFSET ?)",
            (HISTORY_CAP,),
        )
    finally:
        close(conn)


def list_history(limit: int = 50, *, root: Path | None = None) -> list[dict[str, Any]]:
    conn = connect(root, create=False)
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT id, rule_id, fired_at, title, body, deliveries FROM history"
            " ORDER BY fired_at DESC LIMIT ?",
            (max(1, min(int(limit), HISTORY_CAP)),),
        ).fetchall()
    finally:
        close(conn)
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        try:
            item["deliveries"] = json.loads(item.get("deliveries") or "[]")
        except json.JSONDecodeError:  # pragma: no cover
            item["deliveries"] = []
        out.append(item)
    return out


# --------------------------------------------------------------------------- #
# channels
# --------------------------------------------------------------------------- #


def _channel_from_row(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["enabled"] = bool(item["enabled"])
    try:
        item["config"] = json.loads(item.get("config") or "{}")
    except json.JSONDecodeError:  # pragma: no cover
        item["config"] = {}
    return item


def add_channel(
    channel_id: str,
    kind: str,
    *,
    name: str = "",
    config: dict[str, Any] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    cid = normalize_channel_id(channel_id)
    if kind not in CHANNEL_KINDS:
        raise _fail(f"未知渠道类型 {kind!r}；可用：{', '.join(CHANNEL_KINDS)}")
    clean = validate_channel_config(kind, config or {})
    stamp = _now()
    conn = connect(root)
    assert conn is not None
    try:
        exists = conn.execute("SELECT 1 FROM channels WHERE id = ?", (cid,)).fetchone()
        if exists is not None:
            raise _fail(f"渠道 {cid} 已存在；换一个 id 或先删除旧的")
        conn.execute(
            "INSERT INTO channels (id, kind, name, config, enabled, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                cid,
                kind,
                str(name or "").strip()[:60],
                json.dumps(clean, ensure_ascii=False),
                1,
                stamp,
                stamp,
            ),
        )
        row = conn.execute("SELECT * FROM channels WHERE id = ?", (cid,)).fetchone()
    finally:
        close(conn)
    if row is None:  # pragma: no cover
        raise _fail("写入渠道失败")
    return _channel_from_row(row)


def list_channels(
    *, root: Path | None = None, include_disabled: bool = True
) -> list[dict[str, Any]]:
    conn = connect(root, create=False)
    if conn is None:
        return []
    try:
        clause = "" if include_disabled else " WHERE enabled = 1"
        rows = conn.execute(f"SELECT * FROM channels{clause} ORDER BY created_at, id").fetchall()
    finally:
        close(conn)
    return [_channel_from_row(row) for row in rows]


def get_channel(channel_id: str, *, root: Path | None = None) -> dict[str, Any]:
    conn = connect(root, create=False)
    if conn is None:
        raise _fail(f"渠道不存在：{channel_id}", EXIT_NOT_FOUND)
    try:
        row = conn.execute(
            "SELECT * FROM channels WHERE id = ?", (str(channel_id).strip(),)
        ).fetchone()
    finally:
        close(conn)
    if row is None:
        raise _fail(f"渠道不存在：{channel_id}", EXIT_NOT_FOUND)
    return _channel_from_row(row)


def remove_channel(channel_id: str, *, root: Path | None = None) -> dict[str, Any]:
    removed = get_channel(channel_id, root=root)
    conn = connect(root)
    assert conn is not None
    try:
        conn.execute("DELETE FROM channels WHERE id = ?", (channel_id,))
    finally:
        close(conn)
    return {"removed": True, "channel": removed}


def update_channel(
    channel_id: str,
    *,
    root: Path | None = None,
    name: str | None = None,
    config: dict[str, Any] | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    current = get_channel(channel_id, root=root)
    merged_config = {**current["config"], **(config or {})}
    clean = validate_channel_config(current["kind"], merged_config)
    conn = connect(root)
    assert conn is not None
    try:
        conn.execute(
            "UPDATE channels SET name=?, config=?, enabled=?, updated_at=? WHERE id=?",
            (
                str(name if name is not None else current["name"]).strip()[:60],
                json.dumps(clean, ensure_ascii=False),
                (1 if (enabled if enabled is not None else current["enabled"]) else 0),
                _now(),
                channel_id,
            ),
        )
    finally:
        close(conn)
    return get_channel(channel_id, root=root)


__all__ = [
    "DB_NAME",
    "HISTORY_CAP",
    "add_channel",
    "add_rule",
    "append_history",
    "close",
    "connect",
    "db_path",
    "get_channel",
    "get_rule",
    "get_state",
    "list_channels",
    "list_history",
    "list_rules",
    "remove_channel",
    "remove_rule",
    "set_state",
    "toggle_rule",
    "update_channel",
    "update_rule",
]
