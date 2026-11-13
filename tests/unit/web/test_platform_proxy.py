"""同源 `/platform/*` 转发（QUOT-589）。

回归覆盖：
A. 同源化——serving backend 把 `/platform/*` 转发到本实例自己的看门狗；
   前端不再直连 5952。
B. 实例化看门狗——端口由 `LDST_WATCHDOG_PORT` 注入（缺省 5952 = prod 语义
   不变）；dev 实例注入 5953。
C. 只转发 health / restart 两面，白名单外 404；看门狗不可达回 503。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ladderstock.web.platform_proxy import (
    DEFAULT_PROXY_WATCHDOG_PORT,
    WATCHDOG_PORT_ENV,
    forward_platform,
    watchdog_port,
)


class FakeUpstream:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload
        self.headers = {"content-type": "application/json"}

    @property
    def content(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_watchdog_port_defaults_to_prod_5952() -> None:
    # 缺省语义 = prod 看门狗 5952（DEC-063 裁决），deployed 5950 行为不变
    assert watchdog_port({}) == DEFAULT_PROXY_WATCHDOG_PORT == 5952
    assert watchdog_port({WATCHDOG_PORT_ENV: ""}) == 5952


def test_watchdog_port_env_injection_scopes_the_instance() -> None:
    # dev 实例注入 5953：代理只转发到本实例自己的看门狗
    assert watchdog_port({WATCHDOG_PORT_ENV: "5953"}) == 5953


@pytest.mark.parametrize("bad", ["abc", "0", "70000", "-1"])
def test_watchdog_port_falls_back_on_garbage(bad: str) -> None:
    assert watchdog_port({WATCHDOG_PORT_ENV: bad}) == 5952


def test_forward_hits_the_instance_watchdog_not_5952() -> None:
    calls: list[tuple[str, str, dict[str, str]]] = []

    def fake_request(method: str, url: str, headers: dict[str, str], **kw: Any) -> FakeUpstream:
        calls.append((method, url, headers))
        return FakeUpstream(200, {"status": "ok"})

    response = forward_platform(
        "GET", "health", port=5953, request_fn=fake_request
    )
    assert response.status_code == 200
    assert calls[0][1] == "http://127.0.0.1:5953/platform/health"


def test_forward_post_spoofs_watchdog_origin_for_the_whitelist() -> None:
    calls: list[dict[str, str]] = []

    def fake_request(method: str, url: str, headers: dict[str, str], **kw: Any) -> FakeUpstream:
        calls.append(headers)
        return FakeUpstream(200, {"ok": True})

    response = forward_platform(
        "POST", "restart", port=5953, body=b"{}", request_fn=fake_request
    )
    assert response.status_code == 200
    # 服务端转发统一冒充看门狗自己的回环 origin：白名单语义不变，浏览器无需 CORS
    assert calls[0]["Origin"] == "http://127.0.0.1:5953"


def test_forward_returns_503_when_watchdog_unreachable() -> None:
    def fake_request(method: str, url: str, **kw: Any) -> Any:
        raise OSError("connection refused")

    response = forward_platform("GET", "health", port=5953, request_fn=fake_request)
    assert response.status_code == 503
    assert "5953" in response.body.decode("utf-8")


@pytest.mark.parametrize("path", ["../../etc", "anything-else"])
def test_forward_whitelist_paths_only(path: str) -> None:
    def fake_request(method: str, url: str, **kw: Any) -> Any:
        raise AssertionError("白名单外不得出网")

    response = forward_platform("GET", path, port=5953, request_fn=fake_request)
    assert response.status_code == 404


def test_router_mounted_in_serving_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 项验收：serving app 有同源 `/platform/health`，不落 SPA 兜底。"""

    calls: list[str] = []

    def fake_request(method: str, url: str, **kw: Any) -> FakeUpstream:
        calls.append(url)
        return FakeUpstream(200, {"status": "ok", "db_ok": True})

    monkeypatch.setattr("ladderstock.web.platform_proxy.httpx.request", fake_request)
    monkeypatch.setenv(WATCHDOG_PORT_ENV, "5953")

    from ladderstock.web.server import create_app

    app = create_app(data_root=tmp_path, dist_dir=tmp_path / "no-dist")
    client = TestClient(app)
    response = client.get("/platform/health")

    assert response.status_code == 200
    assert calls == ["http://127.0.0.1:5953/platform/health"]

    # SPA 兜底不吞写方法：`/platform` 白名单外仍 404
    assert client.get("/platform/other").status_code == 404
