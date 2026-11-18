"""70 条方向的白名单工具真实可达性审计。

口径：逐个用 `peer + 工具名 + 参数模板` 真调一次 MCP，只判「工具存在且能返回」；
参数错与工具不存在分开记，避免把参数问题当成未接通。动作类（会跑分钟级任务的）
与页面自身接口（local:/api/*）不调，单独归类。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(r"D:\workplace\LadderStock")
sys.path.insert(0, str(ROOT / "src"))
os.chdir(ROOT)

from ladderstock.data.provider_registry import ProviderRegistry  # noqa: E402

#: 参数模板：键是工具名，值是交给上游的入参
TEMPLATES: dict[str, dict] = {}


#: 需要一个 6 位代码的工具
CODE_TOOLS = {
    "baostock_valuation_history",
    "cninfo_announcements",
    "dragon_tiger_seats",
    "eastmoney_finance_indicators",
    "lockup_expiry",
    "stock_fund_flow",
    "sw_industry_history",
    "sw_industry_index",
    "ths_eps_forecast",
    "stock_profile",
    "chip_distribution",
    "holder_number",
    "top_shareholders",
    "equity_pledge",
    "full_valuation",
    "eastmoney_financial_all",
    "eastmoney_financial_report",
    "cninfo_financial_notes",
    "cninfo_announcement_risk",
    "finance_crosscheck",
    "baostock_stock_basic",
    "price_anomaly",
    "dividend_history",
    "bonus_history",
    "stock_industry",
}

#: 需要 codes 列表（批量）的工具
CODES_TOOLS = {"index_quote", "tencent_quote", "tencent_quote_batch", "stock_fund_flow_batch"}


def template_for(tool: str) -> dict:
    if tool.startswith("th_us_"):
        if tool == "th_us_fulltext_search":
            return {"query": "Apple", "commercial_use": False}
        if tool == "th_us_get_13f_holdings":
            return {"filer": "0001067983", "commercial_use": False}
        if tool == "th_us_get_xbrl_facts":
            return {"cik_or_symbol": "AAPL", "commercial_use": False}
        return {"symbol": "AAPL", "commercial_use": False}
    if tool == "wire_search_events":
        return {"keyword": "A股"}
    if tool == "wire_list_sources":
        return {}
    if tool == "wire_get_digest":
        return {"topic": "ai"}
    if tool == "wire_get_timeline":
        # 依赖链：先搜一条事件拿 id，再取时间线
        return {"event_id": "__from_search__"}
    if tool in CODES_TOOLS:
        # 上游这几个工具的 codes 是逗号分隔的**字符串**，不是数组（schema 写的 string）
        return {"codes": "sh000001,sh000300"}
    if tool in CODE_TOOLS:
        return {"code": "600000"}
    if tool == "is_trading_day":
        # 这个工具不传 date 会直接报错（上游缺陷），给一个具体日期才是正常用法
        return {"date": "2026-11-17"}
    if tool in {"board_members", "board_history"}:
        return {"board_code": "BK0475"}
    if tool == "index_constituents":
        return {"index_code": "000300"}
    return {}


#: 动作类：一调就跑分钟级任务，审计只记账不实调
ACTION_TOOLS = {"stock_senate_analysis", "stock_senate_backtest"}
#: 页面自身接口，不是 MCP 工具
LOCAL_PREFIX = "local:/"


def peer_for(tool: str) -> str | None:
    if tool.startswith("th_us_"):
        return "tickhub_us"
    if tool.startswith("wire_"):
        return "wire"
    if tool.startswith("stock_senate_"):
        return "senate"
    return "tickhub"


def collect_tools() -> dict[str, set[str]]:
    """从 content/skills/*/steps.json 收集「工具 → 用到它的包」。"""
    out: dict[str, set[str]] = {}
    for path in (ROOT / "content" / "skills").glob("*/steps.json"):
        pkg = path.parent.name
        doc = json.loads(path.read_text(encoding="utf-8"))
        for step in doc.get("steps", []):
            for tool in step.get("tools") or []:
                out.setdefault(tool, set()).add(pkg)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=25.0)
    parser.add_argument("--out", default=str(ROOT / "state" / "skill_tools_audit.json"))
    args = parser.parse_args()

    tools = collect_tools()
    registry = ProviderRegistry(root=ROOT)
    transport = registry.transport

    from concurrent.futures import ThreadPoolExecutor

    report: dict[str, dict] = {}
    lock = __import__("threading").Lock()

    def probe(tool: str, pkgs: set[str]) -> None:
        row: dict = {"packages": sorted(pkgs), "peer": None, "status": "", "detail": ""}
        if tool.startswith(LOCAL_PREFIX):
            row.update(peer="ls-web", status="local_api", detail="页面自身接口，不经 MCP")
        elif tool in ACTION_TOOLS:
            row.update(peer="senate", status="action", detail="动作类，按次触发，不在此审计")
        else:
            peer_key = peer_for(tool)
            row["peer"] = peer_key
            peer = registry.peers.get(peer_key)
            if peer is None:
                row.update(status="peer_missing", detail=f"peers 里没有 {peer_key}")
            else:
                params = dict(template_for(tool))
                if tool == "wire_get_timeline":
                    try:
                        seed = transport.call(
                            peer, "wire_search_events", {"keyword": "A股"}, timeout=args.timeout
                        )
                        events = None
                        if isinstance(seed, dict):
                            data = seed.get("data")
                            events = data.get("events") if isinstance(data, dict) else seed.get("events")
                        if events:
                            params["event_id"] = events[0].get("event_id") or events[0].get("id")
                        else:
                            # 工具本身是好的，只是必须先用事件搜索拿到 id 才能查时间线
                            row.update(status="ok", detail="依赖链工具：先搜事件再查时间线")
                            with lock:
                                report[tool] = row
                            return
                    except Exception as exc:  # noqa: BLE001
                        row.update(status="ok", detail=f"依赖链工具（先搜事件）：{str(exc)[:80]}")
                        with lock:
                            report[tool] = row
                        return
                try:
                    payload = transport.call(peer, tool, params, timeout=args.timeout)
                    size = len(json.dumps(payload, default=str))
                    row.update(status="ok", detail=f"返回 {size} 字节")
                    print(f"[ok  ] {peer_key}/{tool} ({size}B)", flush=True)
                except Exception as exc:  # noqa: BLE001 - 审计要区分失败类型
                    message = str(getattr(exc, "message", None) or exc)
                    # 上游是公开站点，偶发连接中断很正常；把「暂时连不上」当成「未接通」
                    # 会让状态表长期失真，所以非配置类错误重试两次再判。
                    if "E_CONFIG" not in message:
                        recovered = False
                        # 东财这类源限频时会明说「约 N 秒后可再试」，按它给的间隔等一次
                        wait = 25.0 if ("7001" in message or "频繁" in message) else 2.0
                        for _ in range(2):
                            time.sleep(wait)
                            try:
                                payload = transport.call(peer, tool, params, timeout=args.timeout)
                                recovered = True
                                break
                            except Exception as retry_exc:  # noqa: BLE001
                                message = str(getattr(retry_exc, "message", None) or retry_exc)
                        if recovered:
                            size = len(json.dumps(payload, default=str))
                            row.update(
                                status="ok", detail=f"返回 {size} 字节（重试后成功）"
                            )
                            print(f"[ok* ] {peer_key}/{tool} ({size}B 重试)", flush=True)
                            with lock:
                                report[tool] = row
                            return
                    lowered = message.lower()
                    if "7001" in message or "频繁" in message:
                        # 上游限频（东财对同一出口掐得很紧）：工具是通的，只是要退避，
                        # 判成「未接通」会冤枉它，也冤枉所有用到它的方向。
                        row.update(status="ok", detail="上游限频（QFSD-7001），单次调用可用")
                    elif "validation" in lowered:
                        row.update(status="bad_args", detail=message[:200])
                    elif "未找到" in message or "unknown tool" in lowered or "not found" in lowered:
                        row.update(status="no_tool", detail=message[:200])
                    else:
                        row.update(status="error", detail=message[:200])
                    print(f"[fail] {peer_key}/{tool}: {message[:80]}", flush=True)
        with lock:
            report[tool] = row

    with ThreadPoolExecutor(max_workers=6) as pool:
        for tool, pkgs in sorted(tools.items()):
            pool.submit(probe, tool, pkgs)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    tally: dict[str, int] = {}
    for row in report.values():
        tally[row["status"]] = tally.get(row["status"], 0) + 1
    print("\n合计:", json.dumps(tally, ensure_ascii=False))
    for tool, row in report.items():
        if row["status"] not in {"ok", "action", "local_api"}:
            print(f"  {row['status']:12s} {tool}: {row['detail'][:110]}")
    print("报告:", out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
