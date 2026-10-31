"""`GET /api/limit-pool`：涨停样本只读接口的契约测试（SPEC-LIMIT-POOL）。

钉三件事，全部离线：

1. **两态都是 200**——有数据时 `available=true` + 行；本机缓存为空时
   `available=false` + `rows=[]` + 中文 `reason`，不 500、不造行；
2. **外壳与 `/api/ladder` 一致**（`LadderEnvelope`）：`available=false` 时
   `data_as_of=null`、`confidence=low`、`source_status=partial`，载荷里显式带
   `data_source_status`；
3. `date` 沿用 `/api/sentiment` 的 `_parse_day` 口径：缺省与非法都是 400。

有数据的用例打桩 `dataset_snapshot`（注入 records），空态用例用真实空缓存
（tmp 目录里本来就没有 `market.duckdb` 与 Parquet 分区）。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.core import limit_pool as limit_pool_module
from ladderstock.web.server import create_app

DAY = "2026-10-30"

ENVELOPE_KEYS = {
    "domain",
    "topic",
    "event_id",
    "data_as_of",
    "payload",
    "source",
    "source_detail",
    "fetched_at",
    "freshness",
    "confidence",
    "source_status",
}

ROW_KEYS = {
    "code",
    "name",
    "streak",
    "first_seal_time",
    "last_seal_time",
    "reason",
    "trade_date",
}

RECORDS: list[dict[str, Any]] = [
    {
        "code": "600000",
        "name": "浦发银行",
        "consecutive_boards": 2,
        "first_seal_time": "093015",
        "last_seal_time": "142500",
        "reason": "银行",
        "trade_date": DAY,
    },
    {"code": "000001", "name": "平安银行", "连板数": 5, "trade_date": DAY},
    # 重复 code：去重保留首条
    {"code": "600000", "name": "浦发银行", "boards": 9, "trade_date": DAY},
    # 无连板数：不进 rows
    {"code": "300001", "name": "缺连板数", "trade_date": DAY},
]


def _client(root: Any) -> TestClient:
    return TestClient(create_app(data_root=root, dist_dir=root / "no-dist"))


@pytest.fixture()
def seeded(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_snapshot(dataset: str, day: Any, root: Any = None) -> tuple[list[dict], str]:
        assert dataset == "limit_pool"
        return [dict(record) for record in RECORDS], "duckdb"

    monkeypatch.setattr(limit_pool_module, "dataset_snapshot", fake_snapshot)


def test_success_envelope_and_payload(tmp_path: Any, seeded: None) -> None:
    response = _client(tmp_path).get("/api/limit-pool", params={"date": DAY})

    assert response.status_code == 200
    body = response.json()
    assert set(body) == ENVELOPE_KEYS
    assert body["domain"] == "sentiment" and body["topic"] == "limit_pool"
    assert body["data_as_of"] == DAY
    assert body["confidence"] == "medium" and body["source_status"] == "ok"
    assert body["source"] == "ladderstock"
    assert body["freshness"] == "cached"

    payload = body["payload"]
    assert payload["data_source_status"] == "ok"
    assert payload["date"] == DAY
    assert payload["available"] is True and payload["reason"] is None
    assert payload["source_detail"] == (
        "limit_pool: duckdb（4 条，重复 code 去重 1 条，无连板数或无代码 1 条）"
    )
    assert payload["count"] == 2 == len(payload["rows"])
    for row in payload["rows"]:
        assert set(row) == ROW_KEYS
    # streak 降序、code 升序（与天梯一致）
    assert [row["code"] for row in payload["rows"]] == ["000001", "600000"]
    assert [row["streak"] for row in payload["rows"]] == [5, 2]


def test_missing_fields_are_null_not_defaulted(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        limit_pool_module,
        "dataset_snapshot",
        lambda *_a, **_k: ([{"code": "600001", "name": "某股", "boards": 3}], "duckdb"),
    )
    row = (
        _client(tmp_path)
        .get("/api/limit-pool", params={"date": DAY})
        .json()["payload"]["rows"][0]
    )

    assert row["first_seal_time"] is None
    assert row["last_seal_time"] is None
    assert row["reason"] is None
    assert row["trade_date"] is None


def test_empty_cache_is_200_unavailable(tmp_path: Any) -> None:
    """真实空缓存（tmp 里没有任何涨停池）：200 + available=false + 空 rows。"""
    response = _client(tmp_path).get("/api/limit-pool", params={"date": DAY})

    assert response.status_code == 200
    body = response.json()
    assert body["data_as_of"] is None
    assert body["confidence"] == "low" and body["source_status"] == "partial"

    payload = body["payload"]
    assert payload["data_source_status"] == "missing"
    assert payload["available"] is False
    assert payload["rows"] == [] and payload["count"] == 0
    assert "同步" in payload["reason"] or "连板数" in payload["reason"]
    assert payload["source_detail"].startswith("limit_pool: ")


def test_other_methods_rejected(tmp_path: Any, seeded: None) -> None:
    client = _client(tmp_path)
    assert client.post("/api/limit-pool", params={"date": DAY}).status_code == 405


@pytest.mark.parametrize(
    ("params", "detail"),
    [({}, "缺少 date"), ({"date": "2026/10/30"}, "非法日期")],
)
def test_bad_date_is_400(tmp_path: Any, params: dict[str, str], detail: str) -> None:
    """`date` 口径与 `/api/sentiment` 的 `_parse_day` 一致：缺省 / 非法都是 400。"""
    response = _client(tmp_path).get("/api/limit-pool", params=params)

    assert response.status_code == 400
    assert detail in response.json()["detail"]


def test_non_loopback_still_served(tmp_path: Any, seeded: None) -> None:
    """数据面接口（同 `/api/sentiment` / `/api/ladder`），不加回环独占。"""
    client = TestClient(
        create_app(data_root=tmp_path, dist_dir=tmp_path / "no-dist"),
        client=("10.1.2.3", 54321),
    )
    assert client.get("/api/limit-pool", params={"date": DAY}).status_code == 200
