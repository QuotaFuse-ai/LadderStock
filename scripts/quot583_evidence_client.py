"""QUOT-583 真服务器验收脚本：10 次 curl 中途断开 + 落库读回。

    python scripts/quot583_evidence_client.py
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

DATA_ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("D:/Temp/quot583-evidence-data")
URL = "http://127.0.0.1:5973/ai/chat"


def probe(index: int) -> bool:
    body = json.dumps({"message": f"第 {index} 轮断开测试", "session_id": f"sess-evidence-{index}"})
    proc = subprocess.Popen(
        ["curl", "-sN", "-X", "POST", URL, "-H", "Content-Type: application/json",
         "-H", "Accept: text/event-stream", "--data-raw", body, "--max-time", "30"],
        stdout=subprocess.PIPE,
    )
    got_frame = False
    deadline = time.time() + 5
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        if line.startswith(b"event: block"):
            got_frame = True
            break
    if got_frame:
        time.sleep(0.1)  # 确保下一帧正在路上时才断
    proc.kill()  # 硬断 TCP：等价于客户端中途消失
    proc.stdout.close()
    proc.wait(timeout=5)
    return got_frame


def read_rows() -> list[dict]:
    db = DATA_ROOT / "chat.sqlite"
    if not db.exists():
        return []
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT session_id, seq, status, updated_at, length(blocks_json) AS blocks_len"
            " FROM chat_turn ORDER BY seq"
        )]
    finally:
        conn.close()
    return rows


def main() -> int:
    print("== 10 次 curl 中途断开 ==", flush=True)
    hits = 0
    for i in range(1, 11):
        got = probe(i)
        hits += int(got)
        print(f"round {i}: first_block_seen={got}", flush=True)
        # 生成还在后台跑（约 2s），等它落库
        time.sleep(3)
    print(f"hit_rate={hits}/10", flush=True)

    print("\n== chat_turn 落库读回 ==", flush=True)
    rows = read_rows()
    for r in rows:
        print(
            f"  {r['session_id']} seq={r['seq']} status={r['status']}"
            f" blocks_len={r['blocks_len']}",
            flush=True,
        )
    n_done = sum(1 for r in rows if r["status"] == "done")
    print(f"rows={len(rows)} done={n_done}", flush=True)
    ok = hits == 10 and n_done >= 10
    print("VERDICT:", "PASS" if ok else "FAIL", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
