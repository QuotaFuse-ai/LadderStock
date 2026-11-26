"""安装状态机（DEC-086 决策 6：只有「完整包」一条路径，没有轻量模式）。

状态落 `install_state.json`，页面/CLI 只读这一个文件判断进度，不在内存里另起一套。
任何一步失败都落到 `failed` 并带上 `failed_step` 与原因——「装了一半没人知道」比没装更糟。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

#: 状态：未安装 → 规划 → 取件 → 初始化数据库 → 写配置 → 就绪；失败独立，卸载中独立
STATES: tuple[str, ...] = (
    "not_installed",
    "planning",
    "fetching",
    "initializing",
    "configuring",
    "ready",
    "failed",
    "uninstalling",
)

#: 状态机允许的推进（其余跳转一律拒绝，避免「跳到 ready 但其实没初始化」）
#:
#: `ready → planning` 是**重新安装 / 补齐组件**（例如先不装 embedding，后来补装）：
#: 装完再装一次是正常路径，不该被当成非法跳转——本机实测从设置页点「重新安装」
#: 曾因此直接失败。`ready → fetching` 不放行，重装一律先回到 planning。
TRANSITIONS: dict[str, tuple[str, ...]] = {
    "not_installed": ("planning", "failed"),
    "planning": ("fetching", "failed"),
    "fetching": ("initializing", "failed"),
    "initializing": ("configuring", "failed"),
    "configuring": ("ready", "failed"),
    "ready": ("planning", "uninstalling", "failed"),
    "failed": ("planning", "not_installed", "uninstalling"),
    "uninstalling": ("not_installed", "failed"),
}


class IllegalTransition(ValueError):
    """状态跳转不合法。"""


def read_state(root: Path | None = None) -> dict[str, Any]:
    """读状态；文件缺失按 `not_installed`（没装过就是没装过，不猜）。"""
    from ladderstock.modules.financecoffer import layout

    path = layout.state_path(root)
    if not path.is_file():
        return {"state": "not_installed", "path": str(path)}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": "failed", "detail": f"安装状态文件损坏：{path}", "path": str(path)}
    if not isinstance(document, dict) or document.get("state") not in STATES:
        return {"state": "failed", "detail": f"安装状态文件结构不合法：{path}", "path": str(path)}
    document.setdefault("path", str(path))
    return document


def write_state(
    root: Path | None, state: str, *, detail: str = "", step: str | None = None, **extra: Any
) -> dict[str, Any]:
    """写状态；非法状态或非法跳转直接抛错，不做「尽力而为」写盘。"""
    from ladderstock.modules.financecoffer import layout

    if state not in STATES:
        raise IllegalTransition(f"未知安装状态：{state!r}")
    previous = read_state(root)
    before = str(previous.get("state") or "not_installed")
    if state != before and state not in TRANSITIONS.get(before, ()):
        raise IllegalTransition(f"不允许的状态跳转：{before} → {state}")

    path = layout.state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    document: dict[str, Any] = {
        "state": state,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "detail": detail,
    }
    if step:
        document["step"] = step
    if state == "failed":
        document["failed_step"] = step or previous.get("step") or before
    document.update(extra)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
    return document


__all__ = [
    "STATES",
    "TRANSITIONS",
    "IllegalTransition",
    "read_state",
    "write_state",
]
