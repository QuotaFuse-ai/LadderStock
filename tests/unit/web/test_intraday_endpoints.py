"""QUOT-301：`GET /api/intraday` / `GET /api/intraday/snapshot` 的契约测试。

钉六件事，全部离线（用 QUOT-286 的桩直接跑 `run_slot()` 造真实落盘目录）：

1. **有数据**：`/api/intraday` 返回的 6 个时点与 `data/intraday/<date>/status.json`
   **逐字段一致**，不重算、不补 0；
2. **无数据 / 部分缺失**：空目录、`degraded` 与 `missing`/`skipped`/`failed` 混排、
   status.json 记着 `ok` 但文件不见了——全部 200 + `available=false` + 中文 `reason`，
   不 404、不 500；
3. **快照原文**：`/api/intraday/snapshot` 出的是文件内容本身（`payload.snapshot`），
   后端一个指标都不重算；只额外过一道 `strip_private()`；
4. **参数口径**：`date` 同 `/api/limit-pool`（缺省 / 非法 → 400），`slot` 只认 6 个时点；
5. **回环独占**：非回环 403（即使开了远程访问 + Token）、DNS rebinding 的 `Host` 也 403，
   但 `LDST_CONTROL_ENABLED=0` 不影响这两条只读接口；
6. **只读**：两条 GET 跑完，`data/intraday/` 里的文件一个都不多、一个都不少。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fixtures.intraday_stubs import StubRegistry, default_payloads, quote_records

from ladderstock.core import intraday
from ladderstock.web.server import create_app

DAY = date(2026, 10, 30)  # 周五
DAY_TEXT = "2026-10-30"
TZ = "Asia/Shanghai"
LOOPBACK = ("127.0.0.1", 54321)
REMOTE = ("10.1.2.3", 54321)

#: 每个时点取一个落在有效窗口内的时刻（窗口边界本身由 QUOT-286 的用例锁死）
IN_WINDOW: dict[str, tuple[int, int, int]] = {
    "09:25": (9, 25, 0),
    "09:35": (9, 35, 0),
    "10:00": (10, 0, 3),
    "11:30": (11, 30, 0),
    "14:00": (14, 0, 0),
    "15:00": (15, 0, 0),
}

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

SUMMARY_KEYS = {
    "date",
    "available",
    "reason",
    "has_status_file",
    "slot_count",
    "snapshot_count",
    "status_updated_at",
    "slots",
    "data_source_status",
}

#: 时点行的键：`slot / status / detail / file / price_field / stocks / fresh / at`
#: 加上 `recorded`（status.json 里有没有这条记录）与 `available` / `reason`
ROW_KEYS = {
    "slot",
    "status",
    "recorded",
    "available",
    "reason",
    "file",
    "detail",
    "price_field",
    "stocks",
    "fresh",
    "at",
}

SNAPSHOT_PAYLOAD_KEYS = {
    "date",
    "slot",
    "available",
    "reason",
    "snapshot",
    "data_source_status",
}


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #


def _client(root: Path, client: tuple[str, int] = LOOPBACK) -> TestClient:
    return TestClient(create_app(data_root=root, dist_dir=root / "no-dist"), client=client)


def _moment(slot: str) -> datetime:
    hour, minute, second = IN_WINDOW[slot]
    return datetime(DAY.year, DAY.month, DAY.day, hour, minute, second)


def _run(root: Path, slot: str, *, registry: StubRegistry | None = None) -> dict[str, Any]:
    """按 QUOT-286 的真实链路造一个时点的落盘结果（status.json + 快照文件）。"""
    return intraday.run_slot(
        slot, now=_moment(slot), timezone=TZ, root=root, registry=registry or StubRegistry()
    )


def _stale_quote_registry() -> StubRegistry:
    """报价的行情时间戳是昨天：节假日腾讯回节前收盘价，一条 fresh 读数都没有 → `missing`。"""
    payloads = default_payloads(DAY)
    payloads["spot_quote"] = [
        {**row, "timestamp": f"20261029{str(row['timestamp'])[-6:]}"} for row in quote_records(DAY)
    ]
    return StubRegistry(payloads=payloads, day=DAY)


def _dir_listing(root: Path) -> dict[str, str]:
    base = root / "intraday"
    if not base.is_dir():
        return {}
    return {
        str(path.relative_to(base)): path.read_text(encoding="utf-8")
        for path in sorted(base.rglob("*"))
        if path.is_file()
    }


# --------------------------------------------------------------------------- #
# 1. 有数据：6 个时点与 status.json 逐字段一致
# --------------------------------------------------------------------------- #


def test_summary_matches_status_json_field_by_field(tmp_path: Path) -> None:
    for slot in intraday.SLOTS:
        _run(tmp_path, slot)
    document = json.loads(intraday.status_path(DAY, tmp_path).read_text(encoding="utf-8"))

    response = _client(tmp_path).get("/api/intraday", params={"date": DAY_TEXT})
    assert response.status_code == 200
    body = response.json()
    assert set(body) == ENVELOPE_KEYS
    assert body["domain"] == "sentiment" and body["topic"] == "intraday"
    assert body["data_as_of"] == DAY_TEXT
    assert body["confidence"] == "medium" and body["source_status"] == "ok"

    payload = body["payload"]
    assert set(payload) == SUMMARY_KEYS
    assert payload["data_source_status"] == "ok"
    assert payload["available"] is True and payload["reason"] is None
    assert payload["has_status_file"] is True
    assert payload["slot_count"] == 6 and payload["snapshot_count"] == 6
    assert payload["status_updated_at"] == document["updated_at"]

    rows = payload["slots"]
    assert [row["slot"] for row in rows] == list(intraday.SLOTS)
    for row in rows:
        assert set(row) == ROW_KEYS
        entry = document["slots"][row["slot"]]
        # status.json 里有什么就出什么：不多写、不改写、不补默认值
        for key in ("status", "detail", "price_field", "stocks", "fresh", "at"):
            assert row[key] == entry[key], row["slot"]
        assert row["file"] == entry["file"]  # 文件都还在，名字对得上
        assert row["recorded"] is True
        assert row["available"] is True and row["reason"] is None


def test_summary_reads_status_json_not_the_snapshot_bodies(tmp_path: Path) -> None:
    """`09:25` 的 `price_field` 是 `open`，其余是 `price`：照 status.json 原样透出。"""
    for slot in ("09:25", "10:00"):
        _run(tmp_path, slot)

    rows = {
        row["slot"]: row
        for row in _client(tmp_path)
        .get("/api/intraday", params={"date": DAY_TEXT})
        .json()["payload"]["slots"]
    }
    assert rows["09:25"]["price_field"] == "open"
    assert rows["10:00"]["price_field"] == "price"
    assert rows["09:25"]["available"] is True


# --------------------------------------------------------------------------- #
# 2. 无数据 / 部分缺失：全是 200 + available=false + 中文原因
# --------------------------------------------------------------------------- #


def test_empty_root_is_200_unavailable(tmp_path: Path) -> None:
    body = _client(tmp_path).get("/api/intraday", params={"date": DAY_TEXT}).json()

    assert body["data_as_of"] is None
    assert body["confidence"] == "low" and body["source_status"] == "partial"
    payload = body["payload"]
    assert payload["data_source_status"] == "missing"
    assert payload["available"] is False and payload["snapshot_count"] == 0
    assert payload["has_status_file"] is False
    assert "status.json 不存在" in payload["reason"]
    # 6 个时点固定全给，前端不用自己拼时点表
    assert [row["slot"] for row in payload["slots"]] == list(intraday.SLOTS)
    for row in payload["slots"]:
        assert row["recorded"] is False
        assert row["status"] == "missing"
        assert row["available"] is False
        assert row["file"] is None
        assert row["detail"] is None and row["price_field"] is None
        assert row["stocks"] is None and row["fresh"] is None and row["at"] is None
        assert "不补跑" in row["reason"]


def test_corrupt_status_json_is_200_not_500(tmp_path: Path) -> None:
    path = intraday.status_path(DAY, tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json", encoding="utf-8")

    body = _client(tmp_path).get("/api/intraday", params={"date": DAY_TEXT}).json()
    assert body["payload"]["has_status_file"] is True
    assert body["payload"]["available"] is False
    assert body["payload"]["slots"][0]["recorded"] is False


def test_mixed_statuses_degraded_and_missing(tmp_path: Path) -> None:
    """一台真的跑过一天的机器：ok / degraded / skipped / missing / failed / 没跑过 混排。"""
    _run(tmp_path, "09:25")
    _run(tmp_path, "09:35", registry=StubRegistry(errors={"sentiment": RuntimeError("peer 抖了")}))
    intraday.run_slot(
        "10:00", now=datetime(2026, 10, 30, 11, 0, 0), timezone=TZ, root=tmp_path
    )  # 窗口外 → skipped
    _run(tmp_path, "11:30", registry=_stale_quote_registry())  # 行情不是今天 → missing
    _run(
        tmp_path, "14:00", registry=StubRegistry(errors={"spot_quote": ConnectionError("拒绝连接")})
    )
    # 15:00 一次都没跑 → status.json 里没有记录

    rows = {
        row["slot"]: row
        for row in _client(tmp_path)
        .get("/api/intraday", params={"date": DAY_TEXT})
        .json()["payload"]["slots"]
    }
    expected = {
        "09:25": ("ok", True),
        "09:35": ("degraded", True),
        "10:00": ("skipped", False),
        "11:30": ("missing", False),
        "14:00": ("failed", False),
        "15:00": ("missing", False),
    }
    assert {slot: (row["status"], row["available"]) for slot, row in rows.items()} == expected
    assert rows["15:00"]["recorded"] is False
    assert rows["09:25"]["file"] == "0925.json"
    for slot in ("10:00", "11:30", "14:00"):
        assert rows[slot]["file"] is None
        assert rows[slot]["recorded"] is True
        assert rows[slot]["reason"] == rows[slot]["detail"]

    payload = _client(tmp_path).get("/api/intraday", params={"date": DAY_TEXT}).json()["payload"]
    assert payload["available"] is True and payload["snapshot_count"] == 2


def test_status_record_without_snapshot_file_is_not_claimed(tmp_path: Path) -> None:
    """status.json 记着 `ok` 而文件被手工删了：不谎报有快照，`file` 也不给。"""
    _run(tmp_path, "10:00")
    target = intraday.snapshot_path("10:00", DAY, tmp_path)
    target.unlink()

    row = next(
        item
        for item in _client(tmp_path)
        .get("/api/intraday", params={"date": DAY_TEXT})
        .json()["payload"]["slots"]
        if item["slot"] == "10:00"
    )
    assert row["status"] == "ok"
    assert row["available"] is False
    assert row["file"] is None
    assert "1000.json" in row["reason"]

    payload = _client(tmp_path).get("/api/intraday", params={"date": DAY_TEXT}).json()["payload"]
    assert payload["available"] is False and payload["snapshot_count"] == 0


# --------------------------------------------------------------------------- #
# 3. 单时点快照：原文透出，不重算
# --------------------------------------------------------------------------- #


def test_snapshot_returns_file_body_verbatim(tmp_path: Path) -> None:
    _run(tmp_path, "10:00")
    path = intraday.snapshot_path("10:00", DAY, tmp_path)
    on_disk = json.loads(path.read_text(encoding="utf-8"))

    body = (
        _client(tmp_path)
        .get("/api/intraday/snapshot", params={"date": DAY_TEXT, "slot": "10:00"})
        .json()
    )
    assert body["domain"] == "sentiment" and body["topic"] == "intraday_snapshot"
    assert body["data_as_of"] == DAY_TEXT
    payload = body["payload"]
    assert set(payload) == SNAPSHOT_PAYLOAD_KEYS
    assert payload["available"] is True and payload["reason"] is None
    # 落盘文件里有什么就出什么：聚合块、逐只读数、来源链，一个字段都不重算
    assert payload["snapshot"] == on_disk
    assert set(on_disk) <= {
        "date",
        "slot",
        "captured_at",
        "price_field",
        "source",
        "yesterday_limit_up",
        "sentiment",
    }
    assert (
        payload["snapshot"]["yesterday_limit_up"]["overall"]
        == on_disk["yesterday_limit_up"]["overall"]
    )
    assert body["source_detail"] == on_disk["source"]["source_detail"]


def test_snapshot_missing_file_explains_where_to_look(tmp_path: Path) -> None:
    body = (
        _client(tmp_path)
        .get("/api/intraday/snapshot", params={"date": DAY_TEXT, "slot": "15:00"})
        .json()
    )
    payload = body["payload"]

    assert body["data_as_of"] is None and body["source_status"] == "partial"
    assert payload["available"] is False and payload["snapshot"] is None
    assert "1500.json 不存在" in payload["reason"]
    assert "/api/intraday?date=2026-10-30" in payload["reason"]


@pytest.mark.parametrize("body_text", ["{ not json", "[]", '""'])
def test_unreadable_snapshot_is_200_not_500(tmp_path: Path, body_text: str) -> None:
    path = intraday.snapshot_path("09:25", DAY, tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body_text, encoding="utf-8")

    response = _client(tmp_path).get(
        "/api/intraday/snapshot", params={"date": DAY_TEXT, "slot": "09:25"}
    )
    assert response.status_code == 200
    payload = response.json()["payload"]
    assert payload["available"] is False and payload["snapshot"] is None
    assert payload["reason"]


def test_snapshot_strips_private_keys(tmp_path: Path) -> None:
    """出口兜底：本机快照文件里混进持仓 / 交易日志字段也不出站。"""
    path = intraday.snapshot_path("11:30", DAY, tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "date": DAY_TEXT,
                "slot": "11:30",
                "journal": {"note": "x"},
                "positions": [{"code": "600000"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    snapshot = (
        _client(tmp_path)
        .get("/api/intraday/snapshot", params={"date": DAY_TEXT, "slot": "11:30"})
        .json()["payload"]["snapshot"]
    )
    assert set(snapshot) == {"date", "slot"}


# --------------------------------------------------------------------------- #
# 4. 参数口径
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("path", ["/api/intraday", "/api/intraday/snapshot"])
@pytest.mark.parametrize(
    ("params", "detail"), [({}, "缺少 date"), ({"date": "2026-13-01"}, "非法日期")]
)
def test_bad_date_is_400(tmp_path: Path, path: str, params: dict[str, str], detail: str) -> None:
    response = _client(tmp_path).get(path, params={"slot": "10:00", **params})
    assert response.status_code == 400
    assert detail in response.json()["detail"]


@pytest.mark.parametrize("value", ["10:60", "9:35", "09:45", "noon", "1000:00"])
def test_unknown_slot_is_400(tmp_path: Path, value: str) -> None:
    response = _client(tmp_path).get(
        "/api/intraday/snapshot", params={"date": DAY_TEXT, "slot": value}
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "未知快照时点" in detail or "缺少 slot" in detail
    for option in intraday.SLOTS:
        assert option in detail


def test_missing_slot_is_400(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/api/intraday/snapshot", params={"date": DAY_TEXT})
    assert response.status_code == 400
    assert "缺少 slot" in response.json()["detail"]


def test_compact_slot_accepted(tmp_path: Path) -> None:
    """`slot=1000` 是落盘文件名，照 `date` 的紧凑写法同一条容忍口径接受。"""
    _run(tmp_path, "10:00")
    response = _client(tmp_path).get(
        "/api/intraday/snapshot", params={"date": DAY_TEXT, "slot": "1000"}
    )
    assert response.status_code == 200
    assert response.json()["payload"]["slot"] == "10:00"


def test_other_methods_rejected(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.post("/api/intraday", params={"date": DAY_TEXT}).status_code == 405
    assert (
        client.post(
            "/api/intraday/snapshot", params={"date": DAY_TEXT, "slot": "10:00"}
        ).status_code
        == 405
    )


# --------------------------------------------------------------------------- #
# 5. 回环独占 + 控制面开关无关
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("path", ["/api/intraday", "/api/intraday/snapshot"])
def test_non_loopback_source_is_403(
    tmp_path: Path, path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _run(tmp_path, "10:00")
    monkeypatch.setenv("LDST_WEB_ALLOW_REMOTE", "1")
    monkeypatch.setenv("LDST_WEB_TOKEN", "0123456789abcdef0123")

    response = _client(tmp_path, client=REMOTE).get(
        path, params={"date": DAY_TEXT, "slot": "10:00"}
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "盘中快照只接受回环来源"
    # 快照内容不能随 403 漏出
    assert "000530" not in response.text

    rebinding = _client(tmp_path).get(
        path, params={"date": DAY_TEXT, "slot": "10:00"}, headers={"host": "evil.example.com"}
    )
    assert rebinding.status_code == 403

    assert (
        _client(tmp_path).get(path, params={"date": DAY_TEXT, "slot": "10:00"}).status_code == 200
    )


def test_control_switch_does_not_close_intraday(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`LDST_CONTROL_ENABLED=0` 关的是写面，两条只读接口照旧 200。"""
    _run(tmp_path, "10:00")
    monkeypatch.setenv("LDST_CONTROL_ENABLED", "0")
    client = _client(tmp_path)

    assert client.get("/api/intraday", params={"date": DAY_TEXT}).status_code == 200
    assert (
        client.get("/api/intraday/snapshot", params={"date": DAY_TEXT, "slot": "10:00"}).status_code
        == 200
    )


# --------------------------------------------------------------------------- #
# 6. 只读 + 注册在 SPA 兜底之前（DEC-034 的教训）
# --------------------------------------------------------------------------- #


def test_get_writes_nothing(tmp_path: Path) -> None:
    _run(tmp_path, "09:25")
    _run(tmp_path, "10:00")
    before = _dir_listing(tmp_path)
    assert before  # 确实有目录可比

    client = _client(tmp_path)
    for slot in intraday.SLOTS:
        client.get("/api/intraday/snapshot", params={"date": DAY_TEXT, "slot": slot})
    client.get("/api/intraday", params={"date": DAY_TEXT})
    client.get("/api/intraday", params={"date": "2026-11-11"})

    assert _dir_listing(tmp_path) == before


def test_routes_take_precedence_over_spa_fallback(tmp_path: Path) -> None:
    """没注册的 `/api/*` 由 SPA 兜底回 404 JSON；注册过的两条必须是信封，不是提示页。"""
    client = _client(tmp_path)

    assert client.get("/api/intraday/nope", params={"date": DAY_TEXT}).status_code == 404
    summary = client.get("/api/intraday", params={"date": DAY_TEXT})
    assert summary.status_code == 200
    assert summary.json()["topic"] == "intraday"
    snapshot = client.get("/api/intraday/snapshot", params={"date": DAY_TEXT, "slot": "09:25"})
    assert snapshot.status_code == 200
    assert snapshot.json()["topic"] == "intraday_snapshot"
