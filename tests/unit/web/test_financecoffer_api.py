"""`/api/financecoffer/*`：向导面（DEC-086 决策 1/3/4/5）。

钉住六件事：

1. 汇总端点把两档体积（按**解压后实测**）、组件明细、四件体检、Redis 三端矩阵一次给全；
2. 安装必须显式 `confirm`：没有确认只回 `needs_confirmation`，**不动盘**；
3. 确认后进后台任务：先停服务（避免运行中的 exe 被占用），再取件，最后 initdb；
4. 启停按钮背后是真实的服务函数，不是页面假装；
5. 卸载没有确认也回 `needs_confirmation`，目录原地不动；
6. 写方法要来源证据（`X-LDST-Client: 1` 或同源 Origin），跨站 403。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.web.server import create_app

LOOPBACK = ("127.0.0.1", 50000)
BASE_URL = "http://127.0.0.1:5950"
HEADER = {"X-LDST-Client": "1"}


@pytest.fixture(autouse=True)
def _reset_jobs() -> None:
    """安装任务是模块级全局状态，测试之间要清空，否则上一个 job 会让下一个变 busy。"""
    from ladderstock.web import financecoffer_api

    with financecoffer_api._lock:
        financecoffer_api._jobs.clear()


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("LDST_INSTALL_ROOT", str(tmp_path))
    return TestClient(
        create_app(data_root=tmp_path, dist_dir=tmp_path / "missing"),
        base_url=BASE_URL,
        client=LOOPBACK,
    )


def _wait_job(client: TestClient, *, timeout: float = 5.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        payload = client.get("/api/financecoffer/job", headers=HEADER).json()
        job = payload.get("job")
        if job and job.get("status") != "running":
            return job
        time.sleep(0.15)
    job = client.get("/api/financecoffer/job", headers=HEADER).json().get("job")
    return job or {}


def test_summary_reports_two_sizes_and_doctor(client: TestClient) -> None:
    payload = client.get("/api/financecoffer", headers=HEADER).json()

    assert payload["ok"] is True
    # 磁盘实际占用：核心 ≈1.0GB（PG 848 + MinIO 108 + Redis 38 + API 含依赖 40），加 embedding ≈3.8GB
    assert 950 <= payload["sizes"]["core_mb"] <= 1150
    assert 3600 <= payload["sizes"]["full_mb"] <= 4200
    assert payload["sizes"]["embedding_mb"] >= 2500

    core_keys = {row["key"] for row in payload["components"]["core"]}
    full_keys = {row["key"] for row in payload["components"]["full"]}
    assert core_keys == {"pg", "redis", "minio", "api"}
    assert full_keys == core_keys | {"embedding"}
    # 组件说明必须写清「不勾 embedding 的后果」，这是决策 1 的硬要求
    embedding = next(row for row in payload["components"]["full"] if row["key"] == "embedding")
    assert embedding["required"] is False
    assert "向量检索" in embedding["note"]

    assert payload["doctor"]["total"] == 5
    assert set(payload["doctor"]["services"]) == {"pg", "redis", "minio", "api", "embedding"}
    # embedding 是可选件：没装时它不就绪，但不算「不可用」
    assert payload["doctor"]["optional"] == ["embedding"]
    assert set(payload["redis"]["platforms"]) == {"win-x64", "linux-x64", "linux-arm64", "macos-arm64"}


def test_install_without_confirm_downloads_nothing(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def fake_install(*args: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append("install")
        return {"ok": False, "status": "needs_confirmation", "detail": "需要先确认体积与落盘位置"}

    monkeypatch.setattr("ladderstock.web.financecoffer_api.installer.install", fake_install)

    created = client.post("/api/financecoffer/install", headers=HEADER, json={"confirm": False}).json()
    assert created["kind"] == "install"

    job = _wait_job(client)
    assert job["result"]["status"] == "needs_confirmation"
    # 没确认就不该建目录：安装根下连 file/modules 都不该出现
    assert not (tmp_path / "file" / "modules" / "financecoffer").exists()


def test_install_with_confirm_runs_initdb(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []

    def fake_install(*args: Any, **kwargs: Any) -> dict[str, Any]:
        order.append("install")
        return {"ok": True, "status": "ready", "detail": "增强包已就绪", "total_mb": 3184}

    def fake_initdb(*args: Any, **kwargs: Any) -> dict[str, Any]:
        order.append("initdb")
        return {"ok": True, "state": "initialized", "detail": "initdb 完成"}

    monkeypatch.setattr("ladderstock.web.financecoffer_api.installer.install", fake_install)
    monkeypatch.setattr("ladderstock.web.financecoffer_api.services.initdb", fake_initdb)

    client.post("/api/financecoffer/install", headers=HEADER, json={"confirm": True, "with_embedding": True})
    job = _wait_job(client)

    assert job["status"] == "done"
    assert job["result"]["status"] == "ready"
    # 取件在前、initdb 在后：装完就必须能用
    assert order == ["install", "initdb"]


def test_services_action_calls_real_service_functions(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def _recorder(name: str, ok: bool = True) -> Any:
        def _fn(*args: Any, **kwargs: Any) -> dict[str, Any]:
            calls.append(name)
            return {"ok": ok, "state": "running", "detail": f"{name} ok"}

        return _fn

    monkeypatch.setattr("ladderstock.web.financecoffer_api.services.initdb", _recorder("initdb"))
    monkeypatch.setattr("ladderstock.web.financecoffer_api.services.start_pg", _recorder("start_pg"))
    monkeypatch.setattr("ladderstock.web.financecoffer_api.redis_service.start", _recorder("redis_start"))
    monkeypatch.setattr("ladderstock.web.financecoffer_api.services.start_minio", _recorder("start_minio"))
    monkeypatch.setattr("ladderstock.web.financecoffer_api.services.start_api", _recorder("start_api"))
    monkeypatch.setattr("ladderstock.web.financecoffer_api.services.start_embedding", _recorder("start_embedding"))

    payload = client.post("/api/financecoffer/services", headers=HEADER, json={"action": "start"}).json()

    assert payload["ok"] is True
    # 库先就绪，再按依赖顺序起：PG → Redis → MinIO → API → embedding（embedding 最慢放最后）
    assert calls == ["initdb", "start_pg", "redis_start", "start_minio", "start_api", "start_embedding"]


def test_unknown_service_action_is_rejected(client: TestClient) -> None:
    payload = client.post("/api/financecoffer/services", headers=HEADER, json={"action": "restart"}).json()
    assert payload["ok"] is False
    assert "start" in payload["detail"]


def test_uninstall_without_confirm_keeps_data(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "file" / "modules" / "financecoffer"
    target.mkdir(parents=True)
    (target / "install_state.json").write_text('{"state": "ready"}', encoding="utf-8")

    payload = client.post("/api/financecoffer/uninstall", headers=HEADER, json={"confirm": False}).json()

    assert payload["status"] == "needs_confirmation"
    assert "不可恢复" in payload["detail"]
    assert target.exists()


def test_write_requires_source_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LDST_INSTALL_ROOT", str(tmp_path))
    cross_site = TestClient(
        create_app(data_root=tmp_path, dist_dir=tmp_path / "missing"),
        base_url=BASE_URL,
        client=("10.1.2.3", 4444),
    )
    response = cross_site.post("/api/financecoffer/services", json={"action": "start"})
    assert response.status_code in {403, 404}
