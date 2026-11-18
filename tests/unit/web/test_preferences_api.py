"""`/api/preferences`：偏好要能在页面上改和删，而不只是「看过文件」。

测四条用户会直接看到的契约：

1. 列表带类别中文名与自己写的条目都在；
2. 自己写一条会立刻进列表，且不因为「像行情描述」被抽取规则丢掉；
3. 删一条真的从文件里没了；删不存在的要明确 404，不静默成功；
4. 清空后文件本身被删掉（不是留一个空文件让下次误读）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ladderstock.ai import preferences
from ladderstock.web import preferences_api

BASE = "http://127.0.0.1:5950"
LOOPBACK = ("127.0.0.1", 1234)
HEADERS = {"X-LDST-Client": "1", "Origin": BASE}


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    app = FastAPI()
    app.include_router(preferences_api.build_router(data_root=tmp_path))
    return TestClient(app, base_url=BASE, client=LOOPBACK)


def test_empty_list_is_ok(client: TestClient) -> None:
    response = client.get("/api/preferences")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["count"] == 0
    assert body["labels"]["scope"] == "关注范围"


def test_add_then_list(client: TestClient, tmp_path: Path) -> None:
    response = client.post(
        "/api/preferences/add", json={"text": "我只看创业板和半导体"}, headers=HEADERS
    )
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 1
    assert body["items"][0]["text"] == "我只看创业板和半导体"
    # 类别由同一套词表推断，前端拿到的中文名直接可渲染
    assert body["items"][0]["kind_label"]

    raw = json.loads((tmp_path / preferences.FILENAME).read_text(encoding="utf-8"))
    assert any(item["text"] == "我只看创业板和半导体" for item in raw["items"])


def test_manual_add_keeps_odd_sentence(client: TestClient) -> None:
    """用户自己写的一句不该被「像行情描述」筛掉——那是抽取规则的事，不是编辑的事。"""
    response = client.post(
        "/api/preferences/add",
        json={"text": "回答里先给结论再展开", "kind": "style"},
        headers=HEADERS,
    )
    assert response.status_code == 200
    kinds = {item["kind"] for item in response.json()["items"]}
    assert "style" in kinds


def test_blank_text_is_rejected(client: TestClient) -> None:
    assert (
        client.post("/api/preferences/add", json={"text": "  "}, headers=HEADERS).status_code == 400
    )


def test_delete_removes_from_file(client: TestClient, tmp_path: Path) -> None:
    preferences.add_item("别给我推ST股", data_root=tmp_path)
    response = client.post(
        "/api/preferences/delete", json={"text": "别给我推ST股"}, headers=HEADERS
    )
    assert response.status_code == 200
    assert response.json()["count"] == 0
    raw = json.loads((tmp_path / preferences.FILENAME).read_text(encoding="utf-8"))
    assert all(item["text"] != "别给我推ST股" for item in raw["items"])


def test_delete_unknown_is_404(client: TestClient) -> None:
    response = client.post("/api/preferences/delete", json={"text": "没写过的"}, headers=HEADERS)
    assert response.status_code == 404


def test_clear_removes_file(client: TestClient, tmp_path: Path) -> None:
    preferences.add_item("我只要中文", data_root=tmp_path)
    response = client.post("/api/preferences/clear", headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["count"] == 0
    assert not (tmp_path / preferences.FILENAME).exists()


def test_write_requires_same_origin(client: TestClient) -> None:
    response = client.post("/api/preferences/add", json={"text": "我只看主板"})
    assert response.status_code == 403
