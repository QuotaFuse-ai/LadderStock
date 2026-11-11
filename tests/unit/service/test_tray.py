"""托盘退出契约（QUOT-527）：quit → stop_owned → adopted peer 原样保留。

不创建真实图标：headless 后端与注入的 quit_handler 覆盖全部可判定行为。
"""

from __future__ import annotations

from typing import Any

import pytest

import ladderstock.service.tray as tray_module
from ladderstock.service.tray import (
    HeadlessTrayBackend,
    TrayController,
    TrayUnavailable,
    build_tray_backend,
    tray_available,
)


def test_quit_calls_handler_once_and_reports_kept_adopted() -> None:
    calls: list[int] = []

    def handler() -> dict[str, Any]:
        calls.append(1)
        return {
            "ok": True,
            "modules": {"tickhub": {"ok": True}},
            "kept_adopted": ["tickhub"],
        }

    controller = TrayController(handler)
    result = controller.quit()

    assert calls == [1]
    assert result["ok"] is True
    assert result["quit"] is True
    assert result["kept_adopted"] == ["tickhub"]
    assert result["stop"]["modules"]["tickhub"]["ok"] is True
    assert controller.result is not None


def test_quit_is_idempotent_and_does_not_stop_twice() -> None:
    calls: list[int] = []

    def handler() -> dict[str, Any]:
        calls.append(1)
        return {"ok": True, "kept_adopted": []}

    controller = TrayController(handler)
    first = controller.quit()
    second = controller.quit()

    assert calls == [1]
    assert first == second


def test_quit_reports_failure_without_raising() -> None:
    def handler() -> dict[str, Any]:
        raise RuntimeError("stop 失败")

    result = TrayController(handler).quit()

    assert result["ok"] is False
    assert result["quit"] is True
    assert "stop 失败" in result["error"]
    assert result["kept_adopted"] == []


def test_open_panel_uses_injected_opener_and_tolerates_absence() -> None:
    opened: list[str] = []
    controller = TrayController(
        lambda: {"ok": True},
        panel_url="http://127.0.0.1:5950/",
        open_url=lambda url: opened.append(url) or True,
    )
    assert controller.open_panel() is True
    assert opened == ["http://127.0.0.1:5950/"]

    assert TrayController(lambda: {"ok": True}).open_panel() is False


def test_open_panel_swallows_browser_errors() -> None:
    def boom(_url: str) -> bool:
        raise OSError("no browser")

    controller = TrayController(
        lambda: {"ok": True}, panel_url="http://127.0.0.1:5950/", open_url=boom
    )
    assert controller.open_panel() is False


def test_headless_backend_triggers_the_quit_contract() -> None:
    calls: list[int] = []

    def handler() -> dict[str, Any]:
        calls.append(1)
        return {"ok": True, "kept_adopted": ["financecoffer"]}

    controller = TrayController(handler)
    HeadlessTrayBackend(quit_after=0).run(controller)

    assert calls == [1]
    assert controller.result is not None
    assert controller.result["kept_adopted"] == ["financecoffer"]


def test_build_tray_backend_headless_never_requires_gui() -> None:
    backend = build_tray_backend(headless=True, quit_after=0)
    assert backend.name == "headless"


def test_build_tray_backend_surfaces_missing_optional_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing() -> tuple[Any, Any]:
        raise TrayUnavailable("pip install 'ladderstock[tray]'")

    monkeypatch.setattr(tray_module, "_import_pystray", missing)

    with pytest.raises(TrayUnavailable, match="ladderstock\\[tray\\]"):
        build_tray_backend(headless=False)
    available, detail = tray_available()
    assert available is False
    assert "ladderstock[tray]" in detail
