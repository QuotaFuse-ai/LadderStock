"""QUOT-371：当日实时资金流走**批量**路径，一次调用取整池，不逐只 fan-out。

这组用例锁四件事：

1. 代码池 3 只 → **1 次** `stock_fund_flow_batch` 调用（不是 3 次 `stock_fund_flow`）；
2. 入参是逗号分隔的 `codes`，不是 `code`；
3. `meta.partial=true`（一批失败、另一批成功）时，**取到的行照常落库**且状态降级，
   而不是整包丢弃（DEC-053）；
4. 落库后 `core/pool.py` 的 `fund_flow_snapshot` 优先读这个数据集。

peer 全由 `tests/fixtures/fake_peers.py` 的离线桩扮演，不发任何网络请求。
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
from fixtures.fake_peers import SOURCES_DOC, SYNC_DOC, FakeTransport, make_registry

from ladderstock.cli import sync as sync_module
from ladderstock.core import pool
from ladderstock.data import watermark

DAY = "2026-11-04"
CODES = ["002696", "600261", "600715"]

SOURCES: dict[str, Any] = copy.deepcopy(SOURCES_DOC)
SOURCES["sources"].append(
    {
        "id": "tickhub_fund_flow_batch",
        "dataset": "fund_flow_realtime",
        "via": "tickhub",
        "tool": "stock_fund_flow_batch",
        "primary": True,
        "fallback": [],
        "timeout_sec": 2,
        "retries": 0,
        "cooldown_sec": 60,
    }
)

SYNC: dict[str, Any] = copy.deepcopy(SYNC_DOC)
SYNC["sync"]["datasets"]["fund_flow_realtime"] = {
    "primary": "tickhub_fund_flow_batch",
    "fallback": [],
    "watermark_field": "timestamp",
    "mode": "replace",
    "codes": [],
}


def batch_payload(
    flows: dict[str, dict[str, Any]],
    *,
    batches: int = 1,
    failures: list[dict[str, Any]] | None = None,
    partial: bool = False,
) -> dict[str, Any]:
    """真实 `stock_fund_flow_batch` 的形状（`data.flows` 是 {code: row}）。"""
    failed = failures or []
    payload = {
        "ok": not (failed or partial),
        "data": {
            "total": len(flows) + sum(len(f["codes"]) for f in failed),
            "succeeded": len(flows),
            "failed": sum(len(f["codes"]) for f in failed),
            "dropped": [],
            "flows": flows,
            "failures": failed,
            "batches": [
                {
                    "batch": i,
                    "requested": 150,
                    "returned": 150,
                    "dropped": [],
                    "fetched_at": f"{DAY}T10:0{i}:00+08:00",
                }
                for i in range(batches)
            ],
        },
        "meta": {
            "source": "eastmoney",
            "fetched_at": f"{DAY}T10:00:00+08:00",
            "scope": "当日实时",
            "batches": batches,
            "note": "当日实时累计口径；main_net 单位元",
        },
    }
    if failed or partial:
        payload["meta"].update(
            {"partial": True, "error_code": "QFSD-8001", "retryable": True}
        )
    return payload


def _flows() -> dict[str, dict[str, Any]]:
    return {
        "002696": {"code": "002696", "name": "百洋股份", "price": 11.12,
                   "change_pct": -10.03, "main_net": -171530812.0, "main_net_pct": -30.63},
        "600261": {"code": "600261", "name": "阳光照明", "price": 4.35,
                   "change_pct": -2.1, "main_net": -126896430.0, "main_net_pct": -19.61},
        "600715": {"code": "600715", "name": "文投控股", "price": 2.18,
                   "change_pct": 1.4, "main_net": -27577456.0, "main_net_pct": -1.54},
    }


@pytest.fixture()
def data_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    return tmp_path


def _run(root: Path, *, payload: dict[str, Any], calls: list[dict[str, Any]]) -> dict[str, Any]:
    def handler(params: dict[str, Any]) -> dict[str, Any]:
        calls.append(dict(params))
        return payload

    transport = FakeTransport(
        handlers={
            "stock_fund_flow_batch": handler,
            "stock_fund_flow": lambda p: pytest.fail(
                "批量数据集不该调用逐只工具 stock_fund_flow"
            ),
            "limit_up_pool": lambda _p: {"ok": True, "data": {"items": []}, "meta": {}},
        }
    )
    registry = make_registry(transport, root=root, sources=SOURCES, sync=SYNC)
    payload = sync_module.run_sync(
        registry,
        watermark.WatermarkStore(root=root),
        ["fund_flow_realtime"],
        codes=list(CODES),
    )
    return payload["results"][0]


def test_one_call_for_the_whole_pool(data_home: Path) -> None:
    """3 只票 = 1 次上游往返。fan-out 时这条会是 3 次并打挂在逐只工具上。"""
    calls: list[dict[str, Any]] = []
    out = _run(data_home, payload=batch_payload(_flows()), calls=calls)

    assert len(calls) == 1, f"期望 1 次批量调用，实际 {len(calls)} 次"
    assert out["status"] == "ok"
    assert out["rows"] == 3
    assert "批量 1 次调用" in out["detail"]
    assert "上游往返 1 次" in out["detail"]


def test_params_use_codes_not_code(data_home: Path) -> None:
    calls: list[dict[str, Any]] = []
    _run(data_home, payload=batch_payload(_flows()), calls=calls)

    params = calls[0]
    assert params["codes"] == ",".join(CODES)
    assert "code" not in params


def test_partial_failure_keeps_the_good_batch(data_home: Path) -> None:
    """一批失败、另一批成功：取到的行必须落库，状态标 degraded（DEC-053）。"""
    good = {"002696": _flows()["002696"]}
    payload = batch_payload(
        good,
        batches=1,
        failures=[{"batch": 1, "codes": ["600261", "600715"],
                   "error": {"code": "QFSD-7001", "message": "paced"}}],
        partial=True,
    )
    calls: list[dict[str, Any]] = []
    out = _run(data_home, payload=payload, calls=calls)

    assert out["status"] == "degraded"
    assert out["rows"] == 1
    assert "部分批次未取到" in out["detail"]


def test_pool_reads_the_realtime_partition_first(data_home: Path) -> None:
    """落库后 `fund_flow_snapshot` 优先读实时分区，且 as_of 是请求日、不算回落。"""
    _run(data_home, payload=batch_payload(_flows()), calls=[])

    records, backend, as_of, fell_back = pool.fund_flow_snapshot(DAY, data_home)

    assert backend != "none"
    assert len(records) == 3
    assert as_of is not None and as_of.isoformat() == DAY
    assert fell_back is False
    inflow = pool.fund_inflow_map(records)
    assert inflow["002696"] == pytest.approx(-171530812.0)
    assert inflow["600261"] == pytest.approx(-126896430.0)


def test_next_day_does_not_reuse_yesterdays_realtime(data_home: Path) -> None:
    """实时数据集只在当日有分区：跨日不许拿昨天的实时数冒充今天（DEC-053 后果）。"""
    _run(data_home, payload=batch_payload(_flows()), calls=[])

    records, backend, _as_of, _fell_back = pool.fund_flow_snapshot("2026-11-05", data_home)

    assert records == []
    assert backend == "none"


def test_empty_pool_sends_no_request(data_home: Path) -> None:
    """池子为空时一次调用都不发（空参会被 peer 拒回 QFSD-9001）。"""
    calls: list[dict[str, Any]] = []
    transport = FakeTransport(
        handlers={"stock_fund_flow_batch": lambda p: calls.append(p) or batch_payload({})}
    )
    registry = make_registry(transport, root=data_home, sources=SOURCES, sync=SYNC)

    out = sync_module.run_sync(
        registry,
        watermark.WatermarkStore(root=data_home),
        ["fund_flow_realtime"],
        codes=[],
    )["results"][0]

    assert calls == []
    assert out["status"] in ("skipped", "failed")
    assert "代码池为空" in out["detail"]


def test_batch_cap_is_three_calls_worth() -> None:
    """单轮上限按「150 只一次往返」算，不是逐只的 100 只。"""
    assert sync_module.BATCH_CODES_PER_CALL == 150
    assert sync_module.MAX_BATCH_POOL_CODES == 450
    assert sync_module.MAX_POOL_CODES == 100  # 逐只那条不变
    # 两个数据集是两套口径，不能被合成一个（DEC-053）
    from ladderstock.core.boundary import DATASET_CATALOG

    assert DATASET_CATALOG["fund_flow_realtime"].tool == "stock_fund_flow_batch"
    assert DATASET_CATALOG["fund_flow"].tool == "stock_fund_flow"
    assert DATASET_CATALOG["fund_flow_realtime"].watermark_field == "timestamp"
    assert DATASET_CATALOG["fund_flow"].watermark_field == "trade_date"
