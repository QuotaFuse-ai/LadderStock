"""QUOT-199 / Q172-8：`GET /api/market/history`（近 N 日情绪温度序列）的契约测试。

钉住前端 Q172-7 CH-01 依赖的五件事：

1. 空缓存 → 200 + `available=false`，`payload` 四个键一个不少，`quantile` /
   `delta_vs_prev` 是 `null` 而不是 0（单点画不出折线，前端走「数据未同步」空态）；
2. 有缓存 → 逐日成点、升序、只收 `compute_sentiment` 可用的日子，分位与较昨日
   **以最后一个可用日为准**，数字与 `compute_sentiment` 同源同值；
3. `days` 越界钳制到 1–60、非整数 400、`to` 非 `YYYY-MM-DD` 400；
4. 回环独占：非回环来源 403、`Host` 不在白名单同样 403；只 GET；
5. 出站结构过 `strip_private` 恒等，上游即使混进 `positions` / `journal` / `note`
   也进不了响应。

全程只读临时数据目录，不发网络请求、不触发 `sync`。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.core.privacy import private_keys_present, strip_private
from ladderstock.core.sentiment import compute_sentiment
from ladderstock.data import duckdb_store
from ladderstock.web import control
from ladderstock.web.server import create_app

LOOPBACK = ("127.0.0.1", 54321)
REMOTE = ("10.1.2.3", 54321)

#: 2026-10-19 是周一，连续五个工作日就是本组用例的「交易日」
SESSION_DAYS: tuple[date, ...] = (
    date(2026, 10, 19),
    date(2026, 10, 20),
    date(2026, 10, 21),
    date(2026, 10, 22),
    date(2026, 10, 23),
)
LATEST = SESSION_DAYS[-1]
WEEKEND = date(2026, 10, 24)  # 周六：没有分区，不该进窗口
SENTINEL = "隐私哨兵-QUOT199"

#: 契约形状（Q172-8 接口 1 + 前端 `MarketHistoryPayload`，DEC-031 §2.3）：
#: `data_source_status` 由 envelope 统一追加，一个键都不许多
EXPECTED_PAYLOAD_KEYS = {
    "date",
    "available",
    "reason",
    "window",
    "points",
    "missing_days",
    "quantile",
    "delta_vs_prev",
    "data_source_status",
}
EXPECTED_POINT_KEYS = {"date", "temperature", "promotion_rate", "limit_up_count"}


def _client(root: Path, client: tuple[str, int] = LOOPBACK) -> TestClient:
    return TestClient(create_app(data_root=root, dist_dir=root / "no-dist"), client=client)


def _seed_day(root: Path, day: date, codes: tuple[str, ...], boards: dict[str, int]) -> None:
    """写某交易日的涨停池 / 昨日涨停 / 炸板三张表（`core/sentiment` 的口径来源）。"""
    stamp = day.isoformat()
    duckdb_store.write_dataset(
        "limit_pool",
        [
            {
                "code": code,
                "name": f"股{code}",
                "trade_date": stamp,
                "consecutive_boards": boards.get(code, 1),
            }
            for code in codes
        ],
        root=root,
    )
    duckdb_store.write_dataset(
        "yesterday_limit_up",
        [{"code": code, "trade_date": stamp} for code in codes],
        root=root,
    )
    duckdb_store.write_dataset(
        "broken_board", [{"code": "300999", "trade_date": stamp}], root=root
    )


def _seed_sessions(root: Path) -> None:
    """五天里晋级家数与连板高度都在涨——温度逐日不同，分位才算得出来。"""
    for index, day in enumerate(SESSION_DAYS):
        promoted = ("600001", "600002", "600003")[: index + 1]
        codes = promoted + ("600100", "600101")
        _seed_day(root, day, codes, {code: index + 1 for code in promoted})


def _body(root: Path, params: dict[str, str] | None = None) -> dict[str, Any]:
    response = _client(root).get("/api/market/history", params=params or {})
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


# --------------------------------------------------------------------------- #
# 1. 空态：结构稳定，样本不足的指标是 null 不是 0
# --------------------------------------------------------------------------- #

def test_empty_cache_returns_available_false_with_stable_shape(tmp_path: Path) -> None:
    body = _body(tmp_path)

    assert body["domain"] == "sentiment"
    assert body["topic"] == "shortline_sentiment_history"
    assert body["available"] is False
    assert set(body["payload"]) == EXPECTED_PAYLOAD_KEYS
    assert body["payload"]["available"] is False  # 前端读的是 payload.available
    assert isinstance(body["payload"]["reason"], str) and body["payload"]["reason"]
    assert body["payload"]["window"] == 20
    assert body["payload"]["date"] == date.today().isoformat()
    assert body["payload"]["points"] == []
    assert body["payload"]["missing_days"] == []
    assert body["payload"]["quantile"] is None
    assert body["payload"]["delta_vs_prev"] is None
    assert body["payload"]["data_source_status"] == "missing"
    assert body["source_status"] == "partial"
    assert body["confidence"] == "low"
    assert body["data_as_of"] is None  # 没数据不拿请求日期顶替（D-02）
    # 空态也要说清「为什么没有」，前端才有得显示
    assert "ldst sync" in body["source_detail"]


# --------------------------------------------------------------------------- #
# 2. 有数据：逐日成点，分位与较昨日均以最后一个可用日为准
# --------------------------------------------------------------------------- #

def test_seeded_sessions_return_ascending_points(tmp_path: Path) -> None:
    _seed_sessions(tmp_path)
    body = _body(tmp_path, {"to": LATEST.isoformat()})
    payload = body["payload"]

    assert body["available"] is True
    assert body["source_status"] == "ok"
    assert body["data_as_of"] == LATEST.isoformat()
    assert payload["available"] is True
    assert payload["reason"] is None
    assert payload["date"] == LATEST.isoformat()
    assert payload["window"] == 20
    assert [point["date"] for point in payload["points"]] == [
        day.isoformat() for day in SESSION_DAYS
    ]
    for point in payload["points"]:
        assert set(point) == EXPECTED_POINT_KEYS
        # 与 `compute_sentiment` 同源同值：本接口只做窗口聚合，不重算口径
        expected = compute_sentiment(date.fromisoformat(point["date"]), root=tmp_path)
        assert point["temperature"] == pytest.approx(float(expected["temperature"]))
        assert point["promotion_rate"] == pytest.approx(float(expected["promotion_rate"]))
        assert point["limit_up_count"] == expected["limit_up_count"]


def test_quantile_and_delta_use_last_available_day(tmp_path: Path) -> None:
    _seed_sessions(tmp_path)
    payload = _body(tmp_path, {"to": LATEST.isoformat()})["payload"]
    temperatures = [float(point["temperature"]) for point in payload["points"]]
    latest = temperatures[-1]

    assert payload["delta_vs_prev"] == pytest.approx(round(latest - temperatures[-2], 1))
    at_or_below = sum(1 for value in temperatures if value <= latest)
    assert payload["quantile"] == pytest.approx(
        round(at_or_below / len(temperatures) * 100.0, 1)
    )


def test_weekend_is_skipped_and_window_clips_to_days(tmp_path: Path) -> None:
    _seed_sessions(tmp_path)
    # 请求末日是周六：窗口仍是最近 3 个交易日（10-21 / 10-22 / 10-23）
    payload = _body(tmp_path, {"to": WEEKEND.isoformat(), "days": "3"})["payload"]

    assert [point["date"] for point in payload["points"]] == [
        day.isoformat() for day in SESSION_DAYS[-3:]
    ]
    assert payload["missing_days"] == []
    assert payload["quantile"] == pytest.approx(100.0)


# --------------------------------------------------------------------------- #
# 3. 参数名：前端 api.ts 的 `date` / `window` 是别名，本名 `to` / `days` 优先
# --------------------------------------------------------------------------- #

def test_frontend_aliases_date_and_window_are_accepted(tmp_path: Path) -> None:
    """`?date=&window=`（`frontend/src/api.ts:marketHistory`）与 `?to=&days=` 同义。"""
    _seed_sessions(tmp_path)
    canonical = _body(tmp_path, {"to": LATEST.isoformat(), "days": "3"})["payload"]
    aliased = _body(tmp_path, {"date": LATEST.isoformat(), "window": "3"})["payload"]

    assert aliased == canonical
    assert len(aliased["points"]) == 3


def test_canonical_names_win_when_both_are_given(tmp_path: Path) -> None:
    _seed_sessions(tmp_path)
    payload = _body(
        tmp_path,
        {"to": LATEST.isoformat(), "days": "2", "date": "2026-10-19", "window": "5"},
    )["payload"]

    assert len(payload["points"]) == 2
    assert payload["window"] == 2


# --------------------------------------------------------------------------- #
# 4. 缺日：如实进 missing_days，source_detail 点名缺哪几天
# --------------------------------------------------------------------------- #

def test_partial_days_land_in_missing_days(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_sessions(tmp_path)
    real = compute_sentiment
    dropped = SESSION_DAYS[1]

    def flaky(day: date | str, *, root: Any = None) -> dict[str, Any]:
        if str(day) == dropped.isoformat():
            return {"available": False, "temperature": None, "reason": "涨停池为空"}
        return real(day, root=root)

    monkeypatch.setattr("ladderstock.web.server.compute_sentiment", flaky)
    body = _body(tmp_path, {"to": LATEST.isoformat()})
    payload = body["payload"]

    assert payload["missing_days"] == [dropped.isoformat()]
    assert len(payload["points"]) == len(SESSION_DAYS) - 1
    assert body["available"] is True
    assert dropped.isoformat() in body["source_detail"]


def test_single_point_is_not_a_trend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """只剩 1 个可用点 → `available=false`，分位与较昨日仍是 null。"""
    _seed_sessions(tmp_path)
    real = compute_sentiment
    keep = SESSION_DAYS[-1]

    def only_one(day: date | str, *, root: Any = None) -> dict[str, Any]:
        if str(day) == keep.isoformat():
            return real(day, root=root)
        return {"available": False, "temperature": None, "reason": "缺数"}

    monkeypatch.setattr("ladderstock.web.server.compute_sentiment", only_one)
    body = _body(tmp_path, {"to": LATEST.isoformat()})
    payload = body["payload"]

    assert len(payload["points"]) == 1
    assert set(payload) == EXPECTED_PAYLOAD_KEYS
    assert payload["quantile"] is None
    assert payload["delta_vs_prev"] is None
    assert body["available"] is False
    assert body["data_as_of"] is None
    assert payload["data_source_status"] == "missing"
    assert len(payload["missing_days"]) == len(SESSION_DAYS) - 1


# --------------------------------------------------------------------------- #
# 5. 参数：days 钳制而不是报错，日期严格 YYYY-MM-DD
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("raw", "expected_points"),
    [("0", 1), ("-5", 1), ("1", 1), ("2", 2), ("999", 5), ("60", 5), ("", 5)],
)
def test_days_is_clamped(tmp_path: Path, raw: str, expected_points: int) -> None:
    """缓存里只有 5 个交易日，所以钳到 60 也只有 5 个点——但 1 与 2 证明钳制生效。"""
    _seed_sessions(tmp_path)
    payload = _body(tmp_path, {"to": LATEST.isoformat(), "days": raw})["payload"]
    assert len(payload["points"]) == expected_points, (raw, payload)


@pytest.mark.parametrize(
    "params",
    [
        {"days": "abc"},
        {"days": "1.5"},
        {"days": "nan"},
        {"to": "20261029"},
        {"to": "2026-13-01"},
        {"to": "2026-10-29T00:00"},
        {"to": "昨天"},
        {"date": "20261029"},  # 别名同样严格
        {"window": "abc"},
    ],
)
def test_invalid_params_are_400(tmp_path: Path, params: dict[str, str]) -> None:
    response = _client(tmp_path).get("/api/market/history", params=params)
    assert response.status_code == 400, response.text
    assert isinstance(response.json()["detail"], str)


# --------------------------------------------------------------------------- #
# 6. 回环独占 + 只 GET + 隐私恒等
# --------------------------------------------------------------------------- #

def test_non_loopback_source_is_403(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_sessions(tmp_path)
    monkeypatch.setenv("LDST_WEB_ALLOW_REMOTE", "1")
    monkeypatch.setenv("LDST_WEB_TOKEN", "0123456789abcdef0123")

    response = _client(tmp_path, client=REMOTE).get(
        "/api/market/history", params={"to": LATEST.isoformat()}
    )
    assert response.status_code == 403
    assert "points" not in response.text  # 一个数字都不能随 403 漏出

    # 回环来源但不带合法 Host（DNS rebinding 场景）同样 403
    rebinding = _client(tmp_path).get(
        "/api/market/history", headers={"host": "evil.example.com"}
    )
    assert rebinding.status_code == 403
    # 对照：回环 + 正常 Host 放行
    assert _client(tmp_path).get("/api/market/history").status_code == 200


def test_control_switch_does_not_close_market_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`LDST_CONTROL_ENABLED=0` 只关写面：行情聚合是只读，必须照常 200。"""
    monkeypatch.setenv(control.CONTROL_ENABLED_ENV, "0")
    client = _client(tmp_path)
    assert client.get("/api/market/history").status_code == 200
    assert client.get("/api/control/peers").status_code == 404


def test_only_get_is_allowed(tmp_path: Path) -> None:
    client = _client(tmp_path)
    for method in ("post", "put", "patch", "delete"):
        response = getattr(client, method)("/api/market/history")
        assert response.status_code == 405, (method, response.status_code)
        assert response.headers["allow"] == "GET"


def test_payload_is_strip_private_identity(tmp_path: Path) -> None:
    _seed_sessions(tmp_path)
    body = _body(tmp_path, {"to": LATEST.isoformat()})
    assert strip_private(body) == body
    assert private_keys_present(body) == []


def test_private_keys_from_upstream_never_reach_the_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """上游若把 `positions` / `journal` / `note` 混进返回值，投影层与 envelope 要一起挡住。"""
    _seed_sessions(tmp_path)

    def leaky(day: date | str, *, root: Any = None) -> dict[str, Any]:
        return {
            "available": True,
            "date": str(day),
            "temperature": 55.0,
            "promotion_rate": 40.0,
            "limit_up_count": 3,
            "positions": [{"code": "600000", "note": SENTINEL}],
            "journal": {"trades": [SENTINEL]},
            "note": SENTINEL,
        }

    monkeypatch.setattr("ladderstock.web.server.compute_sentiment", leaky)
    response = _client(tmp_path).get("/api/market/history", params={"to": LATEST.isoformat()})
    body = response.json()

    assert SENTINEL not in response.text
    assert "600000" not in response.text
    assert set(body["payload"]) == EXPECTED_PAYLOAD_KEYS
    assert set(body["payload"]["points"][0]) == EXPECTED_POINT_KEYS
    assert strip_private(body) == body
