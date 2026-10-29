"""旧端口提示（DEC-004）：只提示，不接管、不回退、不重试。

`5940` / `8770` 是前身 vibe-astock 的默认端口；v1.0 起看板固定 `5950`、MCP 固定
`5951`。用户**显式** `--port` 敲了旧端口时，这里只在 stderr 补一句说明，随后仍按
原逻辑执行（退出码不变）——绝不自动改端口、绝不自动重试：

- 自动换端口会让人以为「模型设置/对话记录丢了」（IndexedDB 按 origin 隔离）；
- 自动回退旧端口等于把旧版行为偷偷带回来，与新契约并存只会更难排查。

本模块是**唯一**允许出现旧端口字面量的运行时代码（`tests/compat` 守住这条：
旧端口只配当提示，不许参与 bind / 重试逻辑）。
"""

from __future__ import annotations

import sys

#: 旧端口 → v1.0 起的固定端口（看板 5940→5950，MCP 8770→5951）。
LEGACY_PORTS: dict[int, int] = {5940: 5950, 8770: 5951}


def legacy_port_hint(port: int) -> str:
    """旧端口的提示文案；不是旧端口返回空串（便于直接断言文案）。"""
    replacement = LEGACY_PORTS.get(int(port))
    if replacement is None:
        return ""
    return (
        f"ldst: {port} 是旧端口，v1.0 起固定用 {replacement}；"
        "本程序不会自动切换端口（DEC-004）"
    )


def warn_legacy_port(port: int) -> bool:
    """显式请求旧端口时在 stderr 补一句提示；返回是否命中（命中也不改后续行为）。"""
    hint = legacy_port_hint(port)
    if not hint:
        return False
    sys.stderr.write(f"{hint}\n")
    sys.stderr.flush()
    return True


__all__ = ["LEGACY_PORTS", "legacy_port_hint", "warn_legacy_port"]
