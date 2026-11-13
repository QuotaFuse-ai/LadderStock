"""对话会话库：`chat.sqlite`（QUOT-580 / 任务书阶段 C，D1 拍板「另建独立库」）。

布局::

    <data_dir>/chat.sqlite     # 会话与轮次；data/ 已在 .gitignore

为什么单独一个文件（D1）：

- `market.duckdb` 是行情历史库，`code/trade_date` 语义 + 独占文件锁，跨进程并发会
  直接抛 `IOException`，不适合放「一问一答」这种逐条追加的小写入；
- `journal.sqlite` 的定位写死是「交易日志 / 持仓 / 自选」（DEC-006 三条约），
  混进会话表会让备份 / 审计语义变模糊；
- 独立库 = 独立版本号、独立备份、独立删除（用户在设置里「清空对话」只删这一个文件），
  且**不动** `duckdb_store.py` / `sqlite_store.py` 的任何 schema。

三条约束与隐私库同口径：

1. **只落本机**：本模块没有任何网络分支；
2. **不出本机**：会话标题 / 问题 / 回答块含用户私有意图，不得进入 `LadderEnvelope`、
   MCP 工具结果或 AI prompt（DEC-006）；
3. **版本可控**：`PRAGMA user_version` + `schema_meta` 双写版本号，打开更高版本的库
   直接拒绝（退出码 3），绝不猜未来 schema。

表名带 `chat_` 前缀：SQLite 没有 schema 概念，任务书里写的 `ai.chat_session` /
`ai.chat_turn` 在这里落成 `chat_session` / `chat_turn`。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ladderstock.cli.paths import data_dir
from ladderstock.errors import EXIT_CONFIG, LadderStockError

DB_FILENAME = "chat.sqlite"

#: 会话库 schema 版本；与 `config/schema` 的整数版本号同口径。
#: v1 = 会话 + 轮次（成功后才写一行）；v2 = 轮次加 `status` / `updated_at`，
#: 支持「边到达边 upsert」（见 `migrate()` 里 v2 分支的注释）。
SCHEMA_VERSION = 2

SCHEMA_META_TABLE = "schema_meta"

TABLES: tuple[str, ...] = ("schema_meta", "chat_session", "chat_turn")

# --------------------------------------------------------------------------- #
# 轮次状态（v2）：四态，同一行的字段流转，不是插新行
# --------------------------------------------------------------------------- #
#
# 为什么单独一列而不是塞进 `notice.kind`：老板只批了 `notice.kind="missing_data"`
# 一种新 kind，红线又禁止改名复用既有 6 种 kind。「用户点了停止」是用户行为、
# 内容已定格，不是数据缺失，语义上不属于 `notice`；服务端失败也不属于。
# 前端按 `status` 显示「已停止 / 失败重试 / 生成中」，**不在 notice 里造状态**。
#
# - `STREAMING`：正在生成，`blocks_json` 可能只含已到达的块；首块到达时置这一态。
# - `DONE`     ：正常完成（`done{completed: true}`），`blocks_json` 是本轮完整块。
# - `ABORTED`  ：**用户主动停止**，内容定格；保留已到达的块，可作为下一问的上下文。
# - `FAILED`   ：服务端失败（模型 / 数据错误，`done{completed: false}`），
#                `blocks_json` 存失败说明块，便于回看与重试。
STREAMING = "streaming"
DONE = "done"
ABORTED = "aborted"
FAILED = "failed"

TURN_STATUSES: tuple[str, ...] = (STREAMING, DONE, ABORTED, FAILED)

#: 终态集合：离开 `streaming` 之后不再改写 `blocks_json`
TERMINAL_STATUSES: frozenset[str] = frozenset({DONE, ABORTED, FAILED})

#: 会话标题长度：取首问前 40 个 Unicode 码点，超长硬截断（不加省略号）。
#: 选硬截断而不是「39 字 + …」：截断规则单点可测（`title == first_q[:40]`），
#: 省略号会让 40 字上限变成 39 字内容 + 1 个符号，两种口径都说得通反而说不清。
TITLE_MAX_CHARS = 40

#: 最多保留的会话数；超上限淘汰**最久未更新**的那个，当前会话永不淘汰
MAX_SESSIONS = 50


def db_path(root: Path | None = None) -> Path:
    base = Path(root) if root is not None else data_dir()
    return base / DB_FILENAME


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def title_from(question: str) -> str:
    """会话标题：首问前 `TITLE_MAX_CHARS` 个码点；空白问题给固定占位。"""
    text = " ".join((question or "").split())
    if not text:
        return "未命名对话"
    return text[:TITLE_MAX_CHARS]


def schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("PRAGMA user_version").fetchone()
    version = int(row[0]) if row and row[0] else 0
    if version:
        return version
    if not _has_table(conn, SCHEMA_META_TABLE):
        return 0
    row = conn.execute(
        f"SELECT value FROM {SCHEMA_META_TABLE} WHERE key = 'schema_version'"
    ).fetchone()
    try:
        return int(row[0]) if row and row[0] is not None else 0
    except (TypeError, ValueError):
        return 0


def connect(root: Path | None = None, *, create: bool = True) -> sqlite3.Connection | None:
    """打开会话库并迁移到 `SCHEMA_VERSION`；`create=False` 且文件不存在时返回 `None`。"""
    target = db_path(root)
    if not target.is_file() and not create:
        return None
    if create:
        target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        migrate(conn)
    except Exception:
        conn.close()
        raise
    return conn


def close(conn: sqlite3.Connection | None) -> None:
    if conn is None:
        return
    try:
        conn.close()
    except Exception:  # noqa: BLE001 - 关闭失败不影响主流程
        pass


def migrate(conn: sqlite3.Connection) -> int:
    """幂等迁移到 `SCHEMA_VERSION`；返回迁移后的版本号。

    逐版本跳步（照 `sqlite_store._apply()` 的写法）：每个 `if from_version < N:`
    分支只做「v(N-1) → vN」那一段，登录版本号在同一事务里写。

    **v2（QUOT-580 第二轮）**：`chat_turn` 加 `status` 与 `updated_at`，`blocks_json`
    允许为 `NULL`（流式中尚未有块）。四态语义见模块顶部 `STREAMING / DONE /
    ABORTED / FAILED` 的注释；旧库里的行都是「成功后写」的成品，一律回填 `done`。
    migration 只做 `ALTER TABLE ... ADD COLUMN`（SQLite 不能改列约束），
    所以 `blocks_json` 保持 v1 的 `NOT NULL`，流式首块用 `'[]'` 占位。
    """
    version = schema_version(conn)
    if version > SCHEMA_VERSION:
        raise LadderStockError(
            f"本机会话库版本 {version} 高于当前程序支持的 {SCHEMA_VERSION}；"
            "请升级 LadderStock（不会自动降级或改写数据）",
            EXIT_CONFIG,
        )
    if version >= SCHEMA_VERSION:
        return version
    conn.execute("BEGIN IMMEDIATE")
    try:
        if version < 1:
            for statement in _split_statements(_SCHEMA_V1):
                conn.execute(statement)
        if version < 2:
            for statement in _split_statements(_SCHEMA_V2):
                conn.execute(statement)
            # 回填 `done` 且不依赖 ADD COLUMN 的默认值：
            #   决定论优先——即便某个 SQLite 构建把 DEFAULT 落到实处，这里也统一改写。
            # v1 只有「成功后写」一种写入路径，存量行全部是成品。
            # 四态逐条语义（写在 Python 注释里，SQL 注释会被 _split_statements 剥掉）：
            #   streaming = 正在生成，blocks_json 可能不完整（首块到达时置）
            #   done      = 正常完成（done{completed:true}）
            #   aborted   = 用户主动停止，内容定格，可作为下一问上下文
            #   failed    = 服务端失败（模型/数据错，done{completed:false}）
            conn.execute("UPDATE chat_turn SET status = ?", (DONE,))
        conn.execute(
            f"INSERT INTO {SCHEMA_META_TABLE} (key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return schema_version(conn)


def _split_statements(script: str) -> list[str]:
    body = "\n".join(line.split("--", 1)[0] for line in script.splitlines())
    return [chunk.strip() for chunk in body.split(";") if chunk.strip()]


_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 会话：标题取首问前 40 码点，可由 `rename_session` 改写
CREATE TABLE IF NOT EXISTS chat_session (
    session_id TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 轮次：一轮问答一行；`seq` 是**轮次序号**（1,2,3…），不是 `event: block` 的块序号
-- v1：`blocks_json` 非空、只有成功后才写（v2 起由 ALTER 加 `status` / `updated_at`）
CREATE TABLE IF NOT EXISTS chat_turn (
    session_id  TEXT NOT NULL REFERENCES chat_session(session_id) ON DELETE CASCADE,
    seq         INTEGER NOT NULL,
    question    TEXT NOT NULL,
    blocks_json TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (session_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_chat_session_updated ON chat_session(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_chat_turn_created ON chat_turn(session_id, created_at);
"""

#: v1 → v2：轮次加状态列与更新时间列。`ADD COLUMN` 不带 NOT NULL 约束的回填语义
#: 交给 `migrate()` 里的 `UPDATE ... SET status = 'done'`（存量行都是成品）。
_SCHEMA_V2 = """
ALTER TABLE chat_turn ADD COLUMN status TEXT NOT NULL DEFAULT 'streaming';
ALTER TABLE chat_turn ADD COLUMN updated_at TEXT;
"""


# --------------------------------------------------------------------------- #
# 写：建会话 / 追一轮 / 重命名 / 淘汰
# --------------------------------------------------------------------------- #


def append_turn(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    question: str,
    blocks_json: str,
    status: str = DONE,
    max_sessions: int = MAX_SESSIONS,
    moment: str | None = None,
) -> dict[str, Any]:
    """把一轮问答**一次写完**（终态）并落库，返回 `{session_id, title, seq, created_at, status}`。

    用于「拿到完整结果再写」的调用方；流式路径用
    `begin_turn` → `update_turn_blocks` → `finish_turn`，同一轮只产生一行。

    - 会话不存在则新建，`title` 取本轮问题（即首问）；
    - `seq` = **轮次序号** 现有序号 + 1，从 1 开始（与块的 `seq` 毫无关系）；
    - `created_at` 取 `max(now, 上一轮 created_at)`，保证单调不减（系统时钟回拨也不倒退）；
    - 写完后按 `updated_at` 淘汰最旧会话，**本会话跳过**（当前会话永不淘汰）。
    """
    if status not in TERMINAL_STATUSES:
        raise ValueError(f"append_turn 只接受终态，收到 {status!r}")
    return _insert_turn(
        conn,
        session_id=session_id,
        question=question,
        blocks_json=blocks_json,
        status=status,
        max_sessions=max_sessions,
        moment=moment,
    )


def begin_turn(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    question: str,
    blocks: list[dict[str, Any]] | None = None,
    max_sessions: int = MAX_SESSIONS,
    moment: str | None = None,
) -> dict[str, Any]:
    """首块到达时插入一行 `chat_turn`，`status = streaming`。

    返回同 `append_turn`，外加 `status`。之后同轮用 `update_turn_blocks`
    改这一行的 `blocks_json`（**不插新行**），`finish_turn` 落终态。
    """
    blocks_json = json.dumps(blocks or [], ensure_ascii=False, separators=(",", ":"))
    return _insert_turn(
        conn,
        session_id=session_id,
        question=question,
        blocks_json=blocks_json,
        status=STREAMING,
        max_sessions=max_sessions,
        moment=moment,
    )


def update_turn_blocks(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    seq: int,
    blocks: list[dict[str, Any]],
    moment: str | None = None,
) -> bool:
    """流式增量：把同一轮的 `blocks_json` 换成**已到达的全部块**。

    已落终态的行不再改写（返回 `False`），避免晚到的块覆盖 `aborted` 的定格内容。
    每次更新刷新 `updated_at`（轮级的「最后一块到达时间」）。
    """
    moment = moment or now_iso()
    payload = json.dumps(blocks, ensure_ascii=False, separators=(",", ":"))
    cursor = conn.execute(
        "UPDATE chat_turn SET blocks_json = ?, updated_at = ? "
        "WHERE session_id = ? AND seq = ? AND status = ?",
        (payload, moment, session_id, seq, STREAMING),
    )
    if cursor.rowcount:
        conn.execute(
            "UPDATE chat_session SET updated_at = ? WHERE session_id = ?",
            (moment, session_id),
        )
    return bool(cursor.rowcount)


def finish_turn(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    seq: int,
    status: str,
    blocks: list[dict[str, Any]] | None = None,
    moment: str | None = None,
) -> bool:
    """落终态：`done` / `aborted` / `failed`，可同时带上最终块。

    `blocks` 为 `None` 时保留已到达的块（`aborted` 就是这种用法：内容定格）；
    已是终态的行不会被二次改写（返回 `False`），保证终态只写一次。
    """
    if status not in TERMINAL_STATUSES:
        raise ValueError(f"finish_turn 只接受终态，收到 {status!r}")
    moment = moment or now_iso()
    if blocks is None:
        cursor = conn.execute(
            "UPDATE chat_turn SET status = ?, updated_at = ? "
            "WHERE session_id = ? AND seq = ? AND status = ?",
            (status, moment, session_id, seq, STREAMING),
        )
    else:
        payload = json.dumps(blocks, ensure_ascii=False, separators=(",", ":"))
        cursor = conn.execute(
            "UPDATE chat_turn SET status = ?, blocks_json = ?, updated_at = ? "
            "WHERE session_id = ? AND seq = ? AND status = ?",
            (status, payload, moment, session_id, seq, STREAMING),
        )
    if cursor.rowcount:
        conn.execute(
            "UPDATE chat_session SET updated_at = ? WHERE session_id = ?",
            (moment, session_id),
        )
    return bool(cursor.rowcount)


def _insert_turn(
    conn: sqlite3.Connection,
    *,
    session_id: str,
    question: str,
    blocks_json: str,
    status: str,
    max_sessions: int,
    moment: str | None,
) -> dict[str, Any]:
    moment = moment or now_iso()
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT title, created_at FROM chat_session WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            title = title_from(question)
            conn.execute(
                "INSERT INTO chat_session (session_id, title, created_at, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (session_id, title, moment, moment),
            )
            seq = 1
        else:
            title = str(row["title"])
            last = conn.execute(
                "SELECT MAX(seq) AS seq, MAX(created_at) AS at FROM chat_turn "
                "WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            seq = int(last["seq"] or 0) + 1
            moment = max(moment, str(last["at"] or moment))
        # `updated_at` 一律刷新（新建时上面 INSERT 已写过），当前会话永远是最近更新的
        conn.execute(
            "UPDATE chat_session SET updated_at = ? WHERE session_id = ?",
            (moment, session_id),
        )
        conn.execute(
            "INSERT INTO chat_turn "
            "(session_id, seq, question, blocks_json, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, seq, question, blocks_json, status, moment, moment),
        )
        evicted = _evict(conn, keep=session_id, max_sessions=max_sessions)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return {
        "session_id": session_id,
        "title": title,
        "seq": seq,
        "status": status,
        "created_at": moment,
        "evicted": evicted,
    }


def rename_session(conn: sqlite3.Connection, session_id: str, title: str) -> str | None:
    """重命名会话（阶段 F「可重命名」）；会话不存在返回 `None`。

    调用方负责决定是否是用户操作——首问标题由 `append_turn` 自动写，
    这里只在用户主动改名时用，不碰首问原文。
    """
    text = " ".join((title or "").split())[:TITLE_MAX_CHARS]
    if not text:
        return None
    cursor = conn.execute(
        "UPDATE chat_session SET title = ? WHERE session_id = ?",
        (text, session_id),
    )
    return text if cursor.rowcount else None


def _evict(conn: sqlite3.Connection, *, keep: str, max_sessions: int) -> list[str]:
    """超上限时按 `updated_at` 从旧到新删，跳过 `keep`；返回被删的会话 ID。

    `keep` 是本轮刚写入、本轮规则是「当前会话永不淘汰」——它自己占一个名额，
    其余会话最多留 `max_sessions - 1` 个。`append_turn` 会先把 `keep` 的
    `updated_at` 刷成本轮时间，正常情况下它本来也不会落进最旧的一头。
    """
    rows = conn.execute(
        "SELECT session_id FROM chat_session WHERE session_id != ? "
        "ORDER BY updated_at ASC, session_id ASC",
        (keep,),
    ).fetchall()
    overflow = len(rows) - max(max_sessions - 1, 0)
    if overflow <= 0:
        return []
    victims = [str(row["session_id"]) for row in rows[:overflow]]
    conn.executemany(
        "DELETE FROM chat_turn WHERE session_id = ?",
        [(victim,) for victim in victims],
    )
    conn.executemany(
        "DELETE FROM chat_session WHERE session_id = ?",
        [(victim,) for victim in victims],
    )
    return victims


# --------------------------------------------------------------------------- #
# 读：会话列表 / 取一轮（阶段 F `SessionList` 的输入）
# --------------------------------------------------------------------------- #


def list_sessions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """会话列表：最近更新的在前，附轮数。"""
    rows = conn.execute(
        "SELECT s.session_id, s.title, s.created_at, s.updated_at, "
        "       (SELECT COUNT(*) FROM chat_turn t WHERE t.session_id = s.session_id) AS turns "
        "FROM chat_session s ORDER BY s.updated_at DESC, s.session_id DESC"
    ).fetchall()
    return [
        {
            "session_id": str(row["session_id"]),
            "title": str(row["title"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"]),
            "turns": int(row["turns"]),
        }
        for row in rows
    ]


def get_session(conn: sqlite3.Connection, session_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT session_id, title, created_at, updated_at FROM chat_session "
        "WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "session_id": str(row["session_id"]),
        "title": str(row["title"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
    }


def session_turns(conn: sqlite3.Connection, session_id: str) -> list[dict[str, Any]]:
    """按 `seq`（轮次序号）升序取一个会话的全部轮次。

    `blocks_json` 原样返回、由调用方反序列化；`status` 为四态之一，
    `aborted` / `failed` 的行同样带着已到达的块，供回看与下一问上下文。
    """
    rows = conn.execute(
        "SELECT seq, question, blocks_json, status, created_at, updated_at "
        "FROM chat_turn WHERE session_id = ? ORDER BY seq ASC",
        (session_id,),
    ).fetchall()
    return [
        {
            "session_id": session_id,
            "seq": int(row["seq"]),
            "question": str(row["question"]),
            "blocks_json": str(row["blocks_json"] or "[]"),
            "status": str(row["status"]),
            "created_at": str(row["created_at"]),
            "updated_at": str(row["updated_at"] or row["created_at"]),
        }
        for row in rows
    ]


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


__all__ = [
    "ABORTED",
    "DB_FILENAME",
    "DONE",
    "FAILED",
    "MAX_SESSIONS",
    "SCHEMA_META_TABLE",
    "SCHEMA_VERSION",
    "STREAMING",
    "TABLES",
    "TERMINAL_STATUSES",
    "TITLE_MAX_CHARS",
    "TURN_STATUSES",
    "append_turn",
    "begin_turn",
    "close",
    "connect",
    "db_path",
    "finish_turn",
    "get_session",
    "list_sessions",
    "migrate",
    "now_iso",
    "rename_session",
    "schema_version",
    "session_turns",
    "title_from",
    "update_turn_blocks",
]
