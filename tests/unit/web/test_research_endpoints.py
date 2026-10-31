"""QUOT-275 / SPEC-RESEARCH-PAGES §2：`/api/research/*` 三接口的契约测试。

四条错误语义一条不许含糊（原单：前端已按此写死，不能改）：

1. 正常 → 六段 + 各段 JSON/Markdown 产物，键集合照 SPEC；
2. 研究不存在 → 404；
3. 同标的同日已有任务在跑 → 409；
4. 参数非法（代码 / 日期 / `only`）→ 422。

另外钉住：续跑任务的 argv 等价于 `ldst research <code> --date <date> --only <stage>`、
进度接口按页面的 1s 轮询可用、闸门同控制面、`LDST_CONTROL_ENABLED=0` 不影响研究读写。
端到端那条走真实产物目录（`artifacts.write_stage` 落盘 + 真实 FastAPI 读回），不 mock 文件层。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.core.boundary import Provenance, assert_provenance
from ladderstock.research import artifacts, checkpoint
from ladderstock.web.control import CONTROL_ENABLED_ENV, StepResult
from ladderstock.web.research import ResearchPlane
from ladderstock.web.server import create_app

CODE = "600000"
DAY = "2026-10-30"
LOOPBACK = ("127.0.0.1", 54321)
REMOTE = ("10.1.2.3", 54321)
WRITE_HEADERS = {"X-LDST-Client": "1"}
BASE_URL = "http://127.0.0.1:5950"

#: SPEC §2.1 的顶层键（一个不多一个不少）
STATUS_KEYS = {
    "code",
    "date",
    "available",
    "complete",
    "checkpoint",
    "updated_at",
    "stages",
    "pending",
    "failed",
    "source_status",
    "reason",
    "data_source_status",
}
#: SPEC §2.1 每段的键
STAGE_KEYS = {"name", "title", "status", "mode", "degraded", "artifact"}
#: SPEC §2.3 的键
JOB_KEYS = {"id", "status", "stage", "progress", "step", "error"}

#: 「跑过五段、report 待补」的常用形态
FIVE_DONE = checkpoint.STAGES[:5]


# --------------------------------------------------------------------------- #
# 造盘上真实形态的产物
# --------------------------------------------------------------------------- #

def write_stage_artifact(
    root: Path,
    stage: str,
    *,
    code: str = CODE,
    day: str = DAY,
    degraded: bool = False,
) -> dict[str, Any]:
    """按 R3 的真实形状落一对产物（`<stage>.json` + `<stage>.md`），角标由程序补。"""
    facts = {"pe": 18.4, "revenue_yoy": 37.2}
    narrative = [f"{checkpoint.STAGE_TITLES[stage]}：收入同比 +37.2%，PE 18.4 倍。"]
    records = {
        metric: Provenance(
            value=value,
            unit="倍" if metric == "pe" else "%",
            source="tickhub-cn",
            endpoint=f"{metric}_endpoint",
            fetched_at=f"{day}T09:00:00+08:00",
            as_of="2026-09-30",
            url="http://127.0.0.1:8000/mcp",
            verified=True,
            metric=metric,
        )
        for metric, value in facts.items()
    }
    document = artifacts.build_document(
        code=code,
        day=day,
        stage=stage,
        status="degraded" if degraded else "ok",
        mode="text" if degraded else "structured",
        model="fake-model",
        degraded=degraded,
        narrative=narrative,
        notes=[],
        facts=facts,
        ai={"narrative": narrative, "degraded": degraded},
        provenance=artifacts.provenance_slot(assert_provenance(facts, records), stage=stage),
    )
    artifacts.write_stage(document, root)
    return document


def seed(
    root: Path,
    done: tuple[str, ...] = checkpoint.STAGES,
    *,
    failed: tuple[str, ...] = (),
    code: str = CODE,
    day: str = DAY,
) -> None:
    """落 checkpoint + 已完成段的产物：与 `ldst research` 跑完后的目录形状一致。"""
    document = checkpoint.fresh(code, day)
    for stage in checkpoint.STAGES:
        if stage in failed:
            checkpoint.record(
                document,
                stage,
                status="failed",
                mode="failed",
                narrative=[],
                failure={"kind": "network", "message": "连接超时", "retryable": True},
            )
        elif stage in done:
            write_stage_artifact(root, stage, code=code, day=day)
            checkpoint.record(
                document,
                stage,
                status="ok",
                mode="structured",
                narrative=["…"],
                model="fake-model",
                degraded=False,
            )
    checkpoint.save(document, root)


def mark_degraded(root: Path, stage: str) -> None:
    """把一段改成真实形态的降级产物（`mode=text` + `degraded=true`）。"""
    write_stage_artifact(root, stage, degraded=True)
    document = checkpoint.load(CODE, DAY, root)
    checkpoint.record(
        document, stage, status="degraded", mode="text", narrative=["纯文本降级"], degraded=True
    )
    checkpoint.save(document, root)


# --------------------------------------------------------------------------- #
# 客户端 / 假子进程
# --------------------------------------------------------------------------- #

class FakeRun:
    """假子进程：记录 argv，返回预设退出码；`gate` 不为 None 时阻塞在闸门上。"""

    def __init__(self, returncode: int = 0, output: str = "", gate: Any | None = None) -> None:
        self.returncode = returncode
        self.output = output
        self.gate = gate
        self.calls: list[list[str]] = []
        self.started = threading.Event()

    def __call__(self, args: list[str], cwd: Path, timeout: float) -> StepResult:
        self.calls.append(list(args))
        self.started.set()
        if self.gate is not None:
            self.gate.wait(timeout=10.0)
        return StepResult(self.returncode, self.output)


class _NullControlPlane:
    """占位：这些用例只关心研究接口，不构造真实控制面（peer 目录是整机路径）。"""

    peers: dict[str, Any] = {}


def _plane(root: Path, run: Any) -> ResearchPlane:
    return ResearchPlane(data_root=root, run=run, command=["ldst"])


def _client(
    root: Path,
    run: Any = None,
    *,
    client: tuple[str, int] = LOOPBACK,
    headers: dict[str, str] | None = None,
) -> TestClient:
    app = create_app(
        data_root=root,
        dist_dir=root / "no-dist",
        control_plane=_NullControlPlane(),
        research_plane=_plane(root, run if run is not None else FakeRun()),
    )
    return TestClient(app, base_url=BASE_URL, client=client, headers=headers or {})


def _status(client: TestClient, code: str = CODE, day: str | None = DAY) -> dict[str, Any]:
    params = {} if day is None else {"date": day}
    return client.get(f"/api/research/{code}", params=params).json()


def _run(client: TestClient, body: dict[str, Any], **headers: str) -> Any:
    return client.post(
        f"/api/research/{CODE}/run", json=body, headers={**WRITE_HEADERS, **headers}
    )


def _wait_job(client: TestClient, job_id: str, *, timeout: float = 10.0) -> dict[str, Any]:
    """按页面的轮询节奏取进度，直到离开 `queued / running`（比 1s 快，只为跑得快）。"""
    deadline = time.monotonic() + timeout
    while True:
        payload = client.get(f"/api/research/jobs/{job_id}").json()
        if payload["status"] not in {"queued", "running"}:
            return payload
        assert time.monotonic() < deadline, f"任务没有结束：{payload}"
        time.sleep(0.02)


# --------------------------------------------------------------------------- #
# 1. 正常：六段 + 各段产物
# --------------------------------------------------------------------------- #

def test_status_returns_six_stages_with_json_and_markdown(tmp_path: Path) -> None:
    seed(tmp_path)
    client = _client(tmp_path)

    response = client.get(f"/api/research/{CODE}", params={"date": DAY})

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == STATUS_KEYS
    assert payload["code"] == CODE
    assert payload["date"] == DAY
    assert payload["available"] is True
    assert payload["complete"] is True
    assert payload["source_status"] == "ok"
    assert payload["reason"] is None
    assert payload["pending"] == [] and payload["failed"] == []
    assert payload["updated_at"], "读到的状态必须说明是什么时候跑的"
    assert Path(payload["checkpoint"]).name == f"{DAY}.json"
    assert Path(payload["checkpoint"]).parent.name == CODE
    assert "research_checkpoint" in payload["checkpoint"]

    assert [stage["name"] for stage in payload["stages"]] == list(checkpoint.STAGES)
    for stage in payload["stages"]:
        assert set(stage) == STAGE_KEYS
        assert stage["status"] == "ok" and stage["degraded"] is False
        artifact = stage["artifact"]
        assert set(artifact) == {"document", "markdown"}
        assert artifact["document"]["stage"] == stage["name"]
        # 出处三键固定形状；Markdown 由程序渲染，文末列出落点
        assert set(artifact["document"]["provenance"]) == {"values", "sources", "unverified"}
        assert f"# {stage['title']}" in artifact["markdown"]
        assert "## 来源" in artifact["markdown"]


def test_status_names_the_missing_stages_when_incomplete(tmp_path: Path) -> None:
    seed(tmp_path, done=FIVE_DONE, failed=("valuation",))

    payload = _status(_client(tmp_path))

    assert payload["available"] is True and payload["complete"] is False
    assert payload["source_status"] == "partial"
    assert payload["data_source_status"] == "partial"
    assert payload["failed"] == ["valuation"]
    assert payload["pending"] == ["report"]
    assert payload["reason"] == "六段未完成，缺：valuation、report"
    by_name = {stage["name"]: stage for stage in payload["stages"]}
    assert by_name["valuation"]["status"] == "failed"
    assert by_name["report"]["status"] == "pending" and by_name["report"]["artifact"] is None


def test_status_marks_source_degraded_when_a_stage_degraded(tmp_path: Path) -> None:
    seed(tmp_path)
    mark_degraded(tmp_path, "valuation")

    payload = _status(_client(tmp_path))

    assert payload["complete"] is True  # 降级段仍算跑过（DONE_STATUSES 同口径）
    assert payload["source_status"] == "degraded"
    stage = next(item for item in payload["stages"] if item["name"] == "valuation")
    assert stage["status"] == "degraded" and stage["degraded"] is True
    assert stage["artifact"]["document"]["degraded"] is True


def test_status_without_date_uses_latest_run_day(tmp_path: Path) -> None:
    seed(tmp_path, day="2026-10-28")
    seed(tmp_path, day=DAY)

    payload = _status(_client(tmp_path), day=None)

    assert payload["date"] == DAY
    assert payload["available"] is True


def test_status_has_no_side_effects(tmp_path: Path) -> None:
    seed(tmp_path)
    before = sorted(p.name for p in (tmp_path / "research" / CODE / DAY).iterdir())
    _status(_client(tmp_path))
    assert sorted(p.name for p in (tmp_path / "research" / CODE / DAY).iterdir()) == before


# --------------------------------------------------------------------------- #
# 2. 404：研究不存在 / 任务未知
# --------------------------------------------------------------------------- #

def test_status_404_when_research_absent(tmp_path: Path) -> None:
    response = _client(tmp_path).get("/api/research/600001", params={"date": DAY})

    assert response.status_code == 404
    detail = response.json()["detail"]
    assert "600001" in detail and f"ldst research 600001 --date {DAY}" in detail
    assert not (tmp_path / "research").exists(), "读取接口不允许留下任何副作用"


def test_status_404_when_code_has_no_record_and_no_date(tmp_path: Path) -> None:
    assert _client(tmp_path).get(f"/api/research/{CODE}").status_code == 404


def test_run_404_when_research_absent(tmp_path: Path) -> None:
    run = FakeRun()
    client = _client(tmp_path, run)

    response = _run(client, {"date": DAY, "only": "valuation"})

    assert response.status_code == 404
    assert run.calls == [], "研究不存在时不能起任务"
    assert not (tmp_path / "research").exists()


def test_job_progress_404_for_unknown_job(tmp_path: Path) -> None:
    assert _client(tmp_path).get("/api/research/jobs/nope").status_code == 404


# --------------------------------------------------------------------------- #
# 3. 409：同标的同日已有任务在跑
# --------------------------------------------------------------------------- #

def test_run_409_while_same_code_and_day_is_running(tmp_path: Path) -> None:
    gate = threading.Event()
    run = FakeRun(gate=gate)
    seed(tmp_path, done=FIVE_DONE, failed=("report",))
    client = _client(tmp_path, run)

    try:
        first = _run(client, {"date": DAY, "only": "report"})
        assert first.status_code == 200
        assert run.started.wait(timeout=5.0), "任务在后台跑，不阻塞响应"

        second = _run(client, {"date": DAY, "only": "valuation"})
        assert second.status_code == 409
        detail = second.json()["detail"]
        assert "已有研究任务在跑" in detail and first.json()["job_id"] in detail

        # 同标的**不同日**是另一份研究，不算撞车
        seed(tmp_path, day="2026-10-29", done=FIVE_DONE, failed=("report",))
        assert _run(client, {"date": "2026-10-29", "only": "report"}).status_code == 200
    finally:
        gate.set()


def test_run_409_releases_the_slot_when_the_job_finishes(tmp_path: Path) -> None:
    seed(tmp_path, done=FIVE_DONE, failed=("report",))
    client = _client(tmp_path, FakeRun(0))

    first = _run(client, {"date": DAY, "only": "report"})
    assert first.status_code == 200
    assert _wait_job(client, first.json()["job_id"])["status"] == "ok"

    # 上一段跑完了，同一标的同一天可以再起一段
    assert _run(client, {"date": DAY, "only": "valuation"}).status_code == 200


# --------------------------------------------------------------------------- #
# 4. 422：参数非法
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    ("body", "hint"),
    [
        ({"date": DAY, "only": "everything"}, "六段"),
        ({"date": DAY}, "六段"),
        ({"date": DAY, "only": None}, "六段"),
        ({"date": DAY, "only": ""}, "六段"),
        ({"date": DAY, "only": "Report"}, "六段"),
        ({"date": "20261030", "only": "valuation"}, "YYYY-MM-DD"),
        ({"date": "not-a-date", "only": "valuation"}, "YYYY-MM-DD"),
        ({"date": "2026-13-01", "only": "valuation"}, "不是合法日期"),
    ],
)
def test_run_422_on_bad_parameters(tmp_path: Path, body: dict[str, Any], hint: str) -> None:
    run = FakeRun()
    seed(tmp_path, done=FIVE_DONE, failed=("report",))
    client = _client(tmp_path, run)

    response = _run(client, body)

    assert response.status_code == 422, response.text
    assert hint in response.json()["detail"]
    assert run.calls == []


def test_run_422_when_only_is_not_a_string(tmp_path: Path) -> None:
    """非字符串的 `only` 由 FastAPI 的请求体校验挡下，同样落 422（不是 500）。"""
    seed(tmp_path, done=FIVE_DONE, failed=("report",))
    run = FakeRun()
    response = _run(_client(tmp_path, run), {"date": DAY, "only": 7})
    assert response.status_code == 422
    assert run.calls == []


@pytest.mark.parametrize(
    ("code", "day"),
    [
        ("12345", DAY),
        ("600000SH", DAY),
        ("600000X", DAY),
        ("60-0000", DAY),
        (CODE, "2026/10/30"),
        (CODE, "2026-13-40"),
        (CODE, "20261030"),
    ],
)
def test_status_422_on_bad_code_or_date(tmp_path: Path, code: str, day: str) -> None:
    seed(tmp_path)
    response = _client(tmp_path).get(f"/api/research/{code}", params={"date": day})
    assert response.status_code == 422, response.text
    assert _status_detail(response).startswith(("非法证券代码", "date"))


def _status_detail(response: Any) -> str:
    detail = response.json()["detail"]
    return detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# 续跑：argv 等价 CLI + 进度接口
# --------------------------------------------------------------------------- #

def test_run_invokes_the_equivalent_cli_and_reports_progress(tmp_path: Path) -> None:
    seed(tmp_path, done=FIVE_DONE, failed=("report",))
    run = FakeRun(0, '{"ok": true}')
    client = _client(tmp_path, run)

    response = _run(client, {"date": DAY, "only": "report"})

    assert response.status_code == 200
    assert set(response.json()) == {"job_id", "status"}
    assert response.json()["status"] == "queued"
    job_id = response.json()["job_id"]

    progress = _wait_job(client, job_id)
    assert set(progress) == JOB_KEYS
    assert progress["id"] == job_id
    assert progress["status"] == "ok"
    assert progress["stage"] == "report"
    assert progress["progress"] == 1.0
    assert progress["error"] is None

    argv = run.calls[0]
    assert argv[0] == "ldst"
    assert argv[1:] == [
        "research",
        CODE,
        "--date",
        DAY,
        "--only",
        "report",
        "--output",
        "json",
    ], "续跑必须等价于 CLI 的单段续跑"


def test_run_uses_today_when_date_omitted(tmp_path: Path) -> None:
    from datetime import date

    today = date.today().isoformat()
    seed(tmp_path, done=FIVE_DONE, failed=("report",), day=today)
    run = FakeRun(0)
    client = _client(tmp_path, run)

    response = _run(client, {"only": "report"})

    assert response.status_code == 200
    assert run.calls[0][run.calls[0].index("--date") + 1] == today


def test_run_marks_job_degraded_on_exit_code_9(tmp_path: Path) -> None:
    seed(tmp_path, done=FIVE_DONE, failed=("report",))
    client = _client(tmp_path, FakeRun(9, "AI 降级完成（退出码 9）"))

    job_id = _run(client, {"date": DAY, "only": "report"}).json()["job_id"]

    progress = _wait_job(client, job_id)
    assert progress["status"] == "degraded"
    assert progress["error"] is None


def test_run_failure_keeps_cli_message_as_job_error(tmp_path: Path) -> None:
    seed(tmp_path, done=FIVE_DONE, failed=("report",))
    client = _client(tmp_path, FakeRun(3, "缺少 config/ai.yaml：先配置 AI 再跑研究"))

    job_id = _run(client, {"date": DAY, "only": "report"}).json()["job_id"]

    progress = _wait_job(client, job_id)
    assert progress["status"] == "failed"
    assert "config/ai.yaml" in progress["error"] and "退出码 3" in progress["error"]


# --------------------------------------------------------------------------- #
# 端到端：真实产物目录 → 起任务补段 → 读回可渲染的整页载荷
# --------------------------------------------------------------------------- #

def test_end_to_end_rerun_lands_real_artifacts_and_page_can_render(tmp_path: Path) -> None:
    """`run` 位顶掉子进程，落盘走真实的 `artifacts.write_stage` + `checkpoint.record`。

    验的是「读」与「写」共用同一份目录形状：续跑完成后整页从部分失败态回到六段齐备，
    report 段的 Markdown 带程序补的引用来源。
    """
    seed(tmp_path, done=FIVE_DONE, failed=("report",))

    def complete_report(args: list[str], cwd: Path, timeout: float) -> StepResult:
        assert args[args.index("--only") + 1] == "report"
        write_stage_artifact(tmp_path, "report")
        document = checkpoint.load(CODE, DAY, tmp_path)
        checkpoint.record(
            document,
            "report",
            status="ok",
            mode="structured",
            narrative=["报告合成完成。"],
            model="fake-model",
            degraded=False,
        )
        checkpoint.save(document, tmp_path)
        return StepResult(0, '{"ok": true}')

    client = _client(tmp_path, complete_report)

    before = _status(client)
    assert before["complete"] is False and before["failed"] == ["report"]

    job_id = _run(client, {"date": DAY, "only": "report"}).json()["job_id"]
    assert _wait_job(client, job_id)["status"] == "ok"

    after = _status(client)
    assert after["complete"] is True and after["source_status"] == "ok"
    report = next(stage for stage in after["stages"] if stage["name"] == "report")
    assert report["artifact"]["document"]["facts"]["pe"] == 18.4
    assert "## 来源" in report["artifact"]["markdown"]
    assert (tmp_path / "research" / CODE / DAY / "report.json").is_file()


# --------------------------------------------------------------------------- #
# 闸门与开关
# --------------------------------------------------------------------------- #

def test_non_loopback_source_is_403(tmp_path: Path) -> None:
    seed(tmp_path)
    remote = _client(tmp_path, client=REMOTE)
    assert remote.get(f"/api/research/{CODE}", params={"date": DAY}).status_code == 403


def test_post_without_source_evidence_is_403(tmp_path: Path) -> None:
    seed(tmp_path, done=FIVE_DONE, failed=("report",))
    assert _client(tmp_path).post(
        f"/api/research/{CODE}/run", json={"date": DAY, "only": "report"}
    ).status_code == 403


def test_cross_origin_post_is_403_even_with_client_header(tmp_path: Path) -> None:
    """QUOT-178 同口径：跨站 Origin 不会被自定义头救回来。"""
    seed(tmp_path, done=FIVE_DONE, failed=("report",))
    client = _client(tmp_path, headers={"Origin": "https://evil.example"})
    response = client.post(
        f"/api/research/{CODE}/run",
        json={"date": DAY, "only": "report"},
        headers=WRITE_HEADERS,
    )
    assert response.status_code == 403


def test_same_origin_page_can_read_and_run(tmp_path: Path) -> None:
    seed(tmp_path, done=FIVE_DONE, failed=("report",))
    client = _client(tmp_path, FakeRun(0), headers={"Origin": BASE_URL})
    assert client.get(f"/api/research/{CODE}", params={"date": DAY}).status_code == 200
    assert client.post(
        f"/api/research/{CODE}/run", json={"date": DAY, "only": "report"}
    ).status_code == 200


def test_control_switch_off_keeps_research_readable_and_runnable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """研究读写属于研究面：关掉控制面不能让 R8 两页变空态（DEC-028）。"""
    seed(tmp_path, done=FIVE_DONE, failed=("report",))
    client = _client(tmp_path, FakeRun(0))
    monkeypatch.setenv(CONTROL_ENABLED_ENV, "0")

    assert client.get("/api/control/peers").status_code == 404
    assert client.get(f"/api/research/{CODE}", params={"date": DAY}).status_code == 200
    assert _run(client, {"date": DAY, "only": "report"}).status_code == 200
