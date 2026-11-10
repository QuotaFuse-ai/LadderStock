"""行业数据集一键补齐（QUOT-533 / DEC-074）：`ldst sync industry`。

钉五件事：

1. 范围 = `/industry` 页面读的六个数据集（与 `SNAPSHOT_DATASETS` 同一份）；
2. 代码池 = `config/industry.yaml` 已启用行业的锚点股，**不扩全市场**；
3. 成本 / 上限明示：载荷带 `industry_scope`（条数、来源、预计请求数）；
4. 上限与空池是**发请求之前**就判掉的（不符合就不联网）；
5. 跑完后 `industry list` 的覆盖桶确实从「未取到」往外走——同步不是只读重算。

peer 全部由离线桩扮演，不发生任何真实网络请求。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml
from fixtures.fake_peers import FakeTransport, tickhub_payload
from typer.testing import CliRunner

from ladderstock.cli import sync as sync_module
from ladderstock.cli.main import app

ROOT = Path(__file__).resolve().parents[2]
runner = CliRunner()

DAY = "2026-10-24"

#: 锚点 → 申万一级代码：与 `industry.yaml` 里的两行清单对得上
L1_BY_CODE = {
    "600519": "340000",
    "000858": "340000",
    "600036": "480000",
    "000001": "480000",
}

INDUSTRIES: list[dict[str, Any]] = [
    {"name": "食品饮料", "l1_code": "340000", "anchors": ["600519", "000858"]},
    {"name": "银行", "l1_code": "480000", "anchors": ["600036", "000001"]},
]

ANCHORS = ["600519", "000858", "600036", "000001"]


@pytest.fixture()
def config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, repo: Path) -> Path:
    """独立配置目录：复制真 sources/sync，只换 industry.yaml；数据目录同样落 tmp。"""
    config = tmp_path / "config"
    config.mkdir()
    for name in ("sources.yaml", "sync.yaml"):
        shutil.copy(ROOT / "config" / name, config / name)
    monkeypatch.setenv("LDST_CONFIG_DIR", str(config))
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path / "data"))
    return config


def _write_industry(config: Path, industries: list[dict[str, Any]]) -> None:
    doc = {
        "industry": {
            "version": 1,
            "membership_caliber": "sw_l1",
            "industries": industries,
        }
    }
    (config / "industry.yaml").write_text(
        yaml.safe_dump(doc, allow_unicode=True), encoding="utf-8"
    )


def _handler(dataset: str, payload: Any) -> Any:
    def handle(params: dict[str, Any]) -> dict[str, Any]:
        return tickhub_payload(payload(params))

    return handle


def _industry_transport() -> FakeTransport:
    """六个输入数据集的最小真实形状（每只票一行/几条记录）。"""

    def sw(params: dict[str, Any]) -> dict[str, Any]:
        code = str(params["code"])
        return {
            "code": code,
            "as_of": None,
            "current": {"l1_code": L1_BY_CODE[code]},
            "history": [
                {
                    "code": code,
                    "start_date": "2026-01-05",
                    "l1_code": L1_BY_CODE[code],
                    "l1_name": "食品饮料" if L1_BY_CODE[code] == "340000" else "银行",
                }
            ],
        }

    def finance(params: dict[str, Any]) -> dict[str, Any]:
        return {
            "code": str(params["code"]),
            "indicators": [
                {
                    "report_date": "2026-09-30 00:00:00",
                    "revenue_yoy": 10.0,
                    "profit_yoy": 12.0,
                    "roe": 15.0,
                }
            ],
        }

    def valuation(params: dict[str, Any]) -> dict[str, Any]:
        return {
            "code": str(params["code"]),
            "rows": [
                {
                    "date": DAY,
                    "close": 10.0,
                    "pe_ttm": 12.0,
                    "pb": 1.5,
                    "turnover_rate": 1.2,
                }
            ],
        }

    def fund_flow(params: dict[str, Any]) -> dict[str, Any]:
        return {
            "code": str(params["code"]),
            "name": "stub",
            "flows": [{"date": DAY, "main_net": 1000.0, "main_net_pct": 2.0}],
        }

    def kline(params: dict[str, Any]) -> dict[str, Any]:
        return {
            "code": str(params["code"]),
            "klines": [
                {"date": DAY, "close": 10.0, "amount": 1e8, "change_pct": 1.0}
            ],
        }

    def board_flow(_params: dict[str, Any]) -> dict[str, Any]:
        return {
            "scope": "industry",
            "total": 1,
            "page": 1,
            "page_size": 300,
            "items": [
                {
                    "code": "BK0477",
                    "name": "食品饮料",
                    "main_net": 1e8,
                    "main_net_pct": 2.0,
                    "change_pct": 1.0,
                }
            ],
        }

    return FakeTransport(
        handlers={
            "sw_industry_history": _handler("sw_industry_map", sw),
            "eastmoney_finance_indicators": _handler("finance_indicators", finance),
            "baostock_valuation_history": _handler("valuation_history", valuation),
            "stock_fund_flow": _handler("fund_flow", fund_flow),
            "tencent_kline": _handler("daily_k", kline),
            "board_fund_flow": _handler("industry_fund_flow", board_flow),
        }
    )


def _run(monkeypatch: pytest.MonkeyPatch, transport: FakeTransport, *extra: str):
    monkeypatch.setattr(
        "ladderstock.data.provider_registry.build_transport", lambda _peers: transport
    )
    return runner.invoke(app, ["sync", "industry", "--output", "json", *extra])


def test_sync_industry_uses_catalog_anchors_and_reports_scope(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_industry(config_dir, INDUSTRIES)
    transport = _industry_transport()

    result = _run(monkeypatch, transport)

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    scope = payload["industry_scope"]
    assert scope["datasets"] == list(sync_module.INDUSTRY_SYNC_DATASETS)
    assert scope["code_pool"] == {
        "size": len(ANCHORS),
        "source": sync_module.POOL_INDUSTRY,
        "limit": sync_module.MAX_POOL_CODES,
    }
    assert scope["estimated_requests"] == len(ANCHORS) * 5 + 1
    assert scope["full_market"] is False
    assert "成本非 0" in scope["note"]

    # 逐只数据集各打 len(ANCHORS) 次、板块一次；参数口径与 `ldst sync` 同一条路径
    by_tool: dict[str, list[dict[str, Any]]] = {}
    for call in transport.calls:
        by_tool.setdefault(call.tool, []).append(call.params)
    assert sorted(p["code"] for p in by_tool["tencent_kline"]) == sorted(ANCHORS)
    assert sorted(p["code"] for p in by_tool["sw_industry_history"]) == sorted(ANCHORS)
    assert len(by_tool["board_fund_flow"]) == 1
    assert by_tool["board_fund_flow"][0]["board_type"] == "industry"
    assert all(p["period"] == "daily" and p["adjust"] == "qfq" for p in by_tool["tencent_kline"])


def test_sync_industry_improves_industry_list_coverage(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同步前两行都是「未取到」，同步后出现成分与可用维度（覆盖桶减少）。"""
    _write_industry(config_dir, INDUSTRIES)

    before = runner.invoke(app, ["industry", "list", "--output", "json"])
    assert before.exit_code == 0, before.stderr
    before_rows = json.loads(before.stdout)["industries"]
    assert all(row["data_source_status"] == "missing" for row in before_rows)
    assert all(row["constituent_count"] == 0 for row in before_rows)

    result = _run(monkeypatch, _industry_transport())
    assert result.exit_code == 0, result.stderr

    after = runner.invoke(app, ["industry", "list", "--output", "json"])
    assert after.exit_code == 0, after.stderr
    after_rows = json.loads(after.stdout)["industries"]
    assert any(row["data_source_status"] != "missing" for row in after_rows)
    assert all(row["constituent_count"] > 0 for row in after_rows)


def test_sync_industry_rejects_empty_anchor_pool_before_any_call(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_industry(
        config_dir,
        [{"name": "综合", "l1_code": None, "anchors": [], "enabled": True}],
    )
    transport = _industry_transport()

    result = _run(monkeypatch, transport)

    assert result.exit_code == 3
    assert "没有可用的行业锚点" in result.stderr
    assert transport.calls == []


def test_sync_industry_rejects_pool_over_limit_before_any_call(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    too_many = [str(index).zfill(6) for index in range(sync_module.MAX_POOL_CODES + 1)]
    _write_industry(
        config_dir,
        [{"name": "超限", "l1_code": "110000", "anchors": too_many, "enabled": True}],
    )
    transport = _industry_transport()

    result = _run(monkeypatch, transport)

    assert result.exit_code == 2
    assert str(sync_module.MAX_POOL_CODES) in result.stderr
    assert transport.calls == []


def test_sync_industry_codes_override_and_full_mode(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_industry(config_dir, INDUSTRIES)
    transport = _industry_transport()

    result = _run(monkeypatch, transport, "--codes", "600519", "--full")

    assert result.exit_code == 0, result.stderr
    scope = json.loads(result.stdout)["industry_scope"]
    assert scope["code_pool"]["size"] == 1
    assert scope["code_pool"]["source"] == sync_module.POOL_CLI
    assert scope["mode"] == sync_module.MODE_FULL
    assert all("start" not in call.params for call in transport.calls)
