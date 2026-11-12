"""`chat.sqlite` 会话库（QUOT-580 / 阶段 C）单测。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from ladderstock.data import chat_store
from ladderstock.errors import LadderStockError


def _blocks(text: str = "结论一句话。"):
    return [
        {
            "type": "conclusion",
            "id": "conclusion",
            "payload": {"text": text, "tone": "neutral"},
        }
    ]


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    return tmp_path / "data"


def test_connect_creates_db_file_with_expected_schema(root: Path) -> None:
    conn = chat_store.connect(root)
    assert conn is not None
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert {"schema_meta", "chat_session", "chat_turn"} <= tables
        assert chat_store.schema_version(conn) == chat_store.SCHEMA_VERSION
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        assert version == chat_store.SCHEMA_VERSION
    finally:
        chat_store.close(conn)
    assert chat_store.db_path(root).is_file()


def test_connect_with_create_false_does_not_create_file(root: Path) -> None:
    assert chat_store.connect(root, create=False) is None
    assert not chat_store.db_path(root).exists()


def test_title_truncates_to_forty_code_points(root: Path) -> None:
    question = "问题" * 30  # 60 个码点
    assert len(question) == 60
    title = chat_store.title_from(question)
    assert title == question[: chat_store.TITLE_MAX_CHARS]
    assert len(title) == 40
    # 硬截断：不加省略号
    assert not title.endswith("…")

    conn = chat_store.connect(root)
    assert conn is not None
    try:
        chat_store.append_turn(
            conn,
            session_id="sess-long-question",
            question=question,
            blocks_json=json.dumps(_blocks(), ensure_ascii=False),
        )
        session = chat_store.get_session(conn, "sess-long-question")
    finally:
        chat_store.close(conn)
    assert session is not None
    assert session["title"] == question[:40]


def test_title_counts_unicode_code_points_not_bytes(root: Path) -> None:
    emoji = "🚀" * 50
    title = chat_store.title_from(emoji)
    assert title == "🚀" * 40
    assert len(title) == 40


def test_title_collapses_whitespace_and_falls_back(root: Path) -> None:
    assert chat_store.title_from("  你 好\n\n世界  ") == "你 好 世界"
    assert chat_store.title_from("   ") == "未命名对话"


def test_three_turns_increment_seq_and_monotonic_created_at(root: Path) -> None:
    conn = chat_store.connect(root)
    assert conn is not None
    try:
        # 第二轮给的钟比第一轮还早：落库时取 max(now, 上一轮) 保证不倒退
        moments = [
            "2026-11-13T10:00:00+00:00",
            "2026-11-13T09:00:00+00:00",
            "2026-11-13T10:00:05+00:00",
        ]
        seqs = []
        for index, moment in enumerate(moments, start=1):
            recorded = chat_store.append_turn(
                conn,
                session_id="sess-three",
                question=f"第{index}问",
                blocks_json=json.dumps(_blocks(), ensure_ascii=False),
                moment=moment,
            )
            seqs.append(recorded["seq"])
        turns = chat_store.session_turns(conn, "sess-three")
        session = chat_store.get_session(conn, "sess-three")
    finally:
        chat_store.close(conn)

    assert seqs == [1, 2, 3]
    assert [row["seq"] for row in turns] == [1, 2, 3]
    created = [row["created_at"] for row in turns]
    assert created == sorted(created)
    assert session is not None
    assert session["title"] == "第1问"
    assert session["created_at"] == "2026-11-13T10:00:00+00:00"
    assert session["updated_at"] == created[-1]


def test_blocks_json_round_trips_to_block_array(root: Path) -> None:
    blocks = _blocks("这一轮先看趋势。")
    blocks.append(
        {
            "type": "notice",
            "id": "notice-missing-data",
            "payload": {
                "kind": "missing_data",
                "section": "来源",
                "message": "本轮没有任何可核实的数据来源。",
            },
        }
    )
    conn = chat_store.connect(root)
    assert conn is not None
    try:
        chat_store.append_turn(
            conn,
            session_id="sess-blocks",
            question="大盘怎么样",
            blocks_json=json.dumps(blocks, ensure_ascii=False),
        )
        turns = chat_store.session_turns(conn, "sess-blocks")
    finally:
        chat_store.close(conn)
    assert len(turns) == 1
    assert json.loads(turns[0]["blocks_json"]) == blocks


def test_eviction_drops_oldest_but_never_the_current_session(root: Path) -> None:
    conn = chat_store.connect(root)
    assert conn is not None
    try:
        for index in range(3):
            chat_store.append_turn(
                conn,
                session_id=f"sess-{index}",
                question=f"问{index}",
                blocks_json="[]",
                max_sessions=10,
                moment=f"2026-11-13T0{index}:00:00+00:00",
            )
        # 上限压到 2：写入当前会话 sess-current 之后，只留最近的 sess-2 与它自己
        recorded = chat_store.append_turn(
            conn,
            session_id="sess-current",
            question="当前会话首问",
            blocks_json="[]",
            max_sessions=2,
            moment="2026-11-13T05:00:00+00:00",
        )
        sessions = {row["session_id"] for row in chat_store.list_sessions(conn)}
        # 把 sess-current 改老（模拟长时间未更新），让它在下一轮的淘汰候选里排最前
        conn.execute(
            "UPDATE chat_session SET updated_at = ? WHERE session_id = ?",
            ("2000-01-01T00:00:00+00:00", "sess-current"),
        )
        # 上限放到 3 再写新会话 sess-later：候选里 sess-current 最旧，但它是本次的当前会话，必须留下
        second = chat_store.append_turn(
            conn,
            session_id="sess-later",
            question="后来的会话",
            blocks_json="[]",
            max_sessions=3,
            moment="2026-11-13T06:00:00+00:00",
        )
        remaining = {row["session_id"] for row in chat_store.list_sessions(conn)}
        turns_current = chat_store.session_turns(conn, "sess-current")
        turns_later = chat_store.session_turns(conn, "sess-later")
    finally:
        chat_store.close(conn)

    assert recorded["seq"] == 1
    assert recorded["evicted"] == ["sess-0", "sess-1"]
    assert sessions == {"sess-2", "sess-current"}
    assert second["evicted"] == []
    assert remaining == {"sess-2", "sess-current", "sess-later"}
    assert len(turns_current) == 1
    assert len(turns_later) == 1


def test_eviction_cascades_turns(root: Path) -> None:
    conn = chat_store.connect(root)
    assert conn is not None
    try:
        chat_store.append_turn(
            conn, session_id="sess-old", question="旧", blocks_json="[]",
            max_sessions=10, moment="2026-11-13T01:00:00+00:00",
        )
        chat_store.append_turn(
            conn, session_id="sess-new", question="新", blocks_json="[]",
            max_sessions=1, moment="2026-11-13T02:00:00+00:00",
        )
        left = conn.execute(
            "SELECT COUNT(*) FROM chat_turn WHERE session_id = 'sess-old'"
        ).fetchone()[0]
    finally:
        chat_store.close(conn)
    assert left == 0


def test_reopen_after_close_reads_back_same_rows(root: Path) -> None:
    conn = chat_store.connect(root)
    assert conn is not None
    try:
        for index in range(1, 4):
            chat_store.append_turn(
                conn,
                session_id="sess-restart",
                question=f"第{index}问",
                blocks_json=json.dumps(_blocks(f"第{index}轮结论。"), ensure_ascii=False),
            )
    finally:
        chat_store.close(conn)

    reopened = chat_store.connect(root)
    assert reopened is not None
    try:
        turns = chat_store.session_turns(reopened, "sess-restart")
        session = chat_store.get_session(reopened, "sess-restart")
        listed = chat_store.list_sessions(reopened)
    finally:
        chat_store.close(reopened)

    assert [row["seq"] for row in turns] == [1, 2, 3]
    assert [row["question"] for row in turns] == ["第1问", "第2问", "第3问"]
    assert session is not None and session["title"] == "第1问"
    assert listed[0]["session_id"] == "sess-restart"
    assert listed[0]["turns"] == 3


def test_rename_session_truncates_and_missing_returns_none(root: Path) -> None:
    conn = chat_store.connect(root)
    assert conn is not None
    try:
        chat_store.append_turn(
            conn, session_id="sess-rename", question="首问", blocks_json="[]"
        )
        title = chat_store.rename_session(conn, "sess-rename", "改名" * 40)
        missing = chat_store.rename_session(conn, "sess-missing", "改名")
        session = chat_store.get_session(conn, "sess-rename")
    finally:
        chat_store.close(conn)
    assert title == "改名" * 20
    assert len(title) == 40
    assert missing is None
    assert session is not None and session["title"] == title


def test_refuses_newer_schema(root: Path) -> None:
    conn = chat_store.connect(root)
    assert conn is not None
    conn.close()
    raw = sqlite3.connect(str(chat_store.db_path(root)), isolation_level=None)
    try:
        raw.execute(f"PRAGMA user_version = {chat_store.SCHEMA_VERSION + 1}")
    finally:
        raw.close()
    with pytest.raises(LadderStockError):
        chat_store.connect(root)


# --------------------------------------------------------------------------- #
# v2：轮次 status 四态 + 边到达边 upsert
# --------------------------------------------------------------------------- #


def test_append_turn_writes_done_status(root: Path) -> None:
    conn = chat_store.connect(root)
    assert conn is not None
    try:
        recorded = chat_store.append_turn(
            conn, session_id="sess-done", question="普通一问", blocks_json="[]"
        )
        turns = chat_store.session_turns(conn, "sess-done")
    finally:
        chat_store.close(conn)
    assert recorded["status"] == chat_store.DONE
    assert [row["status"] for row in turns] == [chat_store.DONE]
    assert turns[0]["updated_at"] == turns[0]["created_at"]


def test_append_turn_rejects_non_terminal_status(root: Path) -> None:
    conn = chat_store.connect(root)
    assert conn is not None
    try:
        with pytest.raises(ValueError):
            chat_store.append_turn(
                conn,
                session_id="sess-bad",
                question="问",
                blocks_json="[]",
                status=chat_store.STREAMING,
            )
    finally:
        chat_store.close(conn)


def test_streaming_turn_upserts_one_row_until_done(root: Path) -> None:
    """同一轮多次更新只产生一行；status 从 streaming 流转到 done。"""
    first = _blocks("第一块。")
    second = first + [
        {"type": "citation", "id": "citation", "payload": {"items": []}},
    ]
    conn = chat_store.connect(root)
    assert conn is not None
    try:
        started = chat_store.begin_turn(
            conn,
            session_id="sess-stream",
            question="流式一问",
            blocks=first,
            moment="2026-11-13T01:00:00+00:00",
        )
        mid = chat_store.session_turns(conn, "sess-stream")
        chat_store.update_turn_blocks(
            conn,
            session_id="sess-stream",
            seq=started["seq"],
            blocks=second,
            moment="2026-11-13T01:00:05+00:00",
        )
        chat_store.update_turn_blocks(
            conn,
            session_id="sess-stream",
            seq=started["seq"],
            blocks=second,
            moment="2026-11-13T01:00:06+00:00",
        )
        chat_store.finish_turn(
            conn,
            session_id="sess-stream",
            seq=started["seq"],
            status=chat_store.DONE,
            blocks=second,
            moment="2026-11-13T01:00:07+00:00",
        )
        rows = conn.execute(
            "SELECT COUNT(*) FROM chat_turn WHERE session_id = ?", ("sess-stream",)
        ).fetchone()[0]
        turns = chat_store.session_turns(conn, "sess-stream")
    finally:
        chat_store.close(conn)

    assert len(mid) == 1 and mid[0]["status"] == chat_store.STREAMING
    assert len(mid[0]["blocks_json"]) > 0
    assert rows == 1, "一轮只允许一行 chat_turn"
    assert len(turns) == 1
    assert turns[0]["status"] == chat_store.DONE
    assert turns[0]["updated_at"] == "2026-11-13T01:00:07+00:00"
    assert len(json.loads(turns[0]["blocks_json"])) == 2


def test_aborted_keeps_arrived_blocks_and_cannot_be_overwritten(root: Path) -> None:
    """用户停止：status=aborted，已到达块完整可读；终态不可被后续块改写。"""
    arrived = _blocks("已到达的结论。")
    conn = chat_store.connect(root)
    assert conn is not None
    try:
        started = chat_store.begin_turn(
            conn, session_id="sess-abort", question="用户停了的问", blocks=arrived
        )
        finished = chat_store.finish_turn(
            conn, session_id="sess-abort", seq=started["seq"], status=chat_store.ABORTED
        )
        late = chat_store.update_turn_blocks(
            conn,
            session_id="sess-abort",
            seq=started["seq"],
            blocks=_blocks("晚到的块，不该覆盖"),
        )
        again = chat_store.finish_turn(
            conn, session_id="sess-abort", seq=started["seq"], status=chat_store.DONE
        )
        turns = chat_store.session_turns(conn, "sess-abort")
    finally:
        chat_store.close(conn)

    assert finished is True
    assert late is False
    assert again is False
    assert [row["status"] for row in turns] == [chat_store.ABORTED]
    assert json.loads(turns[0]["blocks_json"]) == arrived


def test_failed_turn_is_distinguishable_from_aborted(root: Path) -> None:
    note = [
        {
            "type": "notice",
            "id": "notice",
            "payload": {"kind": "network", "section": "回答", "message": "网络没通"},
        }
    ]
    conn = chat_store.connect(root)
    assert conn is not None
    try:
        events = []
        for session_id, status in (
            ("sess-aborted", chat_store.ABORTED),
            ("sess-failed", chat_store.FAILED),
        ):
            started = chat_store.begin_turn(
                conn, session_id=session_id, question="同一句话", blocks=note
            )
            chat_store.finish_turn(
                conn,
                session_id=session_id,
                seq=started["seq"],
                status=status,
                blocks=note if status == chat_store.FAILED else None,
            )
            events.append(chat_store.session_turns(conn, session_id)[0]["status"])
    finally:
        chat_store.close(conn)
    assert events == [chat_store.ABORTED, chat_store.FAILED]
    assert chat_store.TURN_STATUSES == ("streaming", "done", "aborted", "failed")


def test_streaming_row_survives_process_exit(root: Path) -> None:
    """模拟「第 2 块后杀进程」：写好 streaming 行后直接关连接，重开仍读得到已到的块。"""
    two_blocks = _blocks("第一块。") + [
        {"type": "citation", "id": "citation", "payload": {"items": [{"name": "来源 A"}]}},
    ]
    conn = chat_store.connect(root)
    assert conn is not None
    started = chat_store.begin_turn(
        conn, session_id="sess-kill", question="杀进程前的一问", blocks=two_blocks
    )
    chat_store.update_turn_blocks(
        conn, session_id="sess-kill", seq=started["seq"], blocks=two_blocks
    )
    # 不放 finish，直接关连接 = 进程退出
    chat_store.close(conn)

    reopened = chat_store.connect(root)
    assert reopened is not None
    try:
        turns = chat_store.session_turns(reopened, "sess-kill")
    finally:
        chat_store.close(reopened)
    assert len(turns) == 1
    assert turns[0]["status"] == chat_store.STREAMING
    blocks = json.loads(turns[0]["blocks_json"])
    assert [block["type"] for block in blocks] == ["conclusion", "citation"]


def test_turn_seq_is_monotonic_and_unrelated_to_block_count(root: Path) -> None:
    """轮次序号 1,2,3 与块数无关；块自己的 seq 不进 `chat_turn.seq`。"""
    conn = chat_store.connect(root)
    assert conn is not None
    try:
        seqs = []
        for turn in range(1, 4):
            started = chat_store.begin_turn(
                conn, session_id="sess-seq", question=f"第{turn}问", blocks=_blocks()
            )
            for extra in range(turn):
                chat_store.update_turn_blocks(
                    conn,
                    session_id="sess-seq",
                    seq=started["seq"],
                    blocks=_blocks() * (extra + 2),
                )
            chat_store.finish_turn(
                conn, session_id="sess-seq", seq=started["seq"], status=chat_store.DONE
            )
            seqs.append(started["seq"])
        turns = chat_store.session_turns(conn, "sess-seq")
    finally:
        chat_store.close(conn)
    assert seqs == [1, 2, 3]
    assert [row["seq"] for row in turns] == [1, 2, 3]


def test_migrates_v1_database_backfilling_done_status(root: Path) -> None:
    """v1 老库升级：加列成功、存量行回填 done、旧数据一块不丢。"""
    root.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(str(chat_store.db_path(root)), isolation_level=None)
    try:
        for statement in (
            "CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
            "CREATE TABLE chat_session ("
            "session_id TEXT PRIMARY KEY, title TEXT NOT NULL, "
            "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)",
            "CREATE TABLE chat_turn ("
            "session_id TEXT NOT NULL REFERENCES chat_session(session_id) "
            "ON DELETE CASCADE, seq INTEGER NOT NULL, question TEXT NOT NULL, "
            "blocks_json TEXT NOT NULL, created_at TEXT NOT NULL, "
            "PRIMARY KEY (session_id, seq))",
            "CREATE INDEX idx_chat_session_updated ON chat_session(updated_at DESC)",
            "CREATE INDEX idx_chat_turn_created ON chat_turn(session_id, created_at)",
            "INSERT INTO schema_meta (key, value) VALUES ('schema_version', '1')",
            "INSERT INTO chat_session VALUES "
            "('sess-v1', '老会话', '2026-11-01T00:00:00+00:00', '2026-11-01T00:00:00+00:00')",
            "INSERT INTO chat_turn VALUES "
            "('sess-v1', 1, '老问题', '[{\"type\": \"conclusion\"}]', "
            "'2026-11-01T00:00:00+00:00')",
            "PRAGMA user_version = 1",
        ):
            raw.execute(statement)
    finally:
        raw.close()

    conn = chat_store.connect(root)
    assert conn is not None
    try:
        assert chat_store.schema_version(conn) == 2
        turns = chat_store.session_turns(conn, "sess-v1")
    finally:
        chat_store.close(conn)
    assert turns[0]["status"] == chat_store.DONE
    assert turns[0]["updated_at"] == "2026-11-01T00:00:00+00:00"
    assert json.loads(turns[0]["blocks_json"]) == [{"type": "conclusion"}]
