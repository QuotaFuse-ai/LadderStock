"""子模块安装状态机（每个 peer 一个状态文件）。

与 `financecoffer/state.py` 的差异：
- 那边一个模块一个状态文件（`<module_root>/install_state.json`）；
- 这边六个 peer 共用一个状态目录（`<modules_root>/_state/<peer>.json`），
  因为 peer 目录在未安装时还不存在，状态文件不能落在它下面。

状态口径保持与增强包一致，前端 / CLI 只读文件，不另起内存态。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

#: 未安装 → 规划 → 取件 → 建环境 → 验入口 → 就绪；失败独立，卸载中独立
STATES: tuple[str, ...] = (
    "not_installed",
    "planning",
    "fetching",
    "preparing",
    "verifying",
    "ready",
    "failed",
    "uninstalling",
)

TRANSITIONS: dict[str, tuple[str, ...]] = {
    "not_installed": ("planning", "failed"),
    "planning": ("fetching", "failed"),
    "fetching": ("preparing", "failed"),
    "preparing": ("verifying", "failed"),
    "verifying": ("ready", "failed"),
    "ready": ("planning", "uninstalling", "failed"),
    "failed": ("planning", "not_installed", "uninstalling"),
    "uninstalling": ("not_installed", "failed"),
}


class IllegalTransition(ValueError):
    """状态跳转不合法。"""


def state_dir(root: Path | None = None) -> Path:
    """状态落盘目录：`<安装根>/file/modules/_state`。"""
    from ladderstock.core import boundary

    return boundary.modules_root() / "_state" if root is None else Path(root) / "file" / "modules" / "_state"


def state_path(peer: str, root: Path | None = None) -> Path:
    return state_dir(root) / f"{peer}.json"


def read_state(peer: str, root: Path | None = None) -> dict[str, Any]:
    """读状态；文件缺失按 `not_installed`。"""
    path = state_path(peer, root)
    if not path.is_file():
        return {"peer": peer, "state": "not_installed", "path": str(path)}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"peer": peer, "state": "failed", "detail": f"安装状态文件损坏：{path}", "path": str(path)}
    if not isinstance(document, dict) or document.get("state") not in STATES:
        return {"peer": peer, "state": "failed", "detail": f"安装状态文件结构不合法：{path}", "path": str(path)}
    document.setdefault("path", str(path))
    return document


def write_state(
    peer: str,
    state: str,
    root: Path | None = None,
    *,
    detail: str = "",
    step: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """写状态；非法跳转直接抛错，不做「尽力而为」写盘。"""
    if state not in STATES:
        raise IllegalTransition(f"未知安装状态：{state!r}")
    previous = read_state(peer, root)
    before = str(previous.get("state") or "not_installed")
    if state != before and state not in TRANSITIONS.get(before, ()):
        raise IllegalTransition(f"不允许的状态跳转（{peer}）：{before} → {state}")

    path = state_path(peer, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    document: dict[str, Any] = {
        "peer": peer,
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


def read_all(root: Path | None = None) -> dict[str, dict[str, Any]]:
    """六个 peer 的状态汇总，给向导页一次读全。"""
    from ladderstock.modules.installer import manifest

    return {item.key: read_state(item.key, root) for item in manifest.PEERS}


__all__ = [
    "STATES",
    "TRANSITIONS",
    "IllegalTransition",
    "read_all",
    "read_state",
    "state_dir",
    "state_path",
    "write_state",
]
