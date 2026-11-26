"""`POST /api/valuation/estimate` 契约（E 包 / DEC-091 §4）。

钉住：非回环 403、缺来源证据 403（POST 写面语义）、`no-store`、
六类换算的 happy path、缺参 / 非法参 400 人话、DCF 护栏（r ≤ g 拒）、
恒带免责声明。换算器**不取数不写库**：响应里没有任何 facts 字段，
也不产生任何文件（临时数据目录前后一致）。
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from ladderstock.web.server import create_app

LOOPBACK = ("127.0.0.1", 54321)
REMOTE = ("10.1.2.3", 54321)
WRITE_HEADERS = {"X-LDST-Client": "1"}

DISCLAIMER = "这是换算器"


def _client(root: Path, *, client: tuple[str, int] = LOOPBACK) -> TestClient:
    app = create_app(data_root=root, dist_dir=root / "no-dist")
    return TestClient(app, client=client)


# --------------------------------------------------------------------- 闸门


def test_estimate_is_loopback_only(tmp_path: Path) -> None:
    response = _client(tmp_path, client=REMOTE).post(
        "/api/valuation/estimate",
        json={"kind": "pe", "price": 10.0, "eps": 2.0},
        headers=WRITE_HEADERS,
    )
    assert response.status_code == 403


def test_estimate_needs_source_evidence(tmp_path: Path) -> None:
    response = _client(tmp_path).post(
        "/api/valuation/estimate", json={"kind": "pe", "price": 10.0, "eps": 2.0}
    )
    assert response.status_code == 403


def test_estimate_never_writes_or_reads_data(tmp_path: Path) -> None:
    before = sorted(p.name for p in tmp_path.iterdir())
    response = _client(tmp_path).post(
        "/api/valuation/estimate",
        json={"kind": "pb", "price": 12.0, "bps": 6.0},
        headers=WRITE_HEADERS,
    )
    assert response.status_code == 200
    after = sorted(p.name for p in tmp_path.iterdir())
    assert before == after  # 数据目录零变化：纯函数，没有库也没有缓存


# ------------------------------------------------------------------ 基本面


def test_pe_and_pb_shapes(tmp_path: Path) -> None:
    client = _client(tmp_path)
    pe = client.post(
        "/api/valuation/estimate",
        json={"kind": "pe", "price": 10.0, "eps": 2.0},
        headers=WRITE_HEADERS,
    )
    assert pe.status_code == 200
    assert pe.headers["cache-control"] == "no-store"
    payload = pe.json()
    assert payload["kind"] == "PE"
    assert payload["value"] == 5.0
    assert DISCLAIMER in payload["disclaimer"]

    pb = client.post(
        "/api/valuation/estimate",
        json={"kind": "pb", "price": 12.0, "bps": 6.0},
        headers=WRITE_HEADERS,
    )
    assert pb.json()["value"] == 2.0


def test_dividend_and_earnings_yield_pct(tmp_path: Path) -> None:
    client = _client(tmp_path)
    dividend = client.post(
        "/api/valuation/estimate",
        json={"kind": "dividend", "price": 20.0, "dps": 0.5},
        headers=WRITE_HEADERS,
    )
    assert dividend.status_code == 200
    assert dividend.json()["value_pct"] == "2.50%"

    ey = client.post(
        "/api/valuation/estimate",
        json={"kind": "earnings-yield", "pe": 25.0},
        headers=WRITE_HEADERS,
    )
    assert ey.status_code == 200
    assert ey.json()["value_pct"] == "4.00%"


def test_dcf_happy_path_and_guardrail(tmp_path: Path) -> None:
    client = _client(tmp_path)
    dcf = client.post(
        "/api/valuation/estimate",
        json={
            "kind": "dcf",
            "fcf": 100.0,
            "growth": 0.08,
            "terminal_growth": 0.03,
            "discount": 0.10,
            "periods": 5,
        },
        headers=WRITE_HEADERS,
    )
    assert dcf.status_code == 200
    payload = dcf.json()
    assert payload["value"] > 0
    assert payload["pv_explicit"] > 0
    assert payload["pv_terminal"] > 0
    assert isinstance(payload["warnings"], list)

    # 折现率 ≤ 终值增速：永续值无意义，400 人话拒绝（不硬算）
    bad = client.post(
        "/api/valuation/estimate",
        json={
            "kind": "dcf",
            "fcf": 100.0,
            "growth": 0.08,
            "terminal_growth": 0.10,
            "discount": 0.09,
            "periods": 5,
        },
        headers=WRITE_HEADERS,
    )
    assert bad.status_code == 400
    assert "大于终值增速" in bad.json()["detail"]


# ------------------------------------------------------------------ 校验链


def test_unknown_kind_and_missing_fields(tmp_path: Path) -> None:
    client = _client(tmp_path)
    unknown = client.post(
        "/api/valuation/estimate", json={"kind": "magic"}, headers=WRITE_HEADERS
    )
    assert unknown.status_code == 400
    assert "换算类型" in unknown.json()["detail"]

    missing = client.post(
        "/api/valuation/estimate", json={"kind": "pe", "price": 10.0}, headers=WRITE_HEADERS
    )
    assert missing.status_code == 400
    assert "eps" in missing.json()["detail"]


def test_bad_numbers_are_usage_errors(tmp_path: Path) -> None:
    client = _client(tmp_path)
    negative = client.post(
        "/api/valuation/estimate",
        json={"kind": "pe", "price": -10.0, "eps": 2.0},
        headers=WRITE_HEADERS,
    )
    assert negative.status_code == 400
    assert "正数" in negative.json()["detail"]

    wrong_type = client.post(
        "/api/valuation/estimate",
        json={"kind": "pe", "price": "贵", "eps": 2.0},
        headers=WRITE_HEADERS,
    )
    assert wrong_type.status_code == 400
    assert "数字" in wrong_type.json()["detail"]

    bool_price = client.post(
        "/api/valuation/estimate",
        json={"kind": "pe", "price": True, "eps": 2.0},
        headers=WRITE_HEADERS,
    )
    assert bool_price.status_code == 400
