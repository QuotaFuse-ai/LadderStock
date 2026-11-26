"""`/api/knowledge` 写入与检索面契约（E 包 / DEC-091）。

钉住：闸门（回环/Host/写面来源证据）、笔记 CRUD、标签过滤、
检索的降级语义（qdrant 不可用 → 本地 LIKE，engine 字段如实分开报）。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from ladderstock.knowledge import notes as notes_store
from ladderstock.web.server import create_app

LOOPBACK = ("127.0.0.1", 54321)
REMOTE = ("10.1.2.3", 54321)
WRITE_HEADERS = {"X-LDST-Client": "1"}


def _client(root: Path, *, client: tuple[str, int] = LOOPBACK) -> TestClient:
    app = create_app(data_root=root, dist_dir=root / "no-dist")
    return TestClient(app, client=client)


# --------------------------------------------------------------------- 闸门


def test_notes_face_is_loopback_only(tmp_path: Path) -> None:
    assert (
        _client(tmp_path, client=REMOTE).get("/api/knowledge/notes").status_code == 403
    )
    assert _client(tmp_path).get("/api/knowledge/notes").status_code == 200


def test_notes_write_needs_source_evidence(tmp_path: Path) -> None:
    denied = _client(tmp_path).post(
        "/api/knowledge/notes", json={"body": "x"}
    )
    assert denied.status_code == 403
    assert notes_store.list_notes(root=tmp_path) == []


def test_search_face_is_loopback_only(tmp_path: Path) -> None:
    assert (
        _client(tmp_path, client=REMOTE).get("/api/knowledge/search?q=利润").status_code
        == 403
    )


# ------------------------------------------------------------------- CRUD


def test_note_crud_over_http(tmp_path: Path) -> None:
    client = _client(tmp_path)
    created = client.post(
        "/api/knowledge/notes",
        json={"body": "等三季报", "target": "SH:600000", "title": "浦发", "tags": ["银行"]},
        headers=WRITE_HEADERS,
    )
    assert created.status_code == 200, created.text
    note = created.json()["note"]
    assert note["symbol"] == "600000" and note["tags"] == ["银行"]

    updated = client.put(
        f"/api/knowledge/notes/{note['id']}",
        json={"body": "看完三季报：不动"},
        headers=WRITE_HEADERS,
    )
    assert updated.status_code == 200
    assert updated.json()["note"]["body"] == "看完三季报：不动"
    assert updated.json()["note"]["tags"] == ["银行"]  # 未传 tags 不动

    listed = client.get("/api/knowledge/notes?target=600000").json()
    assert listed["total"] == 1

    tags = client.get("/api/knowledge/notes/tags").json()["tags"]
    assert tags == [{"tag": "银行", "notes": 1}]

    removed = client.delete(
        f"/api/knowledge/notes/{note['id']}", headers=WRITE_HEADERS
    )
    assert removed.status_code == 200
    assert client.get("/api/knowledge/notes").json()["total"] == 0
    assert (
        client.delete("/api/knowledge/notes/9999", headers=WRITE_HEADERS).status_code
        == 404
    )


def test_note_validation_errors(tmp_path: Path) -> None:
    client = _client(tmp_path)
    empty = client.post("/api/knowledge/notes", json={"body": "  "}, headers=WRITE_HEADERS)
    assert empty.status_code == 400
    oversized = client.post(
        "/api/knowledge/notes",
        json={"body": "x" * (notes_store.MAX_BODY_CHARS + 1)},
        headers=WRITE_HEADERS,
    )
    assert oversized.status_code == 400


# ------------------------------------------------------------------- 检索


def test_search_falls_back_to_local_like(tmp_path: Path) -> None:
    """qdrant 不通（测试环境必然）→ 本地 LIKE 扫档案正文；engine 如实报 local。"""
    (tmp_path / "knowledge" / "SH_600000").mkdir(parents=True)
    (tmp_path / "knowledge" / "SH_600000" / "profile.md").write_text(
        "---\nsymbol: 600000\n---\n## 研究运行\n净利润 12.3 亿，同比增长 5%。\n",
        encoding="utf-8",
    )
    response = _client(tmp_path).get("/api/knowledge/search?q=净利润")
    assert response.status_code == 200
    body = response.json()
    assert body["engine"] == "local"
    assert body["vector_error"] is not None  # 向量层的失败原因如实带回
    assert body["total"] == 1
    hit = body["hits"][0]
    assert hit["source"] == "local"
    assert hit["market"] == "SH" and hit["symbol"] == "600000"
    assert "净利润" in hit["snippet"]


def test_search_query_bounds(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.get("/api/knowledge/search?q=").status_code == 422
    assert client.get("/api/knowledge/search?q=" + "x" * 201).status_code == 422
    assert client.get("/api/knowledge/search?q=x&limit=0").status_code == 422