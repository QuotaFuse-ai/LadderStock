"""QUOT-430 / DEC-056：持仓账本接口契约。

`GET /api/portfolio` 读面 + 四条写面（流水增删、旧记录 adopt / dismiss）。本文件钉住
issue 验收里点名的那几件事：

1. **平面 JSON、回环独占、不进信封**：顶层键固定，递归里不出现
   `holdings` / `positions` / `trades`，所有响应带 `no-store`；
2. **空态不建库**：本机没有账本时 GET 返回空视图，磁盘上不冒出 `journal.sqlite`；
3. **闸门**：非回环 / 坏 `Host` / 缺来源证据 / 跨站 `Sec-Fetch-Site` 一律 403；
4. **行情拼接**：取到价才算市值与盈亏，取不到就是 `null` + 原因、不进人民币合计
   （DEC-028：绝不拿成本价顶替）；
5. **旧仓闭环**：GET 自动迁移 `pending`（先落备份），已迁移的显示 `stale`；
   旧记录改动后成 `conflict`，adopt 原地替换同一笔、不多出第二笔；dismiss 只写标记。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.data import sqlite_store
from ladderstock.journal import portfolio as ledger
from ladderstock.web import radar
from ladderstock.web.radar import (
    POSITIONS_DENIED,
    POSITIONS_WRITE_DENIED,
    RadarPlane,
)
from ladderstock.web.server import create_app

LOOPBACK = ("127.0.0.1", 54321)
REMOTE = ("10.1.2.3", 54321)
SAME_ORIGIN = "http://127.0.0.1:5950"

BROWSER_HEADERS = {"origin": SAME_ORIGIN}
SCRIPT_HEADERS = {"x-ldst-client": "1"}

CODE = "600519"
OTHER = "000001"

TOP_LEVEL_KEYS = {
    "local_only",
    "store",
    "method",
    "method_label",
    "methods",
    "today",
    "price_lookup",
    "daily_lookup",
    "items",
    "closed",
    "realized",
    "dividends",
    "issues",
    "ledger",
    "legacy",
    "migration_pending",
    "migration",
    "totals_cny",
}

#: 请求体用 `trade_date`；响应里的流水行投影成 `date`（与参考实现一致）
def _buy(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "symbol": CODE,
        "name": "贵州茅台",
        "type": "buy",
        "trade_date": "2026-10-20",
        "shares": 100,
        "price": 10,
        "fee": 0,
    }
    body.update(overrides)
    return body


def _empty_price_map(codes: list[str], day: str | None, *, root: Path | None = None):
    return {}, {
        "dataset": "daily_k",
        "date": None,
        "backend": None,
        "reason": "本机缓存里没有 daily_k 数据（先 ldst sync --dataset daily_k）",
    }


def _empty_daily(codes: list[str], day: str | None, *, root: Path | None = None):
    return {}, {
        "dataset": "daily_k",
        "date": None,
        "prev_date": None,
        "backend": None,
        "reason": "本机缓存里没有 daily_k 数据",
    }


@pytest.fixture(autouse=True)
def _offline_prices(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(radar, "price_map", _empty_price_map)
    monkeypatch.setattr(radar, "daily_change_map", _empty_daily)


def _use_prices(
    monkeypatch: pytest.MonkeyPatch,
    prices: dict[str, float],
    daily: dict[str, dict[str, Any]] | None = None,
    *,
    day: str = "2026-11-04",
) -> None:
    monkeypatch.setattr(
        radar,
        "price_map",
        lambda codes, raw_day, *, root=None: (
            dict(prices),
            {"dataset": "daily_k", "date": day, "backend": "stub", "reason": None},
        ),
    )
    monkeypatch.setattr(
        radar,
        "daily_change_map",
        lambda codes, raw_day, *, root=None: (
            dict(daily or {}),
            {
                "dataset": "daily_k",
                "date": day,
                "prev_date": "2026-11-03",
                "backend": "stub",
                "reason": None,
            },
        ),
    )


def _client(root: Path, *, client: tuple[str, int] = LOOPBACK) -> TestClient:
    plane = RadarPlane(data_root=root)
    app = create_app(data_root=root, dist_dir=root / "no-dist", radar_plane=plane)
    return TestClient(app, client=client)


def _add(client: TestClient, body: Any, headers: dict[str, str] | None = None):
    return client.post("/api/portfolio/ledger", json=body, headers=headers or BROWSER_HEADERS)


def _seed_position(
    root: Path,
    *,
    code: str = "600000",
    qty: float = 200,
    cost: float = 8.0,
    entry_date: str | None = "2025-01-02",
) -> int:
    conn = sqlite_store.connect(root=root, create=True)
    assert conn is not None
    try:
        cursor = conn.execute(
            "INSERT INTO positions (code, cost, qty, entry_date, industry, note, updated_at)"
            " VALUES (?, ?, ?, ?, '', '', ?)",
            (code, cost, qty, entry_date, sqlite_store.now_iso()),
        )
        return int(cursor.lastrowid or 0)
    finally:
        sqlite_store.close(conn)


def _position_count(root: Path) -> int:
    conn = sqlite_store.connect(root=root, create=False)
    assert conn is not None
    try:
        return int(conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0])
    finally:
        sqlite_store.close(conn)


def _backups(root: Path) -> list[Path]:
    return sorted(root.glob("*bak-portfolio-*"))


# --------------------------------------------------------------------------- #
# 空态与结构
# --------------------------------------------------------------------------- #


def test_empty_view_does_not_create_the_database(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/api/portfolio")

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == TOP_LEVEL_KEYS
    assert body["local_only"] is True
    assert body["store"] == "local_sqlite"
    assert body["method"] == "average"
    assert body["method_label"] == "移动加权平均"
    assert body["methods"] == [
        {"id": "average", "label": "移动加权平均"},
        {"id": "fifo", "label": "先进先出"},
    ]
    assert body["today"] == date.today().isoformat()
    assert body["items"] == []
    assert body["ledger"] == []
    assert body["legacy"] == []
    assert body["migration_pending"] == 0
    assert body["migration"] == {
        "backup": None,
        "migrated": [],
        "removed_stale": [],
        "failed": [],
        "error": None,
    }
    assert body["totals_cny"]["available"] is False
    assert body["totals_cny"]["reason"] == "本机还没有持仓流水"

    assert not sqlite_store.db_path(tmp_path).exists(), "只读空态不能建库"


def test_add_then_get_projects_the_holding(tmp_path: Path) -> None:
    client = _client(tmp_path)

    created = _add(client, _buy())
    assert created.status_code == 200, created.text
    assert created.json()["trade"]["symbol"] == CODE
    assert created.json()["trade"]["date"] == "2026-10-20"

    body = client.get("/api/portfolio").json()
    assert [row["id"] for row in body["ledger"]] == [1]
    assert body["legacy"] == []
    assert body["issues"] == []
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["symbol"] == CODE
    assert item["name"] == "贵州茅台"
    assert item["shares"] == 100
    assert item["unit_cost"] == 10
    assert item["cost_total"] == 1000
    assert item["opened_at"] == "2026-10-20"
    assert item["trade_count"] == 1
    # 移动加权平均不保留批次；fifo 才逐批列出
    assert item["lots"] == []
    fifo_item = client.get("/api/portfolio?method=fifo").json()["items"][0]
    assert fifo_item["lots"] == [
        {
            "trade_id": 1,
            "date": "2026-10-20",
            "shares": 100,
            "unit_cost": 10,
        }
    ]
    # 本机没有行情缓存：现价如实为 null，不拿成本价顶替
    assert item["latest_price"] is None
    assert item["market_value"] is None
    assert item["pnl"] is None
    assert item["pnl_pct"] is None
    assert "daily_k" in item["price_reason"]
    assert body["totals_cny"]["available"] is False


def test_flat_json_never_carries_private_keys(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _add(client, _buy())

    text = json.dumps(client.get("/api/portfolio").json(), ensure_ascii=False)
    for forbidden in ('"holdings"', '"positions"', '"trades"'):
        assert forbidden not in text


# --------------------------------------------------------------------------- #
# 成本法：同一批流水，两种口径不同
# --------------------------------------------------------------------------- #


def test_fifo_and_average_differ_on_the_same_ledger(tmp_path: Path) -> None:
    client = _client(tmp_path)
    for body in (
        {
            "symbol": CODE,
            "type": "opening",
            "trade_date": "2026-10-01",
            "shares": 100,
            "price": 10,
        },
        {
            "symbol": CODE,
            "type": "buy",
            "trade_date": "2026-10-02",
            "shares": 100,
            "price": 20,
            "fee": 0,
        },
        {
            "symbol": CODE,
            "type": "sell",
            "trade_date": "2026-10-03",
            "shares": 150,
            "price": 30,
            "fee": 0,
        },
    ):
        assert _add(client, body).status_code == 200

    average = client.get("/api/portfolio?method=average").json()
    fifo = client.get("/api/portfolio?method=fifo").json()

    assert average["method_label"] == "移动加权平均"
    assert fifo["method_label"] == "先进先出"
    assert average["items"][0]["shares"] == fifo["items"][0]["shares"] == 50
    assert average["items"][0]["unit_cost"] == 15
    assert average["items"][0]["cost_total"] == 750
    assert fifo["items"][0]["unit_cost"] == 20
    assert fifo["items"][0]["cost_total"] == 1000
    assert average["realized"][0]["pnl"] == 2250
    assert fifo["realized"][0]["pnl"] == 2500
    assert average["realized"][0]["cost"] == 2250
    assert fifo["realized"][0]["cost"] == 2000


def test_invalid_method_is_422(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/api/portfolio?method=last")
    assert response.status_code == 422
    assert "fifo" in response.json()["detail"]


# --------------------------------------------------------------------------- #
# 行情拼接与人民币合计
# --------------------------------------------------------------------------- #


def test_prices_drive_market_value_and_totals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path)
    _add(client, _buy())
    _use_prices(
        monkeypatch,
        {CODE: 12.0},
        {CODE: {"status": "ok", "close": 12.0, "prev_close": 10.0, "change": 2.0}},
    )

    body = client.get("/api/portfolio").json()
    item = body["items"][0]
    assert item["latest_price"] == 12.0
    assert item["change"] == 2.0
    assert item["change_pct"] == 20.0
    assert item["market_value"] == 1200.0
    assert item["pnl"] == 200.0
    assert item["pnl_pct"] == 20.0
    assert item["price_reason"] is None

    totals = body["totals_cny"]
    assert totals == {
        "currency": "CNY",
        "available": True,
        "market_value": 1200.0,
        "cost": 1000.0,
        "pnl": 200.0,
        "pnl_pct": 20.0,
        "priced_count": 1,
        "unpriced_count": 0,
        "reason": None,
    }


def test_unpriced_row_stays_null_and_is_excluded_from_totals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path)
    _add(client, _buy())
    _add(client, _buy(symbol=OTHER, name="平安银行"))
    _use_prices(monkeypatch, {CODE: 12.0}, {})

    body = client.get("/api/portfolio").json()
    by_symbol = {item["symbol"]: item for item in body["items"]}
    assert by_symbol[CODE]["market_value"] == 1200.0
    assert by_symbol[OTHER]["latest_price"] is None
    assert by_symbol[OTHER]["market_value"] is None
    assert by_symbol[OTHER]["pnl"] is None
    assert OTHER in by_symbol[OTHER]["price_reason"]

    totals = body["totals_cny"]
    assert totals["priced_count"] == 1
    assert totals["unpriced_count"] == 1
    assert totals["market_value"] == 1200.0
    assert totals["cost"] == 1000.0
    assert "未计入人民币合计" in totals["reason"]
    assert "未计入人民币合计" in body["price_lookup"]["reason"]


# --------------------------------------------------------------------------- #
# 校验
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("body", "needle"),
    [
        ([{"symbol": CODE}], "JSON 对象"),
        (_buy(shares=None), "数量"),
        ({**_buy(), "mystery": 1}, "不认识的字段"),
        (_buy(price=None), "成交价"),
        (_buy(symbol="900901"), "B 股"),
        (_buy(trade_date="2026-02-30"), "日期"),
        ({"symbol": CODE, "type": "dividend", "trade_date": "2026-10-20", "amount": 0}, "现金分红"),
        ({"symbol": CODE, "type": "split", "trade_date": "2026-10-20", "ratio": 0}, "送转拆股"),
        (_buy(fee=-1), "费用"),
    ],
)
def test_invalid_ledger_bodies_are_400(tmp_path: Path, body: Any, needle: str) -> None:
    response = _add(_client(tmp_path), body)
    assert response.status_code == 400, response.text
    assert needle in response.json()["detail"]
    assert ledger.list_trades(root=tmp_path) == [], "校验失败不能落库"
    assert not sqlite_store.db_path(tmp_path).exists(), "校验失败不能建库"


def test_delete_ledger_backs_up_and_404_afterwards(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _add(client, _buy())

    response = client.post("/api/portfolio/ledger/1/delete", headers=BROWSER_HEADERS)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["removed"]["symbol"] == CODE
    assert "bak-portfolio-" in body["backup"]
    assert (tmp_path / body["backup"]).exists()
    assert ledger.list_trades(root=tmp_path) == []

    missing = client.post("/api/portfolio/ledger/1/delete", headers=BROWSER_HEADERS)
    assert missing.status_code == 404
    assert "1" in missing.json()["detail"]

    non_numeric = client.post("/api/portfolio/ledger/abc/delete", headers=BROWSER_HEADERS)
    assert non_numeric.status_code == 400


# --------------------------------------------------------------------------- #
# 闸门
# --------------------------------------------------------------------------- #


def test_read_requires_loopback(tmp_path: Path) -> None:
    response = _client(tmp_path, client=REMOTE).get("/api/portfolio")
    assert response.status_code == 403
    assert response.json()["detail"] == POSITIONS_DENIED


def test_read_rejects_bad_host_header(tmp_path: Path) -> None:
    response = _client(tmp_path).get(
        "/api/portfolio", headers={"host": "evil.example.com"}
    )
    assert response.status_code == 403
    assert "Host" in response.json()["detail"]


def test_write_requires_loopback(tmp_path: Path) -> None:
    response = _add(_client(tmp_path, client=REMOTE), _buy())
    assert response.status_code == 403
    assert response.json()["detail"] == POSITIONS_DENIED
    assert ledger.list_trades(root=tmp_path) == []


def test_write_requires_source_evidence(tmp_path: Path) -> None:
    client = _client(tmp_path)
    missing = client.post("/api/portfolio/ledger", json=_buy())
    assert missing.status_code == 403
    assert missing.json()["detail"] == POSITIONS_WRITE_DENIED
    assert ledger.list_trades(root=tmp_path) == []

    assert _add(client, _buy(), SCRIPT_HEADERS).status_code == 200


def test_cross_site_fetch_metadata_is_403(tmp_path: Path) -> None:
    client = _client(tmp_path)
    cross = _add(client, _buy(), {**BROWSER_HEADERS, "sec-fetch-site": "cross-site"})
    assert cross.status_code == 403
    assert "Sec-Fetch-Site" in cross.json()["detail"]

    same = _add(client, _buy(), {**BROWSER_HEADERS, "sec-fetch-site": "same-origin"})
    assert same.status_code == 200


def test_every_portfolio_response_is_no_store(tmp_path: Path) -> None:
    client = _client(tmp_path)

    get_response = client.get("/api/portfolio")
    assert get_response.headers["cache-control"] == "no-store"

    add_response = _add(client, _buy())
    assert add_response.headers["cache-control"] == "no-store"
    trade_id = add_response.json()["trade"]["id"]

    delete_response = client.post(
        f"/api/portfolio/ledger/{trade_id}/delete", headers=BROWSER_HEADERS
    )
    assert delete_response.headers["cache-control"] == "no-store"

    # GET 之后才落旧记录：两条都还是 pending，adopt / dismiss 都能直接走
    adopt_pid = _seed_position(tmp_path, code="600000")
    dismiss_pid = _seed_position(tmp_path, code="600036")
    adopt_response = client.post(
        f"/api/portfolio/legacy/{adopt_pid}/adopt", headers=BROWSER_HEADERS
    )
    assert adopt_response.status_code == 200, adopt_response.text
    assert adopt_response.headers["cache-control"] == "no-store"

    dismiss_response = client.post(
        f"/api/portfolio/legacy/{dismiss_pid}/dismiss", headers=BROWSER_HEADERS
    )
    assert dismiss_response.status_code == 200, dismiss_response.text
    assert dismiss_response.headers["cache-control"] == "no-store"


# --------------------------------------------------------------------------- #
# 旧仓迁移闭环
# --------------------------------------------------------------------------- #


def test_get_auto_migrates_pending_legacy_with_a_backup(tmp_path: Path) -> None:
    pid = _seed_position(tmp_path)

    body = _client(tmp_path).get("/api/portfolio").json()

    assert body["migration"]["error"] is None
    assert body["migration"]["migrated"] == [
        {"position_id": pid, "trade_id": 1, "symbol": "600000"}
    ]
    assert "bak-portfolio-" in body["migration"]["backup"]
    assert (tmp_path / body["migration"]["backup"]).exists()
    assert body["migration_pending"] == 0
    assert [row["state"] for row in body["legacy"]] == ["stale"]
    assert body["legacy"][0]["migrated_trade"] == 1
    assert body["ledger"][0]["migrated_from"] == pid
    assert body["ledger"][0]["type"] == "opening"
    assert body["ledger"][0]["shares"] == 200
    assert body["ledger"][0]["price"] == 8.0
    assert body["items"][0]["symbol"] == "600000"
    assert body["items"][0]["shares"] == 200

    # 旧行原样保留（回滚只需删掉 `migrated_from` 对应的流水）
    assert _position_count(tmp_path) == 1

    # 第二次 GET 不再迁移、不再备份
    again = _client(tmp_path).get("/api/portfolio").json()
    assert again["migration"]["backup"] is None
    assert again["migration"]["migrated"] == []
    assert len(again["ledger"]) == 1


def test_undated_legacy_is_flagged_in_ledger(tmp_path: Path) -> None:
    _seed_position(tmp_path, entry_date=None)

    body = _client(tmp_path).get("/api/portfolio").json()

    trade = body["ledger"][0]
    assert trade["undated"] is True
    assert trade["date"] == date.today().isoformat()
    assert "原记录未写建仓日，按迁移日暂记" in trade["note"]


def test_skipped_legacy_is_reported_not_migrated(tmp_path: Path) -> None:
    _seed_position(tmp_path, qty=0)

    body = _client(tmp_path).get("/api/portfolio").json()

    assert body["migration"]["migrated"] == []
    assert body["ledger"] == []
    assert body["legacy"][0]["state"] == "skipped"
    assert "数量" in body["legacy"][0]["why"]


def test_conflict_adopt_replaces_in_place(tmp_path: Path) -> None:
    pid = _seed_position(tmp_path)
    client = _client(tmp_path)
    first = client.get("/api/portfolio").json()
    trade_id = first["ledger"][0]["id"]

    conn = sqlite_store.connect(root=tmp_path, create=True)
    assert conn is not None
    try:
        conn.execute("UPDATE positions SET qty = 150, cost = 12 WHERE id = ?", (pid,))
    finally:
        sqlite_store.close(conn)

    conflicted = client.get("/api/portfolio").json()
    assert conflicted["legacy"][0]["state"] == "conflict"
    assert conflicted["migration_pending"] == 0
    assert len(conflicted["ledger"]) == 1

    adopted = client.post(
        f"/api/portfolio/legacy/{pid}/adopt", headers=BROWSER_HEADERS
    )
    assert adopted.status_code == 200, adopted.text
    assert adopted.json()["migration"]["migrated"][0]["trade_id"] == trade_id

    after = client.get("/api/portfolio").json()
    assert len(after["ledger"]) == 1, "adopt 是原地替换，不能多出第二笔"
    assert after["ledger"][0]["id"] == trade_id
    assert after["ledger"][0]["shares"] == 150
    assert after["ledger"][0]["price"] == 12.0
    assert after["legacy"][0]["state"] == "stale"


def test_dismiss_marks_only_and_blocks_auto_migration(tmp_path: Path) -> None:
    pid = _seed_position(tmp_path)
    client = _client(tmp_path)

    response = client.post(
        f"/api/portfolio/legacy/{pid}/dismiss", headers=BROWSER_HEADERS
    )
    assert response.status_code == 200, response.text
    assert response.json()["mark"]["position_id"] == pid
    assert _position_count(tmp_path) == 1, "dismiss 不删旧行"

    body = client.get("/api/portfolio").json()
    assert body["ledger"] == []
    assert body["migration"]["migrated"] == []
    assert body["migration_pending"] == 0
    assert body["legacy"][0]["state"] == "dismissed"

    # 已迁移（stale）的记录不能再标成不要
    stale_client = _client(tmp_path / "stale")
    stale_pid = _seed_position(tmp_path / "stale")
    stale_client.get("/api/portfolio")
    stale_dismiss = stale_client.post(
        f"/api/portfolio/legacy/{stale_pid}/dismiss", headers=BROWSER_HEADERS
    )
    assert stale_dismiss.status_code == 400
    assert "已经转成期初流水" in stale_dismiss.json()["detail"]


def test_legacy_writes_report_missing_records(tmp_path: Path) -> None:
    client = _client(tmp_path)
    adopt = client.post("/api/portfolio/legacy/99/adopt", headers=BROWSER_HEADERS)
    assert adopt.status_code == 404
    dismiss = client.post("/api/portfolio/legacy/99/dismiss", headers=BROWSER_HEADERS)
    assert dismiss.status_code == 404
