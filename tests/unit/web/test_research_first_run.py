"""QUOT-561：网页「开始研究」要能冷启动——从没有研究记录的代码不再 404。

口径钉三件事：

1. 空盘 + 合法代码 + `only=第一段` → 200 `{job_id, status}`，任务真的跑完（argv 等价
   `ldst research <code> --date <day> --only <stage>`），不伪造产物；页面「开始研究」
   带上今天、而今天还没有产物时同样起跑——否则 GET 说「没有」、POST 却 404；
2. 未知代码（格式合法、前缀不可能是 A 股个股）仍然 404——放宽的不是代码校验；
3. 有历史、但显式 `date` 那天没有研究 → 仍 404，「GET 说没有、POST 却起了任务」的
   旧病不复发；`only` 非法仍 422。
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from ladderstock.data.provider_registry import SourceUnavailable
from ladderstock.research import checkpoint
from ladderstock.web.control import StepResult
from ladderstock.web.research import ResearchPlane
from ladderstock.web.server import create_app

CODE = "600000"
LOOPBACK = ("127.0.0.1", 54321)
WRITE_HEADERS = {"X-LDST-Client": "1"}
BASE_URL = "http://127.0.0.1:5950"
FIRST_STAGE = checkpoint.STAGES[0]


class FakeRun:
    """假子进程：记 argv、返回固定退出码，不碰网络、不落产物。"""

    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], cwd: Path, timeout: float) -> StepResult:
        self.calls.append(list(args))
        return StepResult(self.returncode, "")


class _NullControlPlane:
    peers: dict[str, Any] = {}


class _OfflineRegistry:
    def fetch_live(self, dataset: str, **kwargs: Any) -> Any:
        raise SourceUnavailable("离线用例")


def _client(root: Path, run: FakeRun) -> TestClient:
    plane = ResearchPlane(
        data_root=root, run=run, command=["ldst"], registry=_OfflineRegistry()
    )
    app = create_app(
        data_root=root,
        dist_dir=root / "no-dist",
        control_plane=_NullControlPlane(),
        research_plane=plane,
    )
    return TestClient(app, base_url=BASE_URL, client=LOOPBACK)


def _post(client: TestClient, code: str, body: dict[str, Any]) -> Any:
    return client.post(f"/api/research/{code}/run", json=body, headers=WRITE_HEADERS)


def _wait_job(client: TestClient, job_id: str, *, timeout: float = 10.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        payload = client.get(f"/api/research/jobs/{job_id}").json()
        if payload["status"] not in {"queued", "running"}:
            return payload
        assert time.monotonic() < deadline, f"任务没有结束：{payload}"
        time.sleep(0.02)


def test_first_run_on_empty_root_starts_and_finishes(tmp_path: Path) -> None:
    run = FakeRun()
    client = _client(tmp_path, run)

    response = _post(client, CODE, {"only": FIRST_STAGE})

    assert response.status_code == 200, response.text
    ack = response.json()
    assert set(ack) == {"job_id", "status"}
    assert ack["status"] == "queued"

    job = _wait_job(client, ack["job_id"])
    assert job["status"] == "ok", job
    assert job["stage"] == FIRST_STAGE
    assert len(run.calls) == 1
    argv = run.calls[0]
    assert argv[:2] == ["ldst", "research"]
    assert argv[2] == CODE
    assert argv[argv.index("--only") + 1] == FIRST_STAGE
    # 空盘缺省日期取今天（口径见 `parse_day(allow_empty=True)`），不拿历史日冒充
    assert len(argv[argv.index("--date") + 1]) == 10


def test_first_run_with_frontend_today_date_starts(tmp_path: Path) -> None:
    """报告页空态会把今天作为 `date` 一起提交（GET 用的就是今天）：这条要能起跑。"""
    run = FakeRun()
    client = _client(tmp_path, run)
    today = datetime.now().date().isoformat()

    response = _post(client, CODE, {"date": today, "only": FIRST_STAGE})

    assert response.status_code == 200, response.text
    job = _wait_job(client, response.json()["job_id"])
    assert job["status"] == "ok"
    assert run.calls[0][run.calls[0].index("--date") + 1] == today


def test_unknown_code_still_404(tmp_path: Path) -> None:
    response = _post(_client(tmp_path, FakeRun()), "999999", {"only": FIRST_STAGE})

    assert response.status_code == 404
    assert "999999" in response.json()["detail"]


def test_history_present_but_day_missing_still_404(tmp_path: Path) -> None:
    # 有历史（某天 checkpoint 在），但请求另一天 → 与 GET 同口径 404，不许凭空起任务
    document = checkpoint.fresh(CODE, "2026-10-30")
    document["stages"] = {
        name: {"status": "done", "mode": "live", "degraded": False}
        for name in checkpoint.STAGES
    }
    checkpoint.save(document, tmp_path)
    run = FakeRun()

    response = _post(_client(tmp_path, run), CODE, {"date": "2026-10-29", "only": FIRST_STAGE})

    assert response.status_code == 404, response.text
    assert run.calls == []


def test_bad_stage_still_422(tmp_path: Path) -> None:
    response = _post(_client(tmp_path, FakeRun()), CODE, {"only": "not-a-stage"})

    assert response.status_code == 422
