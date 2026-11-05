"""`GET /api/watchlist` 只读面契约测试（QUOT-380 / DEC-052）。

钉住 DEC-052 §2.1 的三条闸门与 §2.2 的字段口径：

| 场景 | 期望 |
|---|---|
| 非回环来源 | **403**，且不泄露任何一条自选 |
| `Host` 不在白名单 | **403**，`detail` 带上收到的 Host |
| 本机没有自选 | **200** + `available=false` + 空清单（不是 404、不伪造行） |
| 任何响应 | `Cache-Control: no-store` + **平面 JSON**（无 `payload`/`domain`/`fetched_at`） |
| 上游取不到价 | `latest_price=null` + 原因，**不补 0、不拿成本价顶替**（DEC-028） |
| `note` | 这一版**不出**（拍板口径 2） |
| 远程 Token / 控制面开关 | 不影响这条路由（Token 管「谁连得进来」） |

全程离线：`FakeTransport` 从不去拨 peer。
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fixtures.fake_peers import SOURCES_DOC, SYNC_DOC, FakeTransport, make_registry, tickhub_payload

from ladderstock.data import sqlite_store
from ladderstock.web.radar import WATCHLIST_DENIED, RadarPlane
from ladderstock.web.server import create_app

LOOPBACK = ("127.0.0.1", 54321)
REMOTE = ("10.1.2.3", 54321)

SENTINEL = "隐私哨兵-WATCH-4c7a"
FIXTURE = (
    Path(__file__).resolve().parents[2] / "fixtures" / "peer_responses" / "tencent_quote_batch.json"
)

#: fixture 里真实存在的两只（SZ 裸 6 位）
PRICED = ("000530", "000701")
UNPRICED = "600519"


def _quote_data(codes: list[str]) -> dict[str, Any]:
    """按 `tencent_quote_batch` 的真实形状造 `{quotes: {code: row}}`（含 failed 计数）。"""
    captured = json.loads(FIXTURE.read_text(encoding="utf-8"))["data"]["quotes"]
    quotes = {}
    failures = []
    for code in codes:
        row = copy.deepcopy(captured.get(next(iter(captured))))
        row["code"] = code
        row["name"] = f"自选{code}"
        quotes[code] = row
    return {"total": len(codes), "succeeded": len(quotes), "failed": 0,
            "quotes": quotes, "failures": failures}


def _sources_doc() -> dict[str, Any]:
    sources = copy.deepcopy(SOURCES_DOC)
    sources["sources"].append(
        {
            "id": "tickhub_spot",
            "dataset": "spot_quote",
            "via": "tickhub",
            "tool": "tencent_quote_batch",
            "primary": True,
            "fallback": [],
            "timeout_sec": 1,
            "retries": 0,
            "cooldown_sec": 60,
        }
    )
    return sources


def _client(
    root: Path,
    *,
    client: tuple[str, int] = LOOPBACK,
    failing: bool = False,
) -> TestClient:
    def spot(params: dict[str, Any]) -> dict[str, Any]:
        asked = [c for c in str(params.get("codes", "")).split(",") if c]
        if failing:
            raise RuntimeError("QFSD-7001 rate limited")
        return tickhub_payload(_quote_data(asked), source="tencent")

    transport = FakeTransport(
        handlers={"tencent_quote_batch": spot}, tools=["tencent_quote_batch"]
    )
    registry = make_registry(
        transport, root, sources=_sources_doc(), sync=copy.deepcopy(SYNC_DOC)
    )
    plane = RadarPlane(data_root=root, registry=registry)
    app = create_app(data_root=root, dist_dir=root / "no-dist", radar_plane=plane)
    return TestClient(app, client=client)


def _seed_watchlist(root: Path, codes: tuple[str, ...], *, note: str = "") -> None:
    conn = sqlite_store.connect(root=root, create=True)
    assert conn is not None
    try:
        for index, code in enumerate(codes):
            conn.execute(
                "INSERT INTO watchlist (code, note, created_at) VALUES (?, ?, ?)",
                (code, note, f"2026-11-0{index + 1}T09:30:00+08:00"),
            )
    finally:
        sqlite_store.close(conn)


# --------------------------------------------------------------------- 闸门


def test_watchlist_is_loopback_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_watchlist(tmp_path, PRICED, note=SENTINEL)
    # Token 管「谁连得进来」，不管「隐私数据该不该出去」：开了远程也不能读自选
    monkeypatch.setenv("LDST_WEB_ALLOW_REMOTE", "1")
    monkeypatch.setenv("LDST_WEB_TOKEN", "0123456789abcdef0123")

    denied = _client(tmp_path, client=REMOTE).get("/api/watchlist")
    assert denied.status_code == 403
    assert denied.json()["detail"] == WATCHLIST_DENIED
    assert SENTINEL not in denied.text
    assert not any(code in denied.text for code in PRICED)

    assert _client(tmp_path).get("/api/watchlist").status_code == 200


def test_bad_host_header_is_403(tmp_path: Path) -> None:
    _seed_watchlist(tmp_path, PRICED)
    response = _client(tmp_path).get("/api/watchlist", headers={"host": "evil.example.com"})
    assert response.status_code == 403
    assert "Host" in response.json()["detail"]
    assert "evil.example.com" in response.json()["detail"]


def test_control_switch_does_not_close_the_read_face(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`LDST_CONTROL_ENABLED=0` 关的是控制面，不是本机数据面（DEC-033 §2.5 同一条）。"""
    _seed_watchlist(tmp_path, PRICED)
    monkeypatch.setenv("LDST_CONTROL_ENABLED", "0")
    assert _client(tmp_path).get("/api/watchlist").status_code == 200


def test_only_get_and_post_are_routed(tmp_path: Path) -> None:
    """GET 读、POST 写（DEC-055）；其它动词不提供（删除也不换 DELETE）。"""
    _seed_watchlist(tmp_path, PRICED)
    client = _client(tmp_path)
    # 写方法没有来源证据 → 403，不是 405（路由在，闸门先拦）
    assert client.post("/api/watchlist", json={"code": "600000"}).status_code == 403
    assert client.delete("/api/watchlist").status_code == 405
    assert client.put("/api/watchlist", json={"code": "600000"}).status_code == 405


# ------------------------------------------------------------------ 平面 JSON


def test_response_is_flat_json_no_store_and_has_no_envelope_keys(tmp_path: Path) -> None:
    _seed_watchlist(tmp_path, PRICED)
    response = _client(tmp_path).get("/api/watchlist")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"

    body = response.json()
    for envelope_key in ("payload", "domain", "fetched_at"):
        assert envelope_key not in body
    # 字段名不能叫 watchlist / 自选——那两个词在 `PRIVATE_KEYS` 里，出口会被静默剥掉
    assert "watchlist" not in body and "自选" not in body
    assert body["local_only"] is True and body["store"] == "local_sqlite"


def test_note_is_not_exposed(tmp_path: Path) -> None:
    """拍板口径 2：`note` 是自由文本，这一版根本不出（投影层就不读那一列）。"""
    _seed_watchlist(tmp_path, PRICED, note=SENTINEL)
    body = _client(tmp_path).get("/api/watchlist").json()

    assert SENTINEL not in json.dumps(body, ensure_ascii=False)
    assert all("note" not in item for item in body["items"])


# --------------------------------------------------------------------- 空态


def test_empty_watchlist_is_200_not_404(tmp_path: Path) -> None:
    """本机没自选 → 200 + `available=false` + 指向本机 CLI 的文案；且不建库。"""
    response = _client(tmp_path).get("/api/watchlist")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["available"] is False
    assert body["watch_count"] == 0
    assert body["items"] == []
    assert "我的自选" in body["reason"]
    assert (tmp_path / sqlite_store.DB_FILENAME).exists() is False


# ------------------------------------------------------------------ 现价投影


def test_prices_come_from_spot_quote_and_are_not_rescaled(tmp_path: Path) -> None:
    """上游报什么就回什么：价格 / 涨跌幅 / 昨收 / 时间戳逐字取原值，不换算、不改精度。

    期望值**从 fixture 本身读**，不钉死某一版行情数字——`tencent_quote_batch.json`
    会随真实整包换代（QUOT-290 已从拼装样本换成 2026-11-04 盘中 124 行真实整包），
    钉死数字的写法会让「换 fixture」必然打翻一条与口径无关的用例。
    """
    #: `_quote_data` 复制 fixture 里第一条报价、只改 `code` / `name`，期望值同源
    captured = json.loads(FIXTURE.read_text(encoding="utf-8"))["data"]["quotes"]
    template = captured[next(iter(captured))]
    stamp = str(template["timestamp"])  # YYYYMMDDHHMMSS
    hhmmss = f"{stamp[8:10]}:{stamp[10:12]}:{stamp[12:14]}"
    expected_as_of = f"{stamp[0:4]}-{stamp[4:6]}-{stamp[6:8]}T{hhmmss}"

    _seed_watchlist(tmp_path, PRICED)
    body = _client(tmp_path).get("/api/watchlist").json()

    assert body["price_source"] == "spot_quote"
    assert body["watch_count"] == 2
    assert [item["code"] for item in body["items"]] == list(PRICED)  # 按加入时间排序
    for item in body["items"]:
        assert item["available"] is True
        assert item["price_status"] == "ok"
        # fixture 原值，不换算
        assert item["latest_price"] == pytest.approx(float(template["price"]))
        assert item["change_pct"] == pytest.approx(float(template["change_pct"]))
        assert item["prev_close"] == pytest.approx(float(template["prev_close"]))
        assert item["price_as_of"] == expected_as_of
        assert item["added_at"]


def test_unavailable_price_is_null_with_a_reason_never_zero(tmp_path: Path) -> None:
    """上游一只都没给：`latest_price` 是 `null` + 原因，不补 0、不拿成本价顶替。"""
    _seed_watchlist(tmp_path, PRICED)
    body = _client(tmp_path, failing=True).get("/api/watchlist").json()

    assert body["data_source_status"] == "missing"
    for item in body["items"]:
        assert item["available"] is False
        assert item["latest_price"] is None
        assert item["change"] is None
        assert item["price_status"] == "unavailable"
        assert item["price_reason"]
    assert body["price_as_of"] is None


def test_missing_row_for_one_code_only_drops_that_line(tmp_path: Path) -> None:
    """上游少回一只：那一条 `null` + 原因，另一条照出，整面 `partial`。"""
    _seed_watchlist(tmp_path, (PRICED[0], UNPRICED))

    def spot(params: dict[str, Any]) -> dict[str, Any]:
        asked = [c for c in str(params.get("codes", "")).split(",") if c]
        return tickhub_payload(_quote_data([c for c in asked if c != UNPRICED]), source="tencent")

    transport = FakeTransport(handlers={"tencent_quote_batch": spot}, tools=["tencent_quote_batch"])
    registry = make_registry(
        transport, tmp_path, sources=_sources_doc(), sync=copy.deepcopy(SYNC_DOC)
    )
    plane = RadarPlane(data_root=tmp_path, registry=registry)
    app = create_app(data_root=tmp_path, dist_dir=tmp_path / "no-dist", radar_plane=plane)
    body = TestClient(app, client=LOOPBACK).get("/api/watchlist").json()

    by_code = {item["code"]: item for item in body["items"]}
    assert by_code[PRICED[0]]["available"] is True
    assert by_code[UNPRICED]["latest_price"] is None
    assert UNPRICED in by_code[UNPRICED]["price_reason"]
    assert body["data_source_status"] == "partial"


def test_write_surface_is_only_post(tmp_path: Path) -> None:
    """DEC-052 §2.3 经 QUOT-419 / DEC-055 修订：写面是一条 POST + 一条 POST delete。

    没有 `PUT` / `PATCH` / `DELETE` 动词——换动词会绕开「写方法必须有来源证据」这条防线。
    """
    spec = _client(tmp_path).app.openapi()  # type: ignore[attr-defined]
    collection = next(
        item for path, item in spec["paths"].items() if path.endswith("/api/watchlist")
    )
    assert set(collection) == {"get", "post"}
    delete = next(
        item for path, item in spec["paths"].items() if path.endswith("/api/watchlist/{code}/delete")
    )
    assert set(delete) == {"post"}


def test_private_keys_are_stripped_if_anything_leaks_into_an_envelope(tmp_path: Path) -> None:
    """反向确认：这条面不产信封；即便有人把它塞进信封，`strip_private` 也该剥掉。"""
    from ladderstock.core.privacy import private_keys_present, strip_private

    _seed_watchlist(tmp_path, PRICED)
    body = _client(tmp_path).get("/api/watchlist").json()
    assert private_keys_present(body) == []

    shaped = {"watchlist": body["items"], "自选": 1, "items": body["items"]}
    assert set(private_keys_present(shaped)) >= {"watchlist", "自选"}
    assert "watchlist" not in strip_private(shaped)
