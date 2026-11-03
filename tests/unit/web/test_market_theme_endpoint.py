"""QUOT-199 / Q172-8：`GET /api/market/theme`（题材强度轮动）的契约测试。

这个接口的数据源只有一个：QuotaFuse Wire 的 `theme_news`（`core/boundary.py` 里登记为
可选消费）。本机当前没有接入 Wire，所以**上线即为 `available=false` 的空态**——测试把
「空态是预期行为」和「有数据时的字段口径」都钉住：

1. 无当日数据 → 200 + `available=false` + `themes=[]` + `source_detail` 里写「先配置 qfw」；
2. 有数据 → 只出 `theme` / `strength` / `limit_up_count` / `leader` / `delta` 五个白名单
   字段，按 `strength` 降序、最多 20 条，取不到的一律 `null`（不补 0、不编造）；
3. 资讯正文与链接**绝不进响应**（`summary` / `content` / `url` / 正文 / 摘要 / 链接），
   隐私字段也不进（`strip_private(payload) == payload`）；
4. 回环独占（非回环 403、Host 不在白名单 403）、只 GET、`Cache-Control: no-store`。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.core.privacy import private_keys_present, strip_private
from ladderstock.data import duckdb_store
from ladderstock.web.server import MARKET_THEME_MAX_ROWS, create_app

LOOPBACK = ("127.0.0.1", 54321)
REMOTE = ("10.1.2.3", 54321)

DAY = date(2026, 10, 23)
STAMP = DAY.isoformat()
SENTINEL = "隐私哨兵-QUOT199-正文"
LEAKY_URL = "https://wire.example.com/digest/12345?token=abcdef"

#: 契约形状（Q172-8 接口 2 + 前端 `MarketThemePayload`）：`data_source_status` 由
#: envelope 统一追加
EXPECTED_PAYLOAD_KEYS = {"date", "available", "reason", "themes", "data_source_status"}
EXPECTED_THEME_KEYS = {"theme", "strength", "limit_up_count", "leader", "delta"}


def _client(root: Path, client: tuple[str, int] = LOOPBACK) -> TestClient:
    return TestClient(create_app(data_root=root, dist_dir=root / "no-dist"), client=client)


def _seed_theme_news(root: Path, records: list[dict[str, Any]]) -> None:
    """按 `theme_news` 登记的 `replace` 模式落盘（等价于一次 Wire 回放）。"""
    duckdb_store.write_dataset("theme_news", records, root=root)


def _body(root: Path, params: dict[str, str] | None = None) -> dict[str, Any]:
    response = _client(root).get("/api/market/theme", params=params or {"date": STAMP})
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


# --------------------------------------------------------------------------- #
# 1. 空态：未接入 Wire 是预期状态，不是 bug
# --------------------------------------------------------------------------- #

def test_without_theme_news_returns_available_false(tmp_path: Path) -> None:
    body = _body(tmp_path)

    assert body["domain"] == "sentiment"
    assert body["topic"] == "theme_rotation"
    assert body["available"] is False
    assert set(body["payload"]) == EXPECTED_PAYLOAD_KEYS
    assert body["payload"]["available"] is False  # 前端读的是 payload.available
    assert body["payload"]["themes"] == []
    assert body["payload"]["date"] == STAMP
    assert "theme_news 未接入（QuotaFuse Wire）" in body["payload"]["reason"]
    assert body["payload"]["data_source_status"] == "missing"
    assert body["data_as_of"] is None
    assert body["source_status"] == "partial"
    detail = body["source_detail"]
    assert "theme_news 未接入（QuotaFuse Wire）" in detail
    assert "qfw" in detail
    assert "ldst sync" in detail  # 给出可执行的下一步，而不是只说「没有」


def test_records_without_theme_field_are_not_invented(tmp_path: Path) -> None:
    """有记录但都没有题材名 → 仍是空态，并且说清是「没有题材字段」。"""
    _seed_theme_news(tmp_path, [{"strength": 90.0, "trade_date": STAMP}])
    body = _body(tmp_path)

    assert body["available"] is False
    assert body["payload"]["themes"] == []
    assert "没有题材字段" in body["source_detail"]


# --------------------------------------------------------------------------- #
# 2. 有数据：白名单字段、强度降序、缺值给 null
# --------------------------------------------------------------------------- #

def test_seeded_themes_project_whitelist_and_sort_by_strength(tmp_path: Path) -> None:
    _seed_theme_news(
        tmp_path,
        [
            {
                "theme": "算力硬件",
                "strength": 88.5,
                "limit_up_count": 12,
                "leader_code": "300001",
                "leader_sec_name": "某某科技",
                "delta": 3.1,
                "trade_date": STAMP,
            },
            {
                "theme": "机器人",
                "strength": 91.2,
                "limit_up_count": 15,
                "leader": "688002 某某智能",
                "delta": -1.4,
                "trade_date": STAMP,
            },
            {"theme": "创新药", "trade_date": STAMP},  # 只有题材名
        ],
    )
    body = _body(tmp_path)
    themes = body["payload"]["themes"]

    assert body["available"] is True
    assert body["payload"]["available"] is True
    assert body["payload"]["reason"] is None
    assert body["source_status"] == "ok"
    assert body["data_as_of"] == STAMP
    assert [row["theme"] for row in themes] == ["机器人", "算力硬件", "创新药"]
    for row in themes:
        assert set(row) == EXPECTED_THEME_KEYS
    assert themes[0]["strength"] == pytest.approx(91.2)
    assert themes[0]["leader"] == "688002 某某智能"
    assert themes[0]["delta"] == pytest.approx(-1.4)
    assert themes[1]["leader"] == "300001 某某科技"  # 代码 + 名称拼接
    # 取不到的字段是 null，不是 0 / 空串
    assert themes[2] == {
        "theme": "创新药",
        "strength": None,
        "limit_up_count": None,
        "leader": None,
        "delta": None,
    }
    assert "theme_news: parquet" in body["source_detail"]


def test_themes_without_strength_sort_last(tmp_path: Path) -> None:
    _seed_theme_news(
        tmp_path,
        [
            {"theme": "无强度甲", "trade_date": STAMP},
            {"theme": "有强度", "strength": 10.0, "trade_date": STAMP},
            {"theme": "无强度乙", "trade_date": STAMP},
        ],
    )
    themes = _body(tmp_path)["payload"]["themes"]

    assert [row["theme"] for row in themes] == ["有强度", "无强度乙", "无强度甲"]


def test_real_wire_digest_row_yields_name_and_null_metrics(tmp_path: Path) -> None:
    """真实 Wire 落库的行（QUOT-328）：赛道名在 `topic`（**slug**），没有强度类字段。

    钉两件事：① `topic` 要认得出题材名，否则 12 个赛道全被丢弃、接口永远 `unavailable`；
    ② 强度/涨停家数/龙头/变化**如实 `null`**，界面据此画 `—`，不许冒 0。
    """
    _seed_theme_news(
        tmp_path,
        [
            {
                "topic": topic,
                "date": STAMP,
                "summary": "共采集 12 条资讯；模型当前不可用，未生成要点。",
                "key_points": [],
                "items": [{"title": "某资讯", "url": LEAKY_URL}],
                "status": "partial",
                "trade_date": STAMP,
            }
            for topic in ("ai", "semiconductor")
        ],
    )
    body = _body(tmp_path)
    themes = body["payload"]["themes"]

    assert body["payload"]["available"] is True
    assert body["payload"]["data_source_status"] == "ok"
    assert sorted(row["theme"] for row in themes) == ["ai", "semiconductor"]
    for row in themes:
        assert row["strength"] is None
        assert row["limit_up_count"] is None
        assert row["leader"] is None
        assert row["delta"] is None
    assert LEAKY_URL not in json.dumps(body, ensure_ascii=False)


def test_same_theme_rows_aggregate_into_one(tmp_path: Path) -> None:
    """同一题材多条资讯：强度 / 涨停家数取最大，龙头与变化取第一条能取到的。"""
    _seed_theme_news(
        tmp_path,
        [
            {"theme": "算力硬件", "strength": 70.0, "limit_up_count": 5, "trade_date": STAMP},
            {"theme": "算力硬件", "strength": 88.5, "limit_up_count": 12, "trade_date": STAMP},
            {
                "theme": "算力硬件",
                "strength": 60.0,
                "limit_up_count": 3,
                "leader": "300009 某某光",
                "delta": 2.0,
                "trade_date": STAMP,
            },
        ],
    )
    themes = _body(tmp_path)["payload"]["themes"]

    assert len(themes) == 1
    assert themes[0]["strength"] == pytest.approx(88.5)
    assert themes[0]["limit_up_count"] == 12
    assert themes[0]["leader"] == "300009 某某光"
    assert themes[0]["delta"] == pytest.approx(2.0)


def test_rows_are_capped_at_twenty(tmp_path: Path) -> None:
    _seed_theme_news(
        tmp_path,
        [{"theme": f"题材{i}", "strength": float(i), "trade_date": STAMP} for i in range(30)],
    )
    themes = _body(tmp_path)["payload"]["themes"]

    assert len(themes) == MARKET_THEME_MAX_ROWS == 20
    strengths = [float(row["strength"]) for row in themes]
    assert strengths == sorted(strengths, reverse=True)


# --------------------------------------------------------------------------- #
# 3. 隐私：资讯正文与链接绝不进响应
# --------------------------------------------------------------------------- #

def test_news_body_and_url_never_leave_the_box(tmp_path: Path) -> None:
    _seed_theme_news(
        tmp_path,
        [
            {
                "theme": "算力硬件",
                "strength": 88.5,
                "summary": SENTINEL,
                "content": SENTINEL,
                "url": LEAKY_URL,
                "正文": SENTINEL,
                "摘要": SENTINEL,
                "链接": LEAKY_URL,
                "positions": [{"code": "600000", "note": SENTINEL}],
                "journal": {"trades": [SENTINEL]},
                "trade_date": STAMP,
            }
        ],
    )
    response = _client(tmp_path).get("/api/market/theme", params={"date": STAMP})
    body = response.json()

    assert body["available"] is True
    assert SENTINEL not in response.text
    assert "wire.example.com" not in response.text
    assert "600000" not in response.text
    assert set(body["payload"]["themes"][0]) == EXPECTED_THEME_KEYS
    assert strip_private(body) == body
    assert private_keys_present(body) == []


def test_projection_is_strip_private_identity_even_when_upstream_leaks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """上游（Wire 回放的记录）多塞什么字段都改不动出站形状：白名单在投影层收口。"""

    def leaky(dataset: str, day: date | str, root: Any = None) -> tuple[list[dict[str, Any]], str]:
        return (
            [
                {
                    "theme": "算力硬件",
                    "strength": 88.5,
                    "digest": SENTINEL,
                    "title": SENTINEL,
                    "source_url": LEAKY_URL,
                    "trades": [SENTINEL],
                    "nested": {"note": SENTINEL},
                }
            ],
            "parquet",
        )

    monkeypatch.setattr("ladderstock.web.server.dataset_snapshot", leaky)
    response = _client(tmp_path).get("/api/market/theme", params={"date": STAMP})
    body = response.json()

    assert SENTINEL not in response.text
    assert set(body["payload"]) == EXPECTED_PAYLOAD_KEYS
    assert set(body["payload"]["themes"][0]) == EXPECTED_THEME_KEYS
    assert strip_private(body) == body


# --------------------------------------------------------------------------- #
# 4. 参数：date 可选，格式严格
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("raw", ["20261029", "2026-13-01", "2026-10-29T00:00", "昨天", "2026-10"])
def test_invalid_date_is_400(tmp_path: Path, raw: str) -> None:
    response = _client(tmp_path).get("/api/market/theme", params={"date": raw})
    assert response.status_code == 400, response.text
    assert isinstance(response.json()["detail"], str)


def test_date_defaults_to_today(tmp_path: Path) -> None:
    """不传 `date`：按今天查，空态里的日期就是今天，不拿请求值伪造时点。"""
    response = _client(tmp_path).get("/api/market/theme")
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["available"] is False
    assert body["data_as_of"] is None
    assert date.today().isoformat() in body["source_detail"]


# --------------------------------------------------------------------------- #
# 5. 回环独占 + 只 GET
# --------------------------------------------------------------------------- #

def test_non_loopback_source_is_403(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_theme_news(tmp_path, [{"theme": "算力硬件", "strength": 88.5, "trade_date": STAMP}])
    monkeypatch.setenv("LDST_WEB_ALLOW_REMOTE", "1")
    monkeypatch.setenv("LDST_WEB_TOKEN", "0123456789abcdef0123")

    response = _client(tmp_path, client=REMOTE).get("/api/market/theme", params={"date": STAMP})
    assert response.status_code == 403
    assert "themes" not in response.text

    rebinding = _client(tmp_path).get(
        "/api/market/theme", params={"date": STAMP}, headers={"host": "evil.example.com"}
    )
    assert rebinding.status_code == 403
    assert _client(tmp_path).get("/api/market/theme", params={"date": STAMP}).status_code == 200


def test_only_get_is_allowed(tmp_path: Path) -> None:
    client = _client(tmp_path)
    for method in ("post", "put", "patch", "delete"):
        response = getattr(client, method)("/api/market/theme")
        assert response.status_code == 405, (method, response.status_code)
        assert response.headers["allow"] == "GET"
