"""QUOT-276 / SPEC-INDUSTRY-PAGES §2：`/api/industry*` 三个只读接口的契约测试。

错误码照缺口清单（§7）逐条钉死，一条不许含糊——前端已按这些状态码写死展示分支：

| 情况 | 期望 |
| --- | --- |
| 正常 | 200 + `LadderEnvelope` 外壳 |
| 名称不在清单 | **200** + `available=false` + `candidates`（R5 §11 的语义，不是错误） |
| 参数非法（空名 / 超长 / 未知 `rule`） | 422 |
| `config/industry.yaml` 读不动 | 503 |
| 缓存读取失败 | 500 |
| 路由本身没接 | 404（前端据此显示「产业接口未接入」） |
| 非回环来源 | 403 |

另外钉住三件事：R5 载荷的键一个不改（只加 SPEC 要求的投影键）、`related` 在 R6 交付前是
空数组 + `reason` 而不是 404、`LDST_CONTROL_ENABLED=0` 不影响产业读取（数据面）。
所有断言跑真夹具盘 + 真 FastAPI，不 mock 文件层。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fixtures.industry_cases import load_case, prepare

from ladderstock.errors import LadderStockError
from ladderstock.industry import snapshot as snapshot_module
from ladderstock.industry import views
from ladderstock.industry.snapshot import metric_paths
from ladderstock.web import control
from ladderstock.web.industry import IndustryPlane
from ladderstock.web.server import create_app

FULL = "I001-all-dimensions"
EMPTY = "I003-empty-cache"
TARGET = "食品饮料"

LOOPBACK = ("127.0.0.1", 54321)
REMOTE = ("10.1.2.3", 54321)
BASE_URL = "http://127.0.0.1:5950"

#: `LadderEnvelope` 的外壳键（SPEC §2 前言：与 `/api/review` 同外壳）
ENVELOPE_KEYS = {
    "domain",
    "topic",
    "event_id",
    "data_as_of",
    "payload",
    "source",
    "source_detail",
    "fetched_at",
    "freshness",
    "confidence",
    "source_status",
}
#: SPEC §2.1 清单载荷必须的键（外加前端 `unwrap()` 只下钻 `payload`，故 `data_as_of` 也必须在里层）
LIST_KEYS = {
    "available",
    "caliber",
    "windows",
    "count",
    "as_of",
    "industries",
    "datasets",
    "source_detail",
    "reason",
    "data_as_of",
}
#: SPEC §2.3 信号载荷的六键（+ 里层 `data_as_of`）
SIGNAL_KEYS = {
    "available",
    "as_of",
    "rules",
    "signals",
    "counts",
    "reason",
    "data_as_of",
}
#: R5 `show` 载荷本身的键（QUOT-276 一个都不改）
R5_DETAIL_KEYS = {
    "available",
    "industry",
    "membership",
    "dimensions",
    "windows",
    "formulas",
    "datasets",
    "caliber_policy",
    "provenance",
    "metrics_present",
    "missing",
    "unavailable_dimensions",
    "data_source_status",
    "reason",
    "source_detail",
    "requested",
}
#: SPEC §2.2 要求后端补的键
DETAIL_EXTRA_KEYS = {
    "history",
    "constituents",
    "related",
    "related_reason",
    "main_net_daily",
    "data_as_of",
}


# --------------------------------------------------------------------------- #
# 造盘
# --------------------------------------------------------------------------- #

def build_client(
    tmp_path: Path,
    case: str = FULL,
    *,
    config_root: Path | None = None,
    **create_kwargs: Any,
) -> TestClient:
    """夹具盘 → 真 `create_app`（注入 `IndustryPlane`，让它读本次 tmp 的数据与配置）。"""
    data, config = prepare(load_case(case), tmp_path)
    app = create_app(
        data_root=data,
        dist_dir=tmp_path / "dist",
        industry_plane=IndustryPlane(data_root=data, config_root=config_root or config),
        **create_kwargs,
    )
    return TestClient(app, base_url=BASE_URL, client=LOOPBACK)


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return build_client(tmp_path)


def payload_of(response: Any) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    return dict(response.json()["payload"])


# --------------------------------------------------------------------------- #
# GET /api/industry（缺口 #1）
# --------------------------------------------------------------------------- #

def test_list_envelope_and_payload_contract(client: TestClient) -> None:
    """外壳 = `LadderEnvelope`；载荷 = R5 清单 + 每行 `metrics` / `as_of` + 顶层 `as_of`。"""
    body = client.get("/api/industry").json()

    assert set(body) == ENVELOPE_KEYS
    assert body["domain"] == "sentiment"
    assert body["topic"] == "industry_snapshot"
    assert body["freshness"] == "cached"
    assert body["source_status"] in {"ok", "degraded", "partial"}
    payload = payload_of(client.get("/api/industry"))
    assert LIST_KEYS <= set(payload)
    assert payload["available"] is True
    assert payload["count"] == len(payload["industries"])
    assert payload["data_as_of"] == body["data_as_of"]
    assert set(payload["as_of"]) == set(snapshot_module.DIMENSIONS)


def test_list_row_metrics_keys_equal_metric_paths(client: TestClient) -> None:
    """`metrics` 的键与 R5 `metric_paths()` 完全同名；缺值显式 `null`，不省略键。"""
    payload = payload_of(client.get("/api/industry"))
    paths = set(metric_paths(dict(payload["windows"])))

    assert paths
    for row in payload["industries"]:
        assert set(row["metrics"]) == paths
        assert set(row["as_of"]) == set(snapshot_module.DIMENSIONS)
        assert {"name", "l1_code", "bound", "enabled", "available"} <= set(row)
        assert row["data_source_status"] in {"ok", "partial", "missing"}


def test_list_values_equal_cli_snapshot(client: TestClient, tmp_path: Path) -> None:
    """HTTP 行里的数字 = `ldst industry show` 同一批数字（同一次 `build_snapshot`，不是第二套）。"""
    data, config = prepare(load_case(FULL), tmp_path)
    listing = payload_of(client.get("/api/industry"))

    for row in listing["industries"]:
        shown = snapshot_module.industry_snapshot(
            str(row["name"]), root=data, config_root=config
        )
        for path, value in row["metrics"].items():
            assert value == snapshot_module._leaf_value(shown["dimensions"], path), path


def test_list_empty_cache_is_200_with_nulls(tmp_path: Path) -> None:
    """空缓存：200、`available=false`、`data_as_of=null`（不拿请求日期顶替），键还在。"""
    client = build_client(tmp_path, EMPTY)
    body = client.get("/api/industry").json()
    payload = dict(body["payload"])

    assert body["data_as_of"] is None
    assert payload["available"] is False
    assert payload["data_as_of"] is None
    assert payload["industries"]
    for row in payload["industries"]:
        assert all(value is None for value in row["metrics"].values())


def test_list_config_error_is_503(tmp_path: Path) -> None:
    """配置读不动 → 503（缺口清单第 1 行：配置错 503），且不给半个载荷。"""
    client = build_client(tmp_path, config_root=tmp_path / "no-such-config")

    response = client.get("/api/industry")
    assert response.status_code == 503
    assert "config/industry.yaml" in response.json()["detail"]


def test_list_read_failure_is_500(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """缓存读取抛错 → 500 + JSON `detail`（前端 `describeIndustryError` 要能读到原因）。"""

    def boom(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise OSError("缓存目录不可读")

    monkeypatch.setattr(snapshot_module, "industry_list", boom)
    client = build_client(tmp_path)

    response = client.get("/api/industry")
    assert response.status_code == 500
    assert "缓存目录不可读" in response.json()["detail"]


def test_list_rejects_non_loopback(tmp_path: Path) -> None:
    """非回环来源 403：产业读取同样是本机数据，不跟 `/api/review` 一样放开。"""
    data, config = prepare(load_case(FULL), tmp_path)
    app = create_app(
        data_root=data,
        dist_dir=tmp_path / "dist",
        industry_plane=IndustryPlane(data_root=data, config_root=config),
    )
    remote = TestClient(app, base_url=BASE_URL, client=REMOTE)

    assert remote.get("/api/industry").status_code == 403


def test_industry_read_survives_control_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`LDST_CONTROL_ENABLED=0` 只关控制面：产业三接口是数据面，照旧 200。"""
    monkeypatch.setenv(control.CONTROL_ENABLED_ENV, "0")
    client = build_client(tmp_path)

    assert client.get("/api/industry").status_code == 200
    assert client.get(f"/api/industry/{TARGET}").status_code == 200
    assert client.get("/api/signals").status_code == 200
    assert client.get("/api/control/status").status_code == 404


# --------------------------------------------------------------------------- #
# GET /api/industry/{name}（缺口 #2 / #3 / #4 / #5 / #7）
# --------------------------------------------------------------------------- #

def test_detail_keeps_r5_payload_and_adds_spec_blocks(client: TestClient, tmp_path: Path) -> None:
    """R5 `show` 的键一个不改，只补 SPEC §2.2 的四块 + 里层 `data_as_of`。"""
    data, config = prepare(load_case(FULL), tmp_path)
    r5 = snapshot_module.industry_snapshot(TARGET, root=data, config_root=config)
    payload = payload_of(client.get(f"/api/industry/{TARGET}"))

    assert R5_DETAIL_KEYS <= set(r5)
    assert set(payload) - DETAIL_EXTRA_KEYS == set(r5)
    for key in R5_DETAIL_KEYS:
        assert payload[key] == r5[key], key


def test_detail_history_last_point_equals_current_percentile(client: TestClient) -> None:
    """缺口 #3：`history` 的最后一个点 = 载荷里的当期分位（sparkline 与仪表不会打脸）。"""
    payload = payload_of(client.get(f"/api/industry/{TARGET}"))
    dimensions = payload["dimensions"]
    history = payload["history"]

    for key, block, name in (
        ("pe_percentile", dimensions["valuation"], "pe_percentile"),
        ("pb_percentile", dimensions["valuation"], "pb_percentile"),
        ("turnover_percentile", dimensions["crowding"], "turnover_percentile"),
    ):
        assert history[key], key
        assert history[key][-1]["value"] == block[name], key
        for point in history[key]:
            assert set(point) == {"date", "value"}


def test_detail_constituents_contract(client: TestClient) -> None:
    """缺口 #4：逐股九键，行数 = `constituent_count`，`as_of` 是该股自己的数据日。"""
    payload = payload_of(client.get(f"/api/industry/{TARGET}"))
    rows = payload["constituents"]

    assert rows
    assert len(rows) == payload["membership"]["constituent_count"]
    for row in rows:
        assert set(row) == {
            "code",
            "name",
            "pe_ttm",
            "pb",
            "turnover_rate",
            "revenue_yoy",
            "profit_yoy",
            "main_net_nd",
            "as_of",
        }
        assert row["code"]


def test_detail_related_is_empty_array_with_reason(client: TestClient) -> None:
    """缺口 #5：R6 未交付 → 空数组 + `reason`，**不是 404**（404 会被前端读成「接口未接入」）。"""
    payload = payload_of(client.get(f"/api/industry/{TARGET}"))

    assert payload["related"] == []
    assert "news_stream" in str(payload["related_reason"])


def test_detail_main_net_daily_window_matches_fund_days(client: TestClient) -> None:
    """缺口 #7：逐日 `main_net` 与 R5 的 `fund_days` 同一个窗口，齐样本日之和 = N 日合计。"""
    payload = payload_of(client.get(f"/api/industry/{TARGET}"))
    daily = payload["main_net_daily"]
    nd = payload["dimensions"]["capital"]["constituents"]["main_net_inflow_nd"]

    assert len(daily) == int(payload["windows"]["fund_days"])
    assert nd is not None
    total = sum(float(item["main_net"]) for item in daily if item["complete"])
    assert total == pytest.approx(float(nd))


@pytest.mark.parametrize(
    ("path", "reason"),
    [
        ("/api/industry/%20", "为空"),
        ("/api/industry/" + "很" * 41, "过长"),
    ],
)
def test_detail_invalid_name_is_422(client: TestClient, path: str, reason: str) -> None:
    """空名 / 超长名 → 422（前端 `normalizeIndustryName` 同档，超出不猜近似名）。"""
    response = client.get(path)

    assert response.status_code == 422
    assert reason in response.json()["detail"]


def test_detail_unknown_name_is_200_with_candidates(client: TestClient) -> None:
    """名称不在清单 → 200 + `available=false` + `candidates`：载荷完整可渲染成空态。"""
    response = client.get("/api/industry/不存在行业")
    payload = payload_of(response)

    assert payload["available"] is False
    assert payload["industry"]["in_catalog"] is False
    assert TARGET in payload["candidates"]
    assert "不在 config/industry.yaml 清单里" in str(payload["reason"])


def test_detail_route_missing_is_404(client: TestClient) -> None:
    """只有「路由本身没接」才 404：多一段路径不落任何产业路由，回 JSON 404 而非 SPA 兜底页。"""
    response = client.get("/api/industry/%E9%A3%9F%E5%93%81%E9%A5%AE%E6%96%99/extra")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_detail_read_failure_is_500(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """单行业读不动同样 500（与清单同一个映射，不给前端一份残缺载荷）。"""

    def boom(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise LadderStockError("归档分片校验失败", 7)

    monkeypatch.setattr(snapshot_module, "industry_snapshot", boom)
    client = build_client(tmp_path)

    response = client.get(f"/api/industry/{TARGET}")
    assert response.status_code == 500
    assert "归档分片校验失败" in response.json()["detail"]


def test_detail_empty_cache_still_renders(tmp_path: Path) -> None:
    """空缓存 + 合法名：200，五维显式 `null`，序列是空数组而不是缺键。"""
    client = build_client(tmp_path, EMPTY)
    payload = payload_of(client.get(f"/api/industry/{TARGET}"))

    assert payload["available"] is False
    assert payload["history"]["pe_percentile"] == []
    assert payload["constituents"] == []
    assert payload["main_net_daily"] == []


# --------------------------------------------------------------------------- #
# GET /api/signals（缺口 #6）
# --------------------------------------------------------------------------- #

def test_signals_payload_contract(client: TestClient) -> None:
    """载荷 = SPEC §2.3 六键；`rules[]` 四条且 id 与后端规则表一致。"""
    payload = payload_of(client.get("/api/signals"))

    assert SIGNAL_KEYS <= set(payload)
    assert [item["id"] for item in payload["rules"]] == views.rule_ids()
    for rule in payload["rules"]:
        assert set(rule) == {"id", "title", "direction", "expression"}
        assert rule["direction"] in {"bullish", "bearish"}
    assert payload["available"] is True


def test_signals_evidence_rows_are_parseable(client: TestClient) -> None:
    """每条信号的证据数 = 规则条件数，九项齐全非空——前端缺任一项会丢整条证据。"""
    payload = payload_of(client.get("/api/signals"))
    by_id = {rule.id: rule for rule in views.SIGNAL_RULES}
    assert payload["signals"]

    for signal in payload["signals"]:
        rule = by_id[str(signal["rule_id"])]
        assert set(signal) == {
            "id",
            "rule_id",
            "rule_title",
            "industry",
            "direction",
            "severity",
            "as_of",
            "summary",
            "evidence",
        }
        assert len(signal["evidence"]) == len(rule.predicates)
        for item in signal["evidence"]:
            assert set(item) == {
                "path",
                "label",
                "value",
                "unit",
                "as_of",
                "dataset",
                "comparator",
                "threshold",
                "passed",
            }
            assert item["path"] and item["label"] and item["as_of"] and item["dataset"]
            assert isinstance(item["value"], (int, float))
            assert isinstance(item["threshold"], (int, float))
        assert signal["as_of"] == max(str(item["as_of"]) for item in signal["evidence"])
        assert signal["summary"]


def test_signals_envelope_uses_declared_day_and_listing_provenance(client: TestClient) -> None:
    """外壳时点：信号载荷自己声明 `as_of`，外壳与里层都用它；数据集状态借筛选时那份清单。"""
    body = client.get("/api/signals").json()
    payload = dict(body["payload"])

    assert payload["as_of"] == "2026-01-07"
    assert body["data_as_of"] == payload["as_of"]
    assert payload["data_as_of"] == payload["as_of"]
    assert "零外呼" in body["source_detail"]
    assert body["source_status"] in {"ok", "partial"}


def test_signals_counts_match_emitted_rows(client: TestClient) -> None:
    """`counts` 与 `signals[]` 自洽：总数、多空、逐规则计数三处都对得上。"""
    payload = payload_of(client.get("/api/signals"))
    counts = payload["counts"]
    signals = payload["signals"]

    assert set(counts) == {"total", "bullish", "bearish", "by_rule"}
    assert counts["total"] == len(signals)
    assert counts["bullish"] + counts["bearish"] == counts["total"]
    assert sum(int(value) for value in counts["by_rule"].values()) == counts["total"]
    assert set(counts["by_rule"]) >= {str(item["rule_id"]) for item in signals}


def test_signals_filter_by_industry(client: TestClient) -> None:
    """`?industry=` 命中清单内行业：只回该行业的信号，`rules[]` 仍是全量。"""
    everything = payload_of(client.get("/api/signals"))
    name = str(everything["signals"][0]["industry"])

    scoped = payload_of(client.get(f"/api/industry/{name}"))
    filtered = payload_of(client.get(f"/api/signals?industry={name}"))

    assert scoped["industry"]["name"] == name
    assert filtered["signals"]
    assert all(item["industry"] == name for item in filtered["signals"])
    assert [item["id"] for item in filtered["rules"]] == views.rule_ids()


def test_signals_filter_by_rule(client: TestClient) -> None:
    """`?rule=` 只判一条规则：`by_rule` 的键收成这一条，其余计数不出现。"""
    payload = payload_of(client.get("/api/signals?rule=crowding_risk"))

    assert all(item["rule_id"] == "crowding_risk" for item in payload["signals"])
    assert set(payload["counts"]["by_rule"]) == {"crowding_risk"}


def test_signals_unknown_industry_is_200_with_reason(client: TestClient) -> None:
    """筛选参数里的陌生行业不是错误：200 + 空数组 + 说明（同 §2.2 的名称语义）。"""
    payload = payload_of(client.get("/api/signals?industry=不存在行业"))

    assert payload["signals"] == []
    assert payload["counts"]["total"] == 0
    assert "不在 config/industry.yaml 清单里" in str(payload["reason"])


@pytest.mark.parametrize(
    ("query", "detail"),
    [
        ("rule=nope", "未知的信号规则 id"),
        ("rule=crowding_risk&industry=%20", "为空"),
        ("industry=" + "很" * 41, "过长"),
    ],
)
def test_signals_invalid_params_are_422(client: TestClient, query: str, detail: str) -> None:
    """非法参数 422：未知规则 id、空 / 超长行业名都不静默返回全量。"""
    response = client.get(f"/api/signals?{query}")

    assert response.status_code == 422
    assert detail in response.json()["detail"]


def test_signals_empty_params_mean_no_filter(client: TestClient) -> None:
    """空参数 = 不过滤（前端只在有值时才 set），与不带参数同一个载荷。"""
    plain = payload_of(client.get("/api/signals"))
    blank = payload_of(client.get("/api/signals?industry=&rule="))

    assert blank["signals"] == plain["signals"]
    assert blank["counts"] == plain["counts"]


def test_signals_empty_cache_reports_unavailable(tmp_path: Path) -> None:
    """空缓存：200、`available=false`、零信号 + 原因（不是一条「靠 null 判出来」的假信号）。"""
    client = build_client(tmp_path, EMPTY)
    payload = payload_of(client.get("/api/signals"))

    assert payload["available"] is False
    assert payload["signals"] == []
    assert payload["counts"]["total"] == 0
    assert payload["data_as_of"] is None
    assert payload["reason"]


def test_signals_config_error_is_503(tmp_path: Path) -> None:
    """信号判定同样吃配置：配置读不动 → 503，与清单接口一致。"""
    client = build_client(tmp_path, config_root=tmp_path / "no-such-config")

    assert client.get("/api/signals").status_code == 503
