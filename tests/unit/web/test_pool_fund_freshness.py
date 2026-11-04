"""`GET /api/pool` 资金分项的溯源三态（QUOT-372）。

`pool_view` 的 `freshness` 不再硬编码 `"cached"`，按资金数据的真实来源给，载荷里
另带 `fund_freshness`（三态枚举）与 `fund_fetched_at`（落库时刻，分钟级）：

- `realtime`：`fund_flow_realtime` 当日分区命中（QUOT-371 批量链）——信封
  `freshness` 同步升 `realtime`；
- `daily_fallback`：回落日线 `fund_flow`——这批数不是「当日实时」，信封仍是
  `cached`（本地缓存回放），载荷里明确区分；
- `none`：没有任何资金数据——`fund_fetched_at=None`，资金分项 `null` 不补 0
  （QUOT-319 老坑）。

全程离线：用 `duckdb_store.write_dataset` 往 tmp 数据根写真实 Parquet 分区，
`fund_fetched_at` 就是真实落库的 `ingested_at`（写库时生成），不伪造时间戳。
字段存在性也在这里钉住：三个态的载荷都必须带 `fund_freshness` / `fund_fetched_at`
两个键，且不动 `data_as_of` 的日期语义（`data_as_of` 仍是请求日 / missing 时 None）。
"""

from __future__ import annotations

import re
from typing import Any

from fastapi.testclient import TestClient

from ladderstock.data import duckdb_store
from ladderstock.web.server import create_app

DAY = "2026-10-30"
PREV = "2026-10-29"
CODE = "600519"

MINUTE_STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")


def _seed(root: Any, dataset: str, records: list[dict[str, Any]], day: str) -> None:
    duckdb_store.write_dataset(
        dataset,
        [{**record, "trade_date": day} for record in records],
        root=root,
    )


def _seed_pool_inputs(root: Any, day: str) -> None:
    _seed(
        root,
        "limit_pool",
        [
            {"code": CODE, "name": "贵州茅台", "boards": 3, "turnover_rate": 8.0,
             "hot_topic": "白酒"},
            {"code": "000001", "name": "平安银行", "boards": 1, "turnover_rate": 2.0},
        ],
        day,
    )
    _seed(root, "concept_board", [{"name": "白酒", "change_pct": 4.0}], day)


def _get_pool(root: Any, day: str = DAY) -> dict[str, Any]:
    client = TestClient(create_app(data_root=root, dist_dir=root / "no-dist"))
    response = client.get("/api/pool", params={"date": day})
    assert response.status_code == 200, response.text
    return response.json()


def test_realtime_hit_is_labeled_realtime_with_minute_fetched_at(tmp_path: Any) -> None:
    """命中当日实时批量分区：信封与载荷都标 `realtime`，fetched_at 到分钟。"""
    _seed_pool_inputs(tmp_path, DAY)
    _seed(
        tmp_path,
        "fund_flow_realtime",
        [
            {"code": CODE, "name": "贵州茅台", "main_net": -171530812.0,
             "main_net_pct": -30.63},
            {"code": "000001", "name": "平安银行", "main_net": 0.0, "main_net_pct": 0.0},
        ],
        DAY,
    )
    body = _get_pool(tmp_path)
    payload = body["payload"]

    assert body["freshness"] == "realtime"
    assert payload["fund_freshness"] == "realtime"
    assert payload["fund_fetched_at"] is not None
    assert MINUTE_STAMP.match(payload["fund_fetched_at"]), payload["fund_fetched_at"]
    # data_as_of 的日期语义不动：仍是请求日
    assert payload["data_as_of"] == DAY

    top = next(row for row in payload["candidates"] if row["code"] == CODE)
    assert top["parts"]["capital"] is not None
    assert top["raw"]["main_net_pct"] == -30.63


def test_daily_fallback_is_labeled_not_realtime(tmp_path: Any) -> None:
    """回落日线（昨日分区）：载荷明确 `daily_fallback`，信封如实 `cached`，不冒充实时。"""
    _seed_pool_inputs(tmp_path, DAY)
    _seed(
        tmp_path,
        "fund_flow",
        # 日线分区没有 `main_net_pct` 列：净占比如实缺失，不补 0
        [{"code": CODE, "name": "贵州茅台", "main_net": -171530812.0}],
        PREV,
    )
    body = _get_pool(tmp_path)
    payload = body["payload"]

    assert body["freshness"] == "cached"
    assert payload["fund_freshness"] == "daily_fallback"
    assert payload["fund_fetched_at"] is not None
    assert MINUTE_STAMP.match(payload["fund_fetched_at"]), payload["fund_fetched_at"]
    assert payload["meta"]["fund_data_as_of"] == PREV

    top = next(row for row in payload["candidates"] if row["code"] == CODE)
    assert top["parts"]["capital"] is not None
    assert top["raw"]["main_net_pct"] is None


def test_no_fund_data_is_none_and_never_zero(tmp_path: Any) -> None:
    """完全没有资金数据：`fund_freshness=none`、`fetched_at=None`，资金分项 `null`。"""
    _seed_pool_inputs(tmp_path, DAY)
    body = _get_pool(tmp_path)
    payload = body["payload"]

    assert payload["missing"] == ["fund_flow"]
    assert payload["fund_freshness"] == "none"
    assert payload["fund_fetched_at"] is None
    assert body["freshness"] == "cached"

    top = payload["candidates"][0]
    assert top["parts"]["capital"] is None
    assert top["raw"]["main_net_pct"] is None


def test_provenance_fields_always_present_in_payload(tmp_path: Any) -> None:
    """字段存在性（契约）：`fund_freshness` / `fund_fetched_at` 在载荷顶层恒在，
    就地挨着 `data_as_of`，前端按字段读而不是猜。"""
    _seed_pool_inputs(tmp_path, DAY)
    _seed(
        tmp_path,
        "fund_flow_realtime",
        [{"code": CODE, "name": "贵州茅台", "main_net": 1.0, "main_net_pct": 2.0}],
        DAY,
    )
    payload = _get_pool(tmp_path)["payload"]
    assert "fund_freshness" in payload and "fund_fetched_at" in payload
    # 三个键并排出现在同一层
    keys = set(payload)
    assert {"data_as_of", "fund_freshness", "fund_fetched_at"} <= keys


def test_realtime_probe_failure_degrades_honestly_not_crashes(
    tmp_path: Any, monkeypatch: Any
) -> None:
    """实时分区缓存读不动：如实降级成日线口径，不让整条接口 500，也不误标 realtime。"""
    from ladderstock.web import radar

    _seed_pool_inputs(tmp_path, DAY)
    _seed(
        tmp_path,
        "fund_flow_realtime",
        [{"code": CODE, "name": "贵州茅台", "main_net": 1.0, "main_net_pct": 2.0}],
        DAY,
    )
    _seed(tmp_path, "fund_flow", [{"code": CODE, "main_net": 3.0}], PREV)

    real_snapshot = radar.archive.snapshot

    def flaky(dataset: str, *args: Any, **kwargs: Any) -> Any:
        if dataset == radar.FUND_REALTIME_DATASET:
            raise OSError("parquet corrupt")
        return real_snapshot(dataset, *args, **kwargs)

    monkeypatch.setattr(radar.archive, "snapshot", flaky)
    body = _get_pool(tmp_path)
    payload = body["payload"]

    assert payload["fund_freshness"] == "daily_fallback"
    assert payload["fund_fetched_at"] is not None
    assert body["freshness"] == "cached"
    top = next(row for row in payload["candidates"] if row["code"] == CODE)
    assert top["parts"]["capital"] is not None
