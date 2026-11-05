"""QUOT-446：`dragon_tiger` 的「默认全市场 / 显式代码回补」双口径。

`dragon_tiger` 的 `code` 是可选参数：调度口径是当日全市场榜（不带 code），
但事件回看需要某只股票的历史榜，所以 `ldst sync --dataset dragon_tiger
--codes 600000` 必须切到逐只取数。这个分支由 `accepts_optional_code` 控制，
不把 `dragon_tiger` 放进 `CODE_POOL_DATASETS`，否则调度会变成逐只扇出。
"""

from __future__ import annotations

from typing import Any

import pytest
from fixtures.fake_peers import FakeTransport, make_registry, tickhub_payload

from ladderstock.cli.sync import run_sync
from ladderstock.data import duckdb_store, watermark

DAY = "2026-10-24"
CODE = "600000"

SOURCES: dict[str, Any] = {
    "peers": {
        "tickhub": {
            "product": "TickHub-CN",
            "cli": "th-cn",
            "mcp_name": "th-cn",
            "url_env": "LDST_TEST_FAKE_TICKHUB_URL",
            "default_url": "http://127.0.0.1:1/mcp",
            "timeout_sec": 1,
        }
    },
    "sources": [
        {
            "id": "tickhub_dragon_tiger",
            "dataset": "dragon_tiger",
            "via": "tickhub",
            "tool": "dragon_tiger",
            "primary": True,
            "fallback": [],
            "timeout_sec": 1,
            "retries": 0,
            "cooldown_sec": 60,
        }
    ],
}

SYNC: dict[str, Any] = {
    "sync": {
        "datasets": {
            "dragon_tiger": {
                "primary": "tickhub_dragon_tiger",
                "fallback": [],
                "watermark_field": "trade_date",
                "mode": "replace",
            }
        }
    }
}


def _rows() -> list[dict[str, Any]]:
    return [
        {
            "code": CODE,
            "trade_date": DAY,
            "reason": "涨幅偏离",
            "close": 10.5,
            "change_pct": 9.9,
        }
    ]


def _transport() -> FakeTransport:
    return FakeTransport(handlers={"dragon_tiger": lambda _params: tickhub_payload(_rows())})


@pytest.fixture()
def data_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LDST_DATA_DIR", str(tmp_path))
    return tmp_path


def test_dragon_tiger_scheduled_scope_stays_whole_market(data_home) -> None:
    transport = _transport()

    payload = run_sync(
        make_registry(transport, data_home, sources=SOURCES, sync=SYNC),
        watermark.WatermarkStore(root=data_home),
        ["dragon_tiger"],
    )

    assert payload["results"][0]["status"] == "ok"
    assert [call.params for call in transport.calls] == [{}], "调度口径不带 code"
    assert duckdb_store.dataset_rows("dragon_tiger") == 1


def test_dragon_tiger_explicit_codes_switches_to_code_pool(data_home) -> None:
    transport = _transport()

    payload = run_sync(
        make_registry(transport, data_home, sources=SOURCES, sync=SYNC),
        watermark.WatermarkStore(root=data_home),
        ["dragon_tiger"],
        full=True,
        codes=CODE,
    )

    assert payload["results"][0]["status"] == "ok"
    assert [call.params for call in transport.calls] == [{"code": CODE}]
    assert duckdb_store.dataset_rows("dragon_tiger") == 1
