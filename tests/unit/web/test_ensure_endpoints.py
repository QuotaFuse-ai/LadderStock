"""打开即补取的 HTTP 契约测试（QUOT-555 / DEC-080）：冷启动打开页面自动有当日数。

验收逐条钉死（真 FastAPI + 真夹具盘 + FakeTransport，不联网、不 mock 文件层）：

1. 冷启动打开 market/theme、news、pool、industry 各页**自动**补取并出当日数，
   不再触发「先同步再刷新 / 先配置 qfw」手动指引；
2. 补取幂等 + 冷却：连续两次打开，上游只被打一轮（冷却期内不重打）；
3. 上游失败如实 degraded/empty，**不回落演示数据**（themes/items 仍空）；
4. 缺省不接线（`open_ensure=None`）时行为与改造前逐字节一致——既有空态文案不变、零外呼；
5. industry 补取只走 base_pool 常规口径，代码池不扩到锚点之外（DEC-074 付费同步仍显式）。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from fixtures.ensure_peers import ENSURE_CODES, ensure_transport, make_ensure_registry
from fixtures.fake_peers import FakeTransport, make_news_registry
from fixtures.news_samples import news_handlers, news_tools

from ladderstock.industry import snapshot as industry_snapshot
from ladderstock.web.ensure import OpenEnsure
from ladderstock.web.server import create_app

ROOT = Path(__file__).resolve().parents[3]
LOOPBACK = ("127.0.0.1", 54321)
BASE_URL = "http://127.0.0.1:5950"

DAY = "2026-10-24"
NEWS_DAY = "2026-10-30"
INDUSTRY_DATASETS = list(industry_snapshot.SNAPSHOT_DATASETS)
NOTE_MARK = "打开即补取"


@pytest.fixture()
def data_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    return tmp_path


def _client(root: Path, ensure: OpenEnsure | None) -> TestClient:
    app = create_app(data_root=root, dist_dir=root / "no-dist", open_ensure=ensure)
    return TestClient(app, base_url=BASE_URL, client=LOOPBACK)


def _get(client: TestClient, path: str, **params: str) -> dict[str, Any]:
    response = client.get(path, params=params)
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------- #
# 1. 冷启动自动补取 → 当日数，无手动指引
# --------------------------------------------------------------------------- #

def test_theme_cold_start_auto_refetch(data_root: Path) -> None:
    transport = ensure_transport(DAY)
    registry = make_ensure_registry(transport, data_root, datasets=["theme_news"])
    body = _get(
        _client(data_root, OpenEnsure(registry=registry, root=data_root)),
        "/api/market/theme",
        date=DAY,
    )

    assert body["payload"]["available"] is True
    assert {row["theme"] for row in body["payload"]["themes"]} == {"ai", "semiconductor"}
    assert body["payload"]["reason"] is None, "补取成功后不该再给手动指引"
    assert "先配置 qfw" not in body["source_detail"]
    assert NOTE_MARK in body["source_detail"]
    assert transport.calls, "冷启动确实经 peer 补取了一轮"


def test_news_cold_start_auto_refetch(data_root: Path) -> None:
    transport = FakeTransport(handlers=news_handlers(), tools=news_tools())
    registry = make_news_registry(transport, data_root)
    body = _get(
        _client(data_root, OpenEnsure(registry=registry, root=data_root)),
        "/api/news",
        date=NEWS_DAY,
        until=NEWS_DAY,
    )

    assert body["payload"]["summary"]["items"] > 0
    assert NOTE_MARK in body["source_detail"]
    assert transport.calls


def test_pool_cold_start_auto_refetch(data_root: Path) -> None:
    transport = ensure_transport(DAY)
    registry = make_ensure_registry(
        transport, data_root, datasets=["limit_pool", "concept_board", "fund_flow"]
    )
    body = _get(
        _client(data_root, OpenEnsure(registry=registry, root=data_root)),
        "/api/pool",
        date=DAY,
    )

    assert body["payload"]["available"] is True
    assert body["payload"]["candidates"], "补取后候选池应有当日候选"
    assert NOTE_MARK in body["source_detail"]
    assert transport.calls


def test_industry_cold_start_auto_refetch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "config"
    config.mkdir()
    for name in ("sources.yaml", "sync.yaml"):
        shutil.copy(ROOT / "config" / name, config / name)
    (config / "industry.yaml").write_text(
        yaml.safe_dump(
            {
                "industry": {
                    "version": 1,
                    "membership_caliber": "sw_l1",
                    "industries": [
                        {"name": "食品饮料", "l1_code": "340000", "anchors": list(ENSURE_CODES)}
                    ],
                }
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    data = tmp_path / "data"
    monkeypatch.setenv("LDST_CONFIG_DIR", str(config))
    monkeypatch.setenv("LDST_DATA_DIR", str(data))

    transport = ensure_transport(DAY)
    registry = make_ensure_registry(
        transport, data, datasets=INDUSTRY_DATASETS, codes=ENSURE_CODES
    )
    app = create_app(
        data_root=data,
        dist_dir=tmp_path / "dist",
        open_ensure=OpenEnsure(registry=registry, root=data),
    )
    body = _get(TestClient(app, base_url=BASE_URL, client=LOOPBACK), "/api/industry")

    rows = body["payload"]["industries"]
    food = next(row for row in rows if row["name"] == "食品饮料")
    assert food["constituent_count"] > 0, "补取后行业成分应从 0 变正"
    assert food["data_source_status"] != "missing"
    assert NOTE_MARK in body["source_detail"]
    # 付费红线：代码池只在常规 base_pool 子集内，没有扩到锚点之外（DEC-074 显式动作）。
    per_code = {
        str(call.params["code"])
        for call in transport.calls
        if call.tool in {"tencent_kline", "sw_industry_history", "stock_fund_flow"}
    }
    assert per_code == set(ENSURE_CODES)


# --------------------------------------------------------------------------- #
# 2. 冷却：连续打开只打一轮上游
# --------------------------------------------------------------------------- #

def test_cooldown_bounds_upstream_across_opens(data_root: Path) -> None:
    # 上游挂 → 数据始终陈旧：第二次打开应命中冷却，不再打上游。
    transport = ensure_transport(DAY, failing=("wire_get_digest",))
    registry = make_ensure_registry(transport, data_root, datasets=["theme_news"])
    client = _client(data_root, OpenEnsure(registry=registry, root=data_root))

    _get(client, "/api/market/theme", date=DAY)
    after_first = len(transport.calls)
    assert after_first > 0
    _get(client, "/api/market/theme", date=DAY)
    assert len(transport.calls) == after_first, "冷却期内第二次打开不该再 hammer 上游"


# --------------------------------------------------------------------------- #
# 3. 上游失败如实空态，不回落演示
# --------------------------------------------------------------------------- #

def test_theme_failure_is_honest_empty_not_demo(data_root: Path) -> None:
    transport = ensure_transport(DAY, failing=("wire_get_digest",))
    registry = make_ensure_registry(transport, data_root, datasets=["theme_news"])
    body = _get(
        _client(data_root, OpenEnsure(registry=registry, root=data_root)),
        "/api/market/theme",
        date=DAY,
    )

    assert body["payload"]["available"] is False
    assert body["payload"]["themes"] == [], "失败不编数、不回落演示"
    assert NOTE_MARK in body["source_detail"], "如实说明已尝试补取但失败"


def test_news_failure_is_honest_empty_not_demo(data_root: Path) -> None:
    transport = FakeTransport(
        handlers=news_handlers(failing=tuple(news_tools())), tools=news_tools()
    )
    registry = make_news_registry(transport, data_root)
    body = _get(
        _client(data_root, OpenEnsure(registry=registry, root=data_root)),
        "/api/news",
        date=NEWS_DAY,
        until=NEWS_DAY,
    )

    assert body["payload"]["summary"]["items"] == 0
    assert NOTE_MARK in body["source_detail"]


# --------------------------------------------------------------------------- #
# 4. 缺省不接线：行为与改造前逐字节一致（回归守卫）
# --------------------------------------------------------------------------- #

def test_disabled_by_default_keeps_legacy_empty_state(data_root: Path) -> None:
    transport = ensure_transport(DAY)
    body = _get(_client(data_root, None), "/api/market/theme", date=DAY)

    assert body["payload"]["available"] is False
    assert body["payload"]["themes"] == []
    assert "theme_news 未接入（QuotaFuse Wire）" in body["source_detail"]
    assert NOTE_MARK not in body["source_detail"], "未接线不该出现补取说明"
    assert transport.calls == [], "未接线零外呼（纯只读，DEC-074 原口径）"