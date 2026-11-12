"""QUOT-561 / DEC-083：交易日志写入口契约（`POST /api/journal/trades` + `GET /api/journal/write-info`）。

写面是本机隐私数据，验收钉四组：

1. **闸门**：非回环 / 坏 `Host` / 缺来源证据 / 跨站 `Sec-Fetch-Site` 一律 403；
   同源 `Origin` 与 `X-LDST-Client: 1` 二选一放行；
2. **白名单**：多一个键就 400（券商账号这类字段没有入口）、缺必填 400、
   非数值 / 非正数 400、日期形状不对 400、备注超长 400；
3. **语义**：写入落在本机 `journal.sqlite`，与 `journal.log.add_trade` 同一个库；
   写完之后 `GET /api/journal/stats` 立刻能读到（同源同值，不重算）；
4. **出口**：写响应是平面 JSON（非 `LadderEnvelope`）、只回这一笔、`no-store`。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.journal import log as journal_log
from ladderstock.web.journal_write import JOURNAL_DENIED, JOURNAL_WRITE_DENIED
from ladderstock.web.server import create_app

LOOPBACK = ("127.0.0.1", 54321)
REMOTE = ("10.1.2.3", 54321)
SAME_ORIGIN = "http://127.0.0.1:5950"
BROWSER_HEADERS = {"origin": SAME_ORIGIN}
SCRIPT_HEADERS = {"x-ldst-client": "1"}
DATE = "2026-10-22"
ENVELOPE_KEYS = {"domain", "topic", "payload", "source", "fetched_at", "freshness"}
EXPECTED_TRADE_KEYS = {
    "ok",
    "local_only",
    "store",
    "id",
    "trade_date",
    "code",
    "side",
    "price",
    "qty",
    "plan_price",
    "note",
    "created_at",
}


def _client(root: Path, *, client: tuple[str, int] = LOOPBACK) -> TestClient:
    app = create_app(data_root=root, dist_dir=root / "no-dist")
    return TestClient(app, client=client)


def _trade(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "code": "600519",
        "side": "buy",
        "price": 1500.0,
        "qty": 100,
        "trade_date": DATE,
        "note": "首笔买入",
        "plan_price": 1490.0,
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------- 闸门


def test_write_requires_loopback(tmp_path: Path) -> None:
    response = _client(tmp_path, client=REMOTE).post(
        "/api/journal/trades", json=_trade(), headers=BROWSER_HEADERS
    )
    assert response.status_code == 403
    assert response.json()["detail"] == JOURNAL_DENIED
    assert not (tmp_path / "journal.sqlite").exists()


def test_write_requires_source_evidence(tmp_path: Path) -> None:
    response = _client(tmp_path).post("/api/journal/trades", json=_trade())
    assert response.status_code == 403
    assert response.json()["detail"] == JOURNAL_WRITE_DENIED


def test_write_rejects_cross_site_fetch_metadata(tmp_path: Path) -> None:
    response = _client(tmp_path).post(
        "/api/journal/trades",
        json=_trade(),
        headers={**BROWSER_HEADERS, "sec-fetch-site": "cross-site"},
    )
    assert response.status_code == 403


@pytest.mark.parametrize("headers", [BROWSER_HEADERS, SCRIPT_HEADERS])
def test_write_accepts_same_origin_or_client_header(
    tmp_path: Path, headers: dict[str, str]
) -> None:
    response = _client(tmp_path).post("/api/journal/trades", json=_trade(), headers=headers)
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_write_rejects_bad_host(tmp_path: Path) -> None:
    response = _client(tmp_path).post(
        "/api/journal/trades",
        json=_trade(),
        headers={**BROWSER_HEADERS, "host": "evil.example.com"},
    )
    assert response.status_code == 403


# ---------------------------------------------------------------- 白名单与校验


def test_write_rejects_extra_fields(tmp_path: Path) -> None:
    response = _client(tmp_path).post(
        "/api/journal/trades",
        json=_trade(account="888888", password="hunter2"),
        headers=BROWSER_HEADERS,
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "account" in detail and "password" in detail
    assert not (tmp_path / "journal.sqlite").exists()


@pytest.mark.parametrize("missing", ["code", "side", "price", "qty"])
def test_write_requires_required_fields(tmp_path: Path, missing: str) -> None:
    body = _trade()
    body.pop(missing)
    response = _client(tmp_path).post("/api/journal/trades", json=body, headers=BROWSER_HEADERS)
    assert response.status_code == 400
    assert missing in response.json()["detail"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"price": "1500"},  # 数值字段不偷转字符串
        {"qty": True},
        {"price": 0},
        {"qty": -100},
        {"side": "hold"},
        {"code": "   "},
        {"trade_date": "2026/10/22"},
        {"plan_price": 0},
        {"note": 42},
        {"note": "x" * 201},
    ],
)
def test_write_rejects_invalid_values(tmp_path: Path, overrides: dict[str, Any]) -> None:
    response = _client(tmp_path).post(
        "/api/journal/trades", json=_trade(**overrides), headers=BROWSER_HEADERS
    )
    assert response.status_code == 400


def test_write_rejects_non_object_body(tmp_path: Path) -> None:
    response = _client(tmp_path).post(
        "/api/journal/trades", json=["600519"], headers=BROWSER_HEADERS
    )
    assert response.status_code == 400


# ---------------------------------------------------------------- 语义与出口


def test_write_persists_and_stats_see_it(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.post("/api/journal/trades", json=_trade(), headers=BROWSER_HEADERS)
    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == EXPECTED_TRADE_KEYS
    assert not (set(payload) & ENVELOPE_KEYS)
    assert payload["local_only"] is True
    assert payload["store"] == "local_sqlite"
    assert payload["trade_date"] == DATE
    assert payload["side"] == "buy"
    assert response.headers["cache-control"] == "no-store"

    # 同一张本机库：直接读库 + 走聚合读面都能看到这一笔
    rows = journal_log.list_trades(root=tmp_path)
    assert [row["id"] for row in rows] == [payload["id"]]
    stats = client.get("/api/journal/stats")
    assert stats.status_code == 200
    body = stats.json()
    assert body["total_trades"] == 1
    assert body["buy_count"] == 1
    # 自由文本备注不回聚合读面（DEC-015 出口口径不变）
    assert json.dumps(body, ensure_ascii=False).count("首笔买入") == 0


def test_trades_has_no_read_method(tmp_path: Path) -> None:
    """`/api/journal/trades` 只有 POST：取全部成交不在 HTTP 面（同 DEC-015 Tier 2）。"""
    client = _client(tmp_path)
    for method in ("get", "put", "patch", "delete"):
        # SPA 兜底把未注册的 GET 收成 404；其它动词走 405——两种都不是「读到了成交」
        assert getattr(client, method)("/api/journal/trades").status_code in {404, 405}


def test_write_info_is_read_only_probe(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.get("/api/journal/write-info")
    assert response.status_code == 200
    payload = response.json()
    assert payload["writable"] is True
    assert payload["sides"] == ["buy", "sell"]
    assert payload["write_path"].endswith("journal.sqlite")
    assert not (tmp_path / "journal.sqlite").exists()
    assert response.headers["cache-control"] == "no-store"


def test_write_info_requires_loopback(tmp_path: Path) -> None:
    response = _client(tmp_path, client=REMOTE).get("/api/journal/write-info")
    assert response.status_code == 403
