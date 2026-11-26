"""盘中 `GET /api/ladder` 的兜底：当日没归档时用实时龙头名单拼**部分**梯队。

盘中的硬约束：`market_sentiment` 只给涨停家数与龙头前十，不给当日涨停全集，所以每一档
的家数算不全。接口必须把这一点说清楚（`partial` / `leader_only` + `reason`），
页面据此只列龙头、不画"看起来完整"的柱图；完整梯队等 15:35 收盘同步。
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from ladderstock.core import live_sentiment
from ladderstock.web.server import create_app

LOOPBACK = ("127.0.0.1", 54321)
DAY = date(2026, 11, 17)
STAMP = DAY.isoformat()


def _client(root: Path) -> TestClient:
    return TestClient(create_app(data_root=root, dist_dir=root / "no-dist"), client=LOOPBACK)


def _ladder(root: Path) -> dict:
    response = _client(root).get("/api/ladder", params={"date": STAMP})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["topic"] == "limit_ladder"
    assert isinstance(body["payload"]["ladder"], dict)
    return body["payload"]["ladder"]


def _seed_live(root: Path, leaders: list[dict], limit_up: int = 24) -> None:
    live_sentiment.write_snapshot(
        STAMP,
        {
            "date": "20261117",
            "limit_up": limit_up,
            "broken": 10,
            "max_streak": 3,
            "leaders": leaders,
        },
        fetched_at=datetime(2026, 11, 17, 10, 30),
        root=root,
    )


def test_ladder_uses_live_leaders_when_archive_is_missing(tmp_path: Path) -> None:
    _seed_live(
        tmp_path,
        [
            {"code": "002685", "name": "华东重机", "streak": 3},
            {"code": "000533", "name": "顺钠股份", "streak": 2},
            {"code": "000521", "name": "长虹美菱", "streak": 2},
        ],
    )
    ladder = _ladder(tmp_path)

    assert ladder["available"] is True
    assert ladder["partial"] is True
    assert ladder["leader_only"] is True
    assert ladder["height"] == 3
    assert ladder["count"] == 3
    # 只有龙头这几只，家数不是全量 → 明确写在理由里，不当全量用
    assert [row["code"] for row in ladder["leader_candidates"]] == ["002685", "000521", "000533"]
    assert "只有龙头前 3 只" in ladder["source_detail"]
    assert "盘中只能取到龙头前 3 只" in ladder["reason"]


def test_ladder_stays_empty_without_archive_and_without_live(tmp_path: Path) -> None:
    ladder = _ladder(tmp_path)

    assert ladder["available"] is False
    assert ladder["ladder"] == []
    assert not ladder.get("partial")
