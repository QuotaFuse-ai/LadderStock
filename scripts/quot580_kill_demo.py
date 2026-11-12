"""QUOT-580 写入时机（落点 B）验收：第 2 块后杀进程，重启读回前 2 块。

子进程 `child` 建一行 `streaming`、写两块，然后 `os._exit(9)` 硬退（不落终态、
不跑任何清理）；父进程再用新连接打开同一个 `chat.sqlite`，读回会话与块。

    PYTHONPATH=src python scripts/quot580_kill_demo.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def child(root: str) -> None:
    from ladderstock.data import chat_store

    two_blocks = [
        {"type": "conclusion", "payload": {"text": "中短期以震荡为主。", "tone": "neutral"}},
        {
            "type": "citation",
            "payload": {
                "items": [
                    {
                        "name": "本地 /api/control/peers（工作区状态）",
                        "fetched_at": "2026-11-07T09:49:31+00:00",
                        "url": "http://127.0.0.1:5963/api/control/peers",
                    }
                ]
            },
        },
    ]
    conn = chat_store.connect(Path(root))
    started = chat_store.begin_turn(
        conn, session_id="sess-kill", question="杀进程前的一问", blocks=two_blocks[:1]
    )
    chat_store.update_turn_blocks(
        conn, session_id="sess-kill", seq=started["seq"], blocks=two_blocks
    )
    print(f"child: turn_seq={started['seq']} status={chat_store.STREAMING} blocks=2", flush=True)
    print("child: hard-exit", flush=True)
    os._exit(9)


def parent(root: str) -> int:
    from ladderstock.data import chat_store

    conn = chat_store.connect(Path(root), create=False)
    if conn is None:
        print("parent: 没有 chat.sqlite")
        return 1
    try:
        turns = chat_store.session_turns(conn, "sess-kill")
    finally:
        chat_store.close(conn)
    print(f"parent: rows={len(turns)}")
    for row in turns:
        blocks = json.loads(row["blocks_json"])
        print(
            f"parent: turn_seq={row['seq']} status={row['status']} "
            f"blocks={[block['type'] for block in blocks]}"
        )
    ok = (
        len(turns) == 1
        and turns[0]["status"] == "streaming"
        and [block["type"] for block in json.loads(turns[0]["blocks_json"])]
        == ["conclusion", "citation"]
    )
    print("parent: PASS" if ok else "parent: FAIL")
    return 0 if ok else 1


def main() -> int:
    root = tempfile.mkdtemp(prefix="quot580-kill-")
    proc = subprocess.run(
        [sys.executable, __file__, "child", root],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONPATH": "src"},
    )
    print(proc.stdout.strip())
    if proc.returncode != 9:
        print(f"child 没有按预期硬退：returncode={proc.returncode} stderr={proc.stderr[-400:]}")
        return 1
    return parent(root)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "child":
        child(sys.argv[2])
    else:
        raise SystemExit(main())
