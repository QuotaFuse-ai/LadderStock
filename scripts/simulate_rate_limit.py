#!/usr/bin/env python3
"""仿真一个被上游限流打中的交易日：退避 → 昨日池回放 → 整轮 degraded（QUOT-349）。

为什么是「仿真」而不是抓包：限流是**碰运气的返回**，要复现它只能要么刚好撞上 pacing
窗口，要么把上游打到限流里（后者等于对 peer 做一次压测，不该做）。所以这里用真实的
治理栈（`ProviderRegistry` + 仓库 `config/` + `ldst sync` / `ldst schedule` 的同一套
代码路径），只把**传输层**换成一个持续回 `QFSD-7001 rate_limited` 的桩——退避、冷却、
备源、缓存回放、状态判定全是生产逻辑在跑，不是测试桩在自证。

约定与 `release_smoke.py` 一致：不联网、不写仓库 `data/`（`LDST_DATA_DIR` 指向临时目录）、
等待由注入的 `sleeper` 记录（不真睡）、每步打印 `PASS` / `WARN` / `FAIL`，有 FAIL 非零退出。

用法：

```bash
python scripts/simulate_rate_limit.py            # 干净临时目录
python scripts/simulate_rate_limit.py --data-dir D:/tmp/quot349   # 保留产物复看
```
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT / "src", ROOT / "tests"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from fixtures.fake_peers import rate_limited_payload  # noqa: E402  - 先插 sys.path 再导入

from ladderstock.cli.schedule import run_job  # noqa: E402
from ladderstock.cli.sync import run_sync  # noqa: E402
from ladderstock.core import intraday  # noqa: E402
from ladderstock.data import archive, watermark  # noqa: E402
from ladderstock.data.provider_registry import PeerEndpoint, ProviderRegistry  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "peer_responses"

#: 仿真交易日：T 日盘中 10:00（周二，交易日）；上一次成功同步落在 T-1
TODAY = date(2026, 11, 3)
YESTERDAY = date(2026, 11, 2)
SLOT = "10:00"
#: 上游在仿真里给出的建议等待时长（真实 peer 是整秒，来自 `core/http.py`）
RETRY_AFTER_S = 8

POOL_TOOLS = ("yesterday_limit_up_pool", "limit_up_pool", "ths_limit_up_pool")
#: 本轮参与同步的数据集（都不需要代码池，扇出与限流是两条独立的路）
DATASETS = ["yesterday_limit_up", "limit_pool"]

SNAPSHOT_JOB = {
    "id": "snapshot_1000",
    "name": "10:00 盘中快照",
    "kind": "snapshot",
    "trigger": SLOT,
    "enabled": True,
}


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def pool_payload(tool: str, day: date) -> dict[str, Any]:
    """涨停池的真实返回，日期改成仿真的那一天。

    `data_as_of` 的取值顺序是「payload 自己声明的日期 > `meta.date` > 行内日期 >
    抓取时间」（QUOT-233 的口径），所以这几处只要存在就得一起改，漏一处仿真里的
    「昨日池」就不是昨天。
    """
    raw = fixture(tool)
    stamp = day.strftime("%Y%m%d")
    raw["meta"]["date"] = stamp
    raw["meta"]["fetched_at"] = f"{day.isoformat()}T15:05:00+08:00"
    body = raw["data"]
    rows = body if isinstance(body, list) else body.get("items") or []
    if isinstance(body, dict):
        for key in ("date", "trade_date"):
            if key in body:
                body[key] = day.isoformat() if len(str(body[key])) == 10 else stamp
    for row in rows:
        for key in ("date", "trade_date"):
            if key in row:
                row[key] = day.isoformat() if len(str(row[key])) == 10 else stamp
    return raw


def quote_payload(day: date) -> dict[str, Any]:
    """批量报价的真实返回，时间戳改成仿真当天的 10:00:03（快照要今天的读数）。"""
    raw = fixture("tencent_quote_batch")
    stamp = f"{day.strftime('%Y%m%d')}100003"
    for row in raw["data"]["quotes"].values():
        row["timestamp"] = stamp
    raw["meta"]["fetched_at"] = f"{day.isoformat()}T10:00:03+08:00"
    return raw


class StubTransport:
    """按 tool 分派的传输桩；`paced` 打开后除批量报价外一律回 `QFSD-7001`。

    批量报价**不**限流是刻意的：真实事故就是这样——池子那只工具在 pacing 窗口里，
    行情接口还活着。于是降级时快照仍有今天的读数，看板不是空白。

    池子返回的永远是 `YESTERDAY` 那一批（`meta.date` = 上一次成功同步的那一天）：
    T 日开盘后我们再没成功取到过池子，所以本地能回放的「昨日池」只能是 T-1 的。
    """

    def __init__(self) -> None:
        self.paced = False
        self.calls: list[str] = []

    def call(
        self, peer: PeerEndpoint, tool: str, params: dict[str, Any], *, timeout: float
    ) -> dict[str, Any]:
        self.calls.append(f"{peer.key}/{tool}")
        if self.paced and tool != "tencent_quote_batch":
            return rate_limited_payload(RETRY_AFTER_S, tool=tool)
        if tool == "tencent_quote_batch":
            return quote_payload(TODAY)
        if tool in POOL_TOOLS:
            return pool_payload(tool, YESTERDAY)
        raise AssertionError(f"仿真脚本没有为 {tool} 准备返回")

    def probe(self, peer: PeerEndpoint, *, timeout: float) -> dict[str, Any]:
        return {"peer": peer.key, "tools": list(POOL_TOOLS) + ["tencent_quote_batch"]}


class Report:
    def __init__(self) -> None:
        self.failed = 0

    def step(self, title: str) -> None:
        print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")

    def check(self, name: str, condition: bool, detail: str) -> None:
        if not condition:
            self.failed += 1
        print(f"[{'PASS' if condition else 'FAIL'}] {name} — {detail}")

    def note(self, text: str) -> None:
        print(f"       {text}")


def brief(row: dict[str, Any]) -> str:
    return (
        f"{row['dataset']} {row['status']}（水位 {row.get('watermark_before') or '-'} → "
        f"{row.get('watermark_after') or '-'}）：{row['detail']}"
    )


def main() -> int:
    args = sys.argv[1:]
    if "--data-dir" in args:
        home = Path(args[args.index("--data-dir") + 1])
    else:
        home = Path(tempfile.mkdtemp(prefix="quot349-"))
    os.environ["LDST_DATA_DIR"] = str(home)
    transport = StubTransport()
    waits: list[float] = []
    registry = ProviderRegistry(
        transport=transport, root=home, sleeper=waits.append, ttl_sec=0.0
    )
    store = watermark.WatermarkStore(root=home)
    report = Report()

    print(f"仿真数据目录：{home}")
    print(f"交易日 {TODAY.isoformat()}（周二），昨日池 {YESTERDAY.isoformat()}，时点 {SLOT}")

    # ---------------------------------------------------------------- 1. T-1 收盘后：正常同步
    report.step(f"1. {YESTERDAY.isoformat()} 收盘后正常同步（建立本地昨日池）")
    round_one = run_sync(registry, store, DATASETS)
    for row in round_one["results"]:
        report.note(brief(row))
    report.check(
        "昨日池已落本地",
        all(row["status"] == "ok" for row in round_one["results"]),
        f"退出码 {round_one['summary']['exit_code']}，状态 "
        + "、".join(row["status"] for row in round_one["results"]),
    )
    for dataset in DATASETS:
        latest = archive.latest_day(dataset, root=home)
        report.note(f"{dataset} 本地最新在库日：{latest or '(空)'}")

    # ---------------------------------------------------------------- 2. T 日盘中：整条链路被限流
    report.step(f"2. {TODAY.isoformat()} 盘中：上游持续回 QFSD-7001（批量报价除外）")
    transport.paced = True
    transport.calls.clear()
    waits.clear()
    store_doc_before = {d: store.value(d) for d in DATASETS}
    round_two = run_sync(registry, store, DATASETS)
    for row in round_two["results"]:
        report.note(brief(row))
    report.note(f"peer 调用序列：{transport.calls}")
    report.check(
        "按上游建议时长退避",
        waits == [float(RETRY_AFTER_S)] * (registry.rate_limit_retries * len(DATASETS)),
        f"实际等待 {waits} 秒：每次取数各等 "
        f"{registry.rate_limit_retries} 次 × {RETRY_AFTER_S} 秒"
        f"（单次上限 {registry.rate_limit_max_wait_sec:g} 秒，主备共享同一份预算）",
    )
    report.check(
        "退避后走昨日池回放",
        all(row["status"] == "degraded" for row in round_two["results"]),
        "、".join(f"{row['dataset']}={row['status']}" for row in round_two["results"]),
    )
    report.check(
        "日志说明是限流而不是源坏了",
        all("QFSD-7001" in row["detail"] for row in round_two["results"]),
        round_two["results"][0]["detail"],
    )
    report.check(
        "整轮 degraded、退出码仍是 0",
        round_two["summary"]["exit_code"] == 0 and round_two["summary"]["failed"] == 0,
        f"汇总 {round_two['summary']}（只有 failed 才会翻退出码）",
    )
    after = {d: store.value(d) for d in DATASETS}
    unchanged = all(before == after[d] for d, before in store_doc_before.items())
    report.check(
        "降级不推进水位、不重写数据",
        unchanged,
        "、".join(
            f"{d}: {before or '-'} → {after[d] or '-'}"
            for d, before in store_doc_before.items()
        ),
    )

    # ---------------------------------------------------------------- 3. T 日 10:00 快照任务
    report.step(f"3. {TODAY.isoformat()} {SLOT} 盘中快照任务（池子被限流、报价还活着）")
    result = run_job(
        SNAPSHOT_JOB,
        now=datetime(TODAY.year, TODAY.month, TODAY.day, 10, 0, 5),
        timezone="Asia/Shanghai",
        root=home,
        registry=registry,
    )
    report.note(f"任务结果：{result['status']} — {result['detail']}")
    snapshot_file = intraday.snapshot_path(SLOT, TODAY, home)
    payload: dict[str, Any] = {}
    if snapshot_file.is_file():
        payload = json.loads(snapshot_file.read_text(encoding="utf-8"))
    pool_stale = (payload.get("source") or {}).get("pool_stale") or {}
    report.check(
        "任务没有失败",
        result["status"] == "degraded",
        f"status={result['status']}（限流折成 degraded，不是 failed / missing）",
    )
    report.check(
        "看板仍有数据，且带着时点标记",
        bool(pool_stale) and payload.get("yesterday_limit_up", {}).get("fresh", 0) > 0,
        f"昨日池 {pool_stale.get('as_of')}（reason={pool_stale.get('reason')}），"
        f"今天读数 {payload.get('yesterday_limit_up', {}).get('fresh')} 只 / "
        f"共 {payload.get('yesterday_limit_up', {}).get('count')} 只",
    )
    status = intraday.read_status(TODAY, home)
    report.check(
        "状态台账也记成 degraded",
        (status.get("slots", {}).get(SLOT) or {}).get("status") == "degraded",
        json.dumps(status.get("slots", {}).get(SLOT) or {}, ensure_ascii=False),
    )

    # ---------------------------------------------------------------- 4. 观测：健康事件
    report.step("4. `ldst sources health` 会看到什么")
    for event in registry.health.events()[-6:]:
        report.note(f"{event['at']} {event['kind']} {event['source']}: {event['detail'][:110]}")
    cooldowns = registry.health.active_cooldowns()
    report.check(
        "限流单独记一类事件（不是 failure）",
        any(event["kind"] == "rate_limited" for event in registry.health.events()),
        f"活跃冷却 {json.dumps(cooldowns, ensure_ascii=False)}",
    )

    report.step("结论")
    verdict = "FAIL" if report.failed else "PASS"
    summary = (
        f"{report.failed} 项未达成"
        if report.failed
        else "限流退避 / 昨日池降级 / 整轮 degraded 全部成立"
    )
    print(f"{verdict} — {summary}")
    print(f"产物目录：{home}")
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
