"""`POST /api/watch/refresh`：让「立即取数」真的取一次数。

这条接口修的是按钮名与行为不符：此前「立即取数」只把 `GET /api/watch/snapshot`
重发一遍，后端不取数、不写 `minute_bars`，用户点完仍是「还没有取过数」。

钉住四件事：

1. 交易时段取数成功 → `status=ok` + `minute_bars` 写入条数（页面能说明「写了多少」）；
2. 盘后 → `status=skipped`，如实给出原因（收盘价不能冒充盘中信号）；
3. 写请求必须带来源证据（本机页面 Origin 或 `X-LDST-Client: 1`），否则 403；
4. 只接受回环来源。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.web.server import create_app

LOOPBACK = ("127.0.0.1", 50000)
BASE_URL = "http://127.0.0.1:5950"
HEADER = {"X-LDST-Client": "1"}


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    return TestClient(
        create_app(data_root=tmp_path, dist_dir=tmp_path / "missing"),
        base_url=BASE_URL,
        client=LOOPBACK,
    )


def _stub(result: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """替换真正的取数：测试不联网，只验证路由把结果如实转出去。"""
    seen: list[dict[str, Any]] = []

    def _fake(job: Any, **_: Any) -> dict[str, Any]:
        seen.append(dict(job))
        return result

    monkeypatch.setattr("ladderstock.cli.schedule._run_watch_live", _fake)
    return seen


def test_refresh_in_session_reports_bars_written(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _stub(
        {
            "date": "2026-11-17",
            "status": "ok",
            "detail": "5 只自选实时价比对完毕，没有触发提醒；分钟样本 5 条",
            "alerted": 0,
            "minute_bars": {
                "path": "data/minute_bars/2026-11-17.jsonl",
                "written": 5,
                "total": 5,
                "rejected": 0,
            },
        },
        monkeypatch,
    )

    response = client.post("/api/watch/refresh", headers=HEADER)

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["status"] == "ok"
    assert body["minute_bars"]["written"] == 5
    assert body["alerted"] == 0
    assert "分钟样本" in body["detail"]
    assert seen and seen[0]["id"] == "manual-refresh"


def test_refresh_off_session_is_skipped_with_reason(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(
        {
            "date": "2026-11-17",
            "status": "skipped",
            "detail": "非交易时段（09:30–11:30 / 13:00–15:00 才盯盘）",
        },
        monkeypatch,
    )

    body = client.post("/api/watch/refresh", headers=HEADER).json()

    assert body["ok"] is True
    assert body["status"] == "skipped"
    assert "非交易时段" in body["detail"]
    assert body["minute_bars"] is None


def test_refresh_requires_write_source_evidence(client: TestClient) -> None:
    response = client.post("/api/watch/refresh")

    assert response.status_code == 403
    assert "来源证据" in response.json()["detail"]


def test_refresh_rejects_non_loopback(tmp_path: Path) -> None:
    app = create_app(data_root=tmp_path, dist_dir=tmp_path / "missing")
    remote = TestClient(app, base_url="http://10.0.0.8:5950", client=("10.0.0.8", 50000))

    response = remote.post("/api/watch/refresh", headers=HEADER)

    assert response.status_code == 403
    assert "回环" in response.json()["detail"]


def test_snapshot_fills_name_and_streak_when_event_has_neither(
    client: TestClient, tmp_path: Path
) -> None:
    """页面「名称」「连板数」两列不能因为事件里没这两个键就永远空着。

    名称退回状态文件的 `names`（当天样本里已知的代码→名称），连板数退回当日盘中
    情绪快照的龙头表——两处都只补**确实有出处**的值，仍没有就给 `null`。
    """
    import json
    from datetime import date, datetime

    from ladderstock.core import live_sentiment
    from ladderstock.watch import status as watch_status

    now = datetime.now().replace(microsecond=0).isoformat(timespec="seconds")
    document = {
        "running": True,
        "mode": "offline-single-pass",
        "date": "2026-11-17",
        "threshold": 3.0,
        "started_at": now,
        "last_run_at": now,
        "bars": 12,
        "source_detail": "file:2026-11-17.jsonl",
        "event_count": 2,
        "names": {"002187": "广百股份"},
        "recent_events": [
            {
                "ts": "2026-11-17T10:39:28",
                "code": "002187",
                "kind": "seal",
                "value": 8.31,
                "source_detail": "file:2026-11-17.jsonl",
            },
            {
                "ts": "2026-11-17T10:40:00",
                "code": "300824",
                "kind": "threshold_up",
                "value": 3.17,
                "source_detail": "file:2026-11-17.jsonl",
            },
        ],
        "events_file": "events-2026-11-17.jsonl",
    }
    path = watch_status.status_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

    live_sentiment.write_snapshot(
        date(2026, 11, 17),
        {"leaders": [{"code": "002187", "name": "广百股份", "streak": 4}]},
        fetched_at=datetime.now(),
        root=tmp_path,
    )

    body = client.get("/api/watch/snapshot").json()
    rows = {row["code"]: row for row in body["payload"]["rows"]}

    assert rows["002187"]["name"] == "广百股份"
    assert rows["002187"]["limit_up_count"] == 4
    # 没有出处的行仍然留空，不拿别的数据凑数
    assert rows["300824"]["name"] is None
    assert rows["300824"]["limit_up_count"] is None
