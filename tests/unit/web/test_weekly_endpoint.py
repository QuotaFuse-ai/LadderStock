"""`GET /api/weekly` 的契约（QUOT-149 要求 3：页面消费的信封）。

钉住四件事：

1. 有产物 → 200 + `domain=review` / `topic=weekly_report` / `data_as_of=` 本周最后一个有数据的日子；
2. 没产物 → **不 404**，`data_source_status=missing` + `data_as_of=None`（METRICS.md §4.3：
   无数据不拿请求日期顶替数据时点）；
3. 非法 / 缺失 `week` → 400；带 `../` 的穿越标签同样 400，绝不落到文件系统；
4. 出站前过 `strip_private`，坏 JSON 不静默变空态（500）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fixtures import weekly_cache as cache

from ladderstock.ai.orchestrator import RunResult
from ladderstock.web.server import create_app
from ladderstock.weekly import facts as facts_module
from ladderstock.weekly import pipeline

WEEK = cache.WEEK
LOOPBACK = ("127.0.0.1", 54321)


def _client(root: Path) -> TestClient:
    app = create_app(data_root=root, dist_dir=root / "no-dist")
    return TestClient(app, client=LOOPBACK)


def _report(root: Path) -> dict[str, Any]:
    """事实包 + 空编排结果 → 周报文档（等价于 `ldst weekly` 的降级产物）。"""
    pack = facts_module.build_facts(WEEK, root=root)
    run = RunResult(date=WEEK)
    return pipeline.build_report(WEEK, run, pack, pipeline.section_snapshots(pack))


def _write_report(root: Path) -> Path:
    return pipeline.write_report(_report(root), WEEK, root)[0]


def _seed(tmp_path: Path) -> TestClient:
    cache.seed_week(tmp_path)
    _write_report(tmp_path)
    return _client(tmp_path)


# --------------------------------------------------------------------------- #
# 1. 有产物
# --------------------------------------------------------------------------- #

def test_returns_envelope_with_weekly_report(tmp_path: Path) -> None:
    body = _seed(tmp_path).get("/api/weekly", params={"week": WEEK}).json()

    assert body["domain"] == "review"
    assert body["topic"] == "weekly_report"
    assert body["payload"]["week"] == WEEK
    assert body["payload"]["data_source_status"] == "ok"
    # data_as_of = 本周最后一个有数据的日子（不是今天，也不是请求参数）
    assert body["data_as_of"] == cache.EXPECTED["session_days"][-1]

    weekly = body["payload"]["weekly"]
    assert weekly["week"] == WEEK
    assert weekly["range"] == cache.EXPECTED["range"]
    assert weekly["facts"]["sentiment"]["temperature"]["avg"] == pytest.approx(
        cache.EXPECTED["sentiment"]["temperature_avg"]
    )
    assert len(weekly["sections"]) == 5
    assert all(section["narrative"] for section in weekly["sections"])


def test_fact_pack_leaks_no_model_narrative(tmp_path: Path) -> None:
    body = _seed(tmp_path).get("/api/weekly", params={"week": WEEK}).json()

    dumped = json.dumps(body["payload"]["weekly"]["facts"], ensure_ascii=False)
    assert cache.CHECKPOINT_NARRATIVE not in dumped
    assert "narrative" not in dumped


# --------------------------------------------------------------------------- #
# 2. 没有产物
# --------------------------------------------------------------------------- #

def test_missing_artifact_is_not_a_404(tmp_path: Path) -> None:
    body = _client(tmp_path).get("/api/weekly", params={"week": WEEK})

    assert body.status_code == 200
    payload = body.json()
    assert payload["payload"]["data_source_status"] == "missing"
    assert payload["payload"]["weekly"] is None
    assert payload["data_as_of"] is None
    assert WEEK in payload["source_detail"]


def test_empty_week_report_is_reported_as_missing(tmp_path: Path) -> None:
    """事实包 `available=false` 的产物：照实 missing，不伪造 `data_as_of`。"""
    pack = facts_module.build_facts(WEEK, root=tmp_path)
    pipeline.write_report(_report(tmp_path), WEEK, tmp_path)
    assert pack["available"] is False

    body = _client(tmp_path).get("/api/weekly", params={"week": WEEK}).json()
    assert body["payload"]["data_source_status"] == "missing"
    assert body["data_as_of"] is None
    # 五段模板仍然在：页面不会拿到空壳
    assert len(body["payload"]["weekly"]["sections"]) == 5


# --------------------------------------------------------------------------- #
# 3. 参数：非法与穿越
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "params",
    [
        {},
        {"week": ""},
        {"week": "2026-W44x"},
        {"week": "W44"},
        {"week": "2026-10-28"},
        {"week": "2026-W00"},
        {"week": "2025-W53"},
        {"week": "../../../etc/passwd"},
        {"week": "2026-W44/../../secret"},
    ],
)
def test_illegal_week_is_400_and_touches_no_file(tmp_path: Path, params: dict[str, str]) -> None:
    response = _seed(tmp_path).get("/api/weekly", params=params)

    assert response.status_code == 400
    # 非法标签在拼路径之前就被拒：`weekly/` 里只有夹具那两份
    assert sorted(path.name for path in (tmp_path / "weekly").glob("*")) == [
        f"{WEEK}.json",
        f"{WEEK}.md",
    ]


def test_lowercase_label_is_normalized(tmp_path: Path) -> None:
    client = _seed(tmp_path)

    assert client.get("/api/weekly", params={"week": "2026-w44"}).status_code == 200


# --------------------------------------------------------------------------- #
# 4. 坏文件不静默
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("content", ["{not json", json.dumps(["不是对象"])])
def test_unreadable_artifact_is_500(tmp_path: Path, content: Any) -> None:
    _seed(tmp_path)
    (tmp_path / "weekly" / f"{WEEK}.json").write_text(content, encoding="utf-8")

    assert _client(tmp_path).get("/api/weekly", params={"week": WEEK}).status_code == 500
