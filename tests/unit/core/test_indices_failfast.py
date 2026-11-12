"""`core/indices.py` 的 fail-fast 单元测试（QUOT-571）。

钉住四层快降级，任何一层失效都会把 `/api/indices` 拖过前端 8s 门限：

| 机制 | 断言 |
|---|---|
| 健康通道短路 | crash / needs_human → 该 peer 的块零请求、出 missing；recovered / offline 不短路 |
| 块级熔断 | 一格超时 / 拒连 → 同块余下格不再发请求，原因写明「不再逐格等待」 |
| 并行取数 | 一块慢不拖另一块（耗时 ≈ 最慢那块，不是各块之和） |
| 总预算 | 超预算的块出 missing + 原因，不补 0；已取回的真数据照常上屏 |

全程用桩 registry，不拨网络；真实 `ProviderRegistry` 治理在
`tests/unit/web/test_indices_endpoint.py` 钉。
"""

from __future__ import annotations

import functools
import time
from types import SimpleNamespace
from typing import Any

import pytest

from ladderstock.core.indices import (
    BLOCK_BUDGET,
    BLOCK_FAIL_FAST,
    PEER_DOWN_EVENT,
    PeerDownSignal,
    fetch_indices,
)
from ladderstock.data.provider_registry import SourceUnavailable
from ladderstock.supervisor.health_events import (
    EVENT_CRASH,
    EVENT_NEEDS_HUMAN,
    EVENT_OFFLINE,
    EVENT_RECOVERED,
    HealthEventBus,
)

CN_ROWS = [
    {
        "code": code,
        "market": market,
        "is_index": True,
        "price": 3608.1,
        "change": -29.86,
        "change_pct": -0.82,
        "prev_close": 3637.96,
        "timestamp": "20261103161500",
    }
    for code, market in (
        ("000001", "SH"),
        ("000300", "SH"),
        ("399001", "SZ"),
        ("399006", "SZ"),
    )
]
US_ROWS = [
    {
        "symbol": symbol,
        "price": 51570.09,
        "change": 47.03,
        "change_pct": 0.09,
        "prev_close": 51523.06,
        "timestamp": "2026-11-06T22:10:00+08:00",
    }
    for symbol in ("DJI", "IXIC")
]
HK_ROWS = {
    "HSI.HK": {
        "symbol": "HSI.HK",
        "market": "HK",
        "price": 24315.92,
        "change": -241.12,
        "change_pct": -0.98,
        "prev_close": 24557.04,
        "timestamp": "2026-11-11 18:31:03",
    },
    "HSTECH.HK": {
        "symbol": "HSTECH.HK",
        "market": "HK",
        "price": 4027.06,
        "change": -57.39,
        "change_pct": -1.41,
        "prev_close": 4084.45,
        "timestamp": "2026-11-11 16:08:28",
    },
}
US_PEER_SYMBOLS = ("DJI", "IXIC", "HSI.HK", "HSTECH.HK")


class _ScriptedRegistry:
    """按 `(dataset, symbol)` 查脚本：给回包 / 抛异常 / 睡一会儿，`delay=0` 即全健康。"""

    def __init__(
        self,
        *,
        raises: dict[tuple[str, str], BaseException] | None = None,
        delays: dict[tuple[str, str], float] | None = None,
        hk_rows: dict[str, dict[str, Any]] | None = None,
        us_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self.raises = dict(raises or {})
        self.delays = dict(delays or {})
        self.hk_rows = HK_ROWS if hk_rows is None else hk_rows
        self.us_rows = US_ROWS if us_rows is None else us_rows
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def fetch_live(
        self,
        dataset: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
        **_: Any,
    ):
        params = dict(params or {})
        self.calls.append((dataset, params))
        symbol = str(params.get("symbol") or "")
        failure = self.raises.get((dataset, symbol))
        if failure is not None:
            raise failure
        delay = self.delays.get((dataset, symbol), 0.0)
        if delay:
            time.sleep(delay)
        if dataset == "index_quote":
            records: list[dict[str, Any]] = CN_ROWS
        elif dataset == "index_quote_us":
            records = [row for row in self.us_rows if row["symbol"] == symbol]
        elif dataset == "index_quote_hk":
            records = [self.hk_rows[symbol]] if symbol in self.hk_rows else []
        else:  # pragma: no cover - 白名单外的数据集不该被请求
            raise AssertionError(f"未预期的数据集：{dataset}")
        return SimpleNamespace(envelope=SimpleNamespace(payload={"records": records}))

    def peer_symbol_calls(self) -> list[str]:
        return [params["symbol"] for dataset, params in self.calls if dataset != "index_quote"]


def _cells(strip) -> dict[str, dict[str, Any]]:
    return {cell["code"]: cell for cell in strip.payload["cells"]}


def _peer_bus() -> HealthEventBus:
    return HealthEventBus()


def _signal(bus: HealthEventBus, peer: str = "tickhub_us", **reasons: str) -> PeerDownSignal:
    return PeerDownSignal(
        peer=peer,
        subscribe=functools.partial(bus.since, module=peer),
        reasons={EVENT_CRASH: "进程已退出", EVENT_NEEDS_HUMAN: "需人工确认"} | reasons,
    )


def _default_signal(bus: HealthEventBus, peer: str = "tickhub_us") -> PeerDownSignal:
    """没配白话原因的信号：走 `PEER_DOWN_EVENT` 兜底。"""
    return PeerDownSignal(
        peer=peer, subscribe=functools.partial(bus.since, module=peer), reasons={}
    )


def _publish(bus: HealthEventBus, event: str, peer: str = "tickhub_us") -> None:
    bus.publish(module=peer, event=event, health="down", state="down", reason="桩事件")


# --------------------------------------------------------------------------- #
# 健康路径不回退
# --------------------------------------------------------------------------- #


def test_healthy_path_fills_all_eight_cells() -> None:
    registry = _ScriptedRegistry()
    strip = fetch_indices(registry)
    cells = _cells(strip)
    assert all(cell["available"] for cell in cells.values())
    assert strip.status == "ok"
    assert registry.peer_symbol_calls() == list(US_PEER_SYMBOLS)


def test_per_call_timeout_is_handed_to_registry() -> None:
    """单格预算要传下去，否则 registry 用 sources.yaml 的 10s——fail-fast 等于没做。"""
    seen: list[float | None] = []

    class _Spy(_ScriptedRegistry):
        def fetch_live(self, dataset: str, *, timeout: float | None = None, **kw: Any):
            seen.append(timeout)
            return super().fetch_live(dataset, timeout=timeout, **kw)

    fetch_indices(_Spy(), cell_timeout=1.25)
    assert seen == [1.25] * 5


# --------------------------------------------------------------------------- #
# 块级熔断
# --------------------------------------------------------------------------- #


def test_timeout_on_first_symbol_stops_rest_of_block() -> None:
    registry = _ScriptedRegistry(raises={("index_quote_us", "DJI"): TimeoutError("read timed out")})
    strip = fetch_indices(registry)
    cells = _cells(strip)
    # 美股两格都缺：DJI 是真实失败，IXIC 是熔断跳过（原因写明不逐格等）
    assert cells["DJI"]["available"] is False
    assert cells["IXIC"]["available"] is False
    assert cells["IXIC"]["price"] is None
    assert BLOCK_FAIL_FAST in cells["IXIC"]["reason"]
    # 港股块是另一块，照常取（单格失败不连坐另一块）
    assert cells["HSI"]["available"] is True
    assert cells["HSTECH"]["available"] is True
    # IXIC 一次请求都没发
    assert registry.peer_symbol_calls() == ["DJI", "HSI.HK", "HSTECH.HK"]
    assert strip.status == "partial"


def test_connection_error_also_trips_block_breaker() -> None:
    registry = _ScriptedRegistry(raises={("index_quote_hk", "HSI.HK"): ConnectionError("拒绝连接")})
    cells = _cells(fetch_indices(registry))
    assert cells["HSI"]["available"] is False
    assert BLOCK_FAIL_FAST in cells["HSTECH"]["reason"]
    assert registry.peer_symbol_calls() == ["DJI", "IXIC", "HSI.HK"]
    assert cells["DJI"]["available"] is True


def test_business_failure_does_not_trip_block_breaker() -> None:
    """上游拒收 / 无数据只算那一格：下一格照常发请求（可能只是这个 symbol 没行情）。"""
    registry = _ScriptedRegistry(
        raises={("index_quote_hk", "HSI.HK"): SourceUnavailable("腾讯无 HSI.HK 行情")}
    )
    cells = _cells(fetch_indices(registry))
    assert cells["HSI"]["available"] is False
    assert "腾讯无 HSI.HK 行情" in cells["HSI"]["reason"]
    assert cells["HSTECH"]["available"] is True
    assert registry.peer_symbol_calls() == list(US_PEER_SYMBOLS)


# --------------------------------------------------------------------------- #
# 并行与总预算
# --------------------------------------------------------------------------- #


def test_blocks_run_in_parallel() -> None:
    """美股 / 港股各睡 0.3s：并行耗时 ≈ 0.3s，串行会 ≥ 0.6s。"""
    registry = _ScriptedRegistry(
        delays={("index_quote_us", "DJI"): 0.3, ("index_quote_hk", "HSI.HK"): 0.3}
    )
    started = time.monotonic()
    strip = fetch_indices(registry, cell_timeout=2.0, total_budget=3.0)
    elapsed = time.monotonic() - started
    assert strip.status == "ok"
    assert elapsed < 0.55, f"块间并行失效，耗时 {elapsed:.2f}s"


def test_total_budget_returns_missing_instead_of_waiting() -> None:
    """一块睡穿总预算：预算内返回，那块出 missing + 原因，其它块的真数据照常上屏。"""
    registry = _ScriptedRegistry(delays={("index_quote_hk", "HSI.HK"): 3.0})
    started = time.monotonic()
    strip = fetch_indices(registry, cell_timeout=10.0, total_budget=0.3)
    elapsed = time.monotonic() - started
    assert elapsed < 2.5, f"总预算未生效，耗时 {elapsed:.2f}s"
    cells = _cells(strip)
    assert cells["HSI"]["available"] is False
    assert cells["HSI"]["price"] is None
    assert BLOCK_BUDGET in cells["HSI"]["reason"]
    assert cells["HSTECH"]["available"] is False
    assert cells["sh000001"]["available"] is True
    assert cells["DJI"]["available"] is True
    assert strip.status == "partial"


# --------------------------------------------------------------------------- #
# 健康通道短路
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("event", [EVENT_CRASH, EVENT_NEEDS_HUMAN])
def test_health_channel_short_circuits_us_peer(event: str) -> None:
    bus = _peer_bus()
    _publish(bus, event)
    registry = _ScriptedRegistry()
    strip = fetch_indices(registry, peer_down={"tickhub_us": _signal(bus)})
    cells = _cells(strip)
    # 美股 / 港股四格出 missing + 白话原因，一次 peer 请求都没发
    for code in ("DJI", "IXIC", "HSI", "HSTECH"):
        assert cells[code]["available"] is False
        assert cells[code]["price"] is None
        assert "index_quote" in cells[code]["reason"]
    assert registry.calls == [("index_quote", {"codes": "sh000001,sh000300,sz399001,sz399006"})]
    assert cells["sh000001"]["available"] is True
    assert strip.status == "partial"


@pytest.mark.parametrize("event", [EVENT_OFFLINE, EVENT_RECOVERED])
def test_only_confirmed_down_events_short_circuit(event: str) -> None:
    """offline（进程在、端口没听）与 recovered 都不是「确认坏了」：照常发请求。"""
    bus = _peer_bus()
    _publish(bus, event)
    registry = _ScriptedRegistry()
    strip = fetch_indices(registry, peer_down={"tickhub_us": _signal(bus)})
    assert strip.status == "ok"
    assert registry.peer_symbol_calls() == list(US_PEER_SYMBOLS)


def test_recovered_after_crash_clears_the_short_circuit() -> None:
    bus = _peer_bus()
    _publish(bus, EVENT_CRASH)
    _publish(bus, EVENT_RECOVERED)
    registry = _ScriptedRegistry()
    strip = fetch_indices(registry, peer_down={"tickhub_us": _signal(bus)})
    assert strip.status == "ok"
    assert registry.peer_symbol_calls() == list(US_PEER_SYMBOLS)


def test_short_circuit_reason_is_peer_scoped() -> None:
    """另一个 peer 坏了不影响 `tickhub_us` 的块（信号按 peer 归属查）。"""
    bus = _peer_bus()
    _publish(bus, EVENT_CRASH, peer="tickhub")
    registry = _ScriptedRegistry(us_rows=[], hk_rows={})
    strip = fetch_indices(
        registry,
        peer_down={"tickhub": _signal(bus, peer="tickhub"), "tickhub_us": _signal(bus)},
    )
    # A 股块被短路：一次 `index_quote` 都没发；美股 / 港股照常发请求
    assert registry.calls and all(dataset != "index_quote" for dataset, _ in registry.calls)
    assert registry.peer_symbol_calls() == list(US_PEER_SYMBOLS)
    cells = _cells(strip)
    assert "进程已退出" in cells["sh000001"]["reason"]
    assert cells["sh000001"]["price"] is None
    assert cells["DJI"]["available"] is False  # 桩里没给行，但原因来自「上游没回」而非短路
    assert "进程已退出" not in cells["DJI"]["reason"]


def test_short_circuit_uses_peer_specific_reason() -> None:
    bus = _peer_bus()
    _publish(bus, EVENT_NEEDS_HUMAN)
    registry = _ScriptedRegistry()
    strip = fetch_indices(
        registry,
        peer_down={"tickhub_us": _signal(bus, **{EVENT_NEEDS_HUMAN: "自动恢复多次失败"})},
    )
    assert "自动恢复多次失败" in _cells(strip)["DJI"]["reason"]


def test_broken_health_channel_falls_back_to_fetching() -> None:
    """健康通道自身出错不能拖垮取数：当没信号，照常打 peer。"""

    def broken(seq: int, events: Any) -> Any:
        raise RuntimeError("事件总线坏了")

    registry = _ScriptedRegistry()
    strip = fetch_indices(
        registry, peer_down={"tickhub_us": PeerDownSignal("tickhub_us", broken, {})}
    )
    assert strip.status == "ok"
    assert registry.peer_symbol_calls() == list(US_PEER_SYMBOLS)


def test_default_reason_when_signal_carries_no_text() -> None:
    bus = _peer_bus()
    _publish(bus, EVENT_CRASH)
    registry = _ScriptedRegistry()
    strip = fetch_indices(registry, peer_down={"tickhub_us": _default_signal(bus)})
    assert PEER_DOWN_EVENT in _cells(strip)["DJI"]["reason"]
    assert registry.peer_symbol_calls() == []
