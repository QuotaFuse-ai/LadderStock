"""首页指数条 `GET /api/indices` 的契约测试（QUOT-380）。

钉住的口径来自 `docs/specs/HOME-V2-DATA-SURFACE.md` A-1 与 `core/indices.py` 的三段实测坑：

| 场景 | 期望 |
|---|---|
| 四格齐 | 200 + 顺序/名称写死 + 数值照抄 peer（不换算、不补 0） |
| peer 少回一行 | 那一格 `available=false` + 原因，其余照出，整面 `partial` |
| peer 把 `sh000001` 解析成个股 | **不认**：按 `(market, code)` 匹配，那一格 `available=false` |
| peer 不可用 | 200 + `missing` + 四格都 `available=false`（不是 500、不自采、不猜价） |

行数据用 `tests/fixtures/peer_responses/index_quote.json`——2026-11-03 从真实 peer
`index_quote` 抓的原始返回，不是手写的形状。全程离线：`FakeTransport` 从不去拨。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fixtures.fake_peers import SOURCES_DOC, SYNC_DOC, FakeTransport, make_registry, tickhub_payload

from ladderstock.data.provider_registry import SourceUnavailable
from ladderstock.web.radar import RadarPlane
from ladderstock.web.server import create_app

LOOPBACK = ("127.0.0.1", 54321)
REMOTE = ("10.1.2.3", 54321)

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "peer_responses" / "index_quote.json"

#: 首页四格的固定顺序与写死的名字（后端白名单，不向 peer 要清单）
EXPECTED = [
    ("sh000001", "SH", "上证指数"),
    ("sh000300", "SH", "沪深300"),
    ("sz399001", "SZ", "深证成指"),
    ("sz399006", "SZ", "创业板指"),
]


def index_rows() -> list[dict[str, Any]]:
    """真实抓包里的四行（原样，一个字段都不改）。"""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["data"]


def _sources_doc() -> dict[str, Any]:
    sources = copy.deepcopy(SOURCES_DOC)
    sources["sources"].append(
        {
            "id": "tickhub_index_quote",
            "dataset": "index_quote",
            "via": "tickhub",
            "tool": "index_quote",
            "primary": True,
            "fallback": [],
            "timeout_sec": 1,
            "retries": 0,
            "cooldown_sec": 60,
        }
    )
    return sources


def _client(
    tmp_path: Path,
    *,
    rows: list[dict[str, Any]] | None = None,
    failing: bool = False,
    client: tuple[str, int] = LOOPBACK,
) -> tuple[TestClient, FakeTransport]:
    transport = FakeTransport(
        handlers=(
            {}
            if failing
            else {
                "index_quote": lambda params: tickhub_payload(
                    index_rows() if rows is None else rows,
                    source="tencent",
                    fetched_at="2026-11-03T22:47:19+08:00",
                )
            }
        ),
        errors={"index_quote": SourceUnavailable("tickhub index_quote 连不上")} if failing else {},
        tools=["index_quote"],
    )
    registry = make_registry(
        transport, tmp_path, sources=_sources_doc(), sync=copy.deepcopy(SYNC_DOC)
    )
    plane = RadarPlane(data_root=tmp_path, registry=registry)
    app = create_app(
        data_root=tmp_path, dist_dir=tmp_path / "no-dist", radar_plane=plane
    )
    return TestClient(app, client=client), transport


# --------------------------------------------------------------------------- #
# 成功路径
# --------------------------------------------------------------------------- #

def test_indices_return_four_cells_in_whitelist_order(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    response = client.get("/api/indices")
    assert response.status_code == 200
    payload = response.json()["payload"]
    assert [(cell["code"], cell["market"], cell["name"]) for cell in payload["cells"]] == EXPECTED
    assert payload["available"] is True
    assert all(cell["available"] for cell in payload["cells"])


def test_indices_project_peer_values_without_conversion(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    body = client.get("/api/indices").json()
    cells = body["payload"]["cells"]
    for cell, row in zip(cells, index_rows(), strict=True):
        # peer 回什么就用什么：点位 / 涨跌额 / 涨跌幅 / 昨收，一个都不二次换算
        assert cell["price"] == row["price"]
        assert cell["change"] == row["change"]
        assert cell["change_pct"] == row["change_pct"]
        assert cell["prev_close"] == row["prev_close"]
        # 行情时间是行内 `timestamp`（YYYYMMDDHHMMSS）→ 同一个时刻的 ISO 写法，不补时区
        stamp = row["timestamp"]
        assert cell["as_of"] == (
            f"{stamp[0:4]}-{stamp[4:6]}-{stamp[6:8]}T{stamp[8:10]}:{stamp[10:12]}:{stamp[12:14]}"
        )
    assert body["payload"]["data_as_of"] == "2026-11-03"


def test_indices_footnote_says_snapshot(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    payload = client.get("/api/indices").json()["payload"]
    assert payload["footnote"] == "快照，不是逐笔实时"
    assert payload["price_source"] == "index_quote"


def test_indices_call_only_index_quote_with_prefixed_codes(tmp_path: Path) -> None:
    """入参只允许 `sh`/`sz` 前缀形式，且**绝不**走 `tencent_quote_batch`。"""
    client, transport = _client(tmp_path)
    client.get("/api/indices")
    assert transport.tools_called == ["index_quote"]
    assert transport.calls[0].params == {
        "codes": "sh000001,sh000300,sz399001,sz399006"
    }


# --------------------------------------------------------------------------- #
# 三个实测坑
# --------------------------------------------------------------------------- #

def test_stock_row_with_same_code_is_not_taken_as_index(tmp_path: Path) -> None:
    """裸 `000001` / `000001.SH` 的静默回错票：`(market, code)` 不匹配就不认。

    这是实测到的最危险形态——peer 照旧 `ok: true`、照旧标 `is_index: true`，
    但那是**平安银行**（SZ/000001，11.77 元）。按 `code` 建字典的写法会把它当上证指数。
    """
    ping_an = {
        "code": "000001",
        "name": "平安银行",
        "price": 11.77,
        "prev_close": 11.54,
        "change": 0.23,
        "change_pct": 1.99,
        "timestamp": "20261103161500",
        "market": "SZ",
        "is_stale": False,
        "is_index": True,
    }
    client, _ = _client(tmp_path, rows=[ping_an])
    body = client.get("/api/indices").json()
    cells = {cell["code"]: cell for cell in body["payload"]["cells"]}
    assert cells["sh000001"]["available"] is False
    assert cells["sh000001"]["price"] is None
    assert cells["sz399001"]["available"] is False
    assert body["payload"]["data_source_status"] == "missing"


def test_row_without_index_flag_is_rejected(tmp_path: Path) -> None:
    rows = [dict(row, is_index=False) for row in index_rows()]
    client, _ = _client(tmp_path, rows=rows)
    body = client.get("/api/indices").json()
    assert body["payload"]["data_source_status"] == "missing"
    assert all(cell["available"] is False for cell in body["payload"]["cells"])


def test_missing_row_is_unavailable_not_zero(tmp_path: Path) -> None:
    """四回三（真实 peer 就会静默丢代码）：缺的那格是 `null` + 原因，不补 0。"""
    client, _ = _client(tmp_path, rows=index_rows()[:3])
    body = client.get("/api/indices").json()
    cells = {cell["code"]: cell for cell in body["payload"]["cells"]}
    assert body["payload"]["data_source_status"] == "partial"
    assert body["payload"]["available"] is True
    assert cells["sz399006"]["available"] is False
    assert cells["sz399006"]["price"] is None
    assert cells["sz399006"]["change_pct"] is None
    assert "sz399006" in cells["sz399006"]["reason"]
    assert cells["sh000001"]["available"] is True


def test_peer_down_is_200_missing_not_500(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, failing=True)
    response = client.get("/api/indices")
    assert response.status_code == 200
    body = response.json()
    assert body["payload"]["data_source_status"] == "missing"
    assert body["payload"]["data_as_of"] is None
    payload = body["payload"]
    assert payload["available"] is False
    assert "取数失败" in payload["reason"]
    for cell in payload["cells"]:
        assert cell["available"] is False
        assert cell["price"] is None
        assert cell["as_of"] is None


# --------------------------------------------------------------------------- #
# 门面与信封
# --------------------------------------------------------------------------- #

def test_indices_are_enveloped_public_data(tmp_path: Path) -> None:
    """公开行情面：套 `LadderEnvelope`，**不加回环独占**（同 `/api/pool`）。"""
    client, _ = _client(tmp_path, client=REMOTE)
    response = client.get("/api/indices")
    assert response.status_code == 200
    body = response.json()
    assert body["domain"] == "sentiment"
    assert body["topic"] == "index_quote"
    assert set(body) >= {"domain", "topic", "data_as_of", "payload", "fetched_at", "freshness"}


def test_indices_reject_any_query_parameters(tmp_path: Path) -> None:
    """四格由后端写死：不接 `codes` / `date` 之类的入参（防止变成通用取数口子）。"""
    client, transport = _client(tmp_path)
    response = client.get("/api/indices", params={"codes": "sh000001"})
    assert response.status_code == 200
    assert transport.calls[0].params["codes"] == "sh000001,sh000300,sz399001,sz399006"


@pytest.mark.parametrize("status", ["ok", "partial", "missing"])
def test_status_vocabulary_matches_radar_pages(tmp_path: Path, status: str) -> None:
    rows = {"ok": index_rows(), "partial": index_rows()[:2], "missing": []}[status]
    client, _ = _client(tmp_path, rows=rows)
    assert client.get("/api/indices").json()["payload"]["data_source_status"] == status
