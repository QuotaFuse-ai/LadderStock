"""托盘抽象与退出契约（QUOT-527）。

`TrayController.quit()` 是唯一的退出入口，语义固定为：

1. 调 `quit_handler`（生产 = `SupervisorEngine.stop_owned()`），
   只停**本控制面 spawn 的子进程**；
2. adopted peer（tickhub / mem0 / financecoffer 等外部实例）原样保留，
   报告里的 `kept_adopted` 会带回给 UI/日志；
3. 重复退出幂等。

GUI 图标用可选依赖 `pystray`（`pip install 'ladderstock[tray]'`）；未安装时
`service tray --check` 如实报告不可用，`--headless --quit-after` 仍可跑通
退出契约（无图标、只验证生命周期），供 CI/服务端验收。
"""

from __future__ import annotations

import threading
import time
import webbrowser
from collections.abc import Callable, Mapping
from typing import Any, Protocol


class TrayUnavailable(RuntimeError):
    """当前环境没有可用的托盘后端（缺可选依赖 / 无图形会话）。"""


class TrayBackend(Protocol):
    name: str

    def run(self, controller: TrayController) -> None:
        """阻塞运行托盘，直到退出；退出动作必须经过 `controller.quit()`。"""


class TrayController:
    """托盘退出契约：quit → stop_owned → 保留 adopted。"""

    def __init__(
        self,
        quit_handler: Callable[[], Mapping[str, Any]],
        *,
        panel_url: str | None = None,
        open_url: Callable[[str], Any] | None = None,
    ) -> None:
        self._quit_handler = quit_handler
        self.panel_url = panel_url
        self._open_url = open_url or webbrowser.open
        self._lock = threading.Lock()
        self._result: dict[str, Any] | None = None

    @property
    def result(self) -> dict[str, Any] | None:
        """最近一次退出结果；托盘未退出过时为 None。"""
        return dict(self._result) if self._result is not None else None

    def open_panel(self) -> bool:
        if not self.panel_url:
            return False
        try:
            return bool(self._open_url(self.panel_url))
        except Exception:  # noqa: BLE001 - 没有浏览器不应拖垮托盘
            return False

    def quit(self) -> dict[str, Any]:
        """执行退出：只停自有子进程，adopted peer 不碰；重复调用幂等。"""
        with self._lock:
            if self._result is not None:
                return dict(self._result)
            try:
                report = dict(self._quit_handler())
            except Exception as exc:  # noqa: BLE001 - 退出失败也要给 UI 一个明确结果
                result: dict[str, Any] = {
                    "ok": False,
                    "quit": True,
                    "error": str(exc),
                    "kept_adopted": [],
                }
            else:
                result = {
                    "ok": bool(report.get("ok", True)),
                    "quit": True,
                    "stop": report,
                    "kept_adopted": list(report.get("kept_adopted") or []),
                }
            self._result = result
            return dict(result)


class HeadlessTrayBackend:
    """无图标后端：等 `quit_after` 秒后执行一次退出（CI / 服务端验收用）。"""

    name = "headless"

    def __init__(self, quit_after: float = 0.0) -> None:
        self.quit_after = max(0.0, float(quit_after))

    def run(self, controller: TrayController) -> None:
        if self.quit_after:
            time.sleep(self.quit_after)
        controller.quit()


class PystrayTrayBackend:
    """pystray 实现（Windows 通知区 / macOS 菜单栏 / Linux 状态栏）。"""

    name = "pystray"

    def run(self, controller: TrayController) -> None:
        pystray, image_module = _import_pystray()
        holder: list[Any] = []
        icon = pystray.Icon(
            "ladderstock",
            _icon_image(image_module),
            "LadderStock",
            pystray.Menu(
                pystray.MenuItem(
                    "打开看板",
                    lambda _icon, _item: controller.open_panel(),
                    default=True,
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem(
                    "退出（全停）",
                    lambda _icon, _item: _quit_and_stop(holder[0], controller),
                ),
            ),
        )
        holder.append(icon)
        icon.run()


def _quit_and_stop(icon: Any, controller: TrayController) -> None:
    try:
        controller.quit()
    finally:
        icon.stop()


def _icon_image(image_module: Any) -> Any:
    from PIL import ImageDraw

    image = image_module.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((6, 6, 58, 58), fill=(32, 96, 180, 255))
    draw.rectangle((20, 28, 44, 36), fill=(255, 255, 255, 255))
    return image


def _import_pystray() -> tuple[Any, Any]:
    try:
        import pystray  # type: ignore[import-not-found]
        from PIL import Image
    except Exception as exc:  # noqa: BLE001 - 缺依赖/无可显示后端都归为不可用
        raise TrayUnavailable(
            "托盘需要可选依赖：pip install 'ladderstock[tray]'（pystray + pillow）；"
            "无图形会话时可用 `service tray --headless --quit-after 0` 验证退出契约"
        ) from exc
    return pystray, Image


def tray_available() -> tuple[bool, str]:
    """探测 GUI 托盘是否可用（只 import，不创建图标）。"""
    try:
        _import_pystray()
    except TrayUnavailable as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001 - pystray 在无 DISPLAY 时可能导入即失败
        return False, f"托盘后端不可用：{exc}"
    return True, "pystray"


def build_tray_backend(*, headless: bool = False, quit_after: float = 0.0) -> TrayBackend:
    if headless:
        return HeadlessTrayBackend(quit_after)
    _import_pystray()  # 不可用直接抛 TrayUnavailable，调用方落 EXIT_CONFIG
    return PystrayTrayBackend()


__all__ = [
    "HeadlessTrayBackend",
    "PystrayTrayBackend",
    "TrayBackend",
    "TrayController",
    "TrayUnavailable",
    "build_tray_backend",
    "tray_available",
]
