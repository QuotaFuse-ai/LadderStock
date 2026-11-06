"""QUOT-437：流水 CSV 导入的两个接口契约。

钉住 issue 验收里点名的几件事：

1. **预览不写库**：`preview` 之后账本行数不变、一条备份都不落；
2. **预览与写入同一口径**：预览判为可用的行，写入就成功；预览判为有问题的行，
   写入一定 422；
3. **重复 / 超限 / 非法行被拒且不写库**；任何一行不对整批不写；
4. **写入前备份存在**：导入成功后备份文件在磁盘上，名字回给页面；
5. **闸门**：非回环、写面缺来源证据一律 403。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.data import sqlite_store
from ladderstock.journal.portfolio import BACKUP_MARK, list_trades
from ladderstock.journal.portfolio_import import MAX_IMPORT_BYTES, MAX_IMPORT_ROWS
from ladderstock.web.radar import POSITIONS_DENIED, RadarPlane
from ladderstock.web.server import create_app

LOOPBACK = ("127.0.0.1", 54321)
REMOTE = ("10.1.2.3", 54321)
BROWSER_HEADERS = {"origin": "http://127.0.0.1:5950"}
SCRIPT_HEADERS = {"x-ldst-client": "1"}

PREVIEW = "/api/portfolio/ledger/import/preview"
IMPORT = "/api/portfolio/ledger/import"


def _buy(symbol: str = "600000", **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "symbol": symbol,
        "name": "浦发银行",
        "type": "buy",
        "trade_date": "2025-01-02",
        "shares": 100,
        "price": 10,
        "fee": 5,
    }
    body.update(overrides)
    return body


def _dividend(symbol: str, amount: float, trade_date: str) -> dict[str, Any]:
    return {"symbol": symbol, "type": "dividend", "trade_date": trade_date, "amount": amount}


def _client(root: Path, *, client: tuple[str, int] = LOOPBACK) -> TestClient:
    plane = RadarPlane(data_root=root)
    app = create_app(data_root=root, dist_dir=root / "no-dist", radar_plane=plane)
    return TestClient(app, client=client)


def _seed(client: TestClient, body: dict[str, Any]) -> None:
    response = client.post("/api/portfolio/ledger", json=body, headers=BROWSER_HEADERS)
    assert response.status_code == 200, response.text


def _backups(root: Path) -> list[str]:
    return sorted(p.name for p in root.rglob(f"*{BACKUP_MARK}*"))


# --------------------------------------------------------------------------- #
# 预览：只读
# --------------------------------------------------------------------------- #


def test_preview_reports_row_problems_without_touching_the_database(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    body = {
        "method": "average",
        "records": [
            _buy(),
            _buy(type="sell", trade_date="2025-01-06", shares=300),  # 卖超
            {"symbol": "60", "type": "buy", "trade_date": "2025-01-02", "shares": 1, "price": 1},
            _buy(symbol="000001", trade_date="not-a-day"),
        ],
    }

    response = client.post(PREVIEW, json=body, headers=BROWSER_HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True
    assert payload["count"] == 4
    assert payload["limit"] == MAX_IMPORT_ROWS
    assert payload["max_bytes"] == MAX_IMPORT_BYTES
    assert [row["index"] for row in payload["rows"]] == [0, 1, 2, 3]
    assert payload["rows"][0]["problem"] is None
    assert payload["rows"][0]["duplicate"] is None
    assert "卖" in payload["rows"][1]["problem"]
    assert "6 位" in payload["rows"][2]["problem"]
    assert "YYYY-MM-DD" in payload["rows"][3]["problem"]
    assert response.headers["cache-control"] == "no-store"
    assert list_trades(root=tmp_path) == [], "预览不能写库"
    assert _backups(tmp_path) == [], "预览连备份都不落"


def test_preview_marks_duplicates_against_the_ledger_and_the_batch(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    _seed(client, _buy())
    existing_id = list_trades(root=tmp_path)[0].id

    response = client.post(
        PREVIEW,
        json={"records": [_buy(), _buy(symbol="000001"), _buy(symbol="000001")]},
        headers=BROWSER_HEADERS,
    )

    rows = response.json()["rows"]
    assert rows[0]["duplicate"] == "existing"
    assert rows[0]["duplicate_of"] == existing_id
    assert rows[1]["duplicate"] is None
    assert rows[2]["duplicate"] == "table"
    assert rows[2]["duplicate_of"] == 1
    assert list_trades(root=tmp_path) and len(list_trades(root=tmp_path)) == 1


def test_preview_only_simulates_the_checked_rows(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _seed(client, _buy())

    response = client.post(
        PREVIEW,
        json={
            "records": [
                _buy(type="sell", trade_date="2025-01-05", shares=500),
                _buy(type="sell", trade_date="2025-01-06", shares=500),
            ],
            "selected": [False, True],
        },
        headers=BROWSER_HEADERS,
    )

    rows = response.json()["rows"]
    assert rows[0]["problem"] is None, "没勾的行不参与重放"
    assert "卖出" in rows[1]["problem"]
    assert "超过当时持有的 100 股" in rows[1]["problem"]


# --------------------------------------------------------------------------- #
# 写入：先备份、整批成功
# --------------------------------------------------------------------------- #


def test_import_writes_the_rows_preview_said_were_clean(tmp_path: Path) -> None:
    client = _client(tmp_path)
    records = [
        _buy(),
        _buy(symbol="000001", trade_date="2025-01-03", shares=200, price=5),
        _dividend("000002", 88.5, "2025-01-04"),
    ]
    preview = client.post(PREVIEW, json={"records": records}, headers=BROWSER_HEADERS).json()
    assert all(row["problem"] is None for row in preview["rows"])

    response = client.post(IMPORT, json={"records": records}, headers=BROWSER_HEADERS)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["ok"] is True
    assert payload["imported"] == 3
    assert payload["backup"].startswith(f"journal.sqlite.{BACKUP_MARK}")
    assert payload["at"]
    assert len(list_trades(root=tmp_path)) == 3
    assert (sqlite_store.db_path(tmp_path).parent / payload["backup"]).is_file(), (
        "写入前必须已经落一份备份"
    )
    assert response.headers["cache-control"] == "no-store"


def test_import_rejects_everything_when_one_row_is_bad(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _seed(client, _buy())
    records = [_buy(symbol="000001"), _buy(), _buy(symbol="000002")]

    response = client.post(IMPORT, json={"records": records}, headers=SCRIPT_HEADERS)

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert "没有写入" in detail
    assert "完全一样" in detail
    assert len(list_trades(root=tmp_path)) == 1, "重复行不能连带把整批别的行写进去"
    assert _backups(tmp_path) == [], "校验没过就不该备份"


def test_import_refuses_an_overlong_batch_without_writing(tmp_path: Path) -> None:
    client = _client(tmp_path)
    records = [
        _buy(symbol=f"60{index:04d}", trade_date=f"2025-01-{index % 28 + 1:02d}")
        for index in range(MAX_IMPORT_ROWS + 1)
    ]

    for endpoint in (PREVIEW, IMPORT):
        response = client.post(endpoint, json={"records": records}, headers=BROWSER_HEADERS)
        assert response.status_code == 422, response.text
        assert "分成几份" in response.json()["detail"]
    assert list_trades(root=tmp_path) == []


def test_import_refuses_an_overlong_body_without_writing(tmp_path: Path) -> None:
    client = _client(tmp_path)
    records = [
        _buy(note="备注" * 200)
        for _ in range(MAX_IMPORT_ROWS)
    ]

    response = client.post(IMPORT, json={"records": records}, headers=BROWSER_HEADERS)

    assert response.status_code == 422, response.text
    assert "分成几份" in response.json()["detail"]
    assert list_trades(root=tmp_path) == []


@pytest.mark.parametrize(
    ("body", "why"),
    [
        ({"records": []}, "非空数组"),
        ({"records": "not-a-list"}, "非空数组"),
        ({"records": [_buy()], "method": "lifo"}, "成本法"),
        ({"records": [_buy()], "unknown": 1}, "不认识的字段"),
        ({"records": [_buy()], "selected": [True, False]}, "不认识的字段"),
    ],
)
def test_malformed_requests_are_rejected(tmp_path: Path, body: dict, why: str) -> None:
    response = _client(tmp_path).post(IMPORT, json=body, headers=BROWSER_HEADERS)

    assert response.status_code == 422, response.text
    assert why in response.json()["detail"]
    assert list_trades(root=tmp_path) == []


# --------------------------------------------------------------------------- #
# 闸门
# --------------------------------------------------------------------------- #


def test_non_loopback_is_forbidden(tmp_path: Path) -> None:
    response = _client(tmp_path, client=REMOTE).post(
        IMPORT, json={"records": [_buy()]}, headers=BROWSER_HEADERS
    )

    assert response.status_code == 403
    assert response.json()["detail"] == POSITIONS_DENIED
    assert list_trades(root=tmp_path) == []


def test_write_without_source_evidence_is_forbidden(tmp_path: Path) -> None:
    for endpoint in (PREVIEW, IMPORT):
        response = _client(tmp_path).post(endpoint, json={"records": [_buy()]})

        assert response.status_code == 403, endpoint
    assert list_trades(root=tmp_path) == []
