"""F10 查证 API 契约：`/api/verify/*` 的读写分界与"识别失败不算成功"。

钉住四件事：

1. 读面（capabilities / history）回环可用、`no-store`、能力字段齐全；
2. 上传缺来源证据 403；不支持的后缀 415；空文件 400；超上限 413；
3. 上传一份没有文本层的 PDF：HTTP 200 但 `ok=false` + `extract.status` 明确（不假装成功）；
4. 历史只读注入的 data_root。
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.research import verify as verify_module
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


def _blank_pdf() -> bytes:
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=595, height=842)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_capabilities_are_readable_and_complete(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.get("/api/verify/capabilities")
    assert response.status_code == 200
    assert response.headers.get("cache-control") == "no-store"
    payload = response.json()
    assert payload["local_only"] is True
    assert {"pdf_text", "image_vision", "local_ocr", "supported_image_mime", "tolerance"} <= set(payload)
    assert payload["local_ocr"] is False
    assert payload["max_bytes"] > 0


def test_history_is_empty_on_fresh_root(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert client.get("/api/verify/history").json()["items"] == []


def test_upload_requires_write_evidence(tmp_path: Path) -> None:
    client = _client(tmp_path)
    files = {"file": ("a.pdf", b"%PDF-1.4", "application/pdf")}
    assert client.post("/api/verify/file", files=files).status_code == 403


def test_upload_rejects_unsupported_suffix_and_empty(tmp_path: Path) -> None:
    client = _client(tmp_path, WRITE_HEADERS)
    files = {"file": ("a.txt", b"hello", "text/plain")}
    assert client.post("/api/verify/file", files=files).status_code == 415

    empty = {"file": ("a.pdf", b"", "application/pdf")}
    assert client.post("/api/verify/file", files=empty).status_code == 400


def test_upload_too_large_413(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LDST_VERIFY_MAX_BYTES", "10")
    client = _client(tmp_path, WRITE_HEADERS)
    files = {"file": ("a.pdf", b"x" * 50, "application/pdf")}
    assert client.post("/api/verify/file", files=files).status_code == 413


def test_blank_pdf_is_needs_ocr_not_success(tmp_path: Path) -> None:
    client = _client(tmp_path, WRITE_HEADERS)
    files = {"file": ("blank.pdf", _blank_pdf(), "application/pdf")}
    response = client.post("/api/verify/file", files=files)
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is False
    assert payload["extract"]["status"] == verify_module.STATUS_NEEDS_OCR
    assert payload["figures"] == []
    # 落盘照旧（留档以便复核），但结论明确是"没识别"
    assert payload["saved"]
    assert list((tmp_path / "verify").rglob("*.json"))


def test_history_reflects_upload(tmp_path: Path) -> None:
    client = _client(tmp_path, WRITE_HEADERS)
    files = {"file": ("blank.pdf", _blank_pdf(), "application/pdf")}
    assert client.post("/api/verify/file", files=files).status_code == 200
    items = client.get("/api/verify/history").json()["items"]
    assert len(items) == 1
    assert items[0]["filename"] == "blank.pdf"
