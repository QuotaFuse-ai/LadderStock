"""`SourceUnavailable` 的 peer 级故障标记必须穿过真实链路（QUOT-571 缺陷 1）。

真实 `HttpMcpTransport._post` 把 httpx 异常包成 `SourceUnavailable(transport_nested=…)`；
`ProviderRegistry._resolve` 在链路全失败时又聚合出一句新的 `SourceUnavailable`——
如果这一步把标记丢了，`core.indices` 的熔断器在生产里永远判不出「整块不通」，只看
裸 `TimeoutError` / `ConnectionError` 就走空了。

这里走真 `ProviderRegistry` + `FakeTransport`（抛生产形态的包装异常），钉住：
- 传输层故障聚合后 `peer_unreachable=True`；
- 业务型失败（无数据 / 拒收）聚合后仍是 `False`。
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import httpx
import pytest
from fixtures.fake_peers import SOURCES_DOC, SYNC_DOC, FakeTransport, make_registry

from ladderstock.data.provider_registry import SourceUnavailable

SOURCES = copy.deepcopy(SOURCES_DOC)
SOURCES["sources"].extend(
    [
        {
            "id": "tickhub_us_index_quote",
            "dataset": "index_quote_us",
            "via": "tickhub_us",
            "tool": "th_us_get_quote",
            "primary": True,
            "fallback": [],
            "timeout_sec": 3,
            "retries": 0,
            "cooldown_sec": 60,
        }
    ]
)


def _registry(tmp_path: Path, error: Exception) -> Any:
    transport = FakeTransport(
        handlers={}, errors={"th_us_get_quote": error}, tools=["th_us_get_quote"]
    )
    return make_registry(
        transport, tmp_path, sources=copy.deepcopy(SOURCES), sync=copy.deepcopy(SYNC_DOC)
    )


@pytest.mark.parametrize(
    "nested", [httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout]
)
def test_aggregated_transport_failure_keeps_peer_marker(
    tmp_path: Path, nested: type[BaseException]
) -> None:
    wrapped = SourceUnavailable("peer 不可达", transport_nested=nested)
    registry = _registry(tmp_path, wrapped)
    with pytest.raises(SourceUnavailable) as caught:
        registry.fetch_live(
            "index_quote_us", params={"symbol": "DJI", "commercial_use": False}, timeout=3
        )
    # 聚合后的那句新异常仍带 peer 级故障标记——熔断器据此熔断整块
    assert caught.value.peer_unreachable is True
    assert caught.value.transport_nested is not None


def test_business_failure_has_no_peer_marker(tmp_path: Path) -> None:
    registry = _registry(tmp_path, SourceUnavailable("腾讯无 DJI 行情"))
    with pytest.raises(SourceUnavailable) as caught:
        registry.fetch_live(
            "index_quote_us", params={"symbol": "DJI", "commercial_use": False}, timeout=3
        )
    assert caught.value.peer_unreachable is False
