"""QUOT-471 验收取证：离线跑一次 `GET /api/indices`，打印真实 JSON 与上游调用预算。

不是测试，是一次性取证脚本：用 `tests/fixtures` 的真实抓包 + 假 transport，
把接口返回原样 JSON 打到 stdout，并报告本次调用了几次 `th_us_get_quote`。

    PYTHONPATH=src:tests python scripts/quot471_evidence.py
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from fastapi.testclient import TestClient  # noqa: E402

from fixtures.fake_peers import (  # noqa: E402
    SOURCES_DOC,
    SYNC_DOC,
    FakeTransport,
    make_registry,
    tickhub_payload,
    us_quote_payload,
)
from ladderstock.web.radar import RadarPlane  # noqa: E402
from ladderstock.web.server import create_app  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "peer_responses" / "index_quote.json"
US_PRICES = {"DJI": 51570.0898, "IXIC": 24707.8101}


def _sources_doc() -> dict:
    sources = copy.deepcopy(SOURCES_DOC)
    sources["sources"].extend(
        [
            {"id": "tickhub_index_quote", "dataset": "index_quote", "via": "tickhub",
             "tool": "index_quote", "primary": True, "fallback": [], "timeout_sec": 1,
             "retries": 0, "cooldown_sec": 60},
            {"id": "tickhub_us_index_quote", "dataset": "index_quote_us", "via": "tickhub_us",
             "tool": "th_us_get_quote", "primary": True, "fallback": [], "timeout_sec": 1,
             "retries": 0, "cooldown_sec": 60},
        ]
    )
    return sources


def main() -> None:
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))["data"]

    def us_handler(params: dict) -> dict:
        symbol = str(params.get("symbol") or "")
        price = US_PRICES.get(symbol, 100.0)
        return us_quote_payload(symbol, price=price, prev_close=price - 10.0,
                                change=10.0, change_pct=0.1)

    transport = FakeTransport(
        handlers={
            "index_quote": lambda p: tickhub_payload(
                rows, source="tencent", fetched_at="2026-11-03T22:47:19+08:00"
            ),
            "th_us_get_quote": us_handler,
        },
        tools=["index_quote", "th_us_get_quote"],
    )
    root = ROOT / ".quot471-evidence"
    root.mkdir(exist_ok=True)
    registry = make_registry(
        transport, root, sources=_sources_doc(), sync=copy.deepcopy(SYNC_DOC)
    )
    plane = RadarPlane(data_root=root, registry=registry)
    app = create_app(data_root=root, dist_dir=root / "no-dist", radar_plane=plane)
    client = TestClient(app, client=("127.0.0.1", 54321))

    body = client.get("/api/indices").json()
    print("=== GET /api/indices ===")
    print(json.dumps(body, ensure_ascii=False, indent=2))
    print()
    print("=== 上游调用预算（本次首页取数） ===")
    for call in transport.calls:
        print(f"  {call.peer}/{call.tool}  params={json.dumps(call.params, ensure_ascii=False)}")
    us_calls = [c for c in transport.calls if c.tool == "th_us_get_quote"]
    print(f"  th_us_get_quote 次数 = {len(us_calls)}（预期 2：DJI 1 + IXIC 1，无按格扇出）")
    print()
    cells = body["payload"]["cells"]
    print(f"=== 八格摘要（{len(cells)} 格） ===")
    for c in cells:
        print(f"  {c['code']:<8} {c['market']:<3} {c['name']:<8} "
              f"price={c['price']} available={c['available']} reason={c['reason']}")


if __name__ == "__main__":
    main()
