"""QUOT-453：`GET /api/research/{code}/runs` 的契约测试。

报告对比要挑同一标的的两次历史运行，页面需要一份「哪些日期有产物」的清单。
口径与 `_require_research` 同源：checkpoint 或任一阶段产物存在即算一次运行；
没有产物是业务空态（200 + 空列表），未知代码仍是 404。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from ladderstock.research import artifacts, checkpoint
from ladderstock.web.server import create_app

CODE = "600000"
BASE_URL = "http://127.0.0.1:5950"
LOOPBACK = ("127.0.0.1", 54321)


class _NullControlPlane:
    peers: dict[str, Any] = {}


def _client(tmp_path: Path) -> TestClient:
    app = create_app(
        data_root=tmp_path,
        dist_dir=tmp_path / "no-dist",
        control_plane=_NullControlPlane(),
    )
    return TestClient(app, base_url=BASE_URL, client=LOOPBACK)


def _write_checkpoint(root: Path, day: str, *, code: str = CODE) -> None:
    path = checkpoint.checkpoint_path(code, day, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")


def _write_artifact(root: Path, day: str, *, code: str = CODE) -> None:
    directory = artifacts.artifact_dir(code, day, root)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "company.json").write_text("{}", encoding="utf-8")


def _runs(client: TestClient, code: str = CODE) -> Any:
    return client.get(f"/api/research/{code}/runs")


def test_empty_is_business_empty_state(tmp_path: Path) -> None:
    response = _runs(_client(tmp_path))
    assert response.status_code == 200
    assert response.json() == {"code": CODE, "count": 0, "latest": None, "dates": []}


def test_checkpoints_are_listed_newest_first(tmp_path: Path) -> None:
    for day in ("2026-10-30", "2026-11-02", "2026-10-31"):
        _write_checkpoint(tmp_path, day)
    body = _runs(_client(tmp_path)).json()
    assert body["dates"] == ["2026-11-02", "2026-10-31", "2026-10-30"]
    assert body["latest"] == "2026-11-02"
    assert body["count"] == 3


def test_artifact_only_run_counts_and_duplicates_do_not(tmp_path: Path) -> None:
    _write_checkpoint(tmp_path, "2026-11-02")
    _write_artifact(tmp_path, "2026-11-02")
    _write_artifact(tmp_path, "2026-11-03")
    body = _runs(_client(tmp_path)).json()
    assert body["dates"] == ["2026-11-03", "2026-11-02"]
    assert body["count"] == 2


def test_junk_directory_names_are_ignored(tmp_path: Path) -> None:
    _write_artifact(tmp_path, "2026-11-02")
    junk = Path(tmp_path) / artifacts.ARTIFACT_SUBDIR / CODE / "snapshots"
    junk.mkdir(parents=True, exist_ok=True)
    body = _runs(_client(tmp_path)).json()
    assert body["dates"] == ["2026-11-02"]


def test_unknown_code_is_404(tmp_path: Path) -> None:
    response = _runs(_client(tmp_path), "999999")
    assert response.status_code == 404


def test_malformed_code_is_422(tmp_path: Path) -> None:
    response = _runs(_client(tmp_path), "60000X")
    assert response.status_code == 422
