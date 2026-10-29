"""QUOT-115：补齐的只读端点 `GET /api/watch/snapshot` 与 `GET /api/validation?date`。

对照 UI-SPEC §10 与 `frontend/src/api.ts:88-102` 的契约，逐条钉住四件事：

1. 有数据时按 `WatchSnapshotPayload` / `ValidationPayload` 的形状映射，缺的字段给 `null`；
2. 无数据 / 过期 / 坏文件一律 `available=false` + 空数组，不返回旧值、不编造数值；
3. 两个响应都过 `strip_private()`，隐私键（含中文写法）不出现在任何位置；
4. 未实现的 `/api/*` 仍是 404（SPA 兜底不被这两个路由破坏）。
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.watch.status import status_detail
from ladderstock.web.server import WATCH_SNAPSHOT_TTL_SEC, create_app

SENTINEL = "隐私哨兵-QUOT115"
PRIVATE_KEYS = (
    "journal",
    "trades",
    "positions",
    "holdings",
    "watchlist",
    "交易日志",
    "持仓",
    "自选",
)
ENVELOPE_FIELDS = {
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


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(data_root=tmp_path, dist_dir=tmp_path / "missing"))


def _iso(delta_seconds: int = 0) -> str:
    return (datetime.now() + timedelta(seconds=delta_seconds)).isoformat(timespec="seconds")


def _status_document(**overrides: Any) -> dict[str, Any]:
    """`ldst watch start` 落盘的最小合法状态文件（字段见 `watch/status.py:schema_problems`）。"""
    now = _iso()
    document: dict[str, Any] = {
        "running": True,
        "mode": "offline-single-pass",
        "date": date.today().isoformat(),
        "started_at": now,
        "last_run_at": now,
        "bars": 240,
        "event_count": 3,
        "recent_events": [
            {"ts": now, "code": "600000", "kind": "threshold_up", "value": 4.5},
            {"ts": now, "code": "600001", "kind": "seal", "value": 12.3},
            {"ts": now, "code": "600000", "kind": "seal", "value": 10.1},
        ],
    }
    document.update(overrides)
    return document


def _write_status(tmp_path: Path, document: dict[str, Any]) -> Path:
    path = tmp_path / "watch" / "status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


def _write_validation(tmp_path: Path, document: dict[str, Any]) -> Path:
    path = tmp_path / "state" / "validation" / f"{document['date']}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    return path


def _assert_no_private(text: str) -> None:
    lowered = text.lower()
    for key in PRIVATE_KEYS:
        assert key.lower() not in lowered, f"响应里出现隐私键：{key}"
    assert SENTINEL not in text


# --------------------------------------------------------------------------- #
# GET /api/watch/snapshot
# --------------------------------------------------------------------------- #

def test_watch_snapshot_ok_maps_status_file(client: TestClient, tmp_path: Path) -> None:
    document = _status_document()
    _write_status(tmp_path, document)

    response = client.get("/api/watch/snapshot")
    assert response.status_code == 200
    body = response.json()

    assert set(body) == ENVELOPE_FIELDS
    assert body["domain"] == "sentiment"
    assert body["topic"] == "watch_snapshot"
    assert body["data_as_of"] == date.today().isoformat()
    assert body["source_status"] == "ok"
    assert body["payload"]["data_source_status"] == "ok"
    # `source_detail` 用 `watch/status.py:status_detail()` 的文案，不另写一份人话
    assert body["source_detail"] == status_detail(document)

    payload = body["payload"]
    assert payload["trade_date"] == date.today().isoformat()
    rows = {row["code"]: row for row in payload["rows"]}
    assert set(rows) == {"600000", "600001"}
    # 状态文件没有 name / limit_up_count：如实给 null，不借别的数据源补造
    assert rows["600000"]["name"] is None
    assert rows["600000"]["limit_up_count"] is None
    # 同一 code 取最近一条信号：600000 最后一条是封板，涨跌幅不再沿用过时的越阈值
    assert rows["600000"]["status"] == "封板"
    assert rows["600000"]["change_pct"] is None
    assert rows["600001"]["status"] == "封板"
    assert rows["600001"]["change_pct"] is None

    # 信封的 fetched_at 反映真实取数时刻（而不是响应时刻），前端 TTL 才不会误判
    assert body["fetched_at"].startswith(document["last_run_at"].replace(" ", "T"))


def test_watch_snapshot_change_pct_only_from_threshold_signals(
    client: TestClient, tmp_path: Path
) -> None:
    document = _status_document(
        recent_events=[
            {"ts": _iso(), "code": "600000", "kind": "threshold_down", "value": -3.2},
        ],
        event_count=1,
    )
    _write_status(tmp_path, document)

    row = client.get("/api/watch/snapshot").json()["payload"]["rows"][0]
    assert row["code"] == "600000"
    assert row["change_pct"] == pytest.approx(-3.2)
    assert row["status"] == "3 分钟跳水"


def test_watch_snapshot_missing_file_is_unavailable(client: TestClient) -> None:
    response = client.get("/api/watch/snapshot")
    assert response.status_code == 200
    body = response.json()
    assert body["data_as_of"] is None
    assert body["source_status"] == "partial"
    assert body["confidence"] == "low"
    assert body["payload"] == {"trade_date": None, "rows": [], "data_source_status": "missing"}
    assert "不存在" in body["source_detail"]
    assert "ldst watch start" in body["source_detail"]


def test_watch_snapshot_expired_hides_old_values(client: TestClient, tmp_path: Path) -> None:
    stale = _iso(-(WATCH_SNAPSHOT_TTL_SEC + 12))
    _write_status(tmp_path, _status_document(last_run_at=stale))

    body = client.get("/api/watch/snapshot").json()
    # 过期即不返回旧值：行清空、时点置空，并在 source_detail 说明原因
    assert body["payload"]["rows"] == []
    assert body["payload"]["trade_date"] is None
    assert body["payload"]["data_source_status"] == "missing"
    assert body["data_as_of"] is None
    assert "TTL" in body["source_detail"]
    assert "旧值已隐藏" in body["source_detail"]
    assert "600000" not in json.dumps(body, ensure_ascii=False)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("{not json", "不可读"),
        ("[]", "不可读"),
        (json.dumps({"running": True, "mode": "x"}), "结构不合法"),
    ],
)
def test_watch_snapshot_broken_file_is_unavailable(
    client: TestClient, tmp_path: Path, content: str, expected: str
) -> None:
    path = tmp_path / "watch" / "status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

    body = client.get("/api/watch/snapshot").json()
    assert body["payload"]["rows"] == []
    assert body["payload"]["data_source_status"] == "missing"
    assert body["data_as_of"] is None
    assert expected in body["source_detail"]


def test_watch_snapshot_without_usable_timestamp_is_unavailable(
    client: TestClient, tmp_path: Path
) -> None:
    document = _status_document()
    document.pop("last_run_at")
    document.pop("started_at")
    _write_status(tmp_path, document)

    body = client.get("/api/watch/snapshot").json()
    assert body["payload"]["rows"] == []
    assert body["payload"]["data_source_status"] == "missing"
    assert "时间戳" in body["source_detail"]


def test_watch_snapshot_strips_private_keys(client: TestClient, tmp_path: Path) -> None:
    document = _status_document(
        watchlist=[{"code": "600000", "note": SENTINEL}],
        recent_events=[
            {
                "ts": _iso(),
                "code": "600000",
                "kind": "threshold_up",
                "value": 4.5,
                "positions": [{"note": SENTINEL}],
            },
        ],
        event_count=1,
    )
    _write_status(tmp_path, document)

    response = client.get("/api/watch/snapshot")
    assert response.status_code == 200
    assert response.json()["payload"]["rows"][0]["code"] == "600000"
    _assert_no_private(response.text)


# --------------------------------------------------------------------------- #
# GET /api/validation?date
# --------------------------------------------------------------------------- #

def _validation_document() -> dict[str, Any]:
    return {
        "date": "2026-10-27",
        "generated_at": "2026-10-27T15:10:00",
        "source": "review_pipeline",
        "conditions": [
            {
                "id": "temperature_floor",
                "text": "明日情绪温度不低于 45.0（今日 60.0）",
                "metric": "temperature",
                "operator": ">=",
                "value": 45.0,
            },
            {
                "id": "height_floor",
                "text": "明日最高板不低于 4 板（今日 5 板）",
                "metric": "max_board",
                "operator": ">=",
                "value": 4,
            },
        ],
        "reconcile": {
            "status": "ok",
            "next_session": "2026-10-28",
            "checked": 2,
            "passed": 1,
            "failed": 1,
            "results": [
                {"id": "temperature_floor", "passed": True},
                {"id": "height_floor", "passed": False},
            ],
        },
    }


def test_validation_ok_maps_conditions(client: TestClient, tmp_path: Path) -> None:
    _write_validation(tmp_path, _validation_document())

    response = client.get("/api/validation", params={"date": "2026-10-27"})
    assert response.status_code == 200
    body = response.json()

    assert set(body) == ENVELOPE_FIELDS
    assert body["domain"] == "validation"
    assert body["data_as_of"] == "2026-10-27"
    assert body["payload"]["data_source_status"] == "ok"
    assert body["payload"]["trade_date"] == "2026-10-27"
    assert "对账 ok" in body["source_detail"]

    conditions = body["payload"]["conditions"]
    assert [item["trigger"] for item in conditions] == [
        "明日情绪温度不低于 45.0（今日 60.0）",
        "明日最高板不低于 4 板（今日 5 板）",
    ]
    # 源文件没有标的代码：`code` 如实 null，不拿条件 id 冒充
    assert all(item["code"] is None for item in conditions)
    assert [item["note"] for item in conditions] == ["对账：通过", "对账：未通过"]


def test_validation_missing_file_is_unavailable_without_side_effects(
    client: TestClient, tmp_path: Path
) -> None:
    response = client.get("/api/validation", params={"date": "2026-10-27"})
    assert response.status_code == 200
    body = response.json()
    assert body["data_as_of"] is None
    assert body["payload"]["data_source_status"] == "missing"
    assert body["payload"]["conditions"] == []
    assert body["payload"]["trade_date"] == "2026-10-27"
    assert "不存在" in body["source_detail"]
    assert "ldst review --date 2026-10-27" in body["source_detail"]
    # 只读端点不现场重算：不给文件补上新内容
    assert not (tmp_path / "state" / "validation" / "2026-10-27.json").exists()


def test_validation_rejects_bad_or_missing_date(client: TestClient) -> None:
    assert client.get("/api/validation", params={"date": "2026-13-01"}).status_code == 400
    assert client.get("/api/validation").status_code == 400


def test_validation_strips_private_keys(client: TestClient, tmp_path: Path) -> None:
    document = _validation_document()
    document["journal"] = {"note": SENTINEL}
    document["conditions"][0]["positions"] = [{"note": SENTINEL}]
    _write_validation(tmp_path, document)

    response = client.get("/api/validation", params={"date": "2026-10-27"})
    assert response.status_code == 200
    assert len(response.json()["payload"]["conditions"]) == 2
    _assert_no_private(response.text)


# --------------------------------------------------------------------------- #
# 兜底与只读
# --------------------------------------------------------------------------- #

def test_unknown_api_still_404_and_new_routes_are_read_only(client: TestClient) -> None:
    # 未实现的 /api/* 仍走 404，不被新增路由或 SPA 兜底吞掉
    assert client.get("/api/nope").status_code == 404
    assert client.get("/api/watch/nope").status_code == 404
    assert client.get("/api/validation/extra").status_code == 404
    # 只读：写方法 405
    assert client.post("/api/watch/snapshot").status_code == 405
    assert client.put("/api/validation", params={"date": "2026-10-27"}).status_code == 405
