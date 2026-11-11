"""首页指数条 `GET /api/indices` 的契约测试（QUOT-380 / QUOT-471 / QUOT-560）。

钉住的口径来自 `docs/specs/HOME-V2-DATA-SURFACE.md` A-1 与 `core/indices.py` 的实测坑：

| 场景 | 期望 |
|---|---|
| 八格 | 200 + 顺序/名称写死（A 股 4 + 美股 2 + 港股 2） |
| 八格齐 | 顺序/名称写死 + 数值照抄 peer（不换算、不补 0），整面 `ok` |
| peer 少回一行 | 那一格 `available=false` + 原因，其余照出，整面 `partial` |
| `sh000001` 被解析成个股 | **不认**：按 `(market, code)` 匹配，那一格 `available=false` |
| A 股 peer 不可用 | 200 + 四格 `available=false`（不是 500、不自采、不猜价），美股/港股照出 |
| 美股 peer 不可用 | 美股 + 港股四格 `available=false` + 原因（同一 peer 工具），A 股照出 |
| 港股单格失败 | 那一格 `available=false` + 原因，不连坐 A 股 / 美股（块间隔离） |

A 股行数据用 `tests/fixtures/peer_responses/index_quote.json`——2026-11-03 从真实 peer
`index_quote` 抓的原始返回；美股 / 港股点位是 2026-11-06 / 2026-11-11 对真实 peer
`th_us_get_quote` 的实测值。全程离线：`FakeTransport` 从不去拨。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fixtures.fake_peers import (
    SOURCES_DOC,
    SYNC_DOC,
    FakeTransport,
    make_registry,
    tickhub_payload,
    us_quote_payload,
)

from ladderstock.data.provider_registry import SourceUnavailable
from ladderstock.web.radar import RadarPlane
from ladderstock.web.server import create_app

LOOPBACK = ("127.0.0.1", 54321)
REMOTE = ("10.1.2.3", 54321)

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "peer_responses" / "index_quote.json"

#: A 股四格的固定顺序与写死的名字（后端白名单，不向 peer 要清单）
EXPECTED_CN = [
    ("sh000001", "SH", "上证指数"),
    ("sh000300", "SH", "沪深300"),
    ("sz399001", "SZ", "深证成指"),
    ("sz399006", "SZ", "创业板指"),
]

#: 美股两格（QUOT-471）：逐 symbol 打 `th_us_get_quote`
EXPECTED_US = [
    ("DJI", "US", "道琼斯"),
    ("IXIC", "US", "纳斯达克综合"),
]

#: 港股两格（QUOT-560）：同一个 peer 工具，入参是规范形 `HSI.HK` / `HSTECH.HK`
EXPECTED_HK = [
    ("HSI", "HK", "恒生指数"),
    ("HSTECH", "HK", "恒生科技"),
]

EXPECTED = EXPECTED_CN + EXPECTED_US + EXPECTED_HK

#: 美股两格的实测点位（2026-11-06 对真实 peer 抓的两条）
US_PRICES = {"DJI": 51570.0898, "IXIC": 24707.8101}

#: 港股两格的实测四件套（2026-11-11 对真实 peer `th_us_get_quote` 抓的两条，
#: tencent 源；行情时间是交易所当地时间，不带时区）
HK_QUOTES: dict[str, dict[str, Any]] = {
    "HSI.HK": {
        "name": "恒生指数", "price": 24315.92, "prev_close": 24557.04,
        "change": -241.12, "change_pct": -0.98, "timestamp": "2026-11-11 18:31:03",
    },
    "HSTECH.HK": {
        "name": "恒生科技指数", "price": 4027.06, "prev_close": 4084.45,
        "change": -57.39, "change_pct": -1.41, "timestamp": "2026-11-11 16:08:28",
    },
}


def index_rows() -> list[dict[str, Any]]:
    """真实抓包里的四行（原样，一个字段都不改）。"""
    return json.loads(FIXTURE.read_text(encoding="utf-8"))["data"]


def _sources_doc() -> dict[str, Any]:
    sources = copy.deepcopy(SOURCES_DOC)
    sources["sources"].extend(
        [
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
            },
            {
                "id": "tickhub_us_index_quote",
                "dataset": "index_quote_us",
                "via": "tickhub_us",
                "tool": "th_us_get_quote",
                "primary": True,
                "fallback": [],
                "timeout_sec": 1,
                "retries": 0,
                "cooldown_sec": 60,
            },
            {
                "id": "tickhub_hk_index_quote",
                "dataset": "index_quote_hk",
                "via": "tickhub_us",
                "tool": "th_us_get_quote",
                "primary": True,
                "fallback": [],
                "timeout_sec": 1,
                "retries": 0,
                "cooldown_sec": 60,
            },
        ]
    )
    return sources


def _quote_for(symbol: str) -> dict[str, Any]:
    """按 symbol 出实测回包：港股带 market=HK / HKD / 名称，美股保持 QUOT-471 形状。"""
    if symbol in HK_QUOTES:
        q = HK_QUOTES[symbol]
        return us_quote_payload(
            symbol,
            price=q["price"], prev_close=q["prev_close"],
            change=q["change"], change_pct=q["change_pct"],
            source="tencent", market="HK", currency="HKD",
            name=q["name"], timestamp=q["timestamp"],
            warnings=["腾讯行情 属 Tier C：商用需事先书面许可，当前为非商用调用。"],
        )
    price = US_PRICES.get(symbol, 100.0)
    return us_quote_payload(symbol, price=price, prev_close=price - 10.0, change=10.0,
                            change_pct=0.1)


def _us_handler(overrides: dict[str, dict[str, Any]] | None = None):
    """`th_us_get_quote` 桩：按 `symbol` 分派，默认四格都出实测点位。"""

    def handler(params: dict[str, Any]) -> dict[str, Any]:
        symbol = str(params.get("symbol") or "")
        if overrides and symbol in overrides:
            return overrides[symbol]
        return _quote_for(symbol)

    return handler


def _client(
    tmp_path: Path,
    *,
    rows: list[dict[str, Any]] | None = None,
    failing: bool = False,
    us_failing: bool = False,
    us_overrides: dict[str, dict[str, Any]] | None = None,
    client: tuple[str, int] = LOOPBACK,
) -> tuple[TestClient, FakeTransport]:
    handlers: dict[str, Any] = {
        "index_quote": lambda params: tickhub_payload(
            index_rows() if rows is None else rows,
            source="tencent",
            fetched_at="2026-11-03T22:47:19+08:00",
        ),
        "th_us_get_quote": _us_handler(us_overrides),
    }
    errors: dict[str, Exception] = {}
    if failing:
        handlers.pop("index_quote")
        errors["index_quote"] = SourceUnavailable("tickhub index_quote 连不上")
    if us_failing:
        handlers.pop("th_us_get_quote")
        errors["th_us_get_quote"] = SourceUnavailable("tickhub_us th_us_get_quote 连不上")
    transport = FakeTransport(
        handlers=handlers,
        errors=errors,
        tools=["index_quote", "th_us_get_quote"],
    )
    registry = make_registry(
        transport, tmp_path, sources=_sources_doc(), sync=copy.deepcopy(SYNC_DOC)
    )
    plane = RadarPlane(data_root=tmp_path, registry=registry)
    app = create_app(
        data_root=tmp_path, dist_dir=tmp_path / "no-dist", radar_plane=plane
    )
    return TestClient(app, client=client), transport


def _cells(body: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {cell["code"]: cell for cell in body["payload"]["cells"]}


# --------------------------------------------------------------------------- #
# 成功路径
# --------------------------------------------------------------------------- #

def test_indices_return_eight_cells_in_whitelist_order(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    response = client.get("/api/indices")
    assert response.status_code == 200
    payload = response.json()["payload"]
    assert [(c["code"], c["market"], c["name"]) for c in payload["cells"]] == EXPECTED


def test_indices_all_eight_cells_are_available(tmp_path: Path) -> None:
    """QUOT-560 后八格都有源：全填满时整面 `ok`（此前港股恒缺口，`ok` 不可达）。"""
    client, _ = _client(tmp_path)
    body = client.get("/api/indices").json()
    cells = _cells(body)
    for code, _, _ in EXPECTED:
        assert cells[code]["available"] is True, code
        assert cells[code]["price"] is not None, code
    assert body["payload"]["data_source_status"] == "ok"
    assert body["payload"]["available"] is True


def test_indices_project_peer_values_without_conversion(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    body = client.get("/api/indices").json()
    cells = {c["code"]: c for c in body["payload"]["cells"]}
    for (code, _, _), row in zip(EXPECTED_CN, index_rows(), strict=True):
        cell = cells[code]
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
    # data_as_of 取已填格里最新的行情日期：A 股抓包是 11-03，港股实测是 11-11 → 取 11-11
    assert body["payload"]["data_as_of"] == "2026-11-11"


def test_indices_us_cells_carry_price_and_source(tmp_path: Path) -> None:
    """美股两格：`price` 非空，且 `source_detail` 带出上游 + 合规分级（QUOT-471 验收）。"""
    client, _ = _client(tmp_path)
    body = client.get("/api/indices").json()
    cells = _cells(body)
    assert cells["DJI"]["price"] == US_PRICES["DJI"]
    assert cells["IXIC"]["price"] == US_PRICES["IXIC"]
    # 合规字段落进 source_detail（不吞不降级）
    detail = body["source_detail"]
    assert "th_us_get_quote" in detail
    assert "compliance_tier=C" in detail


def test_indices_hk_cells_carry_full_quote_and_compliance(tmp_path: Path) -> None:
    """港股两格（QUOT-560 验收）：四件套齐备、照抄 peer，合规分级落进 source_detail。"""
    client, _ = _client(tmp_path)
    body = client.get("/api/indices").json()
    cells = _cells(body)
    for code, _, _ in EXPECTED_HK:
        cell = cells[code]
        q = HK_QUOTES[f"{code}.HK"]
        assert cell["available"] is True
        assert cell["price"] == q["price"]
        assert cell["change"] == q["change"]
        assert cell["change_pct"] == q["change_pct"]
        assert cell["prev_close"] == q["prev_close"]
        # 交易所当地时间 `YYYY-MM-DD HH:MM:SS` → 同一时刻的 ISO 写法，不补时区
        assert cell["as_of"] == q["timestamp"].replace(" ", "T")
    assert "compliance_tier=C" in body["source_detail"]


def test_indices_footnote_says_snapshot(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    payload = client.get("/api/indices").json()["payload"]
    assert payload["footnote"] == "快照，不是逐笔实时"
    assert payload["price_source"] == "index_quote"
    assert payload["datasets"] == ["index_quote", "index_quote_us", "index_quote_hk"]


def test_indices_call_index_quote_and_us_quote_with_expected_params(tmp_path: Path) -> None:
    """A 股一次批量 `index_quote`；美股 + 港股逐 symbol 共四次 `th_us_get_quote`。

    四次调用各带 `commercial_use=false`（非商用前提）；港股入参是规范形 `.HK` 写法。
    """
    client, transport = _client(tmp_path)
    client.get("/api/indices")
    assert transport.tools_called == [
        "index_quote", "th_us_get_quote", "th_us_get_quote",
        "th_us_get_quote", "th_us_get_quote",
    ]
    assert transport.calls[0].params == {"codes": "sh000001,sh000300,sz399001,sz399006"}
    symbols = [call.params["symbol"] for call in transport.calls[1:]]
    assert symbols == ["DJI", "IXIC", "HSI.HK", "HSTECH.HK"]
    assert all(call.params["commercial_use"] is False for call in transport.calls[1:])


def test_indices_us_request_budget_is_four_per_fetch(tmp_path: Path) -> None:
    """首页一次拉取对 `tickhub_us` 的请求预算 = 4（美股 2 + 港股 2），不按格扇出更多。"""
    client, transport = _client(tmp_path)
    client.get("/api/indices")
    assert [c.tool for c in transport.calls].count("th_us_get_quote") == 4


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
    cells = _cells(client.get("/api/indices").json())
    assert cells["sh000001"]["available"] is False
    assert cells["sh000001"]["price"] is None
    assert cells["sz399001"]["available"] is False


def test_row_without_index_flag_is_rejected(tmp_path: Path) -> None:
    """`is_index` 全假 → A 股四格都取不到（美股不受影响，块间独立）。"""
    rows = [dict(row, is_index=False) for row in index_rows()]
    client, _ = _client(tmp_path, rows=rows)
    cells = _cells(client.get("/api/indices").json())
    assert all(cells[c]["available"] is False for c, _, _ in EXPECTED_CN)
    assert cells["DJI"]["available"] is True


def test_missing_row_is_unavailable_not_zero(tmp_path: Path) -> None:
    """四回三（真实 peer 就会静默丢代码）：缺的那格是 `null` + 原因，不补 0。"""
    client, _ = _client(tmp_path, rows=index_rows()[:3])
    body = client.get("/api/indices").json()
    cells = _cells(body)
    assert cells["sz399006"]["available"] is False
    assert cells["sz399006"]["price"] is None
    assert cells["sz399006"]["change_pct"] is None
    assert "sz399006" in cells["sz399006"]["reason"]
    assert cells["sh000001"]["available"] is True


def test_peer_down_is_200_missing_not_500(tmp_path: Path) -> None:
    """A 股 peer 不可用：该块四格 `available=false` + 原因，美股/港股照出（不连坐）。"""
    client, _ = _client(tmp_path, failing=True)
    response = client.get("/api/indices")
    assert response.status_code == 200
    cells = _cells(response.json())
    for code, _, _ in EXPECTED_CN:
        assert cells[code]["available"] is False
        assert cells[code]["price"] is None
        assert cells[code]["as_of"] is None
        assert "取数失败" in cells[code]["reason"]
    # 美股 / 港股块独立：A 股挂了，海外四格照常
    for code, _, _ in EXPECTED_US + EXPECTED_HK:
        assert cells[code]["available"] is True


def test_us_peer_down_only_us_and_hk_cells_missing(tmp_path: Path) -> None:
    """US peer 不可用：美股 + 港股四格 `available=false` + 各自原因，A 股照出。

    两块走同一个 peer 工具，peer 挂了自然一起缺——但原因按数据集分开写，
    不互相顶替，也不连坐 A 股块。
    """
    client, _ = _client(tmp_path, us_failing=True)
    response = client.get("/api/indices")
    assert response.status_code == 200
    cells = _cells(response.json())
    for code, _, _ in EXPECTED_US:
        assert cells[code]["available"] is False
        assert cells[code]["price"] is None
        assert "index_quote_us 取数失败" in cells[code]["reason"]
    for code, _, _ in EXPECTED_HK:
        assert cells[code]["available"] is False
        assert cells[code]["price"] is None
        assert "index_quote_hk 取数失败" in cells[code]["reason"]
    assert cells["sh000001"]["available"] is True


def test_hk_single_cell_failure_does_not_touch_other_blocks(tmp_path: Path) -> None:
    """港股单格失败（QUOT-560 验收）：HSI 报错只影响港股块，A 股 / 美股照常。

    注意治理口径：`tickhub_hk_index_quote` 是格间共享的源，HSI 失败后该源进入
    冷却，同一轮的 HSTECH 会被冷却跳过（与美股块同一套 `ProviderRegistry` 治理，
    不是本次新增的行为）——两格都如实 `available=false` + 原因，不补 0、不 500。
    """
    client, _ = _client(
        tmp_path,
        us_overrides={
            "HSI.HK": {"error": {"code": "E_NO_DATA", "message": "腾讯无 HSI.HK 行情"}},
        },
    )
    response = client.get("/api/indices")
    assert response.status_code == 200
    body = response.json()
    cells = _cells(body)
    assert cells["HSI"]["available"] is False
    assert cells["HSI"]["price"] is None
    assert "index_quote_hk 取数失败（HSI.HK）" in cells["HSI"]["reason"]
    assert cells["HSTECH"]["available"] is False
    assert cells["HSTECH"]["price"] is None
    # 块间隔离：A 股四格 + 美股两格不受港股单格失败影响
    for code, _, _ in EXPECTED_CN + EXPECTED_US:
        assert cells[code]["available"] is True, code
    assert body["payload"]["data_source_status"] == "partial"


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
    """八格由后端写死：不接 `codes` / `date` 之类的入参（防止变成通用取数口子）。"""
    client, transport = _client(tmp_path)
    response = client.get("/api/indices", params={"codes": "sh000001"})
    assert response.status_code == 200
    assert transport.calls[0].params["codes"] == "sh000001,sh000300,sz399001,sz399006"


@pytest.mark.parametrize("status", ["partial", "missing"])
def test_status_vocabulary_matches_radar_pages(tmp_path: Path, status: str) -> None:
    """部分缺格 → `partial`；A 股 + US peer 全挂 → 八格全空 → `missing`。"""
    rows = {"partial": index_rows()[:2], "missing": []}[status]
    us_failing = status == "missing"  # 全盘无数据：A 股 + 美股 + 港股都不可用
    client, _ = _client(tmp_path, rows=rows, us_failing=us_failing)
    assert client.get("/api/indices").json()["payload"]["data_source_status"] == status