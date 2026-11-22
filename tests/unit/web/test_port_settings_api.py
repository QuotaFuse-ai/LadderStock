# @vitest-environment 无关（pytest）; noqa: ERA001
"""`/api/settings/port`（DEC-092）：读 / 改 / 还原看板端口。

四类红线：范围校验、保留端口拒绝、占用端口 409（带占用者）、保存只写
board_user 且自动避让结果不被吞掉。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ladderstock.supervisor.port_allocator import (
    clear_board_user_pref,
    load_board_override,
    load_board_user_pref,
    save_port_overrides,
)
from ladderstock.web.port_settings import build_port_settings_router

RESERVED = {8000, 8787, 8002, 8790, 8791, 8765, 8786, 8100, 8101, 8788, 8789, 5950, 5951}


def _client(
    tmp_path: Path,
    *,
    port_free=None,
    request_port=None,
) -> TestClient:
    router = build_port_settings_router(
        tmp_path,
        default_board=5950,
        reserved_ports=set(RESERVED),
        port_free=port_free,
        request_port=request_port,
    )
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_get_returns_settings_and_current_port(tmp_path: Path) -> None:
    save_port_overrides(tmp_path, 5950, {"tickhub": (8000, 8787)})
    with _client(tmp_path, request_port=lambda: 5950) as client:
        payload = client.get("/api/settings/port").json()
    assert payload["default_board"] == 5950
    assert payload["user_board"] is None
    assert payload["current_board"] == 5950
    assert payload["takes_effect"] == "restart"


def test_put_saves_user_pref_when_port_free(tmp_path: Path) -> None:
    with _client(tmp_path, port_free=lambda port: True) as client:
        reply = client.put("/api/settings/port", json={"board": 1111})
    assert reply.status_code == 200
    assert reply.json()["board"] == 1111
    assert load_board_user_pref(tmp_path) == 1111


def test_put_keeps_avoidance_record_behind(tmp_path: Path) -> None:
    """保存偏好不能吞掉自动避让记录（board/modules 段原样保留）。"""
    save_port_overrides(tmp_path, 5952, {"tickhub": (8003, 8788)})
    with _client(tmp_path, port_free=lambda port: True) as client:
        assert client.put("/api/settings/port", json={"board": 1111}).status_code == 200
    assert load_board_override(tmp_path) == 5952
    from ladderstock.supervisor.port_allocator import load_port_overrides

    assert load_port_overrides(tmp_path)["tickhub"] == (8003, 8788)

    # 回写避让结果也不得吞掉偏好
    save_port_overrides(tmp_path, 5953, {"tickhub": (8003, 8788)})
    assert load_board_user_pref(tmp_path) == 1111


def test_put_rejects_reserved_port(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        # 默认口 5950 走「设回默认=清除」分支，不算拒绝；其余保留口必须 422
        for bad in (8765, 5951, 8000, 8788):
            reply = client.put("/api/settings/port", json={"board": bad})
            assert reply.status_code == 422, bad
        # 真占用探针下，5950 也照常可“清除”而不误报
        assert client.put("/api/settings/port", json={"board": 5950}).status_code == 200
    assert load_board_user_pref(tmp_path) is None


def test_put_rejects_out_of_range(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        assert client.put("/api/settings/port", json={"board": 80}).status_code == 422
        assert client.put("/api/settings/port", json={"board": 99999}).status_code == 422


def test_put_conflict_409_with_owner_hint(tmp_path: Path) -> None:
    # port_free 语义 = 「端口空闲吗」；2222 被占用 → False
    with _client(tmp_path, port_free=lambda port: port != 2222) as client:
        reply = client.put("/api/settings/port", json={"board": 2222})
    assert reply.status_code == 409
    assert "2222" in reply.json()["detail"]
    assert load_board_user_pref(tmp_path) is None


def test_put_default_port_clears_pref(tmp_path: Path) -> None:
    with _client(tmp_path, port_free=lambda port: True) as client:
        assert client.put("/api/settings/port", json={"board": 1111}).status_code == 200
        reply = client.put("/api/settings/port", json={"board": 5950})
    assert reply.status_code == 200
    assert reply.json()["action"] == "cleared"
    assert load_board_user_pref(tmp_path) is None


def test_delete_clears_pref(tmp_path: Path) -> None:
    from ladderstock.supervisor.port_allocator import save_board_user_pref

    save_board_user_pref(tmp_path, 1111)
    with _client(tmp_path) as client:
        reply = client.delete("/api/settings/port")
    assert reply.status_code == 200
    assert load_board_user_pref(tmp_path) is None

    clear_board_user_pref(tmp_path)  # 幂等：清了再清不报错
