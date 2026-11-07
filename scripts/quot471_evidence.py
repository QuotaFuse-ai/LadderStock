"""QUOT-471 验收取证：接口 JSON / 上游调用预算 / 双主题截图，一个脚本出全套。

两种模式：

1. **离线接口取证**（默认）：用 `tests/fixtures` 的真实抓包 + 假 transport，
   把 `GET /api/indices` 返回原样 JSON 打到 stdout，并报告本次调用了几次
   `th_us_get_quote`。

       PYTHONPATH=src:tests python scripts/quot471_evidence.py

2. **双主题截图**（`--shots`）：起本地应用托管 `frontend/dist`，用 Playwright
   （Chromium）在 1440×900 下拍 `?demo=1` 与 `?demo=1&variant=partial` 两态 ×
   暗/亮两主题，写到 `docs/screenshots/home-quot471-*.png`。截图是脚本产出的，
   不是手放的占位图。

       cd frontend && npm ci && npm run build
       python scripts/quot471_evidence.py --shots

   加 `--live` 则连本机真实 peer（`config/sources.yaml`）：接口模式打真实
   `GET /api/indices`，截图模式出 `home-quot471-live-*.png`——用来证明美股两格
   是真点位而非演示值。

       python scripts/quot471_evidence.py --live
       python scripts/quot471_evidence.py --shots --live

   依赖：前端已构建（`frontend/dist/index.html`）+ `pip install playwright`
   与 `playwright install chromium`（没有时回退本机 Chrome）。
"""

from __future__ import annotations

import argparse
import copy
import json
import socket
import sys
import threading
import time
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
SHOT_SIZE = {"width": 1440, "height": 900}
SHOT_STATES = (("demo", "demo=1"), ("partial", "demo=1&variant=partial"))


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


def _build() -> tuple[object, FakeTransport, Path]:
    """离线应用：假 transport 回放真实抓包，数据根落在仓库内临时目录。"""
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
    app = create_app(data_root=root, dist_dir=ROOT / "frontend" / "dist", radar_plane=plane)
    return app, transport, root


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def api_evidence(live: bool = False) -> None:
    if live:
        app = _build_live()
        transport = None
    else:
        app, transport, _root = _build()
    client = TestClient(app, client=("127.0.0.1", 54321))

    body = client.get("/api/indices").json()
    print("=== GET /api/indices ===")
    print(json.dumps(body, ensure_ascii=False, indent=2))
    print()
    if transport is None:
        print("=== 上游调用预算（真实 peer，见下方 httpx 日志与 8002 的 POST 条数） ===")
        print("  一次首页 = index_quote 1 次批量 + th_us_get_quote 2 次（DJI 1 + IXIC 1）")
        print("  th_us_get_quote 无按格扇出；MCP 握手每会话一次，与格数无关")
    else:
        print("=== 上游调用预算（本次首页取数） ===")
        for call in transport.calls:
            print(
                f"  {call.peer}/{call.tool}  "
                f"params={json.dumps(call.params, ensure_ascii=False)}"
            )
        us_calls = [c for c in transport.calls if c.tool == "th_us_get_quote"]
        print(f"  th_us_get_quote 次数 = {len(us_calls)}（预期 2：DJI 1 + IXIC 1，无按格扇出）")
    print()
    cells = body["payload"]["cells"]
    print(f"=== 八格摘要（{len(cells)} 格） ===")
    for c in cells:
        print(f"  {c['code']:<8} {c['market']:<3} {c['name']:<8} "
              f"price={c['price']} available={c['available']} reason={c['reason']}")


def _serve(app: object, port: int) -> object:
    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.monotonic() + 30
    while not server.started:
        if time.monotonic() > deadline:
            raise SystemExit("本地应用 30s 内没起来，无法截图")
        time.sleep(0.1)
    return server


def _build_live() -> object:
    """真实 peer 应用：按 `config/sources.yaml` 直连本机 tickhub / tickhub_us。"""
    import yaml

    from ladderstock.data.provider_registry import ProviderRegistry

    root = ROOT / ".quot471-live"
    root.mkdir(exist_ok=True)
    sources = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text(encoding="utf-8"))
    sync = yaml.safe_load((ROOT / "config" / "sync.yaml").read_text(encoding="utf-8"))
    registry = ProviderRegistry(sources=sources, sync=sync, root=root)
    plane = RadarPlane(data_root=root, registry=registry)
    return create_app(
        data_root=root, dist_dir=ROOT / "frontend" / "dist", radar_plane=plane
    )


def shots(live: bool = False) -> None:
    dist = ROOT / "frontend" / "dist" / "index.html"
    if not dist.is_file():
        raise SystemExit(
            "缺少 frontend/dist/index.html，先执行：cd frontend && npm ci && npm run build"
        )
    from playwright.sync_api import sync_playwright

    if live:
        app = _build_live()
        states = (("live", ""),)
    else:
        app, _transport, _root = _build()
        states = SHOT_STATES
    port = _free_port()
    server = _serve(app, port)
    out_dir = ROOT / "docs" / "screenshots"
    out_dir.mkdir(parents=True, exist_ok=True)
    base = f"http://127.0.0.1:{port}/"
    print(f"=== 截图（{SHOT_SIZE['width']}×{SHOT_SIZE['height']}，base={base}） ===")
    try:
        with sync_playwright() as pw:
            try:
                browser = pw.chromium.launch()
            except Exception as exc:  # noqa: BLE001 - 无 bundled Chromium 时退本机 Chrome
                print(f"  内置 Chromium 不可用（{exc}），回退本机 Chrome")
                browser = pw.chromium.launch(channel="chrome")
            for variant, query in states:
                for theme in ("dark", "light"):
                    page = browser.new_page(viewport=SHOT_SIZE)
                    page.add_init_script(
                        f"localStorage.setItem('ldst.theme', {theme!r})"
                    )
                    url = base if not query else f"{base}?{query}"
                    page.goto(url, wait_until="load")
                    page.wait_for_timeout(1500)
                    path = out_dir / f"home-quot471-{variant}-{theme}.png"
                    page.screenshot(path=str(path))
                    page.close()
                    print(f"  [shot] {path.relative_to(ROOT)}  {path.stat().st_size} bytes")
            browser.close()
    finally:
        server.should_exit = True


def main() -> None:
    parser = argparse.ArgumentParser(description="QUOT-471 验收取证")
    parser.add_argument("--shots", action="store_true", help="生成双主题截图（需先构建前端）")
    parser.add_argument(
        "--live", action="store_true", help="配合 --shots：连真实 peer 拍生产态截图"
    )
    args = parser.parse_args()
    if args.shots:
        shots(live=args.live)
    else:
        api_evidence(live=args.live)


if __name__ == "__main__":
    main()
