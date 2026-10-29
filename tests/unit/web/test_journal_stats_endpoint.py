"""QUOT-196 / DEC-015 Tier 1：`GET /api/journal/stats` 的契约测试。

四条硬验收（DEC-015 §3 / §6 / §7）：

1. 空库（`journal.sqlite` 不存在）→ 200 + 全零结构，四个「需要样本」的指标是 `null`；
2. 有数据 → 200 + 聚合数字与 `ldst journal stats` 同源同值（`symbols` / 明细被有意去掉）；
3. `strip_private(payload) == payload` 恒等（`deviation_review.trades` 撞 `PRIVATE_KEYS`
   的坑必须被投影层挡住，且 `note` 是**显式丢弃**的）；
4. 非回环来源 → 403（即使开了远程访问 + Token）。

另外钉住：只 GET（405 + `Allow: GET`）、参数 400、`LDST_CONTROL_ENABLED=0` 不影响它。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.core.privacy import private_keys_present, strip_private
from ladderstock.journal import log as journal_log
from ladderstock.web import control
from ladderstock.web.server import create_app

LOOPBACK = ("127.0.0.1", 54321)
REMOTE = ("10.1.2.3", 54321)
DATE = "2026-10-22"
SENTINEL_NOTE = "隐私哨兵-QUOT196-note"

#: 契约形状（DEC-015 §6）：顶层键 + `deviation_review` 子键，一个不多一个不少
EXPECTED_KEYS = {
    "local_only",
    "store",
    "data_source_status",
    "range",
    "total_trades",
    "buy_count",
    "sell_count",
    "closed_rounds",
    "wins",
    "losses",
    "flats",
    "win_rate",
    "avg_win",
    "avg_loss",
    "profit_loss_ratio",
    "realized_pnl",
    "unmatched_sell_qty",
    "deviation_review",
}
EXPECTED_DEVIATION_KEYS = {"threshold_pct", "count"}
#: 永不出现的字段：标的清单、逐笔明细、任何自由文本
FORBIDDEN_KEYS = ("symbols", "trades", "details", "entries", "note", "reason", "remark")


def _all_keys(node: Any) -> set[str]:
    """递归收集 JSON 结构里出现过的**键名**（区分于文本子串：`total_trades` 不是 `trades`）。"""
    found: set[str] = set()
    if isinstance(node, dict):
        for key, item in node.items():
            found.add(str(key).lower())
            found |= _all_keys(item)
    elif isinstance(node, list):
        for item in node:
            found |= _all_keys(item)
    return found


def _client(root: Path, client: tuple[str, int] = LOOPBACK, **kwargs: Any) -> TestClient:
    app = create_app(data_root=root, dist_dir=root / "no-dist", **kwargs)
    return TestClient(app, client=client)


def _seed_trades(root: Path) -> None:
    """买入 10×100 → 卖出 11×100：一个盈利闭环，已实现盈亏 100。"""
    journal_log.add_trade(
        "600000", "buy", 10.0, 100.0, note=SENTINEL_NOTE, trade_date=DATE,
        plan_price=10.0, root=root,
    )
    journal_log.add_trade(
        "600000", "sell", 11.0, 100.0, note=SENTINEL_NOTE, trade_date=DATE, root=root,
    )


# --------------------------------------------------------------------------- #
# 1. 空态：200 而不是 404，四个指标 null 而不是 0
# --------------------------------------------------------------------------- #

def test_empty_store_returns_200_with_zero_structure(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.get("/api/journal/stats")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    body = response.json()

    assert set(body) == EXPECTED_KEYS
    assert set(body["deviation_review"]) == EXPECTED_DEVIATION_KEYS
    assert "domain" not in body and "payload" not in body  # 不是 LadderEnvelope

    assert body["local_only"] is True
    assert body["store"] == "local_sqlite"
    assert body["data_source_status"] == "missing"
    assert body["range"] == {"from": None, "to": None}
    for key in (
        "total_trades", "buy_count", "sell_count", "closed_rounds",
        "wins", "losses", "flats",
    ):
        assert body[key] == 0, key
    # 无样本不拿 0 冒充
    for key in ("win_rate", "avg_win", "avg_loss", "profit_loss_ratio"):
        assert body[key] is None, key
    assert body["realized_pnl"] == 0.0
    assert body["unmatched_sell_qty"] == 0.0
    assert body["deviation_review"] == {"threshold_pct": 1.0, "count": 0}

    # 只读：查询不该在磁盘上建出空库
    assert not (tmp_path / "journal.sqlite").exists()


# --------------------------------------------------------------------------- #
# 2. 有数据：数字与 CLI 同源，且不含 symbols / 明细 / note
# --------------------------------------------------------------------------- #

def test_with_data_returns_aggregates_without_symbols_or_notes(tmp_path: Path) -> None:
    _seed_trades(tmp_path)
    response = _client(tmp_path).get("/api/journal/stats", params={"from": DATE, "to": DATE})
    assert response.status_code == 200, response.text
    body = response.json()

    assert set(body) == EXPECTED_KEYS
    assert body["data_source_status"] == "ok"
    assert body["range"] == {"from": DATE, "to": DATE}
    assert body["total_trades"] == 2
    assert body["buy_count"] == 1
    assert body["sell_count"] == 1
    assert body["closed_rounds"] == 1
    assert body["wins"] == 1
    assert body["win_rate"] == 100.0
    assert body["avg_win"] == 100.0
    assert body["avg_loss"] is None  # 没有亏损样本
    assert body["profit_loss_ratio"] is None
    assert body["realized_pnl"] == 100.0

    text = response.text
    assert SENTINEL_NOTE not in text
    assert "600000" not in text  # 标的代码/清单不外泄
    assert not set(FORBIDDEN_KEYS) & _all_keys(body), _all_keys(body)


def test_deviation_threshold_and_count_only(tmp_path: Path) -> None:
    """偏离只回次数（`count`），不回逐笔明细；阈值参数真的生效。

    这笔：计划价 10.0、成交价 10.05 → 偏离 0.5%。
    - `deviation=0.001`（0.1%）→ 命中 1 笔；
    - 默认 1% → 未命中 0 笔。
    """
    journal_log.add_trade(
        "600519", "buy", 10.05, 10.0, note=SENTINEL_NOTE, trade_date=DATE,
        plan_price=10.0, root=tmp_path,
    )
    body = _client(tmp_path).get("/api/journal/stats", params={"deviation": "0.001"}).json()
    assert body["deviation_review"] == {"threshold_pct": 0.1, "count": 1}
    assert "600519" not in json.dumps(body, ensure_ascii=False)

    body = _client(tmp_path).get("/api/journal/stats").json()  # 默认 1%
    assert body["deviation_review"] == {"threshold_pct": 1.0, "count": 0}


# --------------------------------------------------------------------------- #
# 3. 隐私：投影结果是 strip_private 的不动点
# --------------------------------------------------------------------------- #

def test_payload_is_strip_private_identity(tmp_path: Path) -> None:
    _seed_trades(tmp_path)
    for path in ("/api/journal/stats", "/api/journal/stats?from=2026-01-01&to=2026-12-31"):
        body = _client(tmp_path).get(path).json()
        assert strip_private(body) == body
        assert private_keys_present(body) == []


def test_projection_drops_forbidden_fields_even_if_upstream_adds_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """上游（`compute_stats`）若把 `symbols` / `trades` / `note` 塞回来，投影层必须显式丢弃。"""

    def leaky_stats(**_: Any) -> dict[str, Any]:
        return {
            "local_only": True,
            "store": "local_sqlite",
            "data_source_status": "ok",
            "total_trades": 1,
            "buy_count": 1,
            "sell_count": 0,
            "closed_rounds": 0,
            "wins": 0,
            "losses": 0,
            "flats": 0,
            "win_rate": None,
            "avg_win": None,
            "avg_loss": None,
            "profit_loss_ratio": None,
            "realized_pnl": 0.0,
            "unmatched_sell_qty": 0.0,
            "symbols": ["600000"],  # 标的清单：禁止出站
            "deviation_review": {
                "threshold_pct": 1.0,
                "count": 1,
                "trades": [{"code": "600000", "note": SENTINEL_NOTE}],  # 撞 PRIVATE_KEYS
                "note": SENTINEL_NOTE,
            },
        }

    monkeypatch.setattr("ladderstock.web.server.compute_stats", leaky_stats)
    response = _client(tmp_path).get("/api/journal/stats")
    assert response.status_code == 200, response.text
    body = response.json()

    assert set(body) == EXPECTED_KEYS
    assert set(body["deviation_review"]) == EXPECTED_DEVIATION_KEYS
    text = response.text
    assert SENTINEL_NOTE not in text
    assert "600000" not in text
    assert not set(FORBIDDEN_KEYS) & _all_keys(body), _all_keys(body)
    assert strip_private(body) == body


# --------------------------------------------------------------------------- #
# 4. 回环独占：非回环 403，Host 不在白名单 403
# --------------------------------------------------------------------------- #

def test_non_loopback_source_is_403(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_trades(tmp_path)
    # 即使运维开了远程访问 + Token，也不能把日志放出去（Token 管「谁能进」，不管「数据出不出」）
    monkeypatch.setenv("LDST_WEB_ALLOW_REMOTE", "1")
    monkeypatch.setenv("LDST_WEB_TOKEN", "0123456789abcdef0123")

    response = _client(tmp_path, client=REMOTE).get("/api/journal/stats")
    assert response.status_code == 403
    assert response.json()["detail"] == "日志只接受回环来源"
    assert "100" not in response.text  # 任何聚合数字都不能随 403 漏出

    # 回环来源但不带合法 Host（DNS rebinding 场景）同样 403
    rebinding = _client(tmp_path).get(
        "/api/journal/stats", headers={"host": "evil.example.com"}
    )
    assert rebinding.status_code == 403

    # 对照：回环 + 正常 Host 放行
    assert _client(tmp_path).get("/api/journal/stats").status_code == 200


def test_control_switch_does_not_close_journal_stats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`LDST_CONTROL_ENABLED=0` 只关写面：日志统计是只读，必须照常 200。"""
    monkeypatch.setenv(control.CONTROL_ENABLED_ENV, "0")
    client = _client(tmp_path)
    assert client.get("/api/control/peers").status_code == 404
    assert client.get("/api/journal/stats").status_code == 200


# --------------------------------------------------------------------------- #
# 5. 只 GET + 参数校验 + Tier 2 未开
# --------------------------------------------------------------------------- #

def test_only_get_is_allowed(tmp_path: Path) -> None:
    client = _client(tmp_path)
    for method in ("post", "put", "patch", "delete"):
        response = getattr(client, method)("/api/journal/stats")
        assert response.status_code == 405, (method, response.status_code)
        assert response.headers["allow"] == "GET"


@pytest.mark.parametrize(
    "params",
    [
        {"from": "2026-13-01"},  # 非法日期
        {"from": "20261029"},  # 非 YYYY-MM-DD
        {"to": "2026/10/29"},
        {"deviation": "2"},
        {"deviation": "-0.1"},
        {"deviation": "abc"},
        {"deviation": "nan"},
        {"from": DATE, "to": "2026-01-01"},  # from > to
    ],
)
def test_invalid_params_are_400(tmp_path: Path, params: dict[str, str]) -> None:
    response = _client(tmp_path).get("/api/journal/stats", params=params)
    assert response.status_code == 400, response.text
    assert isinstance(response.json()["detail"], str)


def test_tier2_entries_endpoint_is_not_open(tmp_path: Path) -> None:
    """Tier 2（`/api/journal/entries` 单笔明细）本单不做：路径仍是 404。"""
    assert _client(tmp_path).get("/api/journal/entries").status_code == 404
