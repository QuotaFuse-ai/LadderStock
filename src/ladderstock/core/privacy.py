"""隐私出口的唯一口径：私有字段集合、递归剥离、地址脱敏。

为什么集中在这里：`PRIVATE_KEYS` 原先在 `web/server.py` 与 `mcp/server.py`
各写一份，一次补齐只改了一处（audit F-2）；`export` 又只 `pop` 顶层 `journal`
（audit R-2）。三处各写一半，漏一个字段就漏一个出口。现在只有这一份：

- web 响应（`_strip_private`）、
- MCP 工具结果（`_strip_private`）、
- `ldst export` 复盘 JSON（R-2）

都从这里取集合与实现；新增隐私字段只改 `PRIVATE_KEYS`。

`mask_url()` 解决另一类出口：报错/检视面里的 peer 地址可能带查询串凭据
（audit R-3），统一打码成 `scheme://host[:port]/path`，丢查询串与 userinfo。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

#: 任何出口里都不允许出现的隐私字段名（中英文都列，防止上游换一种写法漏过来）。
PRIVATE_KEYS: tuple[str, ...] = (
    "journal",
    "trades",
    "positions",
    "holdings",
    "watchlist",
    "交易日志",
    "持仓",
    "自选",
)

_PRIVATE_SET = frozenset(key.lower() for key in PRIVATE_KEYS)

UNPARSEABLE_URL = "<无法解析的地址>"


def strip_private(value: Any) -> Any:
    """递归剥掉隐私字段：隐私数据永不出现在任何出口里（任意层级）。"""
    if isinstance(value, dict):
        return {
            key: strip_private(item)
            for key, item in value.items()
            if str(key).lower() not in _PRIVATE_SET
        }
    if isinstance(value, list):
        return [strip_private(item) for item in value]
    return value


def private_keys_present(value: Any) -> list[str]:
    """递归列出命中的隐私字段名（用于导出时向用户明示「剥掉了哪些」）。"""
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                if str(key).lower() in _PRIVATE_SET:
                    name = str(key)
                    if name not in found:
                        found.append(name)
                walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(value)
    return found


def mask_url(url: str) -> str:
    """把地址打码成 `scheme://host[:port]/path`：丢掉 userinfo、查询串与 fragment。

    只保留「哪个 peer、哪个路径」这两个排查必需的信息；`?token=...` 之类的
    凭据永远不会随报错落到 stderr 或工具结果里（audit R-3）。
    """
    text = str(url or "").strip()
    if not text:
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return UNPARSEABLE_URL
    host = parts.hostname
    if not parts.scheme or not host:
        return UNPARSEABLE_URL
    try:
        port = parts.port
    except ValueError:  # 端口非法（urlsplit 延迟解析）
        port = None
    netloc = f"[{host}]" if ":" in host and not host.startswith("[") else host
    if port is not None:
        netloc = f"{netloc}:{port}"
    return f"{parts.scheme}://{netloc}{parts.path or ''}"


__all__ = [
    "PRIVATE_KEYS",
    "mask_url",
    "private_keys_present",
    "strip_private",
]
