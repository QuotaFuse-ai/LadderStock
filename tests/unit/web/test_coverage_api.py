"""F12 覆盖看板 API 契约：`/api/coverage/*` 的读写分界。

钉住四件事：

1. 读面（board / gaps / plan）回环可用、`no-store`、带中文 `labels`；
2. board 在空库上仍然 200（结论是「全部 never/unregistered」，不是 500）；
3. 不认识的 dataset 404，合法但未注册的 400（原话带 sync.yaml）；
4. 补采写面缺来源证据 403；带证据且 `execute` 缺省 → `status=planned`，一个请求都不发。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.web.server import create_app

LOOPBACK = ("127.0.0.1", 54321)
BASE_URL = "http://127.0.0.1:5950"
WRITE_HEADERS = {"X-LDST-Client": "1"}


class _NullControlPlane:
    peers: dict[str, Any] = {}


def _client(root: Path, headers: dict[str, str] | None = None) -> TestClient:
    app = create_app(
        data_root=root,
        dist_dir=root / "no-dist",
        control_plane=_NullControlPlane(),
    )
    return TestClient(app, base_url=BASE_URL, client=LOOPBACK, headers=headers or {})


def test_board_ok_on_empty_db(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.get("/api/coverage/board")
    assert response.status_code == 200
    assert response.headers.get("cache-control") == "no-store"
    payload = response.json()
    assert payload["local_only"] is True
    assert payload["db"]["exists"] is False
    assert payload["datasets"], "空库也要把 62 个目录项列全"
    assert payload["labels"]["ok"] == "正常"
    for row in payload["datasets"]:
        assert row["state"] in payload["labels"]
        assert row["state"] != "ok", "空库不该有 ok"


def test_gaps_sorted_and_consistent(tmp_path: Path) -> None:
    client = _client(tmp_path)
    payload = client.get("/api/coverage/gaps").json()
    assert payload["gaps"]
    assert payload["summary"]["needs_attention"] == len(payload["gaps"])
    board = client.get("/api/coverage/board").json()
    assert [row["dataset"] for row in payload["gaps"]] == [
        row["dataset"] for row in board["gaps"]
    ]


def test_plan_unknown_dataset_is_404(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.get("/api/coverage/plan", params={"dataset": "not_a_dataset"})
    assert response.status_code == 404
    assert "DATASET_CATALOG" in response.json()["detail"]


def test_plan_unregistered_is_400(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.get("/api/coverage/plan", params={"dataset": "us_13f"})
    assert response.status_code == 400
    assert "sync.yaml" in response.json()["detail"]


def test_backfill_requires_write_evidence(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.post("/api/coverage/backfill", params={"dataset": "daily_k"})
    assert response.status_code == 403


def test_backfill_default_is_dry_run(tmp_path: Path) -> None:
    client = _client(tmp_path, WRITE_HEADERS)
    response = client.post(
        "/api/coverage/backfill",
        params={"dataset": "daily_k", "codes": "600519,000858"},
    )
    if response.status_code == 400:
        # 本机 config/sync.yaml 没声明 daily_k 时是配置问题，不是契约问题
        pytest.skip(f"当前配置未注册 daily_k：{response.json()['detail']}")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["execute"] is False
    assert payload["status"] == "planned"
    assert payload["codes"] == ["600519", "000858"]
    assert payload["hint"]
