"""QUOT-583 真服务器验收补充：stop 端点 → aborted，与断开区分。

    python scripts/quot583_evidence_stop.py
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

DATA_ROOT = Path("D:/Temp/quot583-evidence-data")
URL = "http://127.0.0.1:5973/ai/chat"


def main() -> int:
    body = json.dumps({"message": "停止测试", "session_id": "sess-evidence-stop"})
    proc = subprocess.Popen(
        ["curl", "-sN", "-X", "POST", URL, "-H", "Content-Type: application/json",
         "-H", "Accept: text/event-stream", "--data-raw", body, "--max-time", "30"],
        stdout=subprocess.PIPE,
    )
    # 读到第一块就调 stop
    while True:
        line = proc.stdout.readline()
        if not line:
            break
        if line.startswith(b"event: block"):
            break
    stop_body = json.dumps({"session_id": "sess-evidence-stop"})
    out = subprocess.run(
        ["curl", "-s", "-X", "POST", f"{URL}/stop", "-H", "Content-Type: application/json",
         "--data-raw", stop_body, "--max-time", "10"],
        capture_output=True, text=True, timeout=15,
    )
    print("stop endpoint:", out.stdout.strip(), flush=True)
    # 读完剩余帧（done 帧应标 reason=aborted / completed=False）
    tail = proc.stdout.read().decode("utf-8", "replace")
    proc.kill()
    proc.wait(timeout=5)
    done_line = [ln for ln in tail.split("\n") if ln.startswith("data: ")]
    done_data = json.loads(done_line[-1][6:]) if done_line else {}
    print(
        "done reason:",
        done_data.get("reason"),
        "completed:",
        done_data.get("completed"),
        flush=True,
    )

    time.sleep(2)
    db = DATA_ROOT / "chat.sqlite"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT session_id, status, length(blocks_json) AS blocks_len"
            " FROM chat_turn WHERE session_id='sess-evidence-stop'"
        )]
    finally:
        conn.close()
    for r in rows:
        print(f"  {r['session_id']} status={r['status']} blocks_len={r['blocks_len']}", flush=True)
    ok = bool(rows) and rows[0]["status"] == "aborted" and done_data.get("reason") == "aborted"
    print("VERDICT:", "PASS" if ok else "FAIL", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
