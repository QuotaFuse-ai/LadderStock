"""QUOT-488：`GET /api/knowledge/{market}/{symbol}` 契约测试。

| 情况 | 期望 |
| --- | --- |
| 正常档案 | 200 + 正文 + `as_of` + `status` + `sources` |
| 档案不存在 | 404 |
| market 非法（`XX` / 越界） | 400 |
| symbol 目录穿越（`..%2F..%2Fetc`） | 400 |
| refuted 档案 | 200 + `status=refuted`，`recalled=null`（不进召回） |
| as_of 过期 | `status=stale`（`as_of + valid_days` 与当日比较） |
| 非回环来源 | 403 |

跑真 FastAPI + 真数据盘（tmp_path），不 mock 文件层。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ladderstock.web.knowledge import KnowledgePlane
from ladderstock.web.server import create_app

LOOPBACK = ("127.0.0.1", 54321)
REMOTE = ("10.1.2.3", 54321)
BASE_URL = "http://127.0.0.1:5950"

BODY = """# 标的档案：浦发银行

## 1. 业务与产业链位置
- 主营：对公银行

## 2. 关键数据(每条必带 报告期 / 来源 / 抓取日期 / 单位)
| 指标 | 值 | 单位 | 报告期 | 来源 | 抓取日期 | 有效期(天) |
|---|---|---|---|---|---|---|
| 归母净利润 | 123.4 | 亿元 | 2026Q3 | TickHub MCP | 2026-11-08 | 90 |
"""


def build_client(tmp_path: Path, *, client_addr: tuple[str, int] = LOOPBACK) -> TestClient:
    app = create_app(data_root=tmp_path)
    return TestClient(app, base_url=BASE_URL, client=client_addr)


def seed(
    tmp_path: Path, market: str, symbol: str, *, as_of: str, status: str, body: str = BODY
) -> None:
    from ladderstock.knowledge.model import Dossier
    from ladderstock.knowledge.store import write_profile

    dossier = Dossier(
        symbol=symbol, market=market, name="浦发银行", as_of=as_of, status=status, body=body
    )
    write_profile(tmp_path, dossier)


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    return build_client(tmp_path)


def test_returns_body_as_of_status_sources(client: TestClient, tmp_path: Path) -> None:
    seed(tmp_path, "SH", "600000", as_of="2026-11-08", status="fresh")
    response = client.get("/api/knowledge/SH/600000")
    assert response.status_code == 200
    data = response.json()
    assert data["market"] == "SH"
    assert data["symbol"] == "600000"
    assert data["as_of"] == "2026-11-08"
    assert data["status"] in {"fresh", "stale"}
    assert "归母净利润" in data["body"]
    assert data["sources"] == []
    assert data["valid_days"] == 90


def test_missing_profile_is_404(client: TestClient) -> None:
    assert client.get("/api/knowledge/SZ/000001").status_code == 404


@pytest.mark.parametrize("market", ["XX", "US", "HK", "sh2"])
def test_bad_market_is_400(client: TestClient, market: str) -> None:
    response = client.get(f"/api/knowledge/{market}/600000")
    assert response.status_code == 400, response.text
    assert "market" in response.json()["detail"]


@pytest.mark.parametrize("symbol", ["....", "60000", "6000000", "%2e%2e"])
def test_path_traversal_symbol_is_400(client: TestClient, symbol: str) -> None:
    # 不含斜杠的非法形态进参数层，被 normalize_symbol（只放行 6 位数字）拒成 400
    assert client.get(f"/api/knowledge/SH/{symbol}").status_code == 400


@pytest.mark.parametrize("path", ["SH/..%2F..%2Fetc", "SH/600000/extra", "SH%2F600000"])
def test_path_traversal_with_slash_never_reaches_handler(client: TestClient, path: str) -> None:
    # 带 `/`（或编码 `%2F`）的穿越匹配不进 `{market}/{symbol}` 单段路由 → 404，
    # 处理函数与文件系统都不会被触到——拒绝发生在路由层（客户端对 `/../` 的
    # 归一化发生在发出请求之前，这里只测服务端）
    response = client.get(f"/api/knowledge/{path}")
    assert response.status_code in (400, 404)


def test_expired_as_of_reports_stale(client: TestClient, tmp_path: Path) -> None:
    seed(tmp_path, "SH", "600000", as_of="2026-07-01", status="fresh")  # 130 天前 > 90 天
    data = client.get("/api/knowledge/SH/600000").json()
    assert data["status"] == "stale"


def test_refuted_profile_not_recalled(client: TestClient, tmp_path: Path) -> None:
    seed(tmp_path, "SH", "600000", as_of="2026-11-08", status="refuted")
    data = client.get("/api/knowledge/SH/600000").json()
    assert data["status"] == "refuted"
    assert data["recalled"] is None


def test_recall_missing_is_null_not_error(client: TestClient, tmp_path: Path) -> None:
    seed(tmp_path, "SH", "600000", as_of="2026-11-08", status="fresh")
    data = client.get("/api/knowledge/SH/600000").json()
    assert data["recalled"] is None  # profile 在、latest 未归档


def test_remote_source_is_403(tmp_path: Path) -> None:
    client = build_client(tmp_path, client_addr=REMOTE)
    assert client.get("/api/knowledge/SH/600000").status_code == 403


def test_plane_directly(tmp_path: Path) -> None:
    seed(tmp_path, "SZ", "000001", as_of="2026-11-08", status="fresh")
    plane = KnowledgePlane(data_root=tmp_path)
    payload = plane.get("SZ", "000001")
    assert payload["symbol"] == "000001"
