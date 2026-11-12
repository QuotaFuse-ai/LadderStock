"""16:30 自选/持仓档案定期刷新（QUOT-568，kind=`knowledge_refresh`）。

钉住四件事：

1. 注册面：schema / 结构自检 / 内置默认表 / 仓库 `schedule.yaml` 都认
   `knowledge_refresh`，`daily_limit` 只收正整数；任务在 `ldst schedule list`
   的表里看得见，`enabled: false` 就能关掉；
2. 集合面：关注票 = 自选 ∪ 持仓（重放后数量 > 0），自选在前、同一代码只算一次；
3. 落库面：逐只跑研究，产物只经既有 `store.archive_run()` 出口落
   `data/knowledge/`；跑挂的票记原因、不伪造档案；`manifest.json` 与关注票对齐；
4. 告警面：覆盖率 = 有有效档案票数 / 关注票数，低于阈值记一条收件箱问题项
   （同日去重）并弹自愈同款 Toast；告警失败只降级、不掩盖刷新结果。

研究的 AI 侧全部用脚本化假 ChatClient（不拨网络），`root` 指向 tmp_path。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from ladderstock.cli import schedule as schedule_cli
from ladderstock.cli.schedule import (
    DEFAULT_JOBS,
    DEFAULT_KNOWLEDGE_THRESHOLD,
    _default_table,
    _structural_problems,
    knowledge_coverage,
    knowledge_universe,
    load_schedule,
    run_job,
    validate_table,
)
from ladderstock.journal import inbox as inbox_store
from ladderstock.journal import portfolio, watchlist
from ladderstock.knowledge import model, store
from ladderstock.knowledge.recall import recall
from ladderstock.research.pipeline import ResearchPipeline
from ladderstock.supervisor import notify

#: 2026-11-04 是周三；16:30 触发。
DAY = "2026-11-04"
MOMENT = datetime(2026, 11, 4, 16, 30, 0)
URL_ENV = "TEST_KNOWLEDGE_REFRESH_BASE_URL"
KEY_ENV = "TEST_KNOWLEDGE_REFRESH_API_KEY"

REFRESH_JOB: dict[str, Any] = {
    "id": "knowledge_refresh",
    "name": "16:30 自选/持仓档案刷新",
    "kind": "knowledge_refresh",
    "trigger": "16:30",
    "enabled": True,
}


# --------------------------------------------------------------------------- #
# 研究的 AI 侧假实现：按「段落标识」返回可解析 JSON，`fail_on` 里的段抛错
# --------------------------------------------------------------------------- #


class FakeClient:
    def __init__(self, *, fail_on: tuple[str, ...] = ()) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail_on = set(fail_on)

    def complete(self, model: str, system: str, user: str, *, json_mode: bool) -> str:
        from ladderstock.research import checkpoint

        code = next(
            line.split("：", 1)[1] for line in user.splitlines() if line.startswith("证券代码：")
        )
        stage = next(
            line.split("：", 1)[1] for line in user.splitlines() if line.startswith("段落标识：")
        )
        self.calls.append((code, stage))
        if (code, stage) in self.fail_on or stage in self.fail_on:
            raise RuntimeError(f"{code} {stage} 段连不上")
        title = checkpoint.STAGE_TITLES[stage]
        return json.dumps(
            {
                "narrative": [f"{title}：假模型叙述，只引用给定快照。"],
                "highlights": [f"{title}段可复核的定性结论"],
            },
            ensure_ascii=False,
        )

    @property
    def models(self) -> list[str]:
        return ["fake-model"]


@pytest.fixture(autouse=True)
def _configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(URL_ENV, "http://127.0.0.1:1/v1")
    monkeypatch.setenv(KEY_ENV, "test-key-not-real")


def _ai_config_root(tmp_path: Path) -> Path:
    """把研究用到的 AI 配置钉到本用例的假环境（不读仓库 `config/ai.yaml`）。"""
    root = tmp_path / "config"
    root.mkdir(exist_ok=True)
    (root / "ai.yaml").write_text(
        "\n".join(
            [
                "ai:",
                "  default_model: fake-model",
                "  fallback_chain: []",
                f"  base_url_env: {URL_ENV}",
                f"  api_key_env: {KEY_ENV}",
                "  structured_output: prefer",
                "  on_all_failed: degrade_to_text",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture()
def wired(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeClient:
    """让 `schedule._refresh_one` 用假 ChatClient + 本用例的 AI 配置根。"""
    client = FakeClient()
    kwargs: dict[str, Any] = {"client": client, "config_root": _ai_config_root(tmp_path)}
    _patch_pipeline(monkeypatch, kwargs)
    return client


def _patch_pipeline(monkeypatch: pytest.MonkeyPatch, overrides: dict[str, Any]) -> None:
    """替换管线构造的注入项；`config_root` 默认指本用例的假 AI 配置。"""
    original = ResearchPipeline.__init__

    def _init(self: ResearchPipeline, code: Any, **kwargs: Any) -> None:
        original(self, code, **{**kwargs, **overrides})

    monkeypatch.setattr(ResearchPipeline, "__init__", _init)


@pytest.fixture()
def toasts(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """截下 Toast：Windows 通知栏在测试机上不该真弹。"""
    sent: list[dict[str, Any]] = []

    def _failure(self: Any, **kwargs: Any) -> None:
        sent.append(kwargs)

    monkeypatch.setattr(notify.ToastSink, "failure", _failure)
    return sent


def _watch(*codes: str, root: Path) -> None:
    for code in codes:
        watchlist.add_watch(code, root=root)


def _hold(code: str, *, root: Path, shares: float = 100.0) -> None:
    portfolio.add_trade(
        {
            "symbol": code,
            "name": "",
            "type": "buy",
            "trade_date": "2026-10-30",
            "shares": shares,
            "price": 10.0,
            "fee": 0,
        },
        root=root,
    )


def _refresh(root: Path, **job_overrides: Any) -> dict[str, Any]:
    job = {**REFRESH_JOB, **job_overrides}
    return run_job(job, now=MOMENT, timezone="Asia/Shanghai", root=root)


# --------------------------------------------------------------------------- #
# 1. 注册面
# --------------------------------------------------------------------------- #


def test_builtin_defaults_declare_knowledge_refresh_at_1630() -> None:
    jobs = [job for job in DEFAULT_JOBS if job.get("kind") == "knowledge_refresh"]
    assert len(jobs) == 1
    assert jobs[0]["id"] == "knowledge_refresh"
    assert jobs[0]["trigger"] == "16:30"
    assert jobs[0]["enabled"] is True


def test_default_table_validates() -> None:
    validate_table(_default_table())
    assert _structural_problems(_default_table()) == []


def test_schema_enum_contains_knowledge_refresh() -> None:
    schema = json.loads(
        (
            Path(__file__).resolve().parents[2] / "config" / "schema" / "schedule.schema.json"
        ).read_text(encoding="utf-8")
    )
    kinds = schema["properties"]["jobs"]["items"]["properties"]["kind"]["enum"]
    assert "knowledge_refresh" in kinds
    assert schema["properties"]["jobs"]["items"]["properties"]["daily_limit"] == {
        "type": "integer",
        "minimum": 1,
    }


def test_repo_schedule_declares_knowledge_refresh(repo: Path) -> None:
    raw = (repo / "config" / "schedule.yaml").read_text(encoding="utf-8")
    assert "kind: knowledge_refresh" in raw
    jobs = [job for job in _default_table()["jobs"] if job.get("kind") == "knowledge_refresh"]
    assert jobs and jobs[0]["id"] == "knowledge_refresh"


def test_structural_fallback_rejects_bad_daily_limit() -> None:
    table = _default_table()
    job = next(item for item in table["jobs"] if item["kind"] == "knowledge_refresh")
    for bad in (0, -1, "5", 1.5):
        job["daily_limit"] = bad
        problems = _structural_problems(table)
        assert any("daily_limit" in item for item in problems), bad
    job["daily_limit"] = 3
    assert _structural_problems(table) == []


def test_disabled_job_stays_in_table_and_toggleable(repo: Path) -> None:
    """任务在表里可见；仓库配置关掉它只影响它自己，其它任务照旧 enabled。"""
    table = _default_table()
    target = next(item for item in table["jobs"] if item["id"] == "knowledge_refresh")
    target["enabled"] = False
    validate_table(table)
    others = [job for job in table["jobs"] if job["id"] != "knowledge_refresh"]
    assert all(job["enabled"] for job in others)

    table_path = repo / "config" / "schedule.yaml"
    table, warnings, _source = load_schedule(table_path)
    ids = [job["id"] for job in table["jobs"]]
    assert "knowledge_refresh" in ids
    assert "knowledge_refresh" not in "\n".join(warnings)


# --------------------------------------------------------------------------- #
# 2. 集合面
# --------------------------------------------------------------------------- #


def test_universe_is_watch_then_holdings_deduped(tmp_path: Path) -> None:
    _watch("600000", "000001", root=tmp_path)
    _hold("000001", root=tmp_path)  # 已在自选：只补「持有」标记，不重复
    _hold("300750", root=tmp_path)  # 只在持仓

    rows = knowledge_universe(root=tmp_path)

    assert [row["code"] for row in rows] == ["600000", "000001", "300750"]
    assert rows[0]["origin"] == ["自选"]
    assert rows[1]["origin"] == ["自选", "持有"]
    assert rows[2]["origin"] == ["持有"]


def test_universe_ignores_fully_sold_holdings(tmp_path: Path) -> None:
    _hold("600519", shares=100.0, root=tmp_path)
    portfolio.add_trade(
        {
            "symbol": "600519",
            "name": "",
            "type": "sell",
            "trade_date": "2026-11-02",
            "shares": 100.0,
            "price": 11.0,
            "fee": 0,
        },
        root=tmp_path,
    )
    assert knowledge_universe(root=tmp_path) == []


# --------------------------------------------------------------------------- #
# 3. 落库面（真链路：研究 → archive_run → data/knowledge/）
# --------------------------------------------------------------------------- #


def test_refresh_archives_every_watchlist_stock(
    tmp_path: Path, wired: FakeClient, toasts: list[dict[str, Any]]
) -> None:
    _watch("600000", "000001", root=tmp_path)

    result = _refresh(tmp_path)

    assert result["status"] == "ok", (result["detail"], result["failed"])
    assert [row["code"] for row in result["refreshed"]] == ["600000", "000001"]
    assert result["failed"] == []
    assert result["coverage"]["ratio"] == 1.0
    assert result["alert"] is None
    assert toasts == []

    knowledge = tmp_path / "knowledge"
    for code, market in (("600000", "SH"), ("000001", "SZ")):
        directory = knowledge / f"{market}_{code}"
        assert (directory / "latest.md").is_file()
        assert (directory / "runs" / "refresh-20261104.md").is_file()
        hit = recall(tmp_path, market, code)
        assert hit is not None
        assert hit.as_of == DAY, "刷新当天就是档案的证据日期"
        assert hit.status == model.STATUS_FRESH

    manifest = store.load_manifest(tmp_path)
    assert set(manifest["companies"]) == {"SH:600000", "SZ:000001"}
    for key in manifest["companies"]:
        assert manifest["companies"][key]["as_of"] == DAY
        assert manifest["companies"][key]["status"] == model.STATUS_FRESH


def test_failed_stock_keeps_reason_and_no_dossier(
    tmp_path: Path, wired: FakeClient, toasts: list[dict[str, Any]]
) -> None:
    wired.fail_on = {("000001", "report")}
    _watch("600000", "000001", root=tmp_path)

    result = _refresh(tmp_path)

    assert result["status"] == "degraded", result
    assert [row["code"] for row in result["refreshed"]] == ["600000"]
    assert len(result["failed"]) == 1
    failed = result["failed"][0]
    assert failed["code"] == "000001"
    assert "report" in failed["reason"]

    manifest = store.load_manifest(tmp_path)
    assert set(manifest["companies"]) == {"SH:600000"}, "跑挂的票不伪造档案"
    assert not (tmp_path / "knowledge" / "SZ_000001").exists()

    # 覆盖率 1/2 < 0.8：告警走既有两条通道，失败原因留在收件箱正文里
    assert result["coverage"]["ratio"] == 0.5
    assert result["coverage"]["stale"] == ["000001"]
    assert result["alert"] and result["alert"]["title"].endswith("1/2")
    assert len(toasts) == 1
    assert "000001" in toasts[0]["reason"]

    items = inbox_store.list_items(root=tmp_path)
    assert len(items) == 1
    assert items[0]["kind"] == "knowledge"
    assert items[0]["job"] == "knowledge_refresh"
    assert any("000001" in line for line in items[0]["lines"])

    # 同日重跑：去重键命中，只更新原条目，不新增
    _refresh(tmp_path)
    assert len(inbox_store.list_items(root=tmp_path)) == 1


def test_all_failed_is_failed_status(
    tmp_path: Path, wired: FakeClient, toasts: list[dict[str, Any]]
) -> None:
    wired.fail_on = {"report"}
    _watch("600000", root=tmp_path)

    result = _refresh(tmp_path)

    assert result["status"] == "failed"
    assert result["refreshed"] == []
    assert result["coverage"]["ratio"] == 0.0
    assert store.load_manifest(tmp_path)["companies"] == {}


def test_single_stock_exception_does_not_abort_round(
    tmp_path: Path, wired: FakeClient, monkeypatch: pytest.MonkeyPatch, toasts: list[dict[str, Any]]
) -> None:
    _watch("600000", "000001", root=tmp_path)
    original = schedule_cli._refresh_one

    def _boom(code: str, day: Any, data_root: Path) -> dict[str, Any]:
        if code == "600000":
            raise RuntimeError("模拟单票崩溃")
        return original(code, day, data_root)

    monkeypatch.setattr(schedule_cli, "_refresh_one", _boom)
    result = _refresh(tmp_path)

    assert result["status"] == "degraded"
    assert [row["code"] for row in result["refreshed"]] == ["000001"]
    assert result["failed"][0]["code"] == "600000"
    assert "模拟单票崩溃" in result["failed"][0]["reason"]


def test_empty_universe_is_skipped(tmp_path: Path, wired: FakeClient) -> None:
    result = _refresh(tmp_path)
    assert result["status"] == "skipped"
    assert not (tmp_path / "knowledge").exists()


def test_daily_limit_caps_round_but_coverage_counts_everyone(
    tmp_path: Path, wired: FakeClient, toasts: list[dict[str, Any]]
) -> None:
    _watch("600000", "000001", "300750", root=tmp_path)

    result = _refresh(tmp_path, daily_limit=1)

    assert [row["code"] for row in result["refreshed"]] == ["600000"]
    assert result["coverage"]["total"] == 3
    assert result["coverage"]["ratio"] == round(1 / 3, 4)
    assert set(result["coverage"]["stale"]) == {"000001", "300750"}


# --------------------------------------------------------------------------- #
# 4. 告警 / 覆盖率口径
# --------------------------------------------------------------------------- #


def test_alert_failure_only_degrades_detail(
    tmp_path: Path, wired: FakeClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _watch("600000", root=tmp_path)

    def _boom(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("收件箱写不进去")

    monkeypatch.setattr(schedule_cli, "_alert_knowledge_coverage", _boom)

    # 先造出低覆盖率：另一只票跑挂
    wired.fail_on = {"report"}
    result = _refresh(tmp_path)

    assert result["status"] == "failed"
    assert result["alert"] == {"error": "收件箱写不进去"}
    assert "覆盖率告警投递失败：收件箱写不进去" in result["detail"]


def test_coverage_treats_stale_dossier_as_uncovered(tmp_path: Path) -> None:
    profile = model.Dossier(
        symbol="600000",
        market="SH",
        name="浦发银行",
        as_of="2026-08-01",  # 90 天之外 → 过期
        status=model.STATUS_FRESH,
        valid_days=90,
        sources=[],
        body="## 结论\n\n旧档案",
    )
    store.write_profile(tmp_path, profile)

    today = datetime(2026, 11, 4).date()
    covered = knowledge_coverage(["600000"], data_root=tmp_path, day=today)
    assert covered["covered"] == []
    assert covered["stale"] == ["600000"]
    assert covered["ratio"] == 0.0
    assert covered["threshold"] == DEFAULT_KNOWLEDGE_THRESHOLD

    # 同一天手动回填成当天日期即恢复覆盖：过期是 `as_of + valid_days` 的事，不是内容判定
    profile.as_of = DAY
    store.write_profile(tmp_path, profile)
    fresh = knowledge_coverage(["600000"], data_root=tmp_path, day=today)
    assert fresh["covered"] == ["600000"]
    assert fresh["ratio"] == 1.0


def test_coverage_missing_manifest_entry_counts_uncovered(tmp_path: Path) -> None:
    result = knowledge_coverage(
        ["600000", "000001"], data_root=tmp_path, day=datetime(2026, 11, 4).date()
    )
    assert result["covered"] == []
    assert result["stale"] == ["600000", "000001"]
    assert result["ratio"] == 0.0


def test_coverage_empty_universe_is_full(tmp_path: Path) -> None:
    result = knowledge_coverage([], data_root=tmp_path)
    assert result == {
        "total": 0,
        "covered": [],
        "stale": [],
        "ratio": 1.0,
        "threshold": DEFAULT_KNOWLEDGE_THRESHOLD,
    }


# --------------------------------------------------------------------------- #
# 5. 边界：run_job 不因刷新内部异常而抛出
# --------------------------------------------------------------------------- #


def test_run_job_folds_unexpected_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        raise RuntimeError("自选库读坏了")

    monkeypatch.setattr(schedule_cli, "knowledge_universe", _boom)
    result = run_job(REFRESH_JOB, now=MOMENT, timezone="Asia/Shanghai", root=tmp_path)
    assert result["status"] == "failed"
    assert "自选库读坏了" in result["detail"]


# --------------------------------------------------------------------------- #
# 6. 幂等：同日重跑复用 checkpoint，不重复生成研究
# --------------------------------------------------------------------------- #


def test_rerun_same_day_reuses_research(
    tmp_path: Path, wired: FakeClient, toasts: list[dict[str, Any]]
) -> None:
    _watch("600000", root=tmp_path)
    _refresh(tmp_path)
    first_calls = len(wired.calls)
    assert first_calls > 0

    second = _refresh(tmp_path)

    assert second["status"] == "ok"
    assert len(wired.calls) == first_calls, "同日重跑只复用，不重复调模型"
    assert recall(tmp_path, "SH", "600000").as_of == DAY


def test_unconfigured_ai_records_reason_and_no_dossier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, toasts: list[dict[str, Any]]
) -> None:
    """没有 AI 凭据：如实记「未配置」，不落空档案（不假装刷新成功）。"""
    config_root = tmp_path / "config"
    config_root.mkdir()
    (config_root / "ai.yaml").write_text(
        "ai:\n  default_model: fake-model\n  base_url_env: MISSING_URL_ENV_QUOT568\n"
        "  api_key_env: MISSING_KEY_ENV_QUOT568\n",
        encoding="utf-8",
    )
    _patch_pipeline(monkeypatch, {"config_root": config_root})
    _watch("600000", root=tmp_path)

    result = _refresh(tmp_path)

    assert result["status"] == "failed"
    reason = result["failed"][0]["reason"]
    assert "AI 未配置" in reason
    assert store.load_manifest(tmp_path)["companies"] == {}


# --------------------------------------------------------------------------- #
# 7. 状态映射：ok / degraded / failed 与已交付语义一致
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("scenario", "expected"),
    [("clean", "ok"), ("one_failed", "degraded"), ("all_failed", "failed")],
)
def test_status_mapping(
    scenario: str,
    expected: str,
    tmp_path: Path,
    wired: FakeClient,
    toasts: list[dict[str, Any]],
) -> None:
    _watch("600000", "000001", root=tmp_path)
    if scenario == "all_failed":
        wired.fail_on = {"report"}
    elif scenario == "one_failed":
        wired.fail_on = {("000001", "report")}
    assert _refresh(tmp_path)["status"] == expected


def test_result_shape_is_json_safe(
    tmp_path: Path, wired: FakeClient, toasts: list[dict[str, Any]]
) -> None:
    _watch("600000", root=tmp_path)
    result = _refresh(tmp_path)
    # 日志 / `ldst schedule run --output json` 会序列化整条结果
    json.dumps({k: v for k, v in result.items() if k != "finished_at"}, ensure_ascii=False)
    assert isinstance(result["coverage"]["covered"], list)
    assert isinstance(result["refreshed"][0]["archived"], str)


def test_unknown_kind_still_folds(tmp_path: Path) -> None:
    result = run_job(
        {"id": "x", "name": "x", "kind": "not_a_kind", "trigger": "16:30", "enabled": True},
        root=tmp_path,
    )
    assert result["status"] == "failed"


def test_default_table_job_runs_end_to_end(tmp_path: Path, wired: FakeClient) -> None:
    """默认表里那一条原样交给 run_job：注册的 trigger/kind 与执行面接得上。"""
    _watch("600000", root=tmp_path)
    job = next(item for item in DEFAULT_JOBS if item["id"] == "knowledge_refresh")
    result = run_job(job, now=MOMENT, timezone="Asia/Shanghai", root=tmp_path)
    assert result["status"] == "ok", result
