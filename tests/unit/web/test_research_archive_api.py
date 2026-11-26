"""F8 研究档案 API 契约：`/api/research/archive/*` 落在**服务自己的** data_root 上。

钉住四件事：
1. `/archive/list` 不被 `/{code}` 抢走（路由顺序），返回 `items` 而不是"code=archive"；
2. 扫描只读注入的 `data_root`（不是进程默认 data_dir），所以 tmp 里造一份只看到一份；
3. 检索空关键词 422、命中回原文片段；明细不存在 404；
4. 导出是写动作：缺来源证据 403，带 `X-LDST-Client: 1` 才落盘，产物含依据链总览。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.research import artifacts
from ladderstock.web.control import StepResult
from ladderstock.web.research import ResearchPlane
from ladderstock.web.server import create_app

CODE = "600202"
DAY = "2026-11-18"
LOOPBACK = ("127.0.0.1", 54321)
BASE_URL = "http://127.0.0.1:5950"
WRITE_HEADERS = {"X-LDST-Client": "1"}


class _NoRun:
    """档案端点不该起子进程；被调用就说明路由接线错了。"""

    def __call__(self, args: list[str], cwd: Path, timeout: float) -> StepResult:
        raise AssertionError(f"档案端点不应启动研究子进程：{args}")


class _NullControlPlane:
    peers: dict[str, Any] = {}


def _seed(root: Path, code: str = CODE, day: str = DAY, stage: str = "company") -> None:
    document = artifacts.build_document(
        code=code,
        day=day,
        stage=stage,
        status="ok",
        mode="structured",
        model="fake-model",
        degraded=False,
        narrative=["主营业务与产业链位置：快照未提供。"],
        notes=["一条备注。"],
        facts={"pe": 12.3},
        ai={},
        provenance={"values": {}, "sources": [], "unverified": True},
    )
    artifacts.write_stage(document, root=root)


def _client(root: Path, headers: dict[str, str] | None = None) -> TestClient:
    app = create_app(
        data_root=root,
        dist_dir=root / "no-dist",
        control_plane=_NullControlPlane(),
        research_plane=ResearchPlane(data_root=root, run=_NoRun(), command=["ldst"]),
    )
    return TestClient(app, base_url=BASE_URL, client=LOOPBACK, headers=headers or {})


@pytest.mark.parametrize("_case", [0])
def test_archive_list_is_not_swallowed_by_code_route(tmp_path: Path, _case: int) -> None:
    """`/archive/list` 必须先于 `/{code}` 注册：否则 archive 会被当成证券代码。"""
    _seed(tmp_path)
    client = _client(tmp_path)
    response = client.get("/api/research/archive/list", params={"limit": 5})
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True and "items" in payload
    assert payload["count"] == 1
    assert payload["items"][0]["code"] == CODE


def test_archive_scan_reads_injected_data_root(tmp_path: Path) -> None:
    """扫描只看注入的 data_root：空目录就是 0 份，不偷看本机 data/。"""
    client = _client(tmp_path)
    assert client.get("/api/research/archive/list").json()["count"] == 0
    _seed(tmp_path)
    payload = client.get("/api/research/archive/list").json()
    assert payload["count"] == 1
    item = payload["items"][0]
    assert item["complete"] is False
    assert item["unverified"] == ["company"]
    assert item["missing"] == ["financial", "forecast", "valuation", "risk", "report"]


def test_archive_detail_and_search(tmp_path: Path) -> None:
    _seed(tmp_path)
    client = _client(tmp_path)

    assert client.get(f"/api/research/archive/{CODE}", params={"date": DAY}).status_code == 200
    assert client.get(f"/api/research/archive/{CODE}", params={"date": "2026-11-19"}).status_code == 404
    assert client.get("/api/research/archive/000001").status_code == 404

    assert client.get("/api/research/archive/search", params={"q": "  "}).status_code == 422
    hits = client.get("/api/research/archive/search", params={"q": "主营业务"}).json()
    assert hits["count"] == 1
    match = hits["hits"][0]["matches"][0]
    assert match["stage"] == "company" and "主营业务" in match["text"]


def test_archive_export_requires_source_evidence_and_writes_markdown(tmp_path: Path) -> None:
    _seed(tmp_path)
    path = f"/api/research/archive/{CODE}/export"

    # 写动作走同一套来源闸门：没有客户端头 → 403
    assert _client(tmp_path).post(path).status_code == 403

    response = _client(tmp_path, headers=WRITE_HEADERS).post(path)
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True and payload["complete"] is False
    target = Path(payload["path"])
    assert target.is_file()
    assert target.parent == tmp_path / "research_archive" / DAY
    text = target.read_text(encoding="utf-8")
    assert text.startswith(f"# {CODE} 研究档案 · {DAY}")
    assert "## 依据链总览" in text
